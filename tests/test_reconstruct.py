import json

from meridian.db import connect, init_schema
from meridian.ingest_drawings import ingest_drawings
from meridian.paths import DRAWINGS_DIR, MANIFEST_JSON
from meridian.reconstruct import (
    DISCLAIMER,
    PARAMS,
    analytical_mass_g,
    build_all,
    register_reconstructions,
)
from meridian.tools import open_visual


def test_four_param_files_are_defined():
    assert set(PARAMS) == {"D-013", "D-015", "D-016", "D-026"}
    for drawing_id, spec in PARAMS.items():
        assert spec["drawing_id"] == drawing_id
        assert spec["thickness_mm"] > 0
        assert spec["disclaimer"] == DISCLAIMER.format(drawing_id=drawing_id)


def test_build_writes_meshes(tmp_path):
    written = build_all(tmp_path)
    assert set(written) == {"D-013", "D-015", "D-016", "D-026"}
    for drawing_id, path in written.items():
        assert path.is_file()
        assert path.stat().st_size > 200


def test_d016_mass_is_near_title_block_weight():
    mass = analytical_mass_g(PARAMS["D-016"])
    assert abs(mass - 1118.1) / 1118.1 < 0.15


def test_d015_mass_is_near_title_block_weight():
    mass = analytical_mass_g(PARAMS["D-015"])
    assert abs(mass - 4.6) / 4.6 < 0.35


def test_param_json_lists_each_dimension(tmp_path):
    build_all(tmp_path)
    payload = json.loads((tmp_path / "D-013.json").read_text(encoding="utf-8"))
    assert [item["value"] for item in payload["dimensions_used"]] == [
        "270 mm",
        "25 mm",
        "6 mm",
        "4.2 mm",
        "2.5 mm",
    ]
    assert all(item["drawing_id"] == "D-013" and item["page"] == 1 for item in payload["dimensions_used"])


def test_open_visual_includes_reconstruction_after_register(db_path, tmp_path):
    conn = connect(db_path)
    init_schema(conn)
    ingest_drawings(conn, MANIFEST_JSON, DRAWINGS_DIR)
    build_all(tmp_path)
    register_reconstructions(conn, tmp_path)
    result = open_visual(conn, "D-026")
    assert result["reconstruction"] is not None
    assert "Not native CAD" in result["reconstruction"]["disclaimer"]
    assert result["reconstruction"]["drawing_id"] == "D-026"
    disclaimer = result["reconstruction"]["disclaimer"]
    assert "Reconstruction from 2D evidence" in disclaimer
    assert "Not native CAD" in disclaimer
    dims = result["reconstruction"]["dimensions_used"]
    hole = next(item for item in dims if item["value"] == "5.3 mm")
    assert hole["drawing_id"] == "D-026"
    assert hole["page"] == 1
    assert hole["claim_id"]
    columns = [row[1] for row in conn.execute("PRAGMA table_info(reconstructions)")]
    assert "disclaimer" in columns
    assert "dimensions_used" not in columns
    drawing_only = open_visual(conn, "D-001")
    assert drawing_only["drawing_id"] == "D-001"
    assert drawing_only["reconstruction"] is None
    assert drawing_only["path"].endswith("D-001.pdf")
