from meridian.db import connect, init_schema
from meridian.ingest_bom import ingest_bom
from meridian.paths import BOM_CSV
from meridian.tools import interfaces_of


def _conn(db_path):
    conn = connect(db_path)
    init_schema(conn)
    ingest_bom(conn, BOM_CSV)
    return conn


def test_front_door_has_one_outgoing_interface(db_path):
    conn = _conn(db_path)
    door = conn.execute("SELECT id FROM bom_rows WHERE name = 'Front Door'").fetchone()["id"]
    result = interfaces_of(conn, door)
    assert [item["to_name"] for item in result["items"]] == ["Front Side Box"]
    assert result["items"][0]["match_status"] == "exact"


def test_print_platform_has_no_recorded_outgoing_interfaces(db_path):
    conn = _conn(db_path)
    platform = conn.execute(
        "SELECT id FROM bom_rows WHERE name = 'Print Platform'"
    ).fetchone()["id"]
    result = interfaces_of(conn, platform)
    assert result["items"] == []
    assert result["uncertainty"] == "missing"


def test_depth_two_reaches_print_platform_and_stays_in_box(db_path):
    conn = _conn(db_path)
    door = conn.execute("SELECT id FROM bom_rows WHERE name = 'Front Door'").fetchone()["id"]
    result = interfaces_of(conn, door, hops=2)
    first = next(item for item in result["items"] if item["to_name"] == "Front Side Box")
    assert first["depth"] == 1
    assert first["match_status"] == "exact"
    assert first["raw_text"] == "Front Side Box"
    assert first["source_text"]
    assert first["crosses_subsystem"] is False
    assert first["both_directions"] is True
    second = next(
        item
        for item in result["items"]
        if item["to_name"] == "Print Platform" and item["depth"] == 2
    )
    assert second["path"][0] == "Front Door"
    assert second["path"][-1] == "Print Platform"
    assert max(item["depth"] for item in result["items"]) <= 2


def test_hops_are_capped_at_three(db_path):
    conn = _conn(db_path)
    door = conn.execute("SELECT id FROM bom_rows WHERE name = 'Front Door'").fetchone()["id"]
    result = interfaces_of(conn, door, hops=9)
    assert result["items"]
    assert max(item["depth"] for item in result["items"]) <= 3


def test_laser_source_crosses_into_electric(db_path):
    conn = _conn(db_path)
    source = conn.execute(
        "SELECT id FROM bom_rows WHERE name = 'Laser source'"
    ).fetchone()["id"]
    result = interfaces_of(conn, source)
    glams = next(item for item in result["items"] if item["to_name"] == "GLAMS")
    assert glams["crosses_subsystem"] is True
    assert glams["from_family"] == "Optics"
    assert glams["to_family"] == "Electric"
    assert glams["match_status"] == "exact"
    assert glams["raw_text"] == "GLAMS"


def test_unresolved_interface_name_is_kept(db_path):
    conn = _conn(db_path)
    door = conn.execute("SELECT id FROM bom_rows WHERE name = 'Front Door'").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO interfaces (from_bom_row_id, to_name_raw, to_bom_row_id, match_status)
        VALUES (?, 'Not A Real Part', NULL, 'unresolved')
        """,
        (door,),
    )
    conn.commit()
    result = interfaces_of(conn, door)
    item = next(item for item in result["items"] if item["raw_text"] == "Not A Real Part")
    assert item["match_status"] == "unresolved"
    assert item["to_name"] == "Not A Real Part"
    assert item["to_bom_row_id"] is None


def test_interfaces_can_include_incoming_mentions(db_path):
    conn = _conn(db_path)
    platform = conn.execute(
        "SELECT id FROM bom_rows WHERE name = 'Print Platform'"
    ).fetchone()["id"]
    result = interfaces_of(conn, platform, include_incoming=True)
    mentioned_by = {item["from_name"] for item in result["mentioned_by"]}
    assert "Left Side Box" in mentioned_by
    assert "Back Box" in mentioned_by
