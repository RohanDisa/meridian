"""Test double for chat planning.

The live app does not call this module. Tests reach it when llm=False, and the
pytest runner does too so the suite does not call a live model.
"""

from __future__ import annotations

import re

from meridian.tools import (
    find_part,
    get_part_facts,
    interfaces_of,
    open_visual,
    parts_by_material,
    parts_in_subsystem,
    propose_correction,
)

_HANDLERS = {
    "find_part": lambda conn, args: find_part(conn, args["query"]),
    "get_part_facts": lambda conn, args: get_part_facts(
        conn, bom_row_id=args.get("bom_row_id"), drawing_id=args.get("drawing_id")
    ),
    "interfaces_of": lambda conn, args: interfaces_of(
        conn, args["bom_row_id"], hops=args.get("hops", 1), include_incoming=args.get("include_incoming", True)
    ),
    "open_visual": lambda conn, args: open_visual(conn, args["drawing_id"]),
    "parts_by_material": lambda conn, args: parts_by_material(conn, args["material"]),
    "parts_in_subsystem": lambda conn, args: parts_in_subsystem(conn, args["name"]),
    "propose_correction": lambda conn, args: propose_correction(
        conn,
        entity_type=args["entity_type"],
        entity_id=args["entity_id"],
        field=args["field"],
        new_value=args["new_value"],
        reason=args.get("reason"),
        part=args.get("part"),
    ),
}


def plan_tools(question: str) -> list[dict]:
    text = question.lower()
    drawing_id = _drawing_id(text)
    if "interface" in text or "connect" in text:
        return [
            {"tool": "find_part", "args": {"query": _focus_query(question)}},
            {"tool": "interfaces_of", "args": {"from_find": True}},
        ]
    if "stainless" in text or ("which" in text and "material" in text):
        material = "stainless" if "stainless" in text else _focus_query(question)
        return [{"tool": "parts_by_material", "args": {"material": material}}]
    if "subsystem" in text or "z-axis" in text or "recoater" in text and "which" in text:
        return [{"tool": "parts_in_subsystem", "args": {"name": _focus_query(question)}}]
    if "correct" in text and " to " in text:
        return [
            {"tool": "find_part", "args": {"query": _focus_query(question)}},
            {"tool": "propose_correction", "args": {"from_find": True, "raw": question}},
        ]
    steps = [{"tool": "find_part", "args": {"query": drawing_id or _focus_query(question)}}]
    steps.append({"tool": "get_part_facts", "args": {"from_find": True}})
    if drawing_id or _hinted_drawing(question) or any(
        token in text
        for token in (
            "drawing",
            "3d",
            "open",
            "show",
            "plate",
            "wiper",
            "galvo",
            "bundplade",
            "silicone",
            "silikone",
        )
    ):
        steps.append({"tool": "open_visual", "args": {"from_find": True}})
    return steps


def answer_with_keywords(conn, question: str) -> dict:
    plan = plan_tools(question)
    tool_results = []
    found_items = []
    attachments = []
    citations = []
    uncertainty = None

    for step in plan:
        args = dict(step["args"])
        if args.pop("from_find", False):
            if not found_items:
                continue
            first = found_items[0]
            if step["tool"] == "interfaces_of" and first.get("bom_row_id"):
                args["bom_row_id"] = first["bom_row_id"]
            elif step["tool"] == "get_part_facts":
                args["bom_row_id"] = first.get("bom_row_id")
                args["drawing_id"] = first.get("drawing_id")
            elif step["tool"] == "open_visual":
                args["drawing_id"] = first.get("drawing_id") or _hinted_drawing(question)
                if not args.get("drawing_id"):
                    continue
            elif step["tool"] == "propose_correction" and first.get("bom_row_id"):
                field, new_value = _parse_correction(args.pop("raw", question))
                if not field or not new_value:
                    continue
                args.update(
                    {
                        "entity_type": "bom_row",
                        "entity_id": str(first["bom_row_id"]),
                        "part": first.get("name"),
                        "field": field,
                        "new_value": new_value,
                        "reason": question,
                    }
                )
            else:
                continue
        result = _HANDLERS[step["tool"]](conn, args)
        tool_results.append({"tool": step["tool"], "result": result})
        citations.extend(result.get("citations") or [])
        if step["tool"] == "find_part":
            found_items = result.get("items") or []
            asked = _drawing_id(question) or _hinted_drawing(question)
            if asked:
                found_items = sorted(
                    found_items,
                    key=lambda item: 0 if item.get("drawing_id") == asked else 1,
                )
            if result.get("uncertainty"):
                uncertainty = result["uncertainty"]
        if step["tool"] == "open_visual" and result.get("drawing_id"):
            attachments.append(
                {
                    "drawing_id": result["drawing_id"],
                    "path": result.get("path"),
                    "reconstruction": result.get("reconstruction"),
                    "bom_rows": result.get("bom_rows") or [],
                }
            )
        if result.get("uncertainty") and step["tool"] != "find_part":
            uncertainty = result["uncertainty"]

    if not found_items and any(step["tool"] == "find_part" for step in plan) and not attachments:
        uncertainty = "unsupported"

    asked = _drawing_id(question) or _hinted_drawing(question)
    if asked and not any(item.get("drawing_id") == asked for item in attachments):
        visual = open_visual(conn, asked)
        if visual.get("drawing_id"):
            attachments[:] = [
                {
                    "drawing_id": visual["drawing_id"],
                    "path": visual.get("path"),
                    "reconstruction": visual.get("reconstruction"),
                    "bom_rows": visual.get("bom_rows") or [],
                }
            ]
            tool_results.append({"tool": "open_visual", "result": visual})
            if not found_items:
                found_items = [
                    {
                        "name": visual.get("title") or asked,
                        "drawing_id": visual["drawing_id"],
                        "part_family": None,
                    }
                ]
                uncertainty = None

    text = _render_answer(question, found_items, tool_results, uncertainty).replace("**", "")
    return {
        "text": text,
        "citations": citations,
        "attachments": attachments,
        "uncertainty": uncertainty,
        "tool_results": tool_results,
    }


def _render_answer(question: str, found_items: list, tool_results: list, uncertainty: str | None) -> str:
    visual = next((item["result"] for item in tool_results if item["tool"] == "open_visual"), None)
    if uncertainty == "unsupported" and not found_items and not visual:
        return (
            "Not enough evidence in the extracted BOM and drawings to answer that. "
            "The question is outside this OpenLPBF v2 corpus or does not match a known part."
        )
    chunks = []
    facts = next((item["result"] for item in tool_results if item["tool"] == "get_part_facts"), None)
    interfaces = next((item["result"] for item in tool_results if item["tool"] == "interfaces_of"), None)
    materials = next((item["result"] for item in tool_results if item["tool"] == "parts_by_material"), None)
    proposal = next((item["result"] for item in tool_results if item["tool"] == "propose_correction"), None)
    if found_items:
        first = found_items[0]
        chunks.append(
            f"{first.get('name') or first.get('drawing_id')} "
            f"({first.get('part_family') or 'unknown family'})."
        )
    if facts:
        current = facts.get("current_value") or (facts.get("current") or {}).get("material")
        bom_mat = facts.get("original_value") or (facts.get("bom") or {}).get("material")
        draw_mat = (facts.get("drawing") or {}).get("material")
        if current:
            chunks.append(f"Current material: {current}.")
        if bom_mat:
            chunks.append(f"BOM material: {bom_mat}.")
        if draw_mat:
            chunks.append(f"Drawing material: {draw_mat}.")
        if facts.get("conflict"):
            chunks.append("Drawing and BOM disagree; both values are kept.")
    if interfaces:
        names = [item["to_name"] for item in interfaces.get("items", [])]
        if names:
            chunks.append("Interfaces with: " + ", ".join(names) + ".")
        elif interfaces.get("uncertainty") == "missing":
            chunks.append("No outgoing Interface with names were recorded for this row.")
    if materials:
        names = [item["name"] for item in materials.get("items", [])[:12]]
        chunks.append("Matching BOM rows: " + ", ".join(names) + ".")
    if visual:
        chunks.append(f"Opening drawing {visual.get('drawing_id')}.")
        names = [row.get("name") for row in (visual.get("bom_rows") or []) if row.get("name")]
        if len(names) > 1:
            chunks.append(
                "Linked BOM rows: "
                + ", ".join(names)
                + ". The drawing title does not match a single BOM Name; both claims are kept."
            )
        elif names:
            chunks.append(f"BOM row: {names[0]}.")
        if visual.get("reconstruction"):
            chunks.append(visual["reconstruction"]["disclaimer"])
    if proposal:
        chunks.append(
            f"Proposed correction #{proposal.get('id')}: "
            f"{proposal.get('old_value')} → {proposal.get('new_value')}. "
            "It stays pending until accepted on the Review page."
        )
    return " ".join(chunks) if chunks else "Not enough evidence to answer from the extracted knowledge."


def _parse_correction(question: str) -> tuple[str | None, str | None]:
    match = re.search(r"material\s+to\s+(.+)$", question, re.I)
    if match:
        return "material", match.group(1).strip().rstrip(".")
    match = re.search(r"to\s+(.+)$", question, re.I)
    if match:
        return "material", match.group(1).strip().rstrip(".")
    return None, None


def _drawing_id(text: str) -> str | None:
    match = re.search(r"\bd-?(\d{3})\b", text, re.I)
    if match:
        return f"D-{match.group(1)}"
    return None


def _hinted_drawing(question: str) -> str | None:
    text = question.lower()
    hints = (
        ("recoater plate", "D-013"),
        ("silikone wiper", "D-015"),
        ("silicone wiper", "D-015"),
        ("galvo bundplade", "D-016"),
        ("bundplade", "D-016"),
        ("build plate", "D-026"),
        ("byggeplade", "D-026"),
        ("wiper", "D-015"),
        ("galvo", "D-016"),
    )
    for token, drawing_id in hints:
        if token in text:
            return drawing_id
    return None


def _focus_query(question: str) -> str:
    named = re.search(r"correct(?:\s+the)?\s+(.+?)\s+material\s+to", question, re.I)
    if named:
        return named.group(1).strip()
    cleaned = re.sub(r"[?!,.;:]+", " ", question)
    cleaned = re.sub(
        r"\b(what|which|who|where|does|the|a|an|about|tell|me|open|show|drawing|3d|reconstruction|interface|with|and|correct|material|please)\b",
        " ",
        cleaned,
        flags=re.I,
    )
    return re.sub(r"\s+", " ", cleaned).strip() or question
