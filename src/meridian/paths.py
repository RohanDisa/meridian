from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "project-meridian" / "project-meridian"
DATASET_ROOT = CORPUS_ROOT / "dataset"
BOM_CSV = DATASET_ROOT / "context" / "openlpbf-bom.csv"
MANIFEST_JSON = DATASET_ROOT / "manifest.json"
DRAWINGS_DIR = DATASET_ROOT / "drawings"
DATA_DIR = REPO_ROOT / "data"
DB_PATH = DATA_DIR / "meridian.sqlite"
