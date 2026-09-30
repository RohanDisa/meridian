import json

from fastapi.testclient import TestClient

from meridian.app import app
from meridian.db import connect
from meridian.ingest import ingest_all
from meridian.tools import propose_correction


def test_chat_endpoint_opens_build_plate(tmp_path, monkeypatch):
    db_path = tmp_path / "app.sqlite"
    monkeypatch.setattr("meridian.app.DB_PATH", db_path)
    monkeypatch.setattr("meridian.ingest.DB_PATH", db_path)
    ingest_all(db_path)
    client = TestClient(app)
    response = client.post("/api/chat", json={"message": "Open the build plate"})
    assert response.status_code == 200
    body = response.json()
    assert any(att.get("drawing_id") == "D-026" for att in body["attachments"])


def test_bom_evidence_link_includes_drawing(tmp_path, monkeypatch):
    db_path = tmp_path / "app.sqlite"
    monkeypatch.setattr("meridian.app.DB_PATH", db_path)
    monkeypatch.setattr("meridian.ingest.DB_PATH", db_path)
    ingest_all(db_path)
    client = TestClient(app)
    chat = client.post("/api/chat", json={"message": "Tell me about the build plate"})
    cite = next(item for item in chat.json()["citations"] if item.get("kind") == "bom_row")
    assert cite["drawing_id"] == "D-026"
    assert cite["href"] == f"/api/evidence/bom/{cite['bom_row_id']}"
    evidence = client.get(cite["href"])
    assert evidence.status_code == 200
    assert evidence.json()["drawing_id"] == "D-026"


def test_status_flags_a_missing_api_key(monkeypatch):
    monkeypatch.setattr("meridian.app._available_providers", lambda: [])
    client = TestClient(app)
    response = client.get("/api/status")
    assert response.status_code == 200
    body = response.json()
    assert body["api_key_configured"] is False
    assert body.get("provider") is None


def test_about_reads_the_model_config():
    client = TestClient(app)
    body = client.get("/api/about").json()
    assert body["chat_models"]["groq"] == "openai/gpt-oss-120b"
    assert body["vision_model"] is None
    assert "Cursor vision" in body["vision_note"]
    assert body["bom_source_revision"].startswith("98f76da")
    assert body["evidence_kind"] == "recorded_bom_snapshot"


def test_review_api_returns_checks_evidence_and_events(tmp_path, monkeypatch):
    db_path = tmp_path / "app.sqlite"
    monkeypatch.setattr("meridian.app.DB_PATH", db_path)
    monkeypatch.setattr("meridian.ingest.DB_PATH", db_path)
    ingest_all(db_path)
    conn = connect(db_path)
    bom_id = conn.execute("SELECT id FROM bom_rows WHERE name = 'Recoater head'").fetchone()["id"]
    proposal = propose_correction(
        conn,
        entity_type="bom_row",
        entity_id=str(bom_id),
        field="material",
        new_value="EN-AW 5005",
        reason="title-block alloy",
    )
    conn.close()
    client = TestClient(app)
    listed = client.get("/api/corrections")
    assert listed.status_code == 200
    body = listed.json()
    item = next(row for row in body["items"] if row["id"] == proposal["id"])
    checks = json.loads(item["checks"])
    assert checks["bom_value"] == "Aluminium"
    assert checks["file_line"] == 33
    assert "EN-AW 5005" in checks["drawing"]["value"]
    assert any(event["event"] == "proposed" and event["correction_id"] == proposal["id"] for event in body["events"])
    reviewed = client.post(
        f"/api/corrections/{proposal['id']}/review",
        json={"decision": "accepted", "reviewer": "local", "note": "checked the title block"},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["reviewer_note"] == "checked the title block"
    again = client.get("/api/corrections").json()
    assert any(event["event"] == "accepted" for event in again["events"])


def test_drawing_file_is_served(tmp_path, monkeypatch):
    db_path = tmp_path / "app.sqlite"
    monkeypatch.setattr("meridian.app.DB_PATH", db_path)
    client = TestClient(app)
    response = client.get("/api/drawings/D-015/file")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
