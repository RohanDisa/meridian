from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from rapidfuzz import fuzz

from meridian.normalize import normalize_name
from meridian.paths import DRAWINGS_DIR
from meridian.snapshot import recorded_fact

FUZZY_NAME_CUTOFF = 80
_FIND_STOP = {
    "what",
    "whats",
    "which",
    "who",
    "where",
    "when",
    "why",
    "how",
    "does",
    "do",
    "did",
    "is",
    "are",
    "was",
    "were",
    "the",
    "a",
    "an",
    "about",
    "tell",
    "me",
    "please",
    "abt",
    "anything",
    "any",
    "some",
    "with",
    "for",
    "from",
    "into",
    "onto",
    "than",
    "then",
    "vs",
    "too",
    "to",
    "of",
    "in",
    "on",
    "or",
    "and",
    "either",
    "there",
    "their",
    "this",
    "that",
    "these",
    "those",
    "mentions",
    "mention",
    "interface",
    "interfaces",
    "connected",
    "conected",
    "connect",
    "talk",
    "talks",
    "made",
    "list",
    "lists",
    "open",
    "show",
}

REGION_BOXES = {
    "title-block": {"x": 0.58, "y": 0.70, "w": 0.40, "h": 0.28},
    "views": {"x": 0.06, "y": 0.10, "w": 0.72, "h": 0.58},
}

def _query_needles(needle: str) -> list[str]:
    variants = {needle}
    if "silicone" in needle:
        variants.add(needle.replace("silicone", "silikone"))
    if "silikone" in needle:
        variants.add(needle.replace("silikone", "silicone"))
    if "bottom plate" in needle:
        variants.add(needle.replace("bottom plate", "bundplade"))
    if "bundplade" in needle:
        variants.add(needle.replace("bundplade", "bottom plate"))
    if "galvo" in needle:
        variants.add("galvo bundplade")
    if "silicon" in needle and "silicone" not in needle and "silikone" not in needle:
        variants.add(needle.replace("silicon", "silicone"))
        variants.add(needle.replace("silicon", "silikone"))
    return list(variants)


def _tokens(text: str) -> list[str]:
    return [part for part in normalize_name(text).split() if part]


def _tokens_close(left: str, right: str) -> bool:
    if left == right:
        return True
    aliases = {
        "silicon": {"silicone", "silikone"},
        "silicone": {"silicon", "silikone"},
        "silikone": {"silicon", "silicone"},
        "byggeplade": {"build"},
        "bygge": {"build"},
    }
    if right in aliases.get(left, set()) or left in aliases.get(right, set()):
        return True
    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    if len(shorter) >= 4 and longer.startswith(shorter) and len(longer) - len(shorter) <= 3:
        return True
    return fuzz.ratio(left, right) >= 80


def _token_related(query: str, name: str) -> bool:
    """True when most query tokens are the name or a close typo of a name token.

    Sharing one generic word (housing, plate) is not enough, so
    'warp drive housing' does not become Filter housing, and platfrm is not plate.
    """
    q_tokens = _tokens(query)
    n_tokens = _tokens(name)
    if not q_tokens or not n_tokens:
        return False
    matched = sum(1 for qt in q_tokens if any(_tokens_close(qt, nt) for nt in n_tokens))
    if len(q_tokens) == 1:
        return matched == 1
    return matched / len(q_tokens) >= 2 / 3


def _search_needle(query: str) -> str:
    raw = normalize_name(re.sub(r"[?!,.;:]+", " ", query))
    kept = [token for token in raw.split() if token not in _FIND_STOP]
    return " ".join(kept) or raw


def _name_score(query: str, name: str) -> int:
    return max(
        fuzz.WRatio(query, name),
        fuzz.token_set_ratio(query, name),
        fuzz.ratio(query, name),
    )


def _items_for_bom(conn, bom_row_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
          b.id AS bom_row_id,
          b.name,
          b.part_family,
          b.source_row AS csv_line,
          l.drawing_id,
          l.status AS link_status,
          l.method AS link_method
        FROM bom_rows b
        LEFT JOIN drawing_bom_links l
          ON l.bom_row_id = b.id AND l.status IN ('confirmed', 'conflict', 'candidate', 'manual')
        WHERE b.id = ?
        """,
        (bom_row_id,),
    ).fetchall()
    return [_row_to_item(row) for row in rows]


def _row_to_item(row) -> dict:
    return {
        "bom_row_id": row["bom_row_id"],
        "name": row["name"],
        "part_family": row["part_family"],
        "csv_line": row["csv_line"] if "csv_line" in row.keys() else None,
        "drawing_id": row["drawing_id"],
        "link_status": row["link_status"],
        "link_method": row["link_method"],
    }


def _fuzzy_fill_items(conn, needle: str, items: list[dict]) -> list[dict]:
    if items:
        return items
    scored: list[tuple[int, str, int | None, str | None]] = []
    for row in conn.execute("SELECT id, name, name_norm FROM bom_rows"):
        name_norm = row["name_norm"] or ""
        if not _token_related(needle, name_norm):
            continue
        score = _name_score(needle, name_norm)
        if score >= FUZZY_NAME_CUTOFF:
            scored.append((score, "bom", row["id"], None))
    for row in conn.execute("SELECT id, title_extracted FROM drawings"):
        title = normalize_name(row["title_extracted"] or "")
        target = title or normalize_name(row["id"])
        if not _token_related(needle, target):
            continue
        score = max(_name_score(needle, title), _name_score(needle, normalize_name(row["id"])))
        if score >= FUZZY_NAME_CUTOFF:
            scored.append((score, "drawing", None, row["id"]))
    scored.sort(key=lambda item: item[0], reverse=True)
    filled: list[dict] = []
    seen: set[tuple] = set()
    for _score, kind, bom_id, drawing_id in scored[:8]:
        if kind == "bom" and bom_id is not None:
            for item in _items_for_bom(conn, bom_id):
                key = (item["bom_row_id"], item["drawing_id"])
                if key in seen:
                    continue
                seen.add(key)
                filled.append(item)
            continue
        if drawing_id and not any(item.get("drawing_id") == drawing_id for item in filled):
            drawing = conn.execute(
                "SELECT id, title_extracted, subsystem FROM drawings WHERE id = ?",
                (drawing_id,),
            ).fetchone()
            if not drawing:
                continue
            key = (None, drawing["id"])
            if key in seen:
                continue
            seen.add(key)
            filled.append(
                {
                    "bom_row_id": None,
                    "name": drawing["title_extracted"],
                    "part_family": drawing["subsystem"],
                    "csv_line": None,
                    "drawing_id": drawing["id"],
                    "link_status": None,
                    "link_method": None,
                }
            )
    return filled


def _find_rank(item: dict, needle: str) -> tuple:
    drawing_id = normalize_name(item.get("drawing_id") or "")
    name = normalize_name(item.get("name") or "")
    hints = (
        (("wiper", "silicone", "silikone"), "d-015"),
        (("recoater plate",), "d-013"),
        (("galvo", "bundplade"), "d-016"),
        (("build plate", "byggeplade"), "d-026"),
    )
    if drawing_id and drawing_id == needle:
        return (0, name)
    for tokens, target in hints:
        if drawing_id == target and any(token in needle for token in tokens):
            return (0, name)
    if name == needle:
        return (1, name)
    if any(variant in name or variant in drawing_id for variant in _query_needles(needle)):
        return (2, name)
    return (8, name)


FAMILY_BY_SUBSYSTEM = {
    "box": ["box"],
    "powder": ["powder"],
    "recoater": ["recoater"],
    "optical": ["optics"],
    "gas flow": ["gas flow"],
    "z-axis": ["build-plate"],
}


def find_part(conn, query: str) -> dict:
    needle = _search_needle(query)
    if not needle:
        return {"items": [], "citations": [], "uncertainty": "unsupported"}
    needles = _query_needles(needle)
    likes = [f"%{item}%" for item in needles]
    clauses = " OR ".join(
        ["b.name_norm LIKE ?"] * len(likes)
        + [
            """EXISTS (
                SELECT 1 FROM drawings d
                WHERE d.id = l.drawing_id
                  AND (
                    lower(d.id) = ?
                    OR lower(d.title_extracted) LIKE ?
                    OR lower(d.source_filename) LIKE ?
                  )
           )"""
        ]
        * len(needles)
    )
    params: list = []
    params.extend(likes)
    for item in needles:
        params.extend([item, f"%{item}%", f"%{item}%"])
    rows = conn.execute(
        f"""
        SELECT
          b.id AS bom_row_id,
          b.name,
          b.part_family,
          b.source_row AS csv_line,
          l.drawing_id,
          l.status AS link_status,
          l.method AS link_method
        FROM bom_rows b
        LEFT JOIN drawing_bom_links l
          ON l.bom_row_id = b.id AND l.status IN ('confirmed', 'conflict', 'candidate', 'manual')
        WHERE {clauses}
        """,
        params,
    ).fetchall()

    # Also match drawing ids directly.
    if needle.startswith("d-") or needle.startswith("d0"):
        extra = conn.execute(
            """
            SELECT b.id AS bom_row_id, b.name, b.part_family, b.source_row AS csv_line,
                   l.drawing_id, l.status AS link_status, l.method AS link_method
            FROM drawings d
            LEFT JOIN drawing_bom_links l ON l.drawing_id = d.id
            LEFT JOIN bom_rows b ON b.id = l.bom_row_id
            WHERE lower(d.id) = ?
            """,
            (needle,),
        ).fetchall()
        rows = list(rows) + list(extra)

    items = []
    seen = set()
    for row in rows:
        key = (row["bom_row_id"], row["drawing_id"])
        if key in seen:
            continue
        seen.add(key)
        if row["bom_row_id"] is None and row["drawing_id"] is None:
            continue
        items.append(_row_to_item(row))
    items.sort(key=lambda item: _find_rank(item, needle))

    if not any(item.get("drawing_id") for item in items):
        for variant in needles:
            drawing = conn.execute(
                """
                SELECT id, title_extracted, subsystem
                FROM drawings
                WHERE lower(id) = ? OR lower(title_extracted) LIKE ? OR lower(source_filename) LIKE ?
                """,
                (variant.upper(), f"%{variant}%", f"%{variant}%"),
            ).fetchone()
            if drawing:
                items.insert(
                    0,
                    {
                        "bom_row_id": None,
                        "name": drawing["title_extracted"],
                        "part_family": drawing["subsystem"],
                        "csv_line": None,
                        "drawing_id": drawing["id"],
                        "link_status": None,
                        "link_method": None,
                    },
                )
                break

    items = _fuzzy_fill_items(conn, needle, items)

    return {
        "items": items,
        "citations": _fill_bom_source(conn, _bom_name_citations(items[:4])),
        "uncertainty": None if items else "unsupported",
    }


def get_part_facts(conn, bom_row_id: int | None = None, drawing_id: str | None = None) -> dict:
    bom = None
    if bom_row_id is not None:
        bom = conn.execute("SELECT * FROM bom_rows WHERE id = ?", (bom_row_id,)).fetchone()
    drawing = None
    if drawing_id:
        drawing = conn.execute("SELECT * FROM drawings WHERE id = ?", (drawing_id,)).fetchone()
        if bom is None and drawing is not None:
            linked = linked_bom_rows(conn, drawing["id"])
            if linked:
                bom = conn.execute(
                    "SELECT * FROM bom_rows WHERE id = ?", (linked[0]["bom_row_id"],)
                ).fetchone()
    elif bom_row_id is not None:
        link = conn.execute(
            """
            SELECT drawing_id FROM drawing_bom_links
            WHERE bom_row_id = ? AND status IN ('confirmed', 'conflict', 'manual', 'candidate')
            ORDER BY score DESC
            """,
            (bom_row_id,),
        ).fetchone()
        if link:
            drawing = conn.execute(
                "SELECT * FROM drawings WHERE id = ?", (link["drawing_id"],)
            ).fetchone()

    if bom is None and drawing is None:
        return {"items": [], "citations": [], "uncertainty": "unsupported"}

    linked_bom = linked_bom_rows(conn, drawing["id"]) if drawing else []
    if not linked_bom and bom:
        linked_bom = [
            {
                "bom_row_id": bom["id"],
                "name": bom["name"],
                "part_family": bom["part_family"],
                "material": bom["material_raw"],
                "amount": bom["amount_raw"],
                "supplier": bom["supplier_raw"],
                "csv_line": bom["source_row"],
                "link_status": None,
            }
        ]
    conflict_ids = {
        row["bom_row_id"]
        for row in linked_bom
        if row.get("link_status") == "conflict" and row.get("bom_row_id")
    }
    identity_conflict = len(conflict_ids) > 1

    bom_material = bom["material_raw"] if bom else None
    drawing_material = drawing["material_extracted"] if drawing else None
    entity_type = "bom_row" if bom else "drawing"
    entity_id = str(bom["id"] if bom else drawing["id"])
    original_value = bom_material if bom else drawing_material
    current_material = _current_value(
        conn,
        entity_type,
        entity_id,
        "material",
        original_value,
    )
    correction = _latest_accepted(conn, entity_type, entity_id, "material")
    stored_disagreement = bool(
        bom_material
        and drawing_material
        and not _same_material(bom_material, drawing_material)
    )
    current_disagreement = bool(
        current_material
        and drawing_material
        and not _same_material(current_material, drawing_material)
    )
    conflict_history = []
    if correction:
        conflict = False
        material_relation = None
        if stored_disagreement:
            conflict_history.append(
                f"BOM '{bom_material}' vs drawing '{drawing_material}' "
                f"resolved by correction #{correction['id']}"
            )
    elif current_disagreement:
        conflict = True
        material_relation = "conflict"
    elif current_material and drawing_material and _same_material(current_material, drawing_material):
        conflict = False
        material_relation = "same_family"
    elif current_material and drawing_material:
        conflict = False
        material_relation = "different"
    else:
        conflict = False
        material_relation = None
    if correction:
        current_source = {
            "source": "accepted_correction",
            "correction_id": correction["id"],
            "date": correction["reviewed_at"],
        }
    elif bom:
        current_source = {
            "source": "BOM",
            "bom_row_id": bom["id"],
            "csv_line": bom["source_row"],
        }
    else:
        current_source = {
            "source": "drawing",
            "drawing_id": drawing["id"],
        }

    citations = []
    cited_bom = linked_bom if identity_conflict else ([linked_bom[0]] if linked_bom else [])
    if not cited_bom and bom:
        cited_bom = [{"bom_row_id": bom["id"], "name": bom["name"], "material": bom_material}]
    for row in cited_bom:
        citations.append(
            _cite(
                kind="bom_row",
                bom_row_id=row["bom_row_id"],
                name=row.get("name"),
                field="material_raw",
                value=row.get("material") if "material" in row else bom_material,
                drawing_id=drawing["id"] if drawing else linked_drawing_id(conn, row["bom_row_id"]),
            )
        )
    if drawing:
        citations.append(
            _cite(
                kind="drawing",
                drawing_id=drawing["id"],
                page=1,
                field="material",
                value=drawing_material,
                region="title-block",
            )
        )
    if correction:
        citations.append(
            {
                "kind": "correction",
                "id": correction["id"],
                "field": "material",
                "value": correction["new_value"],
            }
        )

    filled = _fill_bom_source(conn, citations)
    original_citation = next(
        (
            cite
            for cite in filled
            if cite.get("kind") == "bom_row"
            and cite.get("field") == "material_raw"
            and (bom is None or cite.get("bom_row_id") == bom["id"])
        ),
        None,
    )
    if original_citation is None and drawing:
        original_citation = next(
            (cite for cite in filled if cite.get("kind") == "drawing" and cite.get("field") == "material"),
            None,
        )

    return {
        "current_value": current_material,
        "current_source": current_source,
        "original_value": original_value,
        "original_citation": original_citation,
        "current": {"material": current_material},
        "conflict_history": conflict_history,
        "bom": {
            "id": bom["id"] if bom else None,
            "name": bom["name"] if bom else None,
            "material": bom_material,
            "amount": bom["amount_raw"] if bom else None,
            "cost_raw": bom["cost_raw"] if bom else None,
            "supplier": bom["supplier_raw"] if bom else None,
            "part_family": bom["part_family"] if bom else None,
        },
        "recorded": {
            "supplier": recorded_fact(bom["supplier_raw"] if bom else None),
            "supplier_order": recorded_fact(bom["supplier_order"] if bom else None),
            "link": recorded_fact(bom["link"] if bom else None),
            "cost": recorded_fact(bom["cost_raw"] if bom else None),
        },
        "linked_bom": _linked_bom_with_corrections(conn, linked_bom),
        "drawing": {
            "id": drawing["id"] if drawing else None,
            "title": drawing["title_extracted"] if drawing else None,
            "material": drawing_material,
            "confidence": drawing["title_confidence"] if drawing else None,
        },
        "conflict": conflict,
        "identity_conflict": identity_conflict,
        "material_relation": material_relation,
        "citations": filled,
        "uncertainty": "conflict"
        if (conflict or identity_conflict) and correction is None
        else None,
    }


def _latest_correction(conn, entity_id, field: str, status: str):
    return conn.execute(
        """
        SELECT * FROM corrections
        WHERE entity_type = 'bom_row' AND entity_id = ? AND field = ? AND status = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (str(entity_id), field, status),
    ).fetchone()


def _linked_bom_with_corrections(conn, rows: list[dict]) -> list[dict]:
    items = []
    for row in rows:
        bom_id = row.get("bom_row_id")
        raw_material = row.get("material")
        item = {
            "bom_row_id": bom_id,
            "name": row.get("name"),
            "csv_line": row.get("csv_line") or row.get("source_row"),
            "material": raw_material,
            "link_status": row.get("link_status"),
        }
        if bom_id is None:
            items.append(item)
            continue
        accepted = _latest_correction(conn, bom_id, "material", "accepted")
        pending = _latest_correction(conn, bom_id, "material", "pending")
        if accepted and accepted["new_value"] != raw_material:
            item["current_material"] = accepted["new_value"]
        if pending:
            item["pending_material"] = pending["new_value"]
        items.append(item)
    return items


def parts_by_material(conn, material: str) -> dict:
    needle = f"%{material.lower()}%"
    rows = conn.execute(
        """
        SELECT b.id, b.name, b.part_family, b.source_row, b.material_raw, b.type,
               l.drawing_id, l.status AS link_status
        FROM bom_rows b
        LEFT JOIN drawing_bom_links l
          ON l.bom_row_id = b.id AND l.status IN ('confirmed', 'conflict', 'manual', 'candidate')
        WHERE lower(b.material_raw) LIKE ?
        ORDER BY b.name
        """,
        (needle,),
    ).fetchall()
    items = []
    seen = set()
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        items.append(
            {
                "bom_row_id": row["id"],
                "name": row["name"],
                "part_family": row["part_family"],
                "csv_line": row["source_row"],
                "material": row["material_raw"],
                "type": row["type"],
                "drawing_id": row["drawing_id"],
                "link_status": row["link_status"],
            }
        )
    return {
        "items": items,
        "item_count": len(items),
        "citations": _fill_bom_source(
            conn,
            [
                _cite(
                    kind="bom_row",
                    bom_row_id=item["bom_row_id"],
                    name=item["name"],
                    field="material_raw",
                    value=item["material"],
                    drawing_id=item.get("drawing_id"),
                )
                for item in items
            ],
        ),
        "uncertainty": None if items else "missing",
    }


def parts_in_subsystem(conn, name: str) -> dict:
    key = normalize_name(name)
    families = FAMILY_BY_SUBSYSTEM.get(key, [key])
    placeholders = ",".join("?" for _ in families)
    rows = conn.execute(
        f"""
        SELECT DISTINCT
          b.id AS bom_row_id,
          b.name,
          b.part_family,
          b.source_row AS csv_line,
          l.drawing_id,
          l.status AS link_status,
          l.method AS link_method,
          d.subsystem AS drawing_subsystem
        FROM bom_rows b
        LEFT JOIN drawing_bom_links l
          ON l.bom_row_id = b.id AND l.status IN ('confirmed', 'conflict', 'manual', 'candidate')
        LEFT JOIN drawings d ON d.id = l.drawing_id
        WHERE lower(b.part_family) IN ({placeholders})
           OR lower(d.subsystem) = ?
        ORDER BY b.name
        """,
        (*families, key),
    ).fetchall()
    items = [
        {
            "bom_row_id": row["bom_row_id"],
            "name": row["name"],
            "part_family": row["part_family"],
            "csv_line": row["csv_line"],
            "drawing_id": row["drawing_id"],
            "link_status": row["link_status"],
            "link_method": row["link_method"],
            "drawing_subsystem": row["drawing_subsystem"],
        }
        for row in rows
    ]
    return {
        "items": items,
        "item_count": len({item["bom_row_id"] for item in items}),
        "citations": _bom_name_citations(items, conn),
        "uncertainty": None if items else "missing",
    }


def interfaces_of(conn, bom_row_id: int, hops: int = 1, include_incoming: bool = False) -> dict:
    hops = min(max(int(hops or 1), 1), 3)
    start = conn.execute(
        "SELECT id, name FROM bom_rows WHERE id = ?", (bom_row_id,)
    ).fetchone()
    if start is None:
        return {"items": [], "citations": [], "uncertainty": "unsupported"}

    items = []
    queue = [(bom_row_id, [start["name"]], [bom_row_id], 0)]
    seen_edges = set()
    while queue:
        node, path, path_ids, depth = queue.pop(0)
        if depth >= hops:
            continue
        rows = conn.execute(
            """
            SELECT i.to_name_raw, i.to_bom_row_id, i.match_status,
                   t.name AS to_name, t.part_family AS to_family,
                   b.name AS from_name, b.part_family AS from_family,
                   b.interface_with_raw AS source_text
            FROM interfaces i
            JOIN bom_rows b ON b.id = i.from_bom_row_id
            LEFT JOIN bom_rows t ON t.id = i.to_bom_row_id
            WHERE i.from_bom_row_id = ?
            ORDER BY i.to_name_raw
            """,
            (node,),
        ).fetchall()
        for row in rows:
            edge_key = (node, row["to_name_raw"])
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            to_name = row["to_name"] or row["to_name_raw"]
            families_differ = bool(
                row["from_family"] and row["to_family"] and row["from_family"] != row["to_family"]
            )
            both = False
            if row["to_bom_row_id"] is not None:
                both = (
                    conn.execute(
                        """
                        SELECT 1 FROM interfaces
                        WHERE from_bom_row_id = ? AND to_bom_row_id = ?
                        """,
                        (row["to_bom_row_id"], node),
                    ).fetchone()
                    is not None
                )
            items.append(
                {
                    "to_name": to_name,
                    "to_bom_row_id": row["to_bom_row_id"],
                    "match_status": row["match_status"],
                    "raw_text": row["to_name_raw"],
                    "source_text": row["source_text"],
                    "depth": depth + 1,
                    "path": path + [to_name],
                    "from_family": row["from_family"],
                    "to_family": row["to_family"],
                    "crosses_subsystem": families_differ,
                    "both_directions": both,
                }
            )
            nxt = row["to_bom_row_id"]
            if nxt is not None and nxt not in path_ids and depth + 1 < hops:
                queue.append((nxt, path + [to_name], path_ids + [nxt], depth + 1))

    mentioned_by = []
    if include_incoming:
        incoming = conn.execute(
            """
            SELECT b.name AS from_name, i.from_bom_row_id, i.match_status, i.to_name_raw
            FROM interfaces i
            JOIN bom_rows b ON b.id = i.from_bom_row_id
            WHERE i.to_bom_row_id = ?
            ORDER BY b.name
            """,
            (bom_row_id,),
        ).fetchall()
        mentioned_by = [
            {
                "from_name": row["from_name"],
                "from_bom_row_id": row["from_bom_row_id"],
                "match_status": row["match_status"],
                "raw_text": row["to_name_raw"],
            }
            for row in incoming
        ]

    payload = {
        "items": items,
        "citations": _fill_bom_source(
            conn,
            [
                _cite(
                    kind="bom_row",
                    bom_row_id=bom_row_id,
                    field="interface_with_raw",
                    value=item["to_name"],
                    drawing_id=linked_drawing_id(conn, bom_row_id),
                )
                for item in items
            ],
        ),
        "uncertainty": None if items or mentioned_by else "missing",
    }
    if include_incoming:
        payload["mentioned_by"] = mentioned_by
    return payload


def linked_bom_rows(conn, drawing_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT
          b.id,
          b.name,
          b.part_family,
          b.material_raw,
          b.amount_raw,
          b.cost_raw,
          b.supplier_raw,
          b.interface_with_raw,
          b.source_row,
          b.source_file,
          l.status,
          l.method
        FROM drawing_bom_links l
        JOIN bom_rows b ON b.id = l.bom_row_id
        WHERE l.drawing_id = ?
          AND l.status IN ('confirmed', 'conflict', 'manual', 'candidate')
        ORDER BY l.score DESC, b.name
        """,
        (drawing_id,),
    ).fetchall()
    cards = []
    seen = set()
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        cards.append(
            {
                "bom_row_id": row["id"],
                "name": row["name"],
                "part_family": row["part_family"],
                "material": row["material_raw"],
                "amount": row["amount_raw"],
                "cost_raw": row["cost_raw"],
                "supplier": row["supplier_raw"],
                "interface_with": row["interface_with_raw"],
                "drawing_id": drawing_id,
                "source_row": row["source_row"],
                "csv_line": row["source_row"],
                "source_file": row["source_file"],
                "link_status": row["status"],
                "link_method": row["method"],
            }
        )
    return cards


def open_visual(conn, drawing_id: str) -> dict:
    drawing = conn.execute("SELECT * FROM drawings WHERE id = ?", (drawing_id,)).fetchone()
    if drawing is None:
        return {"items": [], "citations": [], "uncertainty": "unsupported"}
    reconstruction = conn.execute(
        "SELECT * FROM reconstructions WHERE drawing_id = ?", (drawing_id,)
    ).fetchone()
    path = str(DRAWINGS_DIR / f"{drawing_id}.pdf")
    bom_rows = linked_bom_rows(conn, drawing_id)
    citations = [
        _cite(
            kind="drawing",
            drawing_id=drawing_id,
            page=1,
            field="path",
            value=path,
        )
    ]
    for row in bom_rows:
        citations.append(
            _cite(
                kind="bom_row",
                bom_row_id=row["bom_row_id"],
                name=row["name"],
                field="name",
                value=row["name"],
                drawing_id=drawing_id,
            )
        )
    return {
        "drawing_id": drawing_id,
        "path": path,
        "pages": drawing["pages"],
        "title": drawing["title_extracted"],
        "reconstruction": _reconstruction_payload(conn, reconstruction),
        "bom_rows": bom_rows,
        "citations": _fill_bom_source(conn, citations),
        "uncertainty": None,
    }


def _reconstruction_payload(conn, reconstruction) -> dict | None:
    if reconstruction is None:
        return None
    payload = dict(reconstruction)
    dims = []
    param_path = payload.get("param_path")
    if param_path and Path(param_path).is_file():
        spec = json.loads(Path(param_path).read_text(encoding="utf-8"))
        dims = [dict(item) for item in spec.get("dimensions_used") or []]
    claims = conn.execute(
        """
        SELECT id, value_raw, page FROM drawing_claims
        WHERE drawing_id = ? AND field = 'dimension'
        """,
        (payload.get("drawing_id"),),
    ).fetchall()
    for item in dims:
        item["claim_id"] = _claim_for_dimension(claims, item.get("value"))
        item.setdefault("page", 1)
    payload["dimensions_used"] = dims
    return payload


def _claim_for_dimension(claims, value: str | None) -> int | None:
    wanted = _dimension_numbers(value)
    if not wanted:
        return None
    for claim in claims:
        if wanted[0] in _dimension_numbers(claim["value_raw"]):
            return claim["id"]
    return None


def _dimension_numbers(value: str | None) -> list[str]:
    return [token.replace(",", ".") for token in re.findall(r"\d+(?:[.,]\d+)?", value or "")]


def list_conflicts(conn) -> dict:
    items = []
    dual = conn.execute(
        """
        SELECT l.drawing_id, b.name
        FROM drawing_bom_links l
        JOIN bom_rows b ON b.id = l.bom_row_id
        WHERE l.status = 'conflict'
        ORDER BY l.drawing_id, b.name
        """
    ).fetchall()
    by_drawing: dict[str, list[str]] = {}
    for row in dual:
        by_drawing.setdefault(row["drawing_id"], []).append(row["name"])
    for drawing_id, names in by_drawing.items():
        items.append(
            {
                "kind": "link",
                "drawing_id": drawing_id,
                "bom_names": names,
                "reason": "multiple high-score BOM links",
            }
        )

    facts = conn.execute(
        """
        SELECT b.id, b.name, b.material_raw, d.id AS drawing_id, d.material_extracted
        FROM drawing_bom_links l
        JOIN bom_rows b ON b.id = l.bom_row_id
        JOIN drawings d ON d.id = l.drawing_id
        WHERE l.status IN ('confirmed', 'conflict', 'manual')
          AND b.material_raw IS NOT NULL
          AND d.material_extracted IS NOT NULL
        """
    ).fetchall()
    seen = set()
    for row in facts:
        key = (row["id"], row["drawing_id"])
        if key in seen:
            continue
        seen.add(key)
        if not _same_material(row["material_raw"], row["material_extracted"]):
            items.append(
                {
                    "kind": "material",
                    "drawing_id": row["drawing_id"],
                    "bom_row_id": row["id"],
                    "bom_names": [row["name"]],
                    "bom_material": row["material_raw"],
                    "drawing_material": row["material_extracted"],
                    "reason": "drawing and BOM materials disagree",
                }
            )
    return {"items": items, "citations": [], "uncertainty": None if items else "missing"}


CORRECTABLE_FIELDS = {"material", "supplier", "cost", "title"}


def _proposal_checks(conn, entity_type: str, entity_id: str, field: str, new_value: str) -> dict:
    part_exists = False
    bom_value = None
    csv_line = None
    file_line = None
    part_name = None
    drawing = None
    if entity_type == "bom_row":
        row = conn.execute("SELECT * FROM bom_rows WHERE id = ?", (int(entity_id),)).fetchone()
        part_exists = row is not None
        if row is not None:
            part_name = row["name"]
            csv_line = row["source_row"]
            file_line = row["file_line"]
            column = {"material": "material_raw", "supplier": "supplier_raw", "cost": "cost_raw"}.get(field)
            bom_value = row[column] if column and column in row.keys() else None
            link = conn.execute(
                """
                SELECT drawing_id FROM drawing_bom_links
                WHERE bom_row_id = ? AND status IN ('confirmed', 'conflict', 'manual', 'candidate')
                ORDER BY score DESC LIMIT 1
                """,
                (row["id"],),
            ).fetchone()
            if link:
                claim = conn.execute(
                    """
                    SELECT drawing_id, page, region, value_raw, confidence
                    FROM drawing_claims
                    WHERE drawing_id = ? AND field = ?
                    ORDER BY id LIMIT 1
                    """,
                    (link["drawing_id"], field if field != "title" else "title"),
                ).fetchone()
                if claim:
                    drawing = {
                        "drawing_id": claim["drawing_id"],
                        "page": claim["page"],
                        "region": claim["region"],
                        "value": claim["value_raw"],
                        "confidence": claim["confidence"],
                    }
    elif entity_type == "drawing":
        row = conn.execute("SELECT id FROM drawings WHERE id = ?", (entity_id,)).fetchone()
        part_exists = row is not None
    pending = _latest_correction(conn, entity_id, field, "pending") if entity_type == "bom_row" else None
    accepted = _latest_accepted(conn, entity_type, entity_id, field)
    existing = None
    if pending:
        existing = "pending"
    elif accepted:
        existing = "accepted"
    drawing_value = drawing["value"] if drawing else None
    agrees_bom = _values_agree(new_value, bom_value)
    agrees_drawing = _values_agree(new_value, drawing_value)
    if agrees_bom and agrees_drawing:
        agreement = "both"
    elif agrees_drawing:
        agreement = "drawing"
    elif agrees_bom:
        agreement = "bom"
    else:
        agreement = "neither"
    return {
        "part_exists": part_exists,
        "part_name": part_name,
        "field_correctable": field in CORRECTABLE_FIELDS,
        "bom_value": bom_value,
        "csv_line": csv_line,
        "file_line": file_line,
        "drawing": drawing,
        "agreement": agreement,
        "existing": existing,
    }


def _values_agree(proposed: str | None, stored: str | None) -> bool:
    if not proposed or not stored:
        return False
    if _same_material(proposed, stored):
        return True
    left = normalize_name(proposed)
    right = normalize_name(stored)
    return left in right or right in left


def _check_summary(checks: dict) -> str:
    drawing = checks.get("drawing") or {}
    bits = [
        f"part exists: {checks.get('part_exists')}",
        f"field correctable: {checks.get('field_correctable')}",
        f"BOM {checks.get('bom_value')!r} line {checks.get('file_line')}",
    ]
    if drawing:
        bits.append(
            f"drawing {drawing.get('drawing_id')} p.{drawing.get('page')} {drawing.get('value')!r}"
        )
    bits.append(f"agreement: {checks.get('agreement')}")
    if checks.get("existing"):
        bits.append(f"existing {checks['existing']} correction")
    return "; ".join(bits)


def _resolve_bom_entity_id(conn, entity_id: str, part: str | None) -> str:
    if not part:
        return str(entity_id)
    norm = normalize_name(part)
    row = conn.execute("SELECT id FROM bom_rows WHERE name_norm = ?", (norm,)).fetchone()
    if row:
        return str(row["id"])
    found = find_part(conn, part)
    named = [
        item
        for item in (found.get("items") or [])
        if item.get("bom_row_id") and normalize_name(item.get("name") or "") == norm
    ]
    if named:
        return str(named[0]["bom_row_id"])
    ranked = [item for item in (found.get("items") or []) if item.get("bom_row_id")]
    if ranked:
        return str(ranked[0]["bom_row_id"])
    return str(entity_id)


def propose_correction(
    conn,
    entity_type: str,
    entity_id: str,
    field: str,
    new_value: str,
    reason: str | None = None,
    check_notes: str | None = None,
    old_value: str | None = None,
    part: str | None = None,
) -> dict:
    if entity_type == "bom_row":
        entity_id = _resolve_bom_entity_id(conn, entity_id, part)
    if old_value is None:
        old_value = _original_value(conn, entity_type, entity_id, field)
    if entity_type == "bom_row":
        accepted = _latest_accepted(conn, entity_type, entity_id, field)
        if accepted and (accepted["new_value"] or "").strip() == (new_value or "").strip():
            return {
                "id": accepted["id"],
                "status": "accepted",
                "old_value": accepted["old_value"],
                "new_value": accepted["new_value"],
                "already_accepted": True,
            }
    checks = _proposal_checks(conn, entity_type, entity_id, field, new_value)
    if check_notes is None:
        check_notes = _check_summary(checks)
    target = f"{entity_type}:{entity_id}:{field}"
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO corrections (
          proposed_at, entity_type, entity_id, field, old_value, new_value,
          reason, check_notes, status, target_fact_ref, chat_turn_id, checks
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """,
        (
            now,
            entity_type,
            entity_id,
            field,
            old_value,
            new_value,
            reason,
            check_notes,
            target,
            None,
            json.dumps(checks),
        ),
    )
    _record_correction_event(conn, cur.lastrowid, "proposed", reason, now)
    conn.commit()
    return {
        "id": cur.lastrowid,
        "status": "pending",
        "old_value": old_value,
        "new_value": new_value,
        "checks": checks,
        "target_fact_ref": target,
    }


def review_correction(
    conn, correction_id: int, decision: str, reviewer: str, reviewer_note: str | None = None
) -> dict:
    if decision not in {"accepted", "rejected"}:
        raise ValueError("decision must be accepted or rejected")
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE corrections
        SET status = ?, reviewed_at = ?, reviewer = ?, reviewer_note = ?
        WHERE id = ?
        """,
        (decision, now, reviewer, reviewer_note, correction_id),
    )
    _record_correction_event(conn, correction_id, decision, reviewer, now)
    conn.commit()
    row = conn.execute("SELECT * FROM corrections WHERE id = ?", (correction_id,)).fetchone()
    return dict(row)


def knowledge_version(conn) -> int:
    row = conn.execute("SELECT COALESCE(MAX(id), 0) AS version FROM correction_events").fetchone()
    return int(row["version"] if row else 0)


def correction_history(conn, part: str | None = None) -> list[dict]:
    sql = """
        SELECT e.id, e.correction_id, e.event, e.note, e.created_at,
               c.field, c.old_value, c.new_value, c.entity_type, c.entity_id,
               c.status, b.name AS part_name
        FROM correction_events e
        JOIN corrections c ON c.id = e.correction_id
        LEFT JOIN bom_rows b
          ON c.entity_type = 'bom_row' AND b.id = CAST(c.entity_id AS INTEGER)
    """
    params: tuple = ()
    if part:
        sql += " WHERE lower(b.name) = lower(?)"
        params = (part,)
    sql += " ORDER BY e.created_at DESC, e.id DESC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def _record_correction_event(conn, correction_id: int, event: str, note: str | None, created_at: str) -> None:
    conn.execute(
        """
        INSERT INTO correction_events (correction_id, event, note, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (correction_id, event, note, created_at),
    )


def _current_value(conn, entity_type: str, entity_id: str, field: str, original):
    accepted = _latest_accepted(conn, entity_type, entity_id, field)
    if accepted:
        return accepted["new_value"]
    return original


def _latest_accepted(conn, entity_type: str, entity_id: str, field: str):
    return conn.execute(
        """
        SELECT * FROM corrections
        WHERE entity_type = ? AND entity_id = ? AND field = ? AND status = 'accepted'
        ORDER BY id DESC
        LIMIT 1
        """,
        (entity_type, entity_id, field),
    ).fetchone()


def _original_value(conn, entity_type: str, entity_id: str, field: str) -> str | None:
    if entity_type == "bom_row":
        column = {
            "material": "material_raw",
            "supplier": "supplier_raw",
            "cost": "cost_raw",
        }.get(field, field)
        row = conn.execute(
            f"SELECT {column} FROM bom_rows WHERE id = ?", (int(entity_id),)
        ).fetchone()
        return None if row is None else row[0]
    if entity_type == "drawing":
        column = {
            "material": "material_extracted",
            "title": "title_extracted",
        }.get(field, field)
        row = conn.execute(
            f"SELECT {column} FROM drawings WHERE id = ?", (entity_id,)
        ).fetchone()
        return None if row is None else row[0]
    return None


def _same_material(left: str, right: str) -> bool:
    a = normalize_name(left)
    b = normalize_name(right)
    if a == b:
        return True
    if "," in left or "," in right:
        return False
    aliases = (
        ("stainless", "stainless steel"),
        ("aluminium", "aluminum"),
        ("aluminium", "alu"),
        ("silicone", "silicon"),
    )
    for token_a, token_b in aliases:
        if token_a in a and token_b in b:
            return True
        if token_b in a and token_a in b:
            return True
    return False


def linked_drawing_id(conn, bom_row_id: int | None) -> str | None:
    if bom_row_id is None:
        return None
    row = conn.execute(
        """
        SELECT drawing_id FROM drawing_bom_links
        WHERE bom_row_id = ? AND status IN ('confirmed', 'conflict', 'manual', 'candidate')
        ORDER BY score DESC
        LIMIT 1
        """,
        (bom_row_id,),
    ).fetchone()
    return None if row is None else row["drawing_id"]


def bom_evidence(conn, bom_row_id: int) -> dict:
    row = conn.execute("SELECT * FROM bom_rows WHERE id = ?", (bom_row_id,)).fetchone()
    if row is None:
        return {"uncertainty": "unsupported"}
    drawing_id = linked_drawing_id(conn, bom_row_id)
    visual = open_visual(conn, drawing_id) if drawing_id else None
    return {
        "bom_row_id": row["id"],
        "name": row["name"],
        "part_family": row["part_family"],
        "material": row["material_raw"],
        "amount": row["amount_raw"],
        "cost_raw": row["cost_raw"],
        "supplier": row["supplier_raw"],
        "interface_with": row["interface_with_raw"],
        "drawing_id": drawing_id,
        "source_row": row["source_row"],
        "csv_line": row["source_row"],
        "file_line": row["file_line"],
        "source_file": row["source_file"],
        "recorded": {
            "supplier": recorded_fact(row["supplier_raw"]),
            "supplier_order": recorded_fact(row["supplier_order"]),
            "link": recorded_fact(row["link"]),
            "cost": recorded_fact(row["cost_raw"]),
        },
        "correction_history": correction_history(conn, row["name"]),
        "reconstruction": None if visual is None else visual.get("reconstruction"),
        "href": f"/api/evidence/bom/{bom_row_id}" if row else None,
    }


def _cite(**fields) -> dict:
    kind = fields.get("kind")
    drawing_id = fields.get("drawing_id")
    bom_row_id = fields.get("bom_row_id")
    page = fields.get("page") or 1
    region = fields.get("region")
    fields["page"] = page if drawing_id else None
    fields["extracted_text"] = fields.get("value")
    fields["highlight"] = REGION_BOXES.get(region) if kind == "drawing" else None
    if kind == "drawing" and drawing_id:
        fields["href"] = f"/api/drawings/{drawing_id}/page/{page}"
    elif kind == "bom_row" and bom_row_id:
        fields["href"] = f"/api/evidence/bom/{bom_row_id}"
    else:
        fields["href"] = None
    return fields


def _fill_bom_source(conn, citations: list) -> list:
    ids = [
        cite["bom_row_id"]
        for cite in citations
        if cite.get("kind") == "bom_row" and cite.get("bom_row_id")
    ]
    if not ids:
        return citations
    rows = conn.execute(
        f"SELECT id, source_row, source_file, file_line FROM bom_rows WHERE id IN ({','.join('?' * len(ids))})",
        ids,
    ).fetchall()
    by_id = {row["id"]: row for row in rows}
    for cite in citations:
        row = by_id.get(cite.get("bom_row_id"))
        if row is None:
            continue
        cite["source_row"] = row["source_row"]
        cite["csv_line"] = row["source_row"]
        cite["file_line"] = row["file_line"]
        cite["source_file"] = row["source_file"]
    return citations


def _bom_name_citations(items: list[dict], conn=None) -> list[dict]:
    citations = []
    for item in items:
        if item.get("bom_row_id"):
            citations.append(
                _cite(
                    kind="bom_row",
                    bom_row_id=item["bom_row_id"],
                    name=item.get("name"),
                    field="name",
                    value=item.get("name"),
                    drawing_id=item.get("drawing_id"),
                )
            )
        if item.get("drawing_id"):
            citations.append(
                _cite(
                    kind="drawing",
                    drawing_id=item["drawing_id"],
                    page=1,
                    field="id",
                    value=item["drawing_id"],
                )
            )
    if conn is not None:
        return _fill_bom_source(conn, citations)
    return citations
