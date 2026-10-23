"""Build a Sionna RT / OpenAirTwin scene from building footprints in GeoJSON.

The importer reads an EPSG:3857 (Web Mercator) building GeoJSON, extrudes every
footprint into a closed prism, tiles the result into square tiles measured in
true metres, and writes the same per-tile scene layout used by the rest of the
application::

    scene/
    |-- common/scene_common.xml
    |-- tiles/<tile_id>.xml
    |-- meshes/<tile_id>/BUILDING/<tile_id>_buildings.ply
    |-- meshes/<tile_id>/TERRAIN_TB/<tile_id>_ground.ply
    |-- coverage.json
    `-- scene_metadata.json

The tile origin is chosen so that the application's existing default device
positions (Link Tx/Rx and the Radar target) fall inside the default tile.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
from pathlib import Path
import shutil
import time
from typing import Callable, Iterable
from uuid import uuid4
import xml.etree.ElementTree as ET

import numpy as np

from backend.scene.tile_scene_xml import (
    COMMON_SCENE_RELATIVE_PATH,
    TILE_SCENE_RELATIVE_DIR,
    default_common_scene_root,
    ensure_scene_layout,
)


WEB_MERCATOR_RADIUS_M = 6378137.0
DEFAULT_CENTER_LON = 51.389
DEFAULT_CENTER_LAT = 35.6892
DEFAULT_TILE_SIZE_M = 1500.0
DEFAULT_GROUND_CELL_SIZE_M = 20.0
DEFAULT_SCENE_ID = "tehran_buildings"
DEFAULT_SCENE_LABEL = "Tehran Building Digital Twin"
DEFAULT_MIN_HEIGHT_M = 0.2
DEFAULT_MAX_HEIGHT_M = 500.0
BUILDING_CATEGORY = "BUILDING"
GROUND_CATEGORY = "TERRAIN_TB"
BUILDING_MATERIAL = "itu_concrete"
GROUND_MATERIAL = "itu_medium_dry_ground"
COVERAGE_SCHEMA_VERSION = 1
METADATA_SCHEMA_VERSION = 1

ProgressCallback = Callable[[float, str], None]


@dataclass(frozen=True)
class GeoJsonBuildOptions:
    input_path: Path
    scene_root: Path
    tile_size_m: float = DEFAULT_TILE_SIZE_M
    center_lon: float = DEFAULT_CENTER_LON
    center_lat: float = DEFAULT_CENTER_LAT
    scene_id: str = DEFAULT_SCENE_ID
    scene_label: str = DEFAULT_SCENE_LABEL
    replace: bool = False
    include_ground: bool = True
    ground_cell_size_m: float = DEFAULT_GROUND_CELL_SIZE_M
    max_features: int | None = None

    def validated(self) -> "GeoJsonBuildOptions":
        tile_size = float(self.tile_size_m)
        if not math.isfinite(tile_size) or tile_size < 100.0 or tile_size > 50000.0:
            raise ValueError("tile_size_m must be between 100 and 50000 metres")
        if not math.isfinite(float(self.center_lon)) or not -180.0 <= float(self.center_lon) <= 180.0:
            raise ValueError("center_lon must be a valid longitude")
        if not math.isfinite(float(self.center_lat)) or not -85.0 <= float(self.center_lat) <= 85.0:
            raise ValueError("center_lat must be a valid latitude")
        if self.max_features is not None and int(self.max_features) <= 0:
            raise ValueError("max_features must be positive when provided")
        ground_cell_size = float(self.ground_cell_size_m)
        if self.include_ground and not math.isfinite(ground_cell_size):
            raise ValueError("ground_cell_size_m must be finite")
        if self.include_ground and ground_cell_size > 0.0 and ground_cell_size < 2.0:
            raise ValueError("ground_cell_size_m must be at least 2 metres or 0 to disable tessellation")
        return self


@dataclass(frozen=True)
class GeoJsonBuildResult:
    scene_root: Path
    scene_id: str
    tile_count: int
    building_count: int
    default_tile_ids: tuple[str, ...]
    vertex_count: int
    triangle_count: int
    skipped_count: int


@dataclass
class _TileAccumulator:
    col: int
    row: int
    vertices: list[np.ndarray] = field(default_factory=list)
    faces: list[np.ndarray] = field(default_factory=list)
    vertex_count: int = 0
    building_count: int = 0
    skipped_count: int = 0


def _report(progress_cb: ProgressCallback | None, progress: float, message: str) -> None:
    if progress_cb:
        progress_cb(max(0.0, min(1.0, float(progress))), message)


def wgs84_to_web_mercator(lon: float, lat: float) -> tuple[float, float]:
    lon = float(lon)
    lat = float(lat)
    if not -85.0511287798066 < lat < 85.0511287798066:
        raise ValueError("latitude is outside the Web Mercator range")
    x = WEB_MERCATOR_RADIUS_M * math.radians(lon)
    y = WEB_MERCATOR_RADIUS_M * math.log(math.tan(math.pi / 4.0 + math.radians(lat) / 2.0))
    return x, y


def web_mercator_to_wgs84(x: float, y: float) -> tuple[float, float]:
    lon = math.degrees(float(x) / WEB_MERCATOR_RADIUS_M)
    lat = math.degrees(2.0 * math.atan(math.exp(float(y) / WEB_MERCATOR_RADIUS_M)) - math.pi / 2.0)
    return lon, lat


def local_metres_per_mercator_metre(lat: float) -> float:
    """Return the true-metre scale for a Web Mercator position.

    Web Mercator distances are inflated by ``1 / cos(latitude)``. Multiplying a
    Mercator delta by ``cos(latitude)`` therefore yields local true metres for
    the site scale used by this importer.
    """

    scale = math.cos(math.radians(float(lat)))
    if scale <= 1e-9:
        raise ValueError("latitude is too close to a pole")
    return scale


def _clean_ring(raw_points: Iterable[Iterable[object]]) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for raw_point in raw_points:
        try:
            x = float(raw_point[0])
            y = float(raw_point[1])
        except (TypeError, ValueError, IndexError):
            continue
        if not math.isfinite(x) or not math.isfinite(y):
            continue
        if points and abs(points[-1][0] - x) <= 1e-9 and abs(points[-1][1] - y) <= 1e-9:
            continue
        points.append((x, y))
    if len(points) >= 2 and abs(points[0][0] - points[-1][0]) <= 1e-9 and abs(points[0][1] - points[-1][1]) <= 1e-9:
        points.pop()
    return _remove_collinear(points)


def _remove_collinear(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    if len(points) < 3:
        return points
    cleaned: list[tuple[float, float]] = []
    count = len(points)
    for index in range(count):
        previous = points[(index - 1) % count]
        current = points[index]
        following = points[(index + 1) % count]
        cross = (
            (current[0] - previous[0]) * (following[1] - current[1])
            - (current[1] - previous[1]) * (following[0] - current[0])
        )
        if abs(cross) <= 1e-9:
            continue
        cleaned.append(current)
    return cleaned if len(cleaned) >= 3 else []


def signed_area(points: list[tuple[float, float]]) -> float:
    total = 0.0
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        total += x1 * y2 - x2 * y1
    return total * 0.5


def _is_convex(points: list[tuple[float, float]]) -> bool:
    count = len(points)
    if count < 3:
        return False
    sign = 0
    for index in range(count):
        a = points[index]
        b = points[(index + 1) % count]
        c = points[(index + 2) % count]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) <= 1e-12:
            continue
        current_sign = 1 if cross > 0 else -1
        if sign == 0:
            sign = current_sign
        elif sign != current_sign:
            return False
    return sign != 0


def _point_in_triangle(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> bool:
    def edge_sign(p1: tuple[float, float], p2: tuple[float, float], p3: tuple[float, float]) -> float:
        return (p2[0] - p1[0]) * (p3[1] - p1[1]) - (p2[1] - p1[1]) * (p3[0] - p1[0])

    epsilon = 1e-12
    d1 = edge_sign(a, b, point)
    d2 = edge_sign(b, c, point)
    d3 = edge_sign(c, a, point)
    has_negative = d1 < -epsilon or d2 < -epsilon or d3 < -epsilon
    has_positive = d1 > epsilon or d2 > epsilon or d3 > epsilon
    return not (has_negative and has_positive)


def triangulate_polygon(points: list[tuple[float, float]]) -> list[tuple[int, int, int]]:
    """Triangulate a simple polygon and return triangles in polygon-index space.

    Convex polygons use a cheap fan. Concave polygons use ear clipping, which is
    robust for the small OSM building footprints in the source data.
    """

    count = len(points)
    if count < 3:
        return []
    if count == 3:
        return [(0, 1, 2)]
    if _is_convex(points):
        return [(0, index, index + 1) for index in range(1, count - 1)]

    indices = list(range(count))
    triangles: list[tuple[int, int, int]] = []
    guard = 0
    while len(indices) > 3 and guard < count * count:
        guard += 1
        found_ear = False
        current_count = len(indices)
        for position in range(current_count):
            i0 = indices[(position - 1) % current_count]
            i1 = indices[position]
            i2 = indices[(position + 1) % current_count]
            a, b, c = points[i0], points[i1], points[i2]
            cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
            if cross <= 1e-12:
                continue
            contains_other = False
            for candidate in indices:
                if candidate in (i0, i1, i2):
                    continue
                if _point_in_triangle(points[candidate], a, b, c):
                    contains_other = True
                    break
            if contains_other:
                continue
            triangles.append((i0, i1, i2))
            del indices[position]
            found_ear = True
            break
        if not found_ear:
            break

    if len(indices) == 3:
        triangles.append((indices[0], indices[1], indices[2]))
    elif len(indices) > 3:
        # Degenerate / self-intersecting footprint. Keep a fan so the roof still
        # closes visually instead of dropping the building entirely.
        triangles.extend((indices[0], indices[index], indices[index + 1]) for index in range(1, len(indices) - 1))
    return triangles


def _extrude_polygon(
    ring: list[tuple[float, float]],
    height: float,
) -> tuple[np.ndarray, np.ndarray]:
    count = len(ring)
    bottom = np.asarray([(x, y, 0.0) for x, y in ring], dtype=np.float32)
    top = np.asarray([(x, y, height) for x, y in ring], dtype=np.float32)
    vertices = np.concatenate((bottom, top), axis=0)

    faces: list[tuple[int, int, int]] = []
    for index in range(count):
        following = (index + 1) % count
        faces.append((index, following, count + following))
        faces.append((index, count + following, count + index))
    for a, b, c in triangulate_polygon(ring):
        faces.append((count + a, count + b, count + c))
    faces_array = np.asarray(faces, dtype=np.uint32)
    return vertices, faces_array


def _polygon_rings(geometry: dict | None) -> list[list[list[object]]]:
    if not isinstance(geometry, dict):
        return []
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon" and isinstance(coordinates, list):
        return [coordinates]
    if geometry_type == "MultiPolygon" and isinstance(coordinates, list):
        return [polygon for polygon in coordinates if isinstance(polygon, list)]
    return []


def _feature_height(properties: dict) -> float:
    value = properties.get("height")
    try:
        height = float(value)
    except (TypeError, ValueError):
        height = 3.0
    if not math.isfinite(height):
        height = 3.0
    return min(DEFAULT_MAX_HEIGHT_M, max(DEFAULT_MIN_HEIGHT_M, height))


def _tile_id(col: int, row: int) -> tuple[str, str]:
    tile_id = f"tehran_{int(col):03d}_{int(row):03d}"
    return tile_id, tile_id.replace("_", "-")


def _tile_corners(
    col: int,
    row: int,
    tile_size_m: float,
    origin_mercator: tuple[float, float],
    scale: float,
) -> list[list[float]]:
    west = (col - 0.5) * tile_size_m
    east = (col + 0.5) * tile_size_m
    south = (row - 0.5) * tile_size_m
    north = (row + 0.5) * tile_size_m

    def to_lon_lat(local_x: float, local_y: float) -> list[float]:
        merc_x = origin_mercator[0] + local_x / scale
        merc_y = origin_mercator[1] + local_y / scale
        lon, lat = web_mercator_to_wgs84(merc_x, merc_y)
        return [round(lon, 9), round(lat, 9)]

    return [
        to_lon_lat(west, north),
        to_lon_lat(east, north),
        to_lon_lat(east, south),
        to_lon_lat(west, south),
    ]


def _write_ply(path: Path, vertices: np.ndarray, faces: np.ndarray) -> None:
    vertex_count = int(len(vertices))
    face_count = int(len(faces))
    if vertex_count <= 0 or face_count <= 0:
        raise ValueError(f"Cannot write an empty PLY mesh: {path}")
    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"element vertex {vertex_count}\n"
        "property float x\n"
        "property float y\n"
        "property float z\n"
        f"element face {face_count}\n"
        "property list uchar int vertex_indices\n"
        "end_header\n"
    ).encode("ascii")
    face_block = np.empty(face_count, dtype=np.dtype([("count", "u1"), ("indices", "<i4", (3,))]))
    face_block["count"] = 3
    face_block["indices"] = np.asarray(faces, dtype=np.int64)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        with open(temp_path, "wb") as handle:
            handle.write(header)
            handle.write(np.asarray(vertices, dtype="<f4").reshape(-1).tobytes(order="C"))
            handle.write(face_block.tobytes(order="C"))
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _ground_vertices(
    col: int,
    row: int,
    tile_size_m: float,
    cell_size_m: float = DEFAULT_GROUND_CELL_SIZE_M,
    z: float = -0.02,
) -> tuple[np.ndarray, np.ndarray]:
    """Build a tessellated flat ground mesh for a tile.

    A fine grid is required by the radio-map terrain-patch builder, which selects
    source triangles inside the requested measurement box and subdivides them.
    A single tile-sized quad would otherwise make the ``Auto`` radio-map cell
    size as large as the whole tile.
    """

    west = (col - 0.5) * tile_size_m
    east = (col + 0.5) * tile_size_m
    south = (row - 0.5) * tile_size_m
    north = (row + 0.5) * tile_size_m
    cell_size = float(cell_size_m)
    if not math.isfinite(cell_size) or cell_size <= 0.0:
        vertices = np.asarray(
            [
                (west, south, z),
                (east, south, z),
                (east, north, z),
                (west, north, z),
            ],
            dtype=np.float32,
        )
        faces = np.asarray([(0, 1, 2), (0, 2, 3)], dtype=np.uint32)
        return vertices, faces

    nx = max(1, int(math.ceil(float(tile_size_m) / cell_size)))
    ny = nx
    xs = np.linspace(west, east, nx + 1, dtype=np.float32)
    ys = np.linspace(south, north, ny + 1, dtype=np.float32)
    grid_x, grid_y = np.meshgrid(xs, ys)
    vertices = np.column_stack(
        (grid_x.reshape(-1), grid_y.reshape(-1), np.full((nx + 1) * (ny + 1), z, dtype=np.float32))
    ).astype(np.float32)
    faces = np.empty((nx * ny * 2, 3), dtype=np.uint32)
    for row_index in range(ny):
        for col_index in range(nx):
            lower_left = row_index * (nx + 1) + col_index
            lower_right = lower_left + 1
            upper_left = lower_left + (nx + 1)
            upper_right = upper_left + 1
            face_index = 2 * (row_index * nx + col_index)
            faces[face_index] = (lower_left, lower_right, upper_right)
            faces[face_index + 1] = (lower_left, upper_right, upper_left)
    return vertices, faces


def _tile_xml_tree(common_root: ET.Element, tile_id: str, mesh_records: list[dict]) -> ET.ElementTree:
    root = ET.Element(common_root.tag, dict(common_root.attrib))
    for shape in mesh_records:
        shape_node = ET.SubElement(root, "shape", {"type": "ply", "id": shape["shape_id"]})
        ET.SubElement(shape_node, "string", {"name": "filename", "value": shape["mesh_relpath"]})
        ET.SubElement(shape_node, "boolean", {"name": "face_normals", "value": "true"})
        ET.SubElement(shape_node, "ref", {"name": "bsdf", "id": shape["material_id"]})
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    return tree


def _write_xml(tree: ET.ElementTree, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        tree.write(temp_path, encoding="utf-8", xml_declaration=True)
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _clear_generated_scene(scene_root: Path) -> None:
    for relative in ("meshes", TILE_SCENE_RELATIVE_DIR):
        target = scene_root / relative
        if target.exists():
            shutil.rmtree(target)
    for filename in ("coverage.json", "scene_metadata.json"):
        (scene_root / filename).unlink(missing_ok=True)


def _scene_has_generated_tiles(scene_root: Path) -> bool:
    tile_dir = scene_root / TILE_SCENE_RELATIVE_DIR
    return tile_dir.is_dir() and any(tile_dir.glob("*.xml"))


def _read_geojson(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict) or document.get("type") != "FeatureCollection":
        raise ValueError("Input must be a GeoJSON FeatureCollection")
    features = document.get("features")
    if not isinstance(features, list) or not features:
        raise ValueError("GeoJSON FeatureCollection contains no features")
    return document


def import_geojson_buildings(
    options: GeoJsonBuildOptions,
    *,
    progress_cb: ProgressCallback | None = None,
) -> GeoJsonBuildResult:
    options = options.validated()
    input_path = Path(options.input_path).resolve()
    scene_root = Path(options.scene_root).resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"GeoJSON input does not exist: {input_path}")

    if _scene_has_generated_tiles(scene_root) and not options.replace:
        raise FileExistsError(
            f"{scene_root} already contains tile XML files; pass replace=True to rebuild it"
        )
    if options.replace:
        _report(progress_cb, 0.01, "Clearing previous generated scene")
        _clear_generated_scene(scene_root)
    ensure_scene_layout(scene_root)
    common_root = ET.parse(scene_root / COMMON_SCENE_RELATIVE_PATH).getroot()

    origin_x, origin_y = wgs84_to_web_mercator(options.center_lon, options.center_lat)
    scale = local_metres_per_mercator_metre(options.center_lat)
    tile_size = float(options.tile_size_m)

    _report(progress_cb, 0.03, "Reading building GeoJSON")
    document = _read_geojson(input_path)
    features = document.get("features") or []
    total_features = len(features)
    if options.max_features is not None:
        total_features = min(total_features, int(options.max_features))

    tiles: dict[tuple[int, int], _TileAccumulator] = {}
    building_count = 0
    skipped_count = 0

    for feature_index, feature in enumerate(features[:total_features], start=1):
        if feature_index == 1 or feature_index % 5000 == 0:
            _report(
                progress_cb,
                0.03 + 0.62 * (feature_index / max(1, total_features)),
                f"Extruding building {feature_index:,} of {total_features:,}",
            )
        if not isinstance(feature, dict):
            skipped_count += 1
            continue
        properties = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
        height = _feature_height(properties)
        for polygon in _polygon_rings(feature.get("geometry")):
            if not polygon:
                skipped_count += 1
                continue
            outer = polygon[0] if isinstance(polygon, list) else None
            if not isinstance(outer, list):
                skipped_count += 1
                continue
            mercator_ring: list[tuple[float, float]] = []
            for point in outer:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                try:
                    mercator_ring.append((float(point[0]), float(point[1])))
                except (TypeError, ValueError):
                    continue
            cleaned = _clean_ring(mercator_ring)
            if len(cleaned) < 3:
                skipped_count += 1
                continue
            local_ring = [
                ((x - origin_x) * scale, (y - origin_y) * scale)
                for x, y in cleaned
            ]
            local_ring = _clean_ring(local_ring)
            if len(local_ring) < 3:
                skipped_count += 1
                continue
            area = signed_area(local_ring)
            if abs(area) < 0.25:
                skipped_count += 1
                continue
            if area < 0:
                local_ring.reverse()

            centroid_x = sum(point[0] for point in local_ring) / len(local_ring)
            centroid_y = sum(point[1] for point in local_ring) / len(local_ring)
            col = int(math.floor(centroid_x / tile_size + 0.5))
            row = int(math.floor(centroid_y / tile_size + 0.5))
            tile = tiles.get((col, row))
            if tile is None:
                tile = _TileAccumulator(col=col, row=row)
                tiles[(col, row)] = tile

            vertices, faces = _extrude_polygon(local_ring, height)
            tile.faces.append(faces + np.uint32(tile.vertex_count))
            tile.vertices.append(vertices)
            tile.vertex_count += int(len(vertices))
            tile.building_count += 1
            building_count += 1

    document = None
    if not tiles:
        raise ValueError("The GeoJSON did not contain any usable building polygons")

    # The application's default Link/Radar coordinates live around local
    # (82, 42). Always keep that tile so the default selection has ground.
    if (0, 0) not in tiles:
        tiles[(0, 0)] = _TileAccumulator(col=0, row=0)

    min_col = min(col for col, _row in tiles)
    min_row = min(row for _col, row in tiles)
    indexed_tiles = sorted(tiles.values(), key=lambda item: (item.row, item.col))
    vertex_count = 0
    triangle_count = 0

    _report(progress_cb, 0.66, f"Writing {len(indexed_tiles):,} scene tiles")
    coverage_tiles: list[dict] = []
    for index, tile in enumerate(indexed_tiles, start=1):
        if index == 1 or index % 50 == 0:
            _report(
                progress_cb,
                0.66 + 0.30 * (index / max(1, len(indexed_tiles))),
                f"Writing tile {index:,} of {len(indexed_tiles):,}",
            )
        col_index = tile.col - min_col
        row_index = tile.row - min_row
        tile_id, display_id = _tile_id(col_index, row_index)
        mesh_records: list[dict] = []
        building_mesh_path = Path("meshes") / tile_id / BUILDING_CATEGORY / f"{tile_id}_buildings.ply"
        building_vertex_count = 0
        building_face_count = 0
        if tile.vertices:
            vertices = np.concatenate(tile.vertices, axis=0)
            faces = np.concatenate(tile.faces, axis=0)
            _write_ply(scene_root / building_mesh_path, vertices, faces)
            building_vertex_count = int(len(vertices))
            building_face_count = int(len(faces))
            mesh_records.append(
                {
                    "shape_id": f"obj_{tile_id}_buildings",
                    "material_id": BUILDING_MATERIAL,
                    "mesh_relpath": building_mesh_path.as_posix(),
                }
            )
        ground_mesh_path = Path("meshes") / tile_id / GROUND_CATEGORY / f"{tile_id}_ground.ply"
        if options.include_ground:
            ground_vertices, ground_faces = _ground_vertices(
                tile.col, tile.row, tile_size, options.ground_cell_size_m
            )
            _write_ply(scene_root / ground_mesh_path, ground_vertices, ground_faces)
            mesh_records.append(
                {
                    "shape_id": f"obj_{tile_id}_ground",
                    "material_id": GROUND_MATERIAL,
                    "mesh_relpath": ground_mesh_path.as_posix(),
                }
            )
        _write_xml(_tile_xml_tree(common_root, tile_id, mesh_records), scene_root / TILE_SCENE_RELATIVE_DIR / f"{tile_id}.xml")
        coverage_tiles.append(
            {
                "id": tile_id,
                "display_id": display_id,
                "wgs84_corners": _tile_corners(tile.col, tile.row, tile_size, (origin_x, origin_y), scale),
                "building_count": tile.building_count,
                "mesh_count": len(mesh_records),
            }
        )
        vertex_count += building_vertex_count
        triangle_count += building_face_count

    default_tile_id, _ = _tile_id(-min_col, -min_row)
    coverage = {
        "schema_version": COVERAGE_SCHEMA_VERSION,
        "scene_id": options.scene_id,
        "label": options.scene_label,
        "source_crs": "EPSG:3857",
        "tile_size_m": tile_size,
        "tile_count": len(coverage_tiles),
        "tiles": coverage_tiles,
    }
    _write_json(scene_root / "coverage.json", coverage)

    all_corners = [corner for tile in coverage_tiles for corner in tile["wgs84_corners"]]
    west = min(corner[0] for corner in all_corners)
    east = max(corner[0] for corner in all_corners)
    south = min(corner[1] for corner in all_corners)
    north = max(corner[1] for corner in all_corners)

    metadata = {
        "schema_version": METADATA_SCHEMA_VERSION,
        "scene_id": options.scene_id,
        "label": options.scene_label,
        "source": str(input_path),
        "source_format": "geojson",
        "source_crs": "EPSG:3857",
        "tile_size_m": tile_size,
        "tile_count": len(coverage_tiles),
        "building_count": building_count,
        "skipped_feature_count": skipped_count,
        "origin_world_mercator": [origin_x, origin_y],
        "origin_wgs84": [float(options.center_lon), float(options.center_lat)],
        "local_scale": scale,
        "default_tile_ids": [default_tile_id],
        "default_tx_position": [72.0, 37.0, 40.0],
        "default_rx_position": [90.0, 52.0, 1.5],
        "bounds_wgs84": {
            "west": west,
            "east": east,
            "south": south,
            "north": north,
        },
        "center_wgs84": [float(options.center_lon), float(options.center_lat)],
        "nominatim_countrycodes": "ir",
        "search_place_label": "Tehran",
        "fallback_image": None,
        "basemap_underlay": True,
        "coverage_url": "/api/scene/coverage",
    }
    _write_json(scene_root / "scene_metadata.json", metadata)

    _report(progress_cb, 1.0, "Scene generation complete")
    return GeoJsonBuildResult(
        scene_root=scene_root,
        scene_id=options.scene_id,
        tile_count=len(coverage_tiles),
        building_count=building_count,
        default_tile_ids=(default_tile_id,),
        vertex_count=vertex_count,
        triangle_count=triangle_count,
        skipped_count=skipped_count,
    )
