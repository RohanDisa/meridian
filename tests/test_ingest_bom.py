import csv

from meridian.db import connect, init_schema
from meridian.ingest_bom import ingest_bom


def _load(db_path, bom_csv):
    conn = connect(db_path)
    init_schema(conn)
    ingest_bom(conn, bom_csv)
    return conn


def test_ingests_one_row_per_csv_data_line(db_path, bom_csv):
    conn = _load(db_path, bom_csv)
    count = conn.execute("SELECT COUNT(*) FROM bom_rows").fetchone()[0]
    assert count == 192


def test_file_line_is_where_the_record_starts(db_path, bom_csv):
    conn = _load(db_path, bom_csv)
    head = conn.execute(
        "SELECT source_row, file_line FROM bom_rows WHERE name = ?",
        ("Recoater head",),
    ).fetchone()
    plate = conn.execute(
        "SELECT source_row, file_line FROM bom_rows WHERE name = ?",
        ("Recoater stage plate",),
    ).fetchone()
    assert head["file_line"] == 33
    assert head["source_row"] == 28
    assert plate["file_line"] == 37
    assert plate["source_row"] == 31


def test_source_row_is_the_csv_file_line(db_path, bom_csv):
    conn = _load(db_path, bom_csv)
    with bom_csv.open(encoding="utf-8-sig", newline="") as handle:
        expected = next(
            index
            for index, raw in enumerate(csv.DictReader(handle), start=2)
            if (raw.get("Name") or "").strip() == "Build Plate"
        )
    row = conn.execute(
        "SELECT source_row, source_file FROM bom_rows WHERE name = ?",
        ("Build Plate",),
    ).fetchone()
    assert row["source_row"] == expected
    assert row["source_file"].endswith("openlpbf-bom.csv")


def test_keeps_raw_cost_and_parses_dkk(db_path, bom_csv):
    conn = _load(db_path, bom_csv)
    row = conn.execute(
        "SELECT cost_raw, cost_dkk FROM bom_rows WHERE name = ?",
        ("Front Door",),
    ).fetchone()
    assert row["cost_raw"] == "DKK100.00"
    assert row["cost_dkk"] == 100.0


def test_blank_cost_stays_null(db_path, bom_csv):
    conn = _load(db_path, bom_csv)
    row = conn.execute(
        "SELECT cost_raw, cost_dkk, total_cost_raw, total_cost_dkk "
        "FROM bom_rows WHERE name = ?",
        ("Left Side Box",),
    ).fetchone()
    assert row["cost_raw"] in ("", None)
    assert row["cost_dkk"] is None
    assert row["total_cost_raw"] == "DKK0.00"
    assert row["total_cost_dkk"] == 0.0


def test_name_norm_is_lowercase_collapsed(db_path, bom_csv):
    conn = _load(db_path, bom_csv)
    value = conn.execute(
        "SELECT name_norm FROM bom_rows WHERE name = ?",
        ("Left Side Box",),
    ).fetchone()[0]
    assert value == "left side box"


def test_left_side_box_has_four_outgoing_interfaces(db_path, bom_csv):
    conn = _load(db_path, bom_csv)
    source_id = conn.execute(
        "SELECT id FROM bom_rows WHERE name = ?", ("Left Side Box",)
    ).fetchone()[0]
    rows = conn.execute(
        "SELECT to_name_raw, to_bom_row_id, match_status "
        "FROM interfaces WHERE from_bom_row_id = ? ORDER BY to_name_raw",
        (source_id,),
    ).fetchall()
    names = [r["to_name_raw"] for r in rows]
    assert names == ["Back Box", "Front Side Box", "Print Platform", "Top Side"]
    assert all(r["match_status"] == "exact" for r in rows)
    assert all(r["to_bom_row_id"] is not None for r in rows)


def test_interface_edges_are_one_way(db_path, bom_csv):
    conn = _load(db_path, bom_csv)
    platform_id = conn.execute(
        "SELECT id FROM bom_rows WHERE name = ?", ("Print Platform",)
    ).fetchone()[0]
    outgoing = conn.execute(
        "SELECT COUNT(*) FROM interfaces WHERE from_bom_row_id = ?",
        (platform_id,),
    ).fetchone()[0]
    assert outgoing == 0


def test_front_door_interfaces_to_front_side_box(db_path, bom_csv):
    conn = _load(db_path, bom_csv)
    row = conn.execute(
        """
        SELECT i.to_name_raw, t.name
        FROM interfaces i
        JOIN bom_rows f ON f.id = i.from_bom_row_id
        JOIN bom_rows t ON t.id = i.to_bom_row_id
        WHERE f.name = ?
        """,
        ("Front Door",),
    ).fetchone()
    assert row["to_name_raw"] == "Front Side Box"
    assert row["name"] == "Front Side Box"


def test_unresolved_interface_names_are_kept(db_path, bom_csv, tmp_path):
    import csv

    fixture = tmp_path / "tiny.csv"
    fieldnames = [
        "Name",
        "Part family",
        "Type",
        "Design",
        "Order",
        "V&V",
        "Notes",
        "Design Intend",
        "Image",
        "Material",
        "Product name",
        "Supplier Order",
        "Interface with",
        "Link",
        "Supplier",
        "Datasheet",
        "Amount",
        "Cost",
        "Total cost",
        "Count (Interface with)",
    ]
    with fixture.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerow(
            {
                "Name": "Alpha",
                "Part family": "Box",
                "Type": "Custom",
                "Interface with": "Beta",
                "Amount": "1",
                "Total cost": "DKK0.00",
                "Count (Interface with)": "1",
            }
        )
    conn = connect(db_path)
    init_schema(conn)
    ingest_bom(conn, fixture)
    row = conn.execute("SELECT to_name_raw, to_bom_row_id, match_status FROM interfaces").fetchone()
    assert row["to_name_raw"] == "Beta"
    assert row["to_bom_row_id"] is None
    assert row["match_status"] == "unresolved"
