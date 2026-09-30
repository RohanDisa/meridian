from __future__ import annotations

import json
import re
from pathlib import Path

from meridian.ingest_bom import datasheet_filenames
from meridian.normalize import normalize_name

GLOSSARY = {
    "bygge": "build",
    "plade": "plate",
    "spaend": "clamp",
    "spænd": "clamp",
    "varme": "heating",
    "element": "element",
    "bundplade": "bottom plate",
    "cylinder": "cylinder",
    "ring": "ring",
}


def link_drawings_to_bom(conn) -> None:
    conn.execute("DELETE FROM drawing_bom_links")
    bom_rows = list(conn.execute("SELECT id, name, name_norm, datasheet_raw FROM bom_rows"))
    drawings = list(
        conn.execute(
            "SELECT id, source_filename, title_extracted, subsystem FROM drawings"
        )
    )

    for drawing in drawings:
        scored: list[tuple] = []
        filename = drawing["source_filename"]
        title = drawing["title_extracted"] or ""

        for bom in bom_rows:
            files = datasheet_filenames(bom["datasheet_raw"])
            if any(filename.lower() == item.lower() for item in files):
                scored.append(
                    (
                        bom["id"],
                        "datasheet",
                        f"BOM Datasheet names {filename}",
                        1.0,
                    )
                )
            if title and normalize_name(title) == bom["name_norm"]:
                scored.append(
                    (
                        bom["id"],
                        "title",
                        f"title {title!r} == BOM Name {bom['name']!r}",
                        0.95,
                    )
                )
            file_score, file_evidence = _filename_score(filename, bom["name"])
            if file_score >= 0.70:
                scored.append((bom["id"], "filename", file_evidence, file_score))
            gloss_score, gloss_evidence = _glossary_score(filename, bom["name"])
            if gloss_score >= 0.70:
                scored.append((bom["id"], "glossary", gloss_evidence, gloss_score))

        _write_scored_links(conn, drawing["id"], scored)
    conn.commit()


def apply_mapping_overrides(conn, override_path: Path) -> None:
    payload = json.loads(Path(override_path).read_text(encoding="utf-8"))
    for drawing_id, spec in payload.items():
        name = spec["bom_name"]
        status = spec.get("status", "confirmed")
        row = conn.execute(
            "SELECT id FROM bom_rows WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            continue
        conn.execute(
            """
            INSERT INTO drawing_bom_links (
              drawing_id, bom_row_id, method, evidence, score, status
            ) VALUES (?, ?, 'manual', ?, 1.0, ?)
            """,
            (
                drawing_id,
                row["id"],
                spec.get("evidence") or f"manual override to {name}",
                status,
            ),
        )
    conn.commit()


def _write_scored_links(conn, drawing_id: str, scored: list[tuple]) -> None:
    if not scored:
        conn.execute(
            """
            INSERT INTO drawing_bom_links (
              drawing_id, bom_row_id, method, evidence, score, status
            ) VALUES (?, NULL, 'unmatched', 'no signal >= 0.70', 0, 'unmatched')
            """,
            (drawing_id,),
        )
        return

    by_method: dict[str, list[tuple]] = {}
    for item in scored:
        by_method.setdefault(item[1], []).append(item)

    for method, items in by_method.items():
        unique_bom = {item[0] for item in items}
        if method == "datasheet" and len(unique_bom) > 1:
            status = "conflict"
        elif method in {"datasheet", "title"} and len(unique_bom) == 1:
            status = "confirmed"
        elif max(item[3] for item in items) >= 0.95 and len(unique_bom) == 1:
            status = "confirmed"
        else:
            status = "candidate"
        seen = set()
        for bom_id, method_name, evidence, score in items:
            key = (bom_id, method_name)
            if key in seen:
                continue
            seen.add(key)
            conn.execute(
                """
                INSERT INTO drawing_bom_links (
                  drawing_id, bom_row_id, method, evidence, score, status
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (drawing_id, bom_id, method_name, evidence, score, status),
            )


def _filename_score(filename: str, bom_name: str) -> tuple[float, str]:
    stem = Path(filename).stem
    stem = re.sub(r"_(v\d+|x\d+|loop\d+|version\d+)$", "", stem, flags=re.I)
    tokens = _tokens(stem)
    name_tokens = _tokens(bom_name)
    if not tokens or not name_tokens:
        return 0.0, ""
    overlap = len(set(tokens) & set(name_tokens)) / max(len(set(tokens) | set(name_tokens)), 1)
    if overlap >= 0.99:
        return 0.90, f"filename tokens {tokens} match {name_tokens}"
    if overlap >= 0.6:
        return 0.80, f"filename tokens {tokens} overlap {name_tokens}"
    return 0.0, ""


def _glossary_score(filename: str, bom_name: str) -> tuple[float, str]:
    stem = Path(filename).stem
    translated = []
    for raw in _split_camel(stem):
        key = normalize_name(raw)
        translated.extend(GLOSSARY.get(key, key).split())
    name_tokens = _tokens(bom_name)
    if not translated or not name_tokens:
        return 0.0, ""
    overlap = len(set(translated) & set(name_tokens)) / max(
        len(set(translated) | set(name_tokens)), 1
    )
    if overlap >= 0.6:
        return 0.80, f"glossary {translated} vs {name_tokens}"
    return 0.0, ""


def _tokens(value: str) -> list[str]:
    return [part for part in re.split(r"[^a-z0-9]+", normalize_name(value)) if part]


def _split_camel(value: str) -> list[str]:
    parts = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    parts = re.sub(r"[_-]+", " ", parts)
    return [part for part in re.split(r"\s+", parts) if part]
