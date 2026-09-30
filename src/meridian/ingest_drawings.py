from __future__ import annotations

import json
import re
from pathlib import Path

from meridian.paths import DATA_DIR

BOILERPLATE = {
    "unless otherwise specified:",
    "dimensions are in millimeters",
    "tolerances: din iso 2768-1 m",
    "finish:",
    "deburr and break",
    "sharp edges",
    "date",
    "material:",
    "do not scale drawing",
    "title/description:",
    "company:",
    "drawn",
    "note:",
    "weight [g]:",
}

WEIGHT_LINE = re.compile(
    r"WEIGHT\s*\[g\]\s*:\s*(.*)$",
    re.I | re.M,
)
NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
DIMENSION_LINE = re.compile(
    r"(\d+[.,]\d+|\d+)\s*(?:x\s*)?(?:THRU|M\d|R\d)",
    re.I,
)


def extract_title_block(text: str) -> dict:
    title = None
    material = None
    weight_g = None
    scale = None
    sheet_label = None
    drawn_date = None
    drawn_by = None

    weight_match = WEIGHT_LINE.search(text or "")
    if weight_match:
        pending = []
        rest = weight_match.group(1).strip()
        if rest and not rest.upper().startswith("UNLESS"):
            pending.append(rest)
        for line in text[weight_match.end() :].splitlines():
            cleaned = _clean_line(line)
            if not cleaned:
                continue
            if _is_boilerplate(cleaned):
                break
            pending.append(cleaned)
        material, weight_g, title = _parse_weight_block(pending)

    scale_match = re.search(r"SCALE\s*:\s*([0-9:]+\s*)", text or "", re.I)
    if scale_match:
        scale = scale_match.group(1).strip()
    sheet_match = re.search(r"SHEET\s+(\d+\s+OF\s+\d+)", text or "", re.I)
    if sheet_match:
        sheet_label = sheet_match.group(1).strip()
    date_match = re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", text or "")
    if date_match:
        drawn_date = date_match.group(1)
    if re.search(r"\bMBOKJ\b", text or ""):
        drawn_by = "MBOKJ"

    if not title:
        title = _fallback_title(text or "")

    if not material:
        material = _fallback_material(text or "")

    return {
        "title": title,
        "material": material,
        "weight_g": weight_g,
        "scale": scale,
        "sheet_label": sheet_label,
        "drawn_date": drawn_date,
        "drawn_by": drawn_by,
    }


def ingest_drawings(conn, manifest_path: Path, drawings_dir: Path) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assets = [item for item in manifest["assets"] if item["id"].startswith("D-")]
    conn.execute("DELETE FROM drawing_claims")
    conn.execute("DELETE FROM drawings")

    for asset in assets:
        pdf_path = drawings_dir / f"{asset['id']}.pdf"
        distribution = asset.get("distribution", "clean")
        text = _pdf_text(pdf_path) if pdf_path.is_file() else ""
        block = extract_title_block(text) if text.strip() else {}
        title = block.get("title")
        if distribution == "degraded-only":
            confidence = "illegible" if not title else "low"
            method = "pdf_text" if title else "vision_review"
            if not title:
                title = None
        elif title:
            confidence = "high"
            method = "pdf_text"
        else:
            confidence = "missing"
            method = "pdf_text"

        source_filename = Path(asset["source_path"]).name
        conn.execute(
            """
            INSERT INTO drawings (
              id, path, source_filename, subsystem, pages, distribution, role,
              title_extracted, title_confidence, material_extracted,
              material_confidence, scale, sheet_label, weight_g, weight_note,
              drawn_date, drawn_by, extract_method
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset["id"],
                asset["path"],
                source_filename,
                asset["subsystem"],
                asset.get("pages") or 1,
                distribution,
                asset.get("role"),
                title,
                confidence,
                block.get("material"),
                "high" if block.get("material") and distribution == "clean" else confidence,
                block.get("scale"),
                block.get("sheet_label"),
                block.get("weight_g"),
                block.get("material"),
                block.get("drawn_date"),
                block.get("drawn_by"),
                method,
            ),
        )
        if title:
            _add_claim(conn, asset["id"], "title", title, "title-block", confidence, method)
        if block.get("material"):
            _add_claim(
                conn,
                asset["id"],
                "material",
                block["material"],
                "title-block",
                "high" if distribution == "clean" else confidence,
                method,
            )
        for dim in _dimension_claims(text):
            _add_claim(conn, asset["id"], "dimension", dim, "views", "medium", "pdf_text")

    apply_degraded_reviews(conn)
    conn.commit()
    return len(assets)


def apply_degraded_reviews(conn, review_path: Path | None = None) -> None:
    path = review_path or (DATA_DIR / "degraded_reviews.json")
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    for drawing_id, spec in payload.items():
        title = (spec.get("title") or "").strip() or None
        material = (spec.get("material") or "").strip() or None
        notes = (spec.get("notes") or "").strip() or None
        confidence = spec.get("confidence") or "low"
        conn.execute(
            """
            UPDATE drawings SET
              title_extracted = ?,
              title_confidence = ?,
              material_extracted = ?,
              material_confidence = ?,
              extract_method = 'vision_review',
              weight_note = COALESCE(?, weight_note)
            WHERE id = ?
            """,
            (
                title,
                confidence if title else "illegible",
                material,
                confidence if material else "illegible",
                notes,
                drawing_id,
            ),
        )
        if title:
            _add_claim(conn, drawing_id, "title", title, "title-block", confidence, "vision_review")
        if material:
            _add_claim(
                conn, drawing_id, "material", material, "title-block", confidence, "vision_review"
            )
        if notes:
            _add_claim(conn, drawing_id, "notes", notes, "title-block", confidence, "vision_review")


def _add_claim(conn, drawing_id, field, value, region, confidence, method) -> None:
    conn.execute(
        """
        INSERT INTO drawing_claims (
          drawing_id, page, field, value_raw, value_norm, unit, region, confidence, method
        ) VALUES (?, 1, ?, ?, ?, NULL, ?, ?, ?)
        """,
        (drawing_id, field, value, value.lower(), region, confidence, method),
    )


def _pdf_text(path: Path) -> str:
    import fitz

    doc = fitz.open(path)
    try:
        return "\n".join(page.get_text() for page in doc)
    finally:
        doc.close()


def _dimension_claims(text: str) -> list[str]:
    claims = []
    for line in (text or "").splitlines():
        cleaned = _clean_line(line)
        if DIMENSION_LINE.search(cleaned) and not _is_boilerplate(cleaned):
            claims.append(cleaned)
    return claims[:40]


def _parse_weight_block(lines: list[str]) -> tuple[str | None, float | None, str | None]:
    material = None
    weight_g = None
    title = None
    for line in lines:
        if _is_numeric_noise(line):
            weight_g = float(line.replace(",", "."))
            continue
        if _looks_like_material(line):
            material = line
            parsed_material, parsed_weight = _split_material_and_weight(line, from_weight_field=True)
            material = parsed_material or line
            if parsed_weight is not None and weight_g is None:
                weight_g = parsed_weight
            continue
        if title is None:
            title = line
    return material, weight_g, title


ALLOY_HINTS = {316.0, 6061.0, 7075.0, 5005.0, 3.3315}


def _split_material_and_weight(line: str, *, from_weight_field: bool = False) -> tuple[str | None, float | None]:
    cleaned = line.strip()
    unit = re.search(r"([-+]?\d+(?:[.,]\d+)?)\s*(kg|g)\s*$", cleaned, re.I)
    if unit:
        material = cleaned[: unit.start()].strip(" \t-") or cleaned
        return material, float(unit.group(1).replace(",", "."))
    if not from_weight_field:
        return line, None
    numbers = NUMBER.findall(line)
    if len(numbers) < 2:
        return line, None
    last = numbers[-1]
    last_val = float(last.replace(",", "."))
    if last_val in ALLOY_HINTS:
        return line, None
    material = line[: line.rfind(last)].strip(" \t-") or line
    return material, last_val


def _looks_like_material(line: str) -> bool:
    return bool(
        re.search(
            r"aisi|stainless|steel|alu|aluminium|aluminum|silicon|silicone|en-aw|7075|6061|rubber",
            line,
            re.I,
        )
    )


def _is_numeric_noise(line: str) -> bool:
    compact = line.replace(",", ".").replace(" ", "")
    if NUMBER.fullmatch(compact):
        return True
    if NUMBER.fullmatch(line.replace(",", ".").strip()):
        return True
    return False


def _fallback_title(text: str) -> str | None:
    for line in text.splitlines():
        cleaned = _clean_line(line)
        if not cleaned or _is_boilerplate(cleaned) or _is_numeric_noise(cleaned):
            continue
        if NUMBER.search(cleaned):
            continue
        if 2 <= len(cleaned.split()) <= 6 and cleaned[0].isalpha():
            return cleaned
    return None


def _fallback_material(text: str) -> str | None:
    for token in ("Aluminium", "Aluminum", "Alu.", "Stainless", "Silicone", "Silicon"):
        if re.search(rf"\b{re.escape(token)}\b", text, re.I):
            match = re.search(rf".*{re.escape(token)}.*", text, re.I)
            if match:
                return _clean_line(match.group(0))
    return None


def _clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def _is_boilerplate(line: str) -> bool:
    return line.lower() in BOILERPLATE or line.lower().startswith("scale:")
