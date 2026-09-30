from __future__ import annotations

import json
import math
import struct
from pathlib import Path

DISCLAIMER = (
    "Reconstruction from 2D evidence ({drawing_id}). Not native CAD. "
    "Hole positions and omitted features may be approximate."
)

DIMENSIONS = {
    "D-015": ["270 mm", "14 mm", "1 mm", "3.4 mm"],
    "D-026": ["250 mm", "15 mm", "5.3 mm", "R105 mm"],
    "D-013": ["270 mm", "25 mm", "6 mm", "4.2 mm", "2.5 mm"],
    "D-016": ["200 mm", "283.5 mm", "10 mm", "145 mm", "4.5 mm"],
}

PARAMS: dict[str, dict] = {
    "D-015": {
        "drawing_id": "D-015",
        "label": "Silikone Wiper",
        "shape": "rect",
        "width_mm": 270.0,
        "height_mm": 14.0,
        "thickness_mm": 1.0,
        "density_g_cm3": 1.15,
        "title_weight_g": 4.6,
        "holes": [
            {"x": 12.0 + i * (246.0 / 7.0), "y": 7.0, "d": 3.4} for i in range(8)
        ],
        "assumptions": [
            "Eight Ø3.4 holes spaced evenly along the 270 mm centerline.",
            "End inset of 12 mm is estimated from the view, not a labeled spacing.",
        ],
        "disclaimer": DISCLAIMER.format(drawing_id="D-015"),
    },
    "D-026": {
        "drawing_id": "D-026",
        "label": "Build Plate",
        "shape": "disk",
        "diameter_mm": 250.0,
        "thickness_mm": 15.0,
        "density_g_cm3": 8.00,
        "title_weight_g": 7822.5,
        "holes": [
            {
                "x": 125.0 + 105.0 * math.cos(math.radians(angle)),
                "y": 125.0 + 105.0 * math.sin(math.radians(angle)),
                "d": 5.3,
            }
            for angle in (90.0, 210.0, 330.0)
        ],
        "assumptions": [
            "Thickness taken from the 15.0 side-view dimension, not the 25.0 adjacent number.",
            "Three Ø5.3 holes placed on the labeled R105 pitch circle at 120°.",
            "Counterbore Ø10.0 / 15.4 is omitted.",
            "Title-block weight 7822.5 g is higher than a Ø250 x 15 AISI 316 disk; the mismatch is kept visible.",
        ],
        "disclaimer": DISCLAIMER.format(drawing_id="D-026"),
    },
    "D-013": {
        "drawing_id": "D-013",
        "label": "Recoater Plate",
        "shape": "rect",
        "width_mm": 270.0,
        "height_mm": 25.0,
        "thickness_mm": 6.0,
        "corner_radius_mm": 4.5,
        "density_g_cm3": 2.70,
        "title_weight_g": 87.5,
        "holes": [
            {"x": 20.0, "y": 12.5, "d": 4.2},
            {"x": 250.0, "y": 12.5, "d": 4.2},
            {"x": 55.0, "y": 6.0, "d": 2.5},
            {"x": 55.0, "y": 19.0, "d": 2.5},
            {"x": 215.0, "y": 6.0, "d": 2.5},
            {"x": 215.0, "y": 19.0, "d": 2.5},
        ],
        "slots": [
            {"x": 90.0, "y": 12.5, "w": 10.0, "h": 5.5},
            {"x": 180.0, "y": 12.5, "w": 10.0, "h": 5.5},
        ],
        "assumptions": [
            "Thickness taken from the 6.0 side-view dimension.",
            "Slot and small-hole coordinates are estimated from the front view.",
            "Not every M3 hole is modeled.",
        ],
        "disclaimer": DISCLAIMER.format(drawing_id="D-013"),
    },
    "D-016": {
        "drawing_id": "D-016",
        "label": "Galvo Bundplade",
        "shape": "rect",
        "width_mm": 200.0,
        "height_mm": 283.5,
        "thickness_mm": 10.0,
        "corner_radius_mm": 15.0,
        "density_g_cm3": 2.81,
        "title_weight_g": 1118.1,
        "holes": [
            {"x": 100.0, "y": 95.0, "d": 145.0},
            {"x": 25.0, "y": 250.0, "d": 6.0},
            {"x": 175.0, "y": 250.0, "d": 6.0},
            {"x": 25.0, "y": 200.0, "d": 4.5},
            {"x": 175.0, "y": 200.0, "d": 4.5},
        ],
        "assumptions": [
            "Thickness taken from the 10.0 side view.",
            "Ø145 opening placed on the vertical centerline in the lower half.",
            "Only a subset of the small thru holes is modeled.",
        ],
        "disclaimer": DISCLAIMER.format(drawing_id="D-016"),
    },
}


def build_all(output_dir: Path) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for drawing_id, spec in PARAMS.items():
        json_path = output_dir / f"{drawing_id}.json"
        mesh_path = output_dir / f"{drawing_id}.glb"
        payload = dict(spec)
        payload["dimensions_used"] = [
            {"value": value, "drawing_id": drawing_id, "page": 1}
            for value in DIMENSIONS.get(drawing_id, [])
        ]
        json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        vertices, indices = mesh_from_spec(spec)
        write_glb(mesh_path, vertices, indices)
        written[drawing_id] = mesh_path
    return written


def register_reconstructions(conn, output_dir: Path) -> None:
    conn.execute("DELETE FROM reconstructions")
    for drawing_id, spec in PARAMS.items():
        conn.execute(
            """
            INSERT INTO reconstructions (
              drawing_id, label, param_path, mesh_path, disclaimer, assumptions
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                drawing_id,
                spec["label"],
                str(output_dir / f"{drawing_id}.json"),
                str(output_dir / f"{drawing_id}.glb"),
                spec["disclaimer"],
                json.dumps(spec["assumptions"]),
            ),
        )
    conn.commit()


def analytical_mass_g(spec: dict) -> float:
    thickness = spec["thickness_mm"]
    if spec["shape"] == "disk":
        radius = spec["diameter_mm"] / 2.0
        volume = math.pi * radius * radius * thickness
    else:
        volume = spec["width_mm"] * spec["height_mm"] * thickness
    for hole in spec.get("holes", []):
        r = hole["d"] / 2.0
        volume -= math.pi * r * r * thickness
    for slot in spec.get("slots", []):
        volume -= slot["w"] * slot["h"] * thickness
    return volume / 1000.0 * spec["density_g_cm3"]


def mesh_from_spec(spec: dict) -> tuple[list[tuple[float, float, float]], list[int]]:
    thickness = spec["thickness_mm"]
    if spec["shape"] == "disk":
        width = height = spec["diameter_mm"]

        def inside(x: float, y: float) -> bool:
            cx = cy = spec["diameter_mm"] / 2.0
            if (x - cx) ** 2 + (y - cy) ** 2 > (spec["diameter_mm"] / 2.0) ** 2:
                return False
            return not _in_void(x, y, spec)

    else:
        width = spec["width_mm"]
        height = spec["height_mm"]
        radius = spec.get("corner_radius_mm", 0.0)

        def inside(x: float, y: float) -> bool:
            if x < 0 or y < 0 or x > width or y > height:
                return False
            if radius > 0:
                corners = (
                    (radius, radius),
                    (width - radius, radius),
                    (radius, height - radius),
                    (width - radius, height - radius),
                )
                if x < radius and y < radius and _dist(x, y, *corners[0]) > radius:
                    return False
                if x > width - radius and y < radius and _dist(x, y, *corners[1]) > radius:
                    return False
                if x < radius and y > height - radius and _dist(x, y, *corners[2]) > radius:
                    return False
                if x > width - radius and y > height - radius and _dist(x, y, *corners[3]) > radius:
                    return False
            return not _in_void(x, y, spec)

    step = max(width, height) / 48.0
    xs = _frange(0, width, step)
    ys = _frange(0, height, step)
    vertices: list[tuple[float, float, float]] = []
    indices: list[int] = []

    def add_prism(x0, y0, x1, y1):
        z0, z1 = 0.0, thickness
        base = len(vertices)
        corners = [
            (x0, y0, z0),
            (x1, y0, z0),
            (x1, y1, z0),
            (x0, y1, z0),
            (x0, y0, z1),
            (x1, y0, z1),
            (x1, y1, z1),
            (x0, y1, z1),
        ]
        vertices.extend(corners)
        faces = (
            (0, 1, 2, 3),
            (4, 7, 6, 5),
            (0, 4, 5, 1),
            (1, 5, 6, 2),
            (2, 6, 7, 3),
            (3, 7, 4, 0),
        )
        for a, b, c, d in faces:
            indices.extend([base + a, base + b, base + c, base + a, base + c, base + d])

    for x0, x1 in zip(xs, xs[1:]):
        for y0, y1 in zip(ys, ys[1:]):
            mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            if inside(mx, my):
                add_prism(x0, y0, x1, y1)
    return vertices, indices


def write_glb(path: Path, vertices: list[tuple[float, float, float]], indices: list[int]) -> None:
    if not vertices or not indices:
        raise ValueError("empty mesh")
    pos = b"".join(struct.pack("<fff", *vertex) for vertex in vertices)
    idx_fmt = "<I" if len(vertices) > 65535 else "<H"
    faces = b"".join(struct.pack(idx_fmt, i) for i in indices)
    if len(faces) % 4:
        faces += b"\x00" * (4 - len(faces) % 4)
    bin_chunk = pos + faces
    mins = [min(v[i] for v in vertices) for i in range(3)]
    maxs = [max(v[i] for v in vertices) for i in range(3)]
    index_type = 5125 if idx_fmt == "<I" else 5123
    gltf = {
        "asset": {"version": "2.0", "generator": "meridian-reconstruct"},
        "buffers": [{"byteLength": len(bin_chunk)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos), "byteLength": len(indices) * (4 if idx_fmt == "<I" else 2), "target": 34963},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(vertices),
                "type": "VEC3",
                "min": mins,
                "max": maxs,
            },
            {
                "bufferView": 1,
                "componentType": index_type,
                "count": len(indices),
                "type": "SCALAR",
            },
        ],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "mode": 4}]}],
        "nodes": [{"mesh": 0}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_chunk)
    header = struct.pack("<4sII", b"glTF", 2, total)
    json_header = struct.pack("<II", len(json_bytes), 0x4E4F534A)
    bin_header = struct.pack("<II", len(bin_chunk), 0x004E4942)
    path.write_bytes(header + json_header + json_bytes + bin_header + bin_chunk)


def _in_void(x: float, y: float, spec: dict) -> bool:
    for hole in spec.get("holes", []):
        if _dist(x, y, hole["x"], hole["y"]) <= hole["d"] / 2.0:
            return True
    for slot in spec.get("slots", []):
        if abs(x - slot["x"]) <= slot["w"] / 2.0 and abs(y - slot["y"]) <= slot["h"] / 2.0:
            return True
    return False


def _dist(x0: float, y0: float, x1: float, y1: float) -> float:
    return math.hypot(x0 - x1, y0 - y1)


def _frange(start: float, stop: float, step: float) -> list[float]:
    values = []
    current = start
    while current < stop - step * 0.25:
        values.append(current)
        current += step
    values.append(stop)
    return values
