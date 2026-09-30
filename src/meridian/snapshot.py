"""Pinned BOM identity from dataset/manifest.json. No snapshot date is stored there."""

from __future__ import annotations

import json

from meridian.paths import MANIFEST_JSON

EVIDENCE_KIND = "recorded_bom_snapshot"


def bom_source_revision() -> str:
    payload = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    return str(payload["source_revision"])[:7]


def recorded_fact(value: str | None) -> dict:
    text = (value or "").strip()
    blank = not text
    return {
        "value": None if blank else value,
        "evidence_kind": EVIDENCE_KIND,
        "source_revision": bom_source_revision(),
        "uncertainty": "missing" if blank else None,
    }
