from fastapi.testclient import TestClient

from meridian.app import app
from meridian.ingest import ingest_all
from meridian.tools import REGION_BOXES


def _client(tmp_path, monkeypatch):
    db_path = tmp_path / "app.sqlite"
    monkeypatch.setattr("meridian.app.DB_PATH", db_path)
    monkeypatch.setattr("meridian.ingest.DB_PATH", db_path)
    ingest_all(db_path)
    return TestClient(app)


def test_drawing_citation_points_at_page_region_not_a_pdf_file(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    body = client.post("/api/chat", json={"message": "Tell me about the build plate"}).json()
    drawing = next(
        item
        for item in body["citations"]
        if item["kind"] == "drawing" and item.get("field") == "material"
    )
    assert drawing["drawing_id"] == "D-026"
    assert drawing["page"] == 1
    assert drawing["region"] == "title-block"
    assert drawing["value"]
    assert drawing["href"] == "/api/drawings/D-026/page/1"
    assert drawing["highlight"] == REGION_BOXES["title-block"]
    assert not drawing["href"].endswith(".pdf")
    assert "/file" not in drawing["href"]


def test_bom_citation_points_at_the_row_not_a_download(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    body = client.post("/api/chat", json={"message": "Tell me about the build plate"}).json()
    bom = next(
        item
        for item in body["citations"]
        if item["kind"] == "bom_row" and item.get("field") == "material_raw"
    )
    assert bom["href"].startswith("/api/evidence/bom/")
    assert bom["field"] == "material_raw"
    assert "Stainless" in (bom["value"] or "")
    assert bom["csv_line"] == 48
    assert bom["source_file"].endswith("openlpbf-bom.csv")


def test_drawing_page_is_an_inline_png(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    response = client.get("/api/drawings/D-026/page/1")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert "attachment" not in response.headers.get("content-disposition", "").lower()
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"
