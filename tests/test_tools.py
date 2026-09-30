from meridian.db import connect, init_schema
from meridian.ingest_bom import ingest_bom
from meridian.ingest_drawings import ingest_drawings
from meridian.mapping import link_drawings_to_bom
from meridian.paths import BOM_CSV, DRAWINGS_DIR, MANIFEST_JSON
from meridian.tools import (
    find_part,
    get_part_facts,
    list_conflicts,
    open_visual,
    parts_by_material,
    parts_in_subsystem,
)


def _ready(db_path):
    conn = connect(db_path)
    init_schema(conn)
    ingest_bom(conn, BOM_CSV)
    ingest_drawings(conn, MANIFEST_JSON, DRAWINGS_DIR)
    link_drawings_to_bom(conn)
    return conn


def test_blank_cost_is_recorded_as_missing_on_the_pinned_bom(db_path):
    conn = _ready(db_path)
    row_id = conn.execute(
        "SELECT id FROM bom_rows WHERE name = 'Left Side Box'"
    ).fetchone()["id"]
    result = get_part_facts(conn, bom_row_id=row_id)
    cost = result["recorded"]["cost"]
    assert cost["value"] is None
    assert cost["uncertainty"] == "missing"
    assert cost["evidence_kind"] == "recorded_bom_snapshot"
    assert cost["source_revision"].startswith("98f76da")
    supplier = result["recorded"]["supplier"]
    assert supplier["evidence_kind"] == "recorded_bom_snapshot"
    assert supplier["source_revision"].startswith("98f76da")
    assert supplier["uncertainty"] is None
    assert supplier["value"]


def test_find_part_resolves_build_plate_and_drawing(db_path):
    conn = _ready(db_path)
    result = find_part(conn, "build plate")
    names = {item["name"] for item in result["items"]}
    drawings = {item["drawing_id"] for item in result["items"] if item.get("drawing_id")}
    assert "Build Plate" in names
    assert "D-026" in drawings
    assert result["uncertainty"] is None


def test_find_part_unknown_abstains(db_path):
    conn = _ready(db_path)
    result = find_part(conn, "warp drive housing")
    names = {item["name"] for item in result["items"]}
    assert result["items"] == []
    assert "Filter housing 2\" (with one filter)" not in names
    assert result["uncertainty"] == "unsupported"


def test_find_part_fuzzy_resolves_print_platfrm(db_path):
    conn = _ready(db_path)
    result = find_part(conn, "print platfrm")
    names = {item["name"] for item in result["items"]}
    drawings = {item.get("drawing_id") for item in result["items"]}
    assert "Print Platform" in names
    assert "Build Plate" not in names
    assert "D-026" not in drawings
    assert result["uncertainty"] is None


def test_find_part_keeps_glams_not_clamps(db_path):
    conn = _ready(db_path)
    result = find_part(conn, "glams")
    names = {item["name"] for item in result["items"]}
    assert names == {"GLAMS"}
    assert result["items"][0]["csv_line"] == 106


def test_get_part_facts_keeps_drawing_and_bom_material_separate(db_path):
    conn = _ready(db_path)
    bom_id = conn.execute("SELECT id FROM bom_rows WHERE name = 'Build Plate'").fetchone()["id"]
    result = get_part_facts(conn, bom_row_id=bom_id)
    assert result["bom"]["material"] == "Stainless steel,Steel"
    assert "AISI 316" in (result["drawing"]["material"] or "")
    assert result["conflict"] is True
    assert any(cite["kind"] == "bom_row" for cite in result["citations"])
    assert any(cite["kind"] == "drawing" for cite in result["citations"])


def test_get_part_facts_d013_keeps_both_bom_claims(db_path):
    conn = _ready(db_path)
    result = get_part_facts(conn, drawing_id="D-013")
    names = {row["name"] for row in result["linked_bom"]}
    assert names == {"Recoater head", "Recoater stage plate"}
    assert result["identity_conflict"] is True
    assert result["conflict"] is True
    assert result["material_relation"] == "conflict"
    assert result["uncertainty"] == "conflict"
    assert result["drawing"]["title"] == "Recoater Plate"


def test_parts_by_material_finds_stainless_custom_rows(db_path):
    conn = _ready(db_path)
    result = parts_by_material(conn, "stainless")
    names = {item["name"] for item in result["items"]}
    assert "Left Side Box" in names
    assert "Build Plate" in names


def test_parts_in_subsystem_maps_z_axis_to_build_plate_family(db_path):
    conn = _ready(db_path)
    result = parts_in_subsystem(conn, "Z-axis")
    families = {item["part_family"] for item in result["items"]}
    drawings = {item.get("drawing_id") for item in result["items"]}
    names = {item["name"] for item in result["items"]}
    assert "Build-plate" in families
    assert "D-026" in drawings
    assert "Thermocouple plate" in names
    assert "Stepper driver NEMA23" in names
    assert result["item_count"] == 14
    build_plate = next(item for item in result["items"] if item["name"] == "Build Plate")
    assert build_plate["csv_line"] == 48
    assert any(cite.get("csv_line") == 48 for cite in result["citations"])
    motor = next(item for item in result["items"] if item["name"] == "Motor plate")
    assert motor.get("link_status") == "candidate"
    assert motor.get("drawing_subsystem") == "Powder"


def test_open_visual_returns_drawing_path_without_inventing_3d(db_path):
    conn = _ready(db_path)
    result = open_visual(conn, "D-026")
    assert result["drawing_id"] == "D-026"
    assert result["path"].endswith("D-026.pdf")
    assert result["reconstruction"] is None


def test_list_conflicts_includes_d013_dual_datasheet(db_path):
    conn = _ready(db_path)
    result = list_conflicts(conn)
    d013 = [item for item in result["items"] if item.get("drawing_id") == "D-013"]
    assert d013
    names = {name for item in d013 for name in item.get("bom_names", [])}
    assert "Recoater head" in names
    assert "Recoater stage plate" in names
