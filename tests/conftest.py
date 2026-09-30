from pathlib import Path

import pytest

from meridian.paths import BOM_CSV, MANIFEST_JSON


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.sqlite"


@pytest.fixture
def bom_csv() -> Path:
    assert BOM_CSV.is_file(), f"BOM missing at {BOM_CSV}"
    return BOM_CSV


@pytest.fixture
def manifest_path() -> Path:
    assert MANIFEST_JSON.is_file(), f"manifest missing at {MANIFEST_JSON}"
    return MANIFEST_JSON
