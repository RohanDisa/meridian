from __future__ import annotations

from meridian.db import connect, init_schema
from meridian.ingest_bom import ingest_bom
from meridian.ingest_drawings import ingest_drawings
from meridian.mapping import apply_mapping_overrides, link_drawings_to_bom
from meridian.paths import (
    BOM_CSV,
    DATA_DIR,
    DB_PATH,
    DRAWINGS_DIR,
    MANIFEST_JSON,
)
from meridian.reconstruct import build_all, register_reconstructions

OVERRIDE_PATH = DATA_DIR / "mapping_overrides.json"
RECON_DIR = DATA_DIR / "reconstructions"


def ingest_all(db_path=DB_PATH) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    init_schema(conn)
    ingest_bom(conn, BOM_CSV)
    ingest_drawings(conn, MANIFEST_JSON, DRAWINGS_DIR)
    link_drawings_to_bom(conn)
    if OVERRIDE_PATH.is_file():
        apply_mapping_overrides(conn, OVERRIDE_PATH)
    build_all(RECON_DIR)
    register_reconstructions(conn, RECON_DIR)
    conn.close()


def main() -> None:
    ingest_all()
    print(f"Wrote {DB_PATH}")


if __name__ == "__main__":
    main()
