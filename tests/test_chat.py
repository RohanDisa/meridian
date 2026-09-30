from meridian.chat import answer_question
from meridian.keyword_test_double import plan_tools
from meridian.db import connect, init_schema
from meridian.ingest_bom import ingest_bom
from meridian.ingest_drawings import ingest_drawings
from meridian.mapping import link_drawings_to_bom
from meridian.paths import BOM_CSV, DRAWINGS_DIR, MANIFEST_JSON
from meridian.reconstruct import build_all, register_reconstructions


def _ready(db_path, tmp_path):
    conn = connect(db_path)
    init_schema(conn)
    ingest_bom(conn, BOM_CSV)
    ingest_drawings(conn, MANIFEST_JSON, DRAWINGS_DIR)
    link_drawings_to_bom(conn)
    build_all(tmp_path)
    register_reconstructions(conn, tmp_path)
    return conn


def test_plan_opens_visual_for_named_reconstructed_part():
    plan = plan_tools("Open the build plate drawing and the 3D reconstruction")
    names = [step["tool"] for step in plan]
    assert "find_part" in names
    assert "open_visual" in names


def test_plan_uses_interfaces_for_connection_questions():
    plan = plan_tools("What does the Front Door interface with?")
    assert any(step["tool"] == "interfaces_of" for step in plan)


def test_answer_attaches_drawing_and_3d_for_build_plate(db_path, tmp_path):
    conn = _ready(db_path, tmp_path)
    result = answer_question(conn, "Tell me about the build plate")
    assert result["uncertainty"] in {None, "conflict"}
    assert any(att.get("drawing_id") == "D-026" for att in result["attachments"])
    assert any(att.get("reconstruction") for att in result["attachments"])
    assert any(cite["kind"] in {"bom_row", "drawing"} for cite in result["citations"])
    assert "Build Plate" in result["text"]


def test_answer_abstains_when_part_is_unknown(db_path, tmp_path):
    conn = _ready(db_path, tmp_path)
    result = answer_question(conn, "What is the warp core temperature?")
    assert result["uncertainty"] == "unsupported"
    assert "not enough evidence" in result["text"].lower()


def test_answer_can_propose_a_material_correction(db_path, tmp_path):
    conn = _ready(db_path, tmp_path)
    result = answer_question(
        conn, "Correct the Recoater head material to EN-AW 5005"
    )
    assert "Proposed correction" in result["text"]
    assert "EN-AW 5005" in result["text"]
    pending = conn.execute(
        "SELECT status, new_value FROM corrections ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert pending["status"] == "pending"
    assert pending["new_value"] == "EN-AW 5005"


def test_open_d015_attaches_the_wiper_not_the_build_plate(db_path, tmp_path):
    conn = _ready(db_path, tmp_path)
    result = answer_question(conn, "Open D-015")
    ids = [att["drawing_id"] for att in result["attachments"]]
    assert ids == ["D-015"]
    assert result["attachments"][0]["reconstruction"]["drawing_id"] == "D-015"


def test_silicone_wiper_finds_d015(db_path, tmp_path):
    conn = _ready(db_path, tmp_path)
    result = answer_question(conn, "Tell me about the silicone wiper")
    assert any(att.get("drawing_id") == "D-015" for att in result["attachments"])


def test_recoater_plate_attaches_d013(db_path, tmp_path):
    conn = _ready(db_path, tmp_path)
    result = answer_question(conn, "Open the recoater plate drawing")
    assert [att["drawing_id"] for att in result["attachments"]] == ["D-013"]
    names = {row["name"] for row in result["attachments"][0]["bom_rows"]}
    assert names == {"Recoater head", "Recoater stage plate"}
    assert "Recoater stage plate" in result["text"]


def test_galvo_bundplade_attaches_d016(db_path, tmp_path):
    conn = _ready(db_path, tmp_path)
    result = answer_question(conn, "Tell me about the galvo bundplade")
    assert [att["drawing_id"] for att in result["attachments"]] == ["D-016"]
    assert "not enough evidence" not in result["text"].lower()
    assert "D-016" in result["text"]


def test_answer_lists_front_door_interfaces(db_path, tmp_path):
    conn = _ready(db_path, tmp_path)
    result = answer_question(conn, "What does the Front Door interface with?")
    assert "Front Side Box" in result["text"]
