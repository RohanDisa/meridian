"""Live Groq measurements. Stops on 429. Does not write corrections."""

from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from openai import OpenAI

from meridian import chat
from meridian.chat import load_dotenv
from meridian.db import connect
from meridian.paths import DB_PATH, REPO_ROOT

SLEEP_S = 15
OUT_DIR = REPO_ROOT / "eval" / "results"

CASES = [
    {
        "id": "e2e-01",
        "group": "e2e",
        "q": "Show me the Build Plate",
        "need_any": ["Build Plate", "D-026"],
        "need_attach": ["D-026"],
    },
    {
        "id": "e2e-02",
        "group": "e2e",
        "q": "What material is the recoater head?",
        "need": ["Recoater head"],
        "need_any": ["Aluminium", "Aluminum", "5005", "EN-AW"],
    },
    {
        "id": "e2e-03",
        "group": "e2e",
        "q": "Which parts use drawing D-013?",
        "need": ["Recoater head", "Recoater stage plate"],
    },
    {
        "id": "e2e-04",
        "group": "e2e",
        "q": "What does D-022 show?",
        "need_any": ["Inlet Box", "D-022"],
    },
    {
        "id": "e2e-05",
        "group": "e2e",
        "q": "Which parts are made of aluminium?",
        "need_any": ["Recoater head", "Front Door", "Print Platform", "Build cylinder"],
    },
    {
        "id": "e2e-06",
        "group": "e2e",
        "q": "What does the left side box interface with?",
        "need": ["Left Side Box"],
        "need_any": ["Print Platform", "Back Box", "Front Side Box", "Top Side"],
    },
    {
        "id": "e2e-07",
        "group": "e2e",
        "q": "What did the front door latch cost and who supplied it?",
        "need": ["Front door latch"],
        "need_any": ["Misumi", "225"],
    },
    {
        "id": "e2e-08",
        "group": "abstention",
        "q": "What laser power does the machine use?",
        "need_any": ["not enough", "unsupported", "not recorded", "no evidence", "does not"],
        "forbid": ["watt", "kW", "kilowatt"],
    },
    {
        "id": "abs-16",
        "group": "abstention",
        "q": "what does the window frame connect to",
        "need_any": ["not recorded", "no outgoing", "Window Frame", "not enough", "blank"],
    },
    {
        "id": "abs-18",
        "group": "abstention",
        "q": "what is the mass of the front door",
        "need_any": ["not enough", "not stored", "no weight", "not recorded", "no evidence"],
        "forbid": [" kg"],
    },
    {
        "id": "repro-02a",
        "group": "repro",
        "repeat_of": "e2e-02",
        "q": "What material is the recoater head?",
        "need": ["Recoater head"],
        "need_any": ["Aluminium", "Aluminum", "5005", "EN-AW"],
    },
    {
        "id": "repro-08a",
        "group": "repro",
        "repeat_of": "e2e-08",
        "q": "What laser power does the machine use?",
        "need_any": ["not enough", "unsupported", "not recorded", "no evidence", "does not"],
        "forbid": ["watt", "kW", "kilowatt"],
    },
]


def grade(text: str, case: dict, attachments: list) -> tuple[bool, list[str]]:
    low = (text or "").lower()
    fails = []
    for token in case.get("need") or []:
        if token.lower() not in low:
            fails.append(f"missing:{token}")
    if case.get("need_any") and not any(token.lower() in low for token in case["need_any"]):
        fails.append("missing_any:" + "|".join(case["need_any"]))
    for token in case.get("forbid") or []:
        if token.lower() in low:
            fails.append(f"forbid:{token}")
    for drawing_id in case.get("need_attach") or []:
        if drawing_id not in attachments:
            fails.append(f"missing_attach:{drawing_id}")
    return not fails, fails


def is_unavailable(text: str) -> str | None:
    low = (text or "").lower()
    if "rate limit" in low or "429" in low:
        return "rate_limit"
    if "no credits" in low or "local matcher" in low:
        return "fallback"
    if "api key was rejected" in low:
        return "bad_key"
    return None


def install_usage_probe(bucket: list[dict]) -> None:
    orig = chat._complete_openai_compatible

    def wrapped(messages, tools, provider, tool_choice):
        cfg = chat._chat_client_config(provider)
        client_kwargs = {"api_key": cfg["api_key"]}
        if cfg.get("base_url"):
            client_kwargs["base_url"] = cfg["base_url"]
        client = OpenAI(**client_kwargs)
        response = client.chat.completions.create(
            model=cfg["model"],
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=0.2,
        )
        usage = response.usage
        bucket.append(
            {
                "provider": provider,
                "model": cfg["model"],
                "prompt": getattr(usage, "prompt_tokens", None) if usage else None,
                "completion": getattr(usage, "completion_tokens", None) if usage else None,
                "total": getattr(usage, "total_tokens", None) if usage else None,
            }
        )
        return response.choices[0].message

    chat._complete_openai_compatible = wrapped
    return orig


def citation_keys(result: dict) -> list[str]:
    keys = []
    for cite in result.get("citations") or []:
        keys.append(
            "|".join(
                str(cite.get(name) or "")
                for name in ("kind", "drawing_id", "file_line", "csv_line", "field", "name")
            )
        )
    return sorted(keys)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv()
    usages: list[dict] = []
    orig = install_usage_probe(usages)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect(DB_PATH)
    rows = []
    stopped = None
    try:
        for i, case in enumerate(CASES):
            start_usage = len(usages)
            t0 = time.perf_counter()
            result = chat.answer_question(conn, case["q"], llm=True)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            text = result.get("text") or ""
            tools = [item["tool"] for item in result.get("tool_results") or []]
            attachments = [item.get("drawing_id") for item in result.get("attachments") or []]
            unavailable = is_unavailable(text)
            good, fails = grade(text, case, attachments)
            if unavailable:
                good = False
                fails = [unavailable] + fails
            turn_usage = usages[start_usage:]
            tokens = sum(item.get("total") or 0 for item in turn_usage)
            row = {
                "id": case["id"],
                "group": case["group"],
                "q": case["q"],
                "pass": good,
                "fails": fails,
                "elapsed_ms": elapsed_ms,
                "tokens": tokens,
                "llm_calls": len(turn_usage),
                "tools": tools,
                "attachments": attachments,
                "citation_keys": citation_keys(result),
                "uncertainty": result.get("uncertainty"),
                "text": text[:500],
            }
            rows.append(row)
            mark = "PASS" if good else "FAIL"
            print(f"{mark} {case['id']} {elapsed_ms}ms {tokens}tok {case['q']}", flush=True)
            if fails:
                print("   ", fails, flush=True)
            if unavailable == "rate_limit":
                stopped = "rate_limit"
                print("STOP rate limit", flush=True)
                break
            if i < len(CASES) - 1:
                time.sleep(SLEEP_S)
    finally:
        chat._complete_openai_compatible = orig
        conn.close()

    by_id = {row["id"]: row for row in rows}
    repro_pairs = []
    for row in rows:
        if row["group"] != "repro":
            continue
        base_id = next(c["repeat_of"] for c in CASES if c["id"] == row["id"])
        base = by_id.get(base_id)
        if not base:
            continue
        same_tools = base["tools"] == row["tools"]
        same_cites = base["citation_keys"] == row["citation_keys"]
        repro_pairs.append(
            {
                "base": base_id,
                "repeat": row["id"],
                "same_tools": same_tools,
                "same_citations": same_cites,
                "match": same_tools and same_cites,
            }
        )

    finished = [row for row in rows if "rate_limit" not in row["fails"] and "fallback" not in row["fails"]]
    e2e = [row for row in finished if row["group"] == "e2e"]
    abstain = [row for row in finished if row["group"] == "abstention"]
    lat = [row["elapsed_ms"] for row in finished]
    tok = [row["tokens"] for row in finished if row["tokens"]]
    median = int(statistics.median(lat)) if lat else None
    p95 = int(sorted(lat)[max(0, int(round(0.95 * (len(lat) - 1))))]) if lat else None

    summary = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model": "openai/gpt-oss-120b",
        "provider": "groq",
        "sleep_s": SLEEP_S,
        "stopped": stopped,
        "e2e_pass": sum(1 for row in e2e if row["pass"]),
        "e2e_n": len(e2e),
        "abstention_pass": sum(1 for row in abstain if row["pass"]),
        "abstention_n": len(abstain),
        "latency_median_ms": median,
        "latency_p95_ms": p95,
        "tokens_total": sum(item.get("total") or 0 for item in usages),
        "tokens_per_turn_median": int(statistics.median(tok)) if tok else None,
        "cost_usd": 0,
        "cost_note": "Groq openai/gpt-oss-120b, no paid invoice in this run",
        "repro": repro_pairs,
        "repro_match": sum(1 for item in repro_pairs if item["match"]),
        "repro_n": len(repro_pairs),
        "rows": rows,
    }
    out = OUT_DIR / "llm_measurements.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(
        f"e2e {summary['e2e_pass']}/{summary['e2e_n']} "
        f"abstain {summary['abstention_pass']}/{summary['abstention_n']} "
        f"repro {summary['repro_match']}/{summary['repro_n']} "
        f"median {median}ms p95 {p95}ms tokens {summary['tokens_total']} -> {out}",
        flush=True,
    )


if __name__ == "__main__":
    main()
