from __future__ import annotations

import json
import os
import re
from pathlib import Path

from meridian.models import ANTHROPIC_MODEL_FALLBACKS, CHAT_MODELS
from meridian.paths import REPO_ROOT
from meridian.tools import (
    find_part,
    correction_history,
    get_part_facts,
    interfaces_of,
    knowledge_version,
    open_visual,
    parts_by_material,
    parts_in_subsystem,
    propose_correction,
)

TOOL_HANDLERS = {
    "find_part": lambda conn, args: find_part(conn, args["query"]),
    "get_part_facts": lambda conn, args: get_part_facts(
        conn,
        bom_row_id=args.get("bom_row_id"),
        drawing_id=args.get("drawing_id"),
    ),
    "interfaces_of": lambda conn, args: interfaces_of(
        conn,
        args["bom_row_id"],
        hops=args.get("hops", 1),
        include_incoming=args.get("include_incoming", True),
    ),
    "correction_history": lambda conn, args: {
        "items": correction_history(conn, args.get("part")),
        "uncertainty": None,
    },
    "open_visual": lambda conn, args: open_visual(conn, args["drawing_id"]),
    "parts_by_material": lambda conn, args: parts_by_material(conn, args["material"]),
    "parts_in_subsystem": lambda conn, args: parts_in_subsystem(conn, args["name"]),
    "propose_correction": lambda conn, args: propose_correction(
        conn,
        **{
            key: args[key]
            for key in (
                "entity_type",
                "entity_id",
                "field",
                "new_value",
                "reason",
                "check_notes",
                "old_value",
                "part",
            )
            if key in args
        },
    ),
}

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_part",
            "description": (
                "Find BOM rows and linked drawings. Pass the user's part tokens as written "
                "first, including odd names (GLAMS) and typos (platfrm). The tool fuzzy-matches "
                "typos to known names. Do not replace an unusual token with a common English "
                "lookalike. Drawing ids like D-026 are also valid. Use this first for any named part."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_part_facts",
            "description": (
                "Get BOM and drawing materials for a part. Read speak_notes in the result "
                "and follow them in the user answer. Do not pick a winner when two BOM "
                "lines claim one drawing. Quote both metal strings when they differ."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "bom_row_id": {"type": "integer"},
                    "drawing_id": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "interfaces_of",
            "description": (
                "List Interface with targets for a BOM row. Use after find_part. "
                "Set include_incoming true for 'who connects / interfaces / mentions' questions "
                "(default is true)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "bom_row_id": {"type": "integer"},
                    "include_incoming": {"type": "boolean"},
                    "hops": {"type": "integer", "minimum": 1, "maximum": 3},
                },
                "required": ["bom_row_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_visual",
            "description": (
                "Open a drawing and any stored 3D reconstruction. Only pass a drawing_id that "
                "find_part or get_part_facts just returned for the asked part. If that part has "
                "no drawing_id, do not open another sheet. This corpus has no native CAD; 3D is "
                "a stored reconstruction from the 2D drawing."
            ),
            "parameters": {
                "type": "object",
                "properties": {"drawing_id": {"type": "string"}},
                "required": ["drawing_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parts_by_material",
            "description": "List BOM rows whose material field matches a material word.",
            "parameters": {
                "type": "object",
                "properties": {"material": {"type": "string"}},
                "required": ["material"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "parts_in_subsystem",
            "description": (
                "List every BOM row in a subsystem or part family (box, recoater, z-axis). "
                "z-axis maps to Build-plate. Items include csv_line and link_status. "
                "part_family is the BOM family; drawing_subsystem is only the linked sheet folder."
            ),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_correction",
            "description": (
                "Propose a field correction. It stays pending until a reviewer accepts it. "
                "For a BOM row, pass part as the BOM name from find_part, and entity_id as "
                "that item's bom_row_id. Never use csv_line or file_line as entity_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {"type": "string", "enum": ["bom_row", "drawing"]},
                    "entity_id": {
                        "type": "string",
                        "description": (
                            "bom_row_id from find_part or get_part_facts, as a string. "
                            "Not csv_line and not file_line."
                        ),
                    },
                    "part": {
                        "type": "string",
                        "description": "BOM name from find_part. Required for entity_type bom_row.",
                    },
                    "field": {"type": "string"},
                    "new_value": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["entity_type", "entity_id", "field", "new_value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "correction_history",
            "description": "List correction events. Pass part to limit the list to one BOM name.",
            "parameters": {
                "type": "object",
                "properties": {"part": {"type": "string"}},
            },
        },
    },
]

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = CHAT_MODELS["groq"]
OPENAI_DEFAULT_MODEL = CHAT_MODELS["openai"]
ANTHROPIC_DEFAULT_MODEL = CHAT_MODELS["anthropic"]

_active_provider: str | None = None
_resolved_anthropic_model: str | None = None


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or (REPO_ROOT / ".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ[key] = value


load_dotenv()


SYSTEM_PROMPT = """You are Meridian. You answer questions about the OpenLPBF v2 machine after reading a BOM spreadsheet and extracted drawings. You sound like a person who opened those sources, not like software.

How to write
- Plain sentences only. Never use markdown bold. Do not use the characters ** anywhere. Do not wrap words in asterisks or underscores.
- Never say identity_conflict, material_relation, link_status, candidate, flag, or "the system flags". If speak_notes is present, follow that wording.
- When you name a line in the file, use file_line. That is the physical line where the CSV record starts. csv_line is the record number and stays in the tool JSON. Never treat bom_row_id or bom.id as a line in the spoken answer. Never write #27 or row 27 for an internal id.
- When you call get_part_facts, interfaces_of, or propose_correction, bom_row_id and entity_id must be bom_row_id from find_part. Never pass csv_line or file_line there. For propose_correction on a BOM row, also pass part as that BOM name.
- Supplier, supplier order, link, and cost come from recorded on the tool result. Say they are recorded values from the pinned BOM at that source_revision, not a current price or current availability. If uncertainty is missing, the field is blank. Do not estimate it.
- Do not tell the user to download a PDF. The app shows the drawing in place.
- If a list comes back, name every item. Do not drop rows.

How to read names
- The roster below is every real BOM name and drawing title in this corpus.
- A close misspelling of a roster name is that part. A name that is not close to any roster name is not here. Say there is not enough evidence.
- Call find_part with the user's tokens first (glams, platfrm, boxx). Do not rewrite an unusual roster name into a common English lookalike. GLAMS is a BOM name, not clamps.

How to use tools
- Never invent parts, materials, interfaces, or 3D geometry. Only use tool results.
- Usual order: find_part, then get_part_facts and/or interfaces_of, then open_visual only if that same result has a drawing_id for the asked part.
- If find_part returns no items, stop. Do not open a substitute drawing.
- If the user asks for CAD or a 3D file, say this corpus has no native CAD. Open the drawing if one exists and mention the stored reconstruction only.
- For connected / interface / who-mentions questions, call interfaces_of and report mentioned_by as other parts that list this name.
- part_family is the BOM family. drawing_subsystem is only the folder the sheet was stored in.

How to turn tool JSON into an answer
- Tool JSON is for you. Do not repeat field names. Speak the facts.
- Two BOM lines claim one drawing: name both parts and both CSV lines. Say they disagree on the part name. Do not pick a winner and do not collapse them into one name.
- Aluminium vs EN-AW or 3.3315, and stainless vs AISI 316: if no correction has been accepted, quote both strings and say they disagree. You may add that the drawing name is a more specific grade of the same alloy. If the drawing string looks cut off, say it is cut off.
- If an accepted correction sets a current material, the first sentence is that value for that part. Then cite the original BOM cell and the drawing. Do not say that part is still only the original BOM word. Another BOM row that was not corrected keeps its own cell.
- current_value is the material to state. current_source says whether it comes from the BOM, the drawing, or an accepted correction (id and date). original_value is the unchanged cell. Cite original_citation.
- If current_source is an accepted correction, the first sentence is current_value for that part, and it names the correction. Then cite original_value. Do not say that part is still only the original cell.
- conflict is false once an accepted correction resolves the field. conflict_history is the earlier disagreement. Do not describe that field as unsettled or awaiting review.
- pending_material means a different proposal is still waiting. If it is absent, do not say awaiting review.
- If propose_correction returns already_accepted, the value is already current. Do not say you filed a new proposal.
- Tool results in the current turn override anything stated in earlier turns.
- Earlier turns only resolve words like "that", "now", and "the plate".
- An unverified drawing link: say the link is unverified. A confirmed or manual link is a firm match.
- Say clearly when a drawing has no 3D reconstruction.

Example of tone (follow this style, do not copy the facts unless the tools return them)
Bad: The system flags an identity_conflict. BOM rows #4 and #9 say **Brass**.
Good: Two BOM lines both point at drawing D-900 (Sample Bracket): Left sample arm on CSV line 4 and Right sample arm on CSV line 9. Both lines say Brass. The title block was read as CuZn39 with the rest cut off. Those strings are recorded as different. CuZn39 is a more specific brass alloy, but the wording does not match. Which of the two BOM names the sheet is has not been settled.
"""


def corpus_roster(conn) -> str:
    names = [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM bom_rows WHERE name IS NOT NULL AND trim(name) != '' "
            "ORDER BY source_row, id"
        )
    ]
    drawings = [
        f"{row['id']}: {row['title_extracted'] or '(no title)'}"
        for row in conn.execute("SELECT id, title_extracted FROM drawings ORDER BY id")
    ]
    return (
        "Known BOM part names in this corpus. A close misspelling of one of these is that part. "
        "A user name that is not close to any of these is not in the corpus; do not invent one "
        "or swap it for a lookalike English word (GLAMS is a part name, not clamps):\n"
        + ", ".join(names)
        + "\n\nKnown drawings (id: title):\n"
        + "; ".join(drawings)
    )


def _system_prompt(conn) -> str:
    return SYSTEM_PROMPT + "\n\n" + corpus_roster(conn)


def _wants_opened_visual(question: str) -> bool:
    return bool(re.search(r"\b(open|show|cad|3d)\b", question, flags=re.I))


def _strip_markdown_emphasis(text: str) -> str:
    return (text or "").replace("**", "").replace("__", "")


_PROVIDER_ORDER = ("groq", "anthropic", "openai")
_PROVIDER_KEYS = {
    "groq": "GROQ_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
}
_PROVIDER_LABELS = {"groq": "Groq", "anthropic": "Anthropic", "openai": "OpenAI"}


def _available_providers() -> list[str]:
    return [name for name in _PROVIDER_ORDER if os.environ.get(_PROVIDER_KEYS[name])]


def _chat_client_config(provider: str | None = None) -> dict:
    provider = provider or _active_provider
    if not provider:
        providers = _available_providers()
        provider = providers[0] if providers else None
    if provider == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return {
            "provider": "anthropic",
            "api_key": os.environ["ANTHROPIC_API_KEY"],
            "base_url": None,
            "model": os.environ.get("MERIDIAN_ANTHROPIC_MODEL", ANTHROPIC_DEFAULT_MODEL),
        }
    if provider == "groq" and os.environ.get("GROQ_API_KEY"):
        return {
            "provider": "groq",
            "api_key": os.environ["GROQ_API_KEY"],
            "base_url": os.environ.get("MERIDIAN_CHAT_BASE_URL", GROQ_BASE_URL),
            "model": os.environ.get("MERIDIAN_GROQ_MODEL")
            or os.environ.get("MERIDIAN_CHAT_MODEL", GROQ_DEFAULT_MODEL),
        }
    if provider == "openai" and os.environ.get("OPENAI_API_KEY"):
        return {
            "provider": "openai",
            "api_key": os.environ["OPENAI_API_KEY"],
            "base_url": os.environ.get("MERIDIAN_CHAT_BASE_URL"),
            "model": os.environ.get("MERIDIAN_OPENAI_MODEL")
            or os.environ.get("MERIDIAN_CHAT_MODEL", OPENAI_DEFAULT_MODEL),
        }
    for name in _available_providers():
        return _chat_client_config(name)
    raise RuntimeError("no chat API key in ANTHROPIC_API_KEY, GROQ_API_KEY, or OPENAI_API_KEY")


class _FnCall:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, ident, name, arguments):
        self.id = ident
        self.function = _FnCall(name, arguments)


class _ChatMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


def _anthropic_tools(tools: list) -> list:
    converted = []
    for item in tools:
        fn = item.get("function") or item
        schema = dict(fn.get("parameters") or {"type": "object", "properties": {}})
        schema.setdefault("type", "object")
        converted.append(
            {
                "name": fn["name"],
                "description": fn.get("description") or "",
                "input_schema": schema,
            }
        )
    return converted


def _to_anthropic_messages(messages: list) -> tuple[str, list]:
    system_parts = []
    converted = []
    pending_tools = []

    def flush_tools():
        if pending_tools:
            converted.append({"role": "user", "content": list(pending_tools)})
            pending_tools.clear()

    for message in messages:
        role = message["role"]
        if role == "system":
            system_parts.append(message.get("content") or "")
            continue
        if role == "tool":
            pending_tools.append(
                {
                    "type": "tool_result",
                    "tool_use_id": message["tool_call_id"],
                    "content": message.get("content") or "",
                }
            )
            continue
        flush_tools()
        if role == "user":
            converted.append({"role": "user", "content": message.get("content") or ""})
            continue
        if role == "assistant":
            content = []
            text = message.get("content") or ""
            if text:
                content.append({"type": "text", "text": text})
            for call in message.get("tool_calls") or []:
                raw_args = call["function"]["arguments"]
                if isinstance(raw_args, str):
                    try:
                        parsed = json.loads(raw_args or "{}")
                    except json.JSONDecodeError:
                        parsed = {}
                else:
                    parsed = raw_args or {}
                content.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "input": parsed,
                    }
                )
            if content:
                converted.append({"role": "assistant", "content": content})
    flush_tools()
    return "\n\n".join(part for part in system_parts if part), converted


def _anthropic_models() -> list[str]:
    if _resolved_anthropic_model:
        return [_resolved_anthropic_model]
    models = []
    explicit = os.environ.get("MERIDIAN_ANTHROPIC_MODEL")
    if explicit:
        models.append(explicit)
    for model in ANTHROPIC_MODEL_FALLBACKS:
        if model not in models:
            models.append(model)
    return models


def _complete_anthropic(messages: list, tools: list, tool_choice: dict):
    import anthropic

    global _resolved_anthropic_model
    cfg = _chat_client_config("anthropic")
    client = anthropic.Anthropic(api_key=cfg["api_key"])
    system, converted = _to_anthropic_messages(messages)
    last_exc = None
    response = None
    for model in _anthropic_models():
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=system,
                tools=_anthropic_tools(tools),
                tool_choice=tool_choice,
                messages=converted,
            )
            _resolved_anthropic_model = model
            break
        except Exception as exc:
            last_exc = exc
            text = str(exc).lower()
            if "not_found_error" in text or "not_found" in text or "404" in text:
                _resolved_anthropic_model = None
                continue
            raise
    if response is None:
        raise last_exc or RuntimeError("Anthropic returned no response")
    text_parts = []
    tool_calls = []
    for block in response.content:
        kind = getattr(block, "type", None)
        if kind == "text":
            text_parts.append(block.text or "")
        elif kind == "tool_use":
            tool_calls.append(
                _ToolCall(block.id, block.name, json.dumps(block.input or {}))
            )
    return _ChatMessage("".join(text_parts), tool_calls or None)


def _complete_openai_compatible(messages: list, tools: list, provider: str, tool_choice: str):
    from openai import OpenAI

    cfg = _chat_client_config(provider)
    client_kwargs = {"api_key": cfg["api_key"]}
    if cfg.get("base_url"):
        client_kwargs["base_url"] = cfg["base_url"]
    client = OpenAI(**client_kwargs)
    response = client.chat.completions.create(
        model=cfg["model"],
        messages=messages,
        tools=tools,
        tool_choice=tool_choice,
        temperature=0.2,
    )
    return response.choices[0].message


def _provider_tool_choice(provider: str, tool_choice: str):
    if provider == "anthropic":
        return {"type": "any"} if tool_choice == "required" else {"type": "auto"}
    return tool_choice


def complete_chat(messages: list, tools: list, tool_choice: str = "auto"):
    provider = _active_provider
    if not provider:
        providers = _available_providers()
        provider = providers[0] if providers else None
    if provider == "anthropic":
        return _complete_anthropic(messages, tools, _provider_tool_choice(provider, tool_choice))
    if provider in {"groq", "openai"}:
        return _complete_openai_compatible(
            messages, tools, provider, _provider_tool_choice(provider, tool_choice)
        )
    raise RuntimeError("no chat provider configured")


def _use_llm(llm: bool | None) -> bool:
    if llm is False:
        return False
    if llm is True:
        return True
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return bool(_available_providers())


def _more_specific_grade(left: str, right: str) -> bool:
    a = left.lower()
    b = right.lower()
    pairs = (
        ("aluminium", "en-aw"),
        ("aluminium", "3.3315"),
        ("aluminum", "en-aw"),
        ("aluminum", "3.3315"),
        ("stainless", "aisi"),
    )
    return any((token in a and grade in b) or (grade in a and token in b) for token, grade in pairs)


def _metal_looks_cut_off(value: str | None) -> bool:
    if not value:
        return False
    text = value.strip()
    return text.endswith("(") or (text.startswith("3.3315") and not text.endswith(")"))


def _speak_notes(result: dict) -> list[str]:
    notes: list[str] = []
    part = (result.get("checks") or {}).get("part_name")
    if part and result.get("status") in {"pending", "accepted"}:
        notes.append(
            f"This proposal is for {part}. Name that part. Do not attach it to a different BOM name."
        )
    linked = result.get("linked_bom") or []
    if result.get("identity_conflict"):
        bits = []
        for row in linked:
            name = row.get("name") or "unnamed"
            line = row.get("csv_line")
            bits.append(f"{name} on CSV line {line}" if line else name)
        if bits:
            corrected = [
                row.get("name")
                for row in linked
                if row.get("current_material") and row.get("current_material") != row.get("material")
            ]
            tail = " Name every line. Do not decide which name the drawing belongs to."
            if corrected:
                tail += " Do say the accepted material of " + " and ".join(corrected) + "."
            else:
                tail += " Do not pick which name is correct."
            notes.append(
                "Two or more BOM lines claim this drawing: " + "; ".join(bits) + "." + tail
            )
    bom_mat = result.get("original_value") or (result.get("bom") or {}).get("material")
    draw_mat = (result.get("drawing") or {}).get("material")
    current = result.get("current_value")
    if current is None:
        current = (result.get("current") or {}).get("material")
    relation = result.get("material_relation")
    accepted = bool(current and bom_mat and current.strip() != bom_mat.strip())
    if accepted:
        name = (result.get("bom") or {}).get("name") or "This part"
        source = result.get("current_source") or {}
        correction_id = source.get("correction_id")
        when = (source.get("date") or "")[:10]
        named = (
            f"correction #{correction_id}" + (f" reviewed {when}" if when else "")
            if correction_id
            else "the accepted correction"
        )
        note = (
            f"An accepted correction sets the current material of {name} to {current!r}. "
            f"The first sentence must say {name} is now {current} and must name {named}. "
            f"The original BOM cell for {name} is still {bom_mat!r}."
        )
        others = [
            row
            for row in linked
            if row.get("name") and row.get("name") != name and row.get("material")
        ]
        if others:
            note += " Rows that were not corrected: " + "; ".join(
                f"{row['name']} is still {row['material']!r}" for row in others
            ) + "."
        if draw_mat:
            note += f" The drawing text is still {draw_mat!r}. Cite it."
        history = result.get("conflict_history") or []
        if history:
            note += " Earlier disagreement, now resolved: " + " ".join(history) + "."
        note += " There is no correction waiting on Review. Do not say the change is pending."
        notes.append(note)
    elif (relation == "conflict" or result.get("conflict")) and bom_mat and draw_mat:
        if _more_specific_grade(bom_mat, draw_mat):
            notes.append(
                f"The stored strings disagree. Quote BOM {bom_mat!r} and drawing {draw_mat!r}. "
                "Flag them as different. You may add that the drawing name is a more specific grade of the same alloy."
            )
        else:
            notes.append(
                f"The metals disagree. Quote BOM {bom_mat!r} and drawing {draw_mat!r}."
            )
        if _metal_looks_cut_off(draw_mat):
            notes.append("The drawing alloy string looks cut off. Say that.")
    elif relation == "same_family" and bom_mat and draw_mat:
        notes.append(
            f"Quote both metal strings: BOM {bom_mat!r} and drawing {draw_mat!r}. "
            "Same metal family, not the same wording."
        )
    if result.get("bom") and result["bom"].get("id") is not None:
        notes.append(
            "bom.id is not an Excel line. When you name a line in the file, use file_line. "
            "Never write # and that id."
        )
    rows = [row for row in list(result.get("items") or []) + list(linked) if isinstance(row, dict)]
    if any(row.get("link_status") == "candidate" for row in rows):
        notes.append("A candidate drawing link is unverified. Say unverified, not candidate.")
    return notes


def _compact_tool_result(result: dict) -> dict:
    payload = {}
    for key in (
        "items",
        "item_count",
        "mentioned_by",
        "uncertainty",
        "drawing_id",
        "title",
        "pages",
        "current_value",
        "current_source",
        "original_value",
        "original_citation",
        "conflict",
        "conflict_history",
        "bom",
        "linked_bom",
        "drawing",
        "id",
        "status",
        "old_value",
        "new_value",
        "already_accepted",
        "check_notes",
        "checks",
    ):
        if key in result:
            payload[key] = result[key]
    notes = _speak_notes(result)
    if notes:
        payload["speak_notes"] = notes
    if result.get("reconstruction"):
        rec = result["reconstruction"]
        payload["reconstruction"] = {
            "drawing_id": rec.get("drawing_id"),
            "label": rec.get("label"),
            "disclaimer": rec.get("disclaimer"),
        }
    elif "reconstruction" in result:
        payload["reconstruction"] = None
    if result.get("bom_rows"):
        payload["bom_rows"] = [
            {
                "bom_row_id": row.get("bom_row_id"),
                "name": row.get("name"),
                "csv_line": row.get("csv_line"),
                "material": row.get("material"),
            }
            for row in result["bom_rows"]
        ]
    payload["citations"] = [
        {
            key: cite.get(key)
            for key in (
                "kind",
                "name",
                "field",
                "csv_line",
                "file_line",
                "drawing_id",
                "page",
                "region",
                "value",
            )
            if cite.get(key) is not None
        }
        for cite in result.get("citations") or []
    ]
    return payload


def _run_named_tool(conn, name: str, args: dict) -> dict:
    if name not in TOOL_HANDLERS:
        return {"uncertainty": "unsupported", "error": f"unknown tool {name}"}
    cleaned = dict(args)
    if cleaned.get("bom_row_id") is not None:
        cleaned["bom_row_id"] = int(cleaned["bom_row_id"])
    try:
        return TOOL_HANDLERS[name](conn, cleaned)
    except Exception as exc:
        return {"uncertainty": "unsupported", "error": str(exc)}


def _collect_from_tool(name: str, result: dict, found_items: list, attachments: list, citations: list):
    citations.extend(result.get("citations") or [])
    if name == "find_part":
        found_items[:] = result.get("items") or []
    if name == "open_visual" and result.get("drawing_id"):
        attachments.append(
            {
                "drawing_id": result["drawing_id"],
                "path": result.get("path"),
                "reconstruction": result.get("reconstruction"),
                "bom_rows": result.get("bom_rows") or [],
            }
        )


def _attach_first_drawing(
    conn, question: str, found_items: list, attachments: list, tool_results: list, citations: list
):
    if attachments or not _wants_opened_visual(question):
        return
    drawing_id = next((item.get("drawing_id") for item in found_items if item.get("drawing_id")), None)
    if not drawing_id:
        return
    visual = open_visual(conn, drawing_id)
    if visual.get("drawing_id"):
        _collect_from_tool("open_visual", visual, found_items, attachments, citations)
        tool_results.append({"tool": "open_visual", "result": visual})


def _history_version(item) -> int | None:
    if isinstance(item, dict):
        raw = item.get("knowledge_version")
    else:
        raw = getattr(item, "knowledge_version", None)
    if raw is None or raw == "":
        return None
    return int(raw)


def _knowledge_change_note(conn, since_version: int) -> str | None:
    rows = conn.execute(
        """
        SELECT e.correction_id, e.event, e.created_at,
               c.field, c.old_value, c.new_value, c.entity_type, c.entity_id,
               b.name AS part_name
        FROM correction_events e
        JOIN corrections c ON c.id = e.correction_id
        LEFT JOIN bom_rows b
          ON c.entity_type = 'bom_row' AND b.id = CAST(c.entity_id AS INTEGER)
        WHERE e.id > ? AND e.event IN ('accepted', 'rejected')
        ORDER BY e.id
        """,
        (since_version,),
    ).fetchall()
    if not rows:
        return None
    bits = []
    for row in rows:
        part = row["part_name"] or f"{row['entity_type']} {row['entity_id']}"
        when = (row["created_at"] or "")[:10]
        bits.append(
            f"#{row['correction_id']} {row['event']} {when} {part} "
            f"{row['field']} {row['old_value']} → {row['new_value']}"
        )
    return (
        "Knowledge changed since an earlier reply. Earlier replies about these parts are outdated. "
        + "; ".join(bits)
        + "."
    )


def _history_messages(history: list | None, conn=None) -> list[dict]:
    turns = []
    versions = []
    for item in (history or [])[-6:]:
        if isinstance(item, dict):
            role = item.get("role")
            text = item.get("text") or item.get("content") or ""
        else:
            role = getattr(item, "role", None)
            text = getattr(item, "text", "") or ""
        if role not in {"user", "assistant"} or not str(text).strip():
            continue
        version = _history_version(item)
        if role == "assistant" and version is not None:
            versions.append(version)
        turns.append({"role": role, "content": str(text).strip()[:800]})
    if conn is not None and versions:
        current = knowledge_version(conn)
        stale = [version for version in versions if version < current]
        if stale:
            note = _knowledge_change_note(conn, min(stale))
            if note:
                turns.append({"role": "system", "content": note})
    return turns


def _answer_with_llm(conn, question: str, history: list | None = None) -> dict:
    messages = [{"role": "system", "content": _system_prompt(conn)}]
    messages.extend(_history_messages(history, conn))
    messages.append({"role": "user", "content": question})
    tool_results = []
    found_items = []
    attachments = []
    citations = []
    uncertainty = None

    first_step = True
    for _ in range(8):
        message = complete_chat(
            messages, OPENAI_TOOLS, tool_choice="required" if first_step else "auto"
        )
        first_step = False
        tool_calls = list(message.tool_calls or [])
        if not tool_calls:
            text = (message.content or "").strip()
            _attach_first_drawing(conn, question, found_items, attachments, tool_results, citations)
            if not found_items and not attachments and not text:
                uncertainty = "unsupported"
                text = (
                    "Not enough evidence in the extracted BOM and drawings to answer that. "
                    "The question is outside this OpenLPBF v2 corpus or does not match a known part."
                )
            return {
                "text": _strip_markdown_emphasis(text),
                "citations": citations,
                "attachments": attachments,
                "uncertainty": uncertainty,
                "tool_results": tool_results,
            }

        messages.append(
            {
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in tool_calls
                ],
            }
        )
        for call in tool_calls:
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = _run_named_tool(conn, call.function.name, args)
            tool_results.append({"tool": call.function.name, "args": args, "result": result})
            _collect_from_tool(call.function.name, result, found_items, attachments, citations)
            if result.get("uncertainty") == "unsupported" and call.function.name == "find_part":
                uncertainty = "unsupported"
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.function.name,
                    "content": json.dumps(_compact_tool_result(result), default=str),
                }
            )

    _attach_first_drawing(conn, question, found_items, attachments, tool_results, citations)
    return {
        "text": "The tools ran but no final explanation was returned.",
        "citations": citations,
        "attachments": attachments,
        "uncertainty": uncertainty,
        "tool_results": tool_results,
    }


def _failure_reason(exc: Exception) -> str:
    text = str(exc).lower()
    if "credit_balance_exhausted" in text or "no credits remaining" in text or "insufficient_quota" in text:
        return "no credits left"
    if "rate" in text or "too many requests" in text or "429" in text:
        return "rate limit"
    if "invalid_api_key" in text or "invalid api key" in text or "incorrect api key" in text or "authentication" in text:
        return "key rejected"
    return exc.__class__.__name__


def _all_keys_failed_answer(failures: list[tuple[str, Exception]]) -> dict:
    labels = [_PROVIDER_LABELS[name] for name, _exc in failures]
    if len(labels) == 1:
        tried = labels[0]
    elif len(labels) == 2:
        tried = f"{labels[0]} and {labels[1]}"
    else:
        tried = ", ".join(labels[:-1]) + f", and {labels[-1]}"
    details = "; ".join(
        f"{_PROVIDER_LABELS[name]}: {_failure_reason(exc)}" for name, exc in failures
    )
    return {
        "text": f"None of the API keys worked. Tried {tried}. {details}.",
        "citations": [],
        "attachments": [],
        "uncertainty": "model_unavailable",
        "tool_results": [],
    }


def _missing_api_key_answer() -> dict:
    return {
        "text": (
            "No API key in .env. Add GROQ_API_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY, "
            "then restart the API."
        ),
        "citations": [],
        "attachments": [],
        "uncertainty": "no_api_key",
        "tool_results": [],
    }


def answer_question(conn, question: str, *, llm: bool | None = None, history: list | None = None) -> dict:
    global _active_provider
    load_dotenv()
    # Keyword checker is off in the app. Tests still reach it with llm=False,
    # and the pytest runner does too so the suite does not call a live model.
    if llm is False or (llm is None and os.environ.get("PYTEST_CURRENT_TEST")):
        from meridian.keyword_test_double import answer_with_keywords

        return _with_version(conn, answer_with_keywords(conn, question))
    providers = _available_providers()
    if not providers:
        return _with_version(conn, _missing_api_key_answer())
    failures: list[tuple[str, Exception]] = []
    for provider in providers:
        _active_provider = provider
        try:
            return _with_version(conn, _answer_with_llm(conn, question, history))
        except Exception as exc:
            failures.append((provider, exc))
            continue
        finally:
            _active_provider = None
    return _with_version(conn, _all_keys_failed_answer(failures))


def _with_version(conn, payload: dict) -> dict:
    payload["knowledge_version"] = knowledge_version(conn)
    return payload
