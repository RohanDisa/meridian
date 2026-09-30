from __future__ import annotations

import csv
import re
from pathlib import Path

from meridian.normalize import normalize_name, parse_amount, parse_dkk, split_interfaces


def ingest_bom(conn, csv_path: Path) -> int:
    conn.execute("DELETE FROM interfaces")
    conn.execute("DELETE FROM drawing_bom_links")
    conn.execute("DELETE FROM bom_rows")

    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        previous_line = reader.line_num
        records = []
        record_index = 0
        for raw_row in reader:
            file_line = previous_line + 1
            previous_line = reader.line_num
            record_index += 1
            if len(raw_row) < len(header):
                raw_row = raw_row + [""] * (len(header) - len(raw_row))
            raw = {header[i]: raw_row[i] for i in range(len(header))}
            records.append((record_index, file_line, raw))

    inserted = 0
    for index, file_line, raw in records:
        name = (raw.get("Name") or "").strip()
        if not name:
            continue
        conn.execute(
            """
            INSERT INTO bom_rows (
              id, name, name_norm, part_family, type, design_state, order_state,
              vv_state, notes_raw, design_intent_raw, material_raw, product_name,
              supplier_order, interface_with_raw, link, supplier_raw, datasheet_raw,
              amount_raw, amount_parsed, cost_raw, cost_dkk, total_cost_raw,
              total_cost_dkk, interface_count_raw, source_file, source_row, file_line
            ) VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                index,
                name,
                normalize_name(name),
                raw.get("Part family") or None,
                raw.get("Type") or None,
                raw.get("Design") or None,
                raw.get("Order") or None,
                raw.get("V&V") or None,
                raw.get("Notes") or None,
                raw.get("Design Intend") or None,
                raw.get("Material") or None,
                raw.get("Product name") or None,
                raw.get("Supplier Order") or None,
                raw.get("Interface with") or None,
                raw.get("Link") or None,
                raw.get("Supplier") or None,
                raw.get("Datasheet") or None,
                raw.get("Amount") or None,
                parse_amount(raw.get("Amount")),
                raw.get("Cost") or None,
                parse_dkk(raw.get("Cost")),
                raw.get("Total cost") or None,
                parse_dkk(raw.get("Total cost")),
                raw.get("Count (Interface with)") or None,
                csv_path.name,
                index + 1,
                file_line,
            ),
        )
        inserted += 1

    _link_interfaces(conn)
    conn.commit()
    return inserted


def _link_interfaces(conn) -> None:
    by_norm = {
        row["name_norm"]: row["id"]
        for row in conn.execute("SELECT id, name_norm FROM bom_rows")
    }
    for row in conn.execute("SELECT id, interface_with_raw FROM bom_rows"):
        for target in split_interfaces(row["interface_with_raw"]):
            target_id = by_norm.get(normalize_name(target))
            conn.execute(
                """
                INSERT INTO interfaces (
                  from_bom_row_id, to_name_raw, to_bom_row_id, match_status
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    row["id"],
                    target,
                    target_id,
                    "exact" if target_id is not None else "unresolved",
                ),
            )


def datasheet_filenames(datasheet_raw: str | None) -> list[str]:
    if not datasheet_raw:
        return []
    return [
        match.group(1).strip()
        for match in re.finditer(r"([^,()]+?\.pdf)\s*\(", datasheet_raw, re.I)
    ]
