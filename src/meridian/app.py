from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from meridian.chat import _available_providers, answer_question
from meridian.models import CHAT_MODELS, VISION_MODEL, VISION_NOTE
from meridian.snapshot import bom_source_revision
from meridian.db import connect, init_schema
from meridian.ingest import ingest_all
from meridian.paths import DATA_DIR, DB_PATH, DRAWINGS_DIR, REPO_ROOT
from meridian.render import render_drawing_page
from meridian.tools import bom_evidence, correction_history, open_visual, review_correction


class HistoryTurn(BaseModel):
    role: str
    text: str = ""
    knowledge_version: int | None = None


class ChatRequest(BaseModel):
    message: str
    history: list[HistoryTurn] = []


class ReviewRequest(BaseModel):
    decision: str
    reviewer: str = "reviewer"
    note: str | None = None


def get_conn():
    if not DB_PATH.is_file():
        ingest_all()
    conn = connect(DB_PATH)
    init_schema(conn)
    return conn


@asynccontextmanager
async def lifespan(_app: FastAPI):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.is_file():
        ingest_all()
    yield


app = FastAPI(title="Meridian", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/about")
def about():
    return {
        "chat_models": CHAT_MODELS,
        "vision_model": VISION_MODEL,
        "vision_note": VISION_NOTE,
        "bom_source_revision": bom_source_revision(),
        "evidence_kind": "recorded_bom_snapshot",
    }


@app.get("/api/status")
def status():
    from meridian.chat import load_dotenv
    import os

    load_dotenv()
    providers = _available_providers()
    return {
        "api_key_configured": bool(providers),
        "provider": providers[0] if providers else None,
        "providers": providers,
        "preferred": (os.environ.get("MERIDIAN_CHAT_PROVIDER") or "").strip() or None,
        "code": "try-groq-anthropic-openai",
    }


@app.post("/api/chat")
def chat(payload: ChatRequest):
    conn = get_conn()
    try:
        return answer_question(
            conn,
            payload.message,
            history=[
                {
                    "role": turn.role,
                    "text": turn.text,
                    "knowledge_version": turn.knowledge_version,
                }
                for turn in payload.history
            ],
        )
    finally:
        conn.close()


@app.get("/api/drawings/{drawing_id}/page/{page}")
def drawing_page(drawing_id: str, page: int = 1):
    try:
        png = render_drawing_page(drawing_id, page)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="drawing not found") from None
    return Response(
        content=png,
        media_type="image/png",
        headers={"Content-Disposition": "inline", "Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/drawings/{drawing_id}/file")
def drawing_file(drawing_id: str):
    path = DRAWINGS_DIR / f"{drawing_id}.pdf"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="drawing not found")
    return FileResponse(
        path,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{path.name}"'},
    )


@app.get("/api/reconstructions/{drawing_id}/file")
def reconstruction_file(drawing_id: str):
    path = DATA_DIR / "reconstructions" / f"{drawing_id}.glb"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="reconstruction not found")
    return FileResponse(path, media_type="model/gltf-binary", filename=path.name)


@app.get("/api/evidence/bom/{bom_row_id}")
def evidence_bom(bom_row_id: int):
    conn = get_conn()
    try:
        payload = bom_evidence(conn, bom_row_id)
        if payload.get("uncertainty") == "unsupported":
            raise HTTPException(status_code=404, detail="BOM row not found")
        return payload
    finally:
        conn.close()


@app.get("/api/evidence/drawing/{drawing_id}")
def evidence_drawing(drawing_id: str):
    conn = get_conn()
    try:
        payload = open_visual(conn, drawing_id)
        if payload.get("uncertainty") == "unsupported":
            raise HTTPException(status_code=404, detail="drawing not found")
        return payload
    finally:
        conn.close()


@app.get("/api/corrections")
def list_corrections():
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM corrections ORDER BY id DESC"
        ).fetchall()
        return {"items": [dict(row) for row in rows], "events": correction_history(conn)}
    finally:
        conn.close()


@app.post("/api/corrections/{correction_id}/review")
def review(correction_id: int, payload: ReviewRequest):
    if payload.decision not in {"accepted", "rejected"}:
        raise HTTPException(status_code=400, detail="decision must be accepted or rejected")
    conn = get_conn()
    try:
        return review_correction(
            conn, correction_id, payload.decision, payload.reviewer, payload.note
        )
    finally:
        conn.close()


frontend_dist = REPO_ROOT / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="ui")
