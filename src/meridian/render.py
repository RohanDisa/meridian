from __future__ import annotations

import fitz

from meridian.paths import DRAWINGS_DIR


def render_drawing_page(drawing_id: str, page: int = 1, scale: float = 1.7) -> bytes:
    path = DRAWINGS_DIR / f"{drawing_id}.pdf"
    if not path.is_file():
        raise FileNotFoundError(drawing_id)
    document = fitz.open(path)
    try:
        index = max(0, min(page - 1, document.page_count - 1))
        pixmap = document[index].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        return pixmap.tobytes("png")
    finally:
        document.close()
