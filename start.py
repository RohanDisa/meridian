#!/usr/bin/env python3
"""Create the venv if needed, ingest if needed, then run the API and UI."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API_HOST = "127.0.0.1"
API_PORT = "8001"


def venv_python() -> Path:
    if os.name == "nt":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def load_env(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ[key] = value


def run(args: list[str], **kwargs) -> None:
    subprocess.run(args, check=True, cwd=ROOT, **kwargs)


def npm_cmd() -> str:
    found = shutil.which("npm")
    if not found:
        raise SystemExit("npm was not found on PATH. Install Node.js and retry.")
    return found


def main() -> int:
    os.chdir(ROOT)
    load_env(ROOT / ".env")

    py = venv_python()
    if not py.is_file():
        run([sys.executable, "-m", "venv", str(ROOT / ".venv")])
        py = venv_python()
        if not py.is_file():
            raise SystemExit(f"venv python missing at {py}")

    run([str(py), "-m", "pip", "install", "-q", "-e", ".[dev]"])

    db = ROOT / "data" / "meridian.sqlite"
    if not db.is_file():
        run([str(py), "-m", "meridian.ingest"])

    npm = npm_cmd()
    if not (ROOT / "frontend" / "node_modules").is_dir():
        run([npm, "--prefix", str(ROOT / "frontend"), "install"])

    api = subprocess.Popen(
        [
            str(py),
            "-m",
            "uvicorn",
            "meridian.app:app",
            "--app-dir",
            "src",
            "--host",
            API_HOST,
            "--port",
            API_PORT,
        ],
        cwd=ROOT,
    )
    try:
        return subprocess.run(
            [npm, "--prefix", str(ROOT / "frontend"), "run", "dev"],
            cwd=ROOT,
        ).returncode
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api.kill()


if __name__ == "__main__":
    raise SystemExit(main())
