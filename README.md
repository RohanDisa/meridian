# Meridian

A local chat app for questioning the design evidence behind OpenLPBF v2, an open metal laser powder-bed-fusion machine. It answers from a structured SQLite extract of the pinned BOM and 30 fabrication drawings, cites the evidence behind every answer, and opens the relevant drawing (and 3D reconstruction, where one exists) in the same turn. Corrections proposed in chat go through a human review step and never overwrite the original evidence.

| Requirement | Section |
|---|---|
| Chat over extracted knowledge | [Architecture](#architecture), [Uncertainty codes](#uncertainty-codes) |
| 2D drawing plus orbitable 3D for 3+ parts | [3D reconstructions](#3d-reconstructions) |
| Chat opens the drawing and 3D | `open_visual`, [Example questions](#example-questions) |
| One governed correction | [Corrections](#corrections) |

## Quick start

Requirements: Python 3.11+, Node 22.22.1 (installed here; no minimum declared), and an API key for one supported provider.

Copy `.env.example` to `.env` and add `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, or `OPENAI_API_KEY`. Keep the OpenLPBF corpus in local folder `project-meridian/`. It is not part of this repository.

```
python start.py
```

`start.py` creates `.venv`, installs dependencies, builds `data/meridian.sqlite` if missing, and starts the API (port 8001) and UI (http://127.0.0.1:5173).

Manual start:

```
python -m pip install -e ".[dev]"
python -m meridian.ingest
python -m uvicorn meridian.app:app --app-dir src --host 127.0.0.1 --port 8001
```

```
cd frontend
npm install
npm run dev
```

Tests: `python -m pytest -q`

Without an API key the chat displays a warning and does not answer. There is no offline fallback, because a keyword matcher would answer without the tool and citation guarantees described below. (`src/meridian/keyword_test_double.py` exists only as a test double.)

## Architecture

```
dataset/  (BOM CSV, 30 drawing PDFs)
   │  ingest: runs once
   ▼
data/meridian.sqlite ──► fixed tools (SQL) ──► JSON with citations ──► LLM writes the reply
                                                      └──► UI opens drawing / 3D
```

**Storage.** `bom_rows` holds each BOM record with original cells and parsed values side by side. `drawings` holds one row per sheet (title, material, weight, scale, confidence, read method). `drawing_claims` holds each extracted fact with drawing, page, sheet region, confidence, and method. `interfaces` holds relationships parsed from the BOM `Interface with` column. `corrections` and `correction_events` hold proposed changes and their review history.

**Tools.** `find_part`, `get_part_facts`, `interfaces_of`, `parts_by_material`, `parts_in_subsystem`, `list_conflicts`, `open_visual`, `propose_correction`, `correction_history`. Each runs fixed SQL and returns items, citations, and an uncertainty code. The model selects a tool and phrases the result. It never writes SQL and cannot cite anything a tool did not return.

## Key decisions

**Answer from extracted data, not documents.** The BOM and drawings are parsed once at ingest. At answer time nothing reads the CSV or PDFs, so every answer is traceable to a stored row.

**SQLite, including relationships.** The corpus is 30 drawings and one BOM for a single local user. Relationships are rows in `interfaces`, traversed in Python. A graph database would add setup without enabling any query this dataset needs.

**Drawing-to-BOM links carry their own evidence.** A link is never a silent merge:

| Basis | Status |
|---|---|
| BOM `Datasheet` filename matches the drawing file | confirmed |
| Exact title match | confirmed |
| Manual entry in `data/mapping_overrides.json` | confirmed (manual) |
| Token overlap or Danish glossary (`Bygge`, `Plade`, `Spænd`, `Varme`, `Bundplade`) | candidate, reported as unverified |
| Two BOM rows name the same file | conflict, both rows kept |

D-013 (`RecoaterPlate.pdf`) is named by both Recoater head and Recoater stage plate and remains a conflict. D-014 (`RecoaterStagePlate.pdf`) is not named in `Datasheet` and is linked to Recoater stage plate by manual override.

**Material strings are not normalized away.** Spelling variants match (aluminium, aluminum). A more specific designation does not: `Aluminium` versus `3.3315 (EN-AW 5005)` is reported as a disagreement, quoting both, with a note that one may be a more specific grade.

**Raw values are preserved.** Parsed amounts and costs sit beside the original cell text. Blank cells are reported as missing, never inferred.

**Citations point to the physical CSV line.** Answers cite `file_line`, the line where a record starts in `openlpbf-bom.csv`. The record index (`csv_line`) is kept in tool output. The two differ where a quoted cell spans several lines (Recoater head: file line 33, record 28).

**Degraded drawings are read once, at build time.** D-001, D-003, D-009, D-011, D-020, D-022, D-023, and D-030 have no usable text layer. Each was read once by a vision model, and the results are stored in `data/degraded_reviews.json` and loaded at low confidence. Illegible fields stay illegible. Chat never calls a vision model.

**Procurement data is a snapshot.** Supplier, order number, link, and cost are labelled `recorded_bom_snapshot` at upstream revision `98f76da` (from `source_revision` in `dataset/manifest.json`). They are not current prices or availability. Finding alternative suppliers is out of scope.

**Chat history is kept but versioned.** Earlier replies can become outdated when a correction is reviewed. Every reply is stamped with a knowledge version (the latest correction event id). If knowledge has changed since an earlier reply, the next request includes a note listing exactly what changed. The first model step must call a tool, and the system prompt states that current tool results override earlier turns.

## Uncertainty codes

| Code | Set when | Reply behaviour |
|---|---|---|
| `null` | The tool found what was asked | Answer with citations |
| `unsupported` | No matching part, unknown id, or tool error | States there is not enough evidence |
| `missing` | The part exists but the field or relationship is blank | States the value is not recorded |
| `conflict` | Two BOM rows claim one drawing, or the current value disagrees with the drawing and no accepted correction resolves it | Shows both values with citations |

## Interfaces

The BOM `Interface with` cell is split on commas. Each token is matched to `bom_rows.name_norm` after lowercasing and collapsing spaces. A hit is stored as `exact` with `to_bom_row_id` set. A miss is stored as `unresolved`, with the raw token kept and `to_bom_row_id` left empty. Ingest has no fuzzy match type. `interfaces_of` follows outgoing edges at most 3 hops and sets `crosses_subsystem` when both parts have a `part_family` and the families differ.

| match_status | rows |
|---|---|
| exact | 24 |
| unresolved | 0 |
| total | 24 |

## 3D reconstructions

Four parts have an orbitable 3D model built from their drawings. Each view is labelled "Reconstruction from 2D evidence (drawing id). Not native CAD. Hole positions and omitted features may be approximate." and lists the dimensions used with drawing and page citations.

| Part | Drawing | Method | Assumptions |
|---|---|---|---|
| Silikone Wiper | D-015 | Extruded rectangular strip (prism grid from the 2D outline) with eight through holes | Eight 3.4 mm holes spaced evenly on the 270 mm centerline. The 12 mm end inset is estimated from the view, not a labeled spacing. |
| Recoater Plate | D-013 | Extruded rectangle with corner radius (prism grid), holes, and two slots | Thickness from the 6.0 mm side view. Slot and small-hole coordinates are estimated from the front view. Not every M3 hole is modeled. |
| Galvo Bundplade | D-016 | Extruded rectangle with corner radius (prism grid), a 145 mm opening, and a subset of small holes | Thickness from the 10.0 mm side view. The 145 mm opening is on the vertical centerline in the lower half. Only a subset of the small thru holes is modeled. |
| Build Plate | D-026 | Extruded disk (prism grid from the 2D outline) with three through holes | Thickness from the 15.0 mm side view, not the adjacent 25.0. Three 5.3 mm holes on the labeled R105 pitch circle at 120°. Counterbore 10.0 / 15.4 mm is omitted. Title-block weight 7822.5 g is higher than a 250 x 15 mm AISI 316 disk. |

The Recoater Plate reconstruction follows drawing D-013. That sheet is claimed by both Recoater head and Recoater stage plate, so the drawing's BOM link is an open conflict.

## Corrections

1. **Propose.** The user states a correction in chat. `propose_correction` stores it as pending and records automatic checks: the part and field exist, the current BOM value and citation, the drawing value and citation, and whether the proposal agrees with either. Correctable fields: material, supplier, cost, title.
2. **Review.** The Review page shows the original evidence, the proposal, and the check results. The reviewer accepts or rejects, with an optional note.
3. **Accepted.** `get_part_facts` returns the new `current_value` with its correction id and date, alongside `original_value` and `original_citation`. The conflict for that field is resolved and recorded in `conflict_history`.
4. **Rejected.** Answers are unchanged.
5. **History.** Every proposal, acceptance, and rejection is an event in `correction_events`. Nothing is edited or deleted. History is visible on the Review page and through `correction_history` in chat.

## Example questions

| Question | Demonstrates |
|---|---|
| "Show me the Build Plate" | Drawing and 3D open in one turn |
| "What material is the recoater head?" | BOM and drawing disagreement, both cited |
| "Which parts use drawing D-013?" | One drawing claimed by two BOM rows |
| "What does D-022 show?" | Degraded drawing, low confidence |
| "Which parts are made of aluminium?" | Shared material across parts |
| "What does the left side box interface with?" | Interface traversal |
| "What did the front door latch cost and who supplied it?" | Recorded snapshot with caveat |
| "What laser power does the machine use?" | Abstention |
| Recoater head material, before and after an accepted correction | Changed answer, original still cited |

## Models, services, and data sent externally

| Use | Model | Data sent |
|---|---|---|
| Chat | `claude-opus-5-5` (Anthropic), `openai/gpt-oss-120b` (Groq), `gpt-4o-mini` (OpenAI) | The question, recent chat history, and tool JSON |
| Degraded drawing reading, once at build time | Cursor vision (no API model id recorded) | **Page images of the 8 degraded drawings** |
| PDF parsing and rendering | PyMuPDF 1.28.2 (local) | Nothing |

The BOM file and clean drawings are never sent. Each question tries Groq, then Anthropic, then OpenAI. A provider is skipped when its key is missing or the call fails. If every key fails, the reply says none of them worked. If no key is set, the reply says so. `GET /api/about` returns the model ids and the pinned BOM revision. No credentials are included in this repository.

## Measurements

Tool-layer: README example questions through `_run_named_tool` (no LLM). Extraction counts: `data/meridian.sqlite`. Live LLM: Groq `openai/gpt-oss-120b`, 12 turns with 15 s between turns, `eval/run_llm_measurements.py` → `eval/results/llm_measurements.json`. No 429 on that run. Pytest: 89 passed (`python -m pytest -q`).

| Measure | Method | Result | 
|---|---|---|
| Tool-layer correctness | README example questions, tools only, no LLM | 9 / 9 |
| End-to-end answer quality | 7 README questions through Groq (Build Plate, recoater head material, D-013, D-022, aluminium list, left side box interfaces, front door latch cost) | 7 / 7 |
| Abstention | Laser power, window-frame interfaces, front-door mass, through Groq | 3 / 3 |
| Extraction accuracy | Stored drawing and link counts. No separate hand-labelled score file. | 30 / 30 drawings have a title (22 `pdf_text`, 8 `vision_review`). 8 / 8 degraded-only sheets are `low` confidence. `drawing_bom_links` has 40 rows for those 30 drawings: 7 confirmed, 2 conflict, 15 candidate, and 16 unmatched (drawing with no BOM row). 125 drawing claims. |
| Latency | Wall time per Groq turn (12 turns) | Median 39 s, p95 49 s |
| Cost | Groq `openai/gpt-oss-120b` | $0. 128,002 tokens (median 10,382 per turn). |
| Reproducibility | Recoater-head material and laser-power questions, each asked 5| 2 / 2 same tools and same citations |

## Limitations and error analysis

**Stale answers after a correction (fixed).** After a correction was accepted, chat still reported the old material. There were three causes: earlier replies in chat history stated the old value, the system prompt's example answer used this same part, and the conflict flag compared the original cell to the drawing rather than the current value. Fixed with versioned history, a neutral example, and conflict checks against the current value.

**Truncated alloy designation (fixed).** The title-block parser treated a trailing number as a weight, storing `3.3315 (EN-AW 5005)` as `3.3315 (EN-AW`. A trailing number is now removed only when it has a weight unit or comes from the weight field.

**Slow turns.** A median turn uses about 10,400 tokens, above Groq's free limit of 8,000 tokens per minute, so turns wait on rate limits. Median latency is 39 s.

**Degraded drawings were read once, outside the pipeline.** The eight degraded pages were read through Cursor's vision, with no recorded model id and no second reading. Re-running ingest reuses `data/degraded_reviews.json` rather than reproducing that step.

**Small evaluation set.** Quality figures come from 7 to 12 questions and are indicative, not statistically strong.

## Saved output

`docs/` contains screenshots of the app, including the 2D drawings and 3D reconstructions for the four reconstructed parts.

## Attribution

The dataset is third-party open hardware material. See `ATTRIBUTION.md` and the included license. Do not publish this code or its results without that attribution.