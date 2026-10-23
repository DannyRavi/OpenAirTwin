from __future__ import annotations

import json
import math
from pathlib import Path
import tempfile
import unittest

from backend.scene.geojson_buildings import (
    GeoJsonBuildOptions,
    import_geojson_buildings,
    signed_area,
    triangulate_polygon,
    wgs84_to_web_mercator,
)
from backend.scene.scene_catalog import load_scene_coverage, load_scene_metadata
from backend.scene.xml_catalog import load_scene_manifest


def _rectangle(x: float, y: float, width: float, height: float) -> list[list[float]]:
    return [
        [x, y],
        [x + width, y],
        [x + width, y + height],
        [x, y + height],
        [x, y],
    ]


def _write_geojson(path: Path, center_lon: float, center_lat: float) -> None:
    origin_x, origin_y = wgs84_to_web_mercator(center_lon, center_lat)
    features = [
        {
            "type": "Feature",
            "properties": {"id": "a", "height": 12.5},
            "geometry": {
                "type": "Polygon",
                "coordinates": [_rectangle(origin_x + 70.0, origin_y + 30.0, 20.0, 10.0)],
            },
        },
        {
            "type": "Feature",
            "properties": {"id": "b", "height": 4.0},
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": [
                    [_rectangle(origin_x - 5000.0, origin_y - 4000.0, 30.0, 30.0)],
                    [_rectangle(origin_x - 4950.0, origin_y - 3960.0, 10.0, 10.0)],
                ],
            },
        },
    ]
    path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "name": "test-buildings",
                "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::3857"}},
                "features": features,
            }
        ),
        encoding="utf-8",
    )


class TriangulationTests(unittest.TestCase):
    def test_convex_polygon_uses_fan_and_count_is_correct(self) -> None:
        points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        self.assertGreater(signed_area(points), 0)
        self.assertEqual(len(triangulate_polygon(points)), 2)

    def test_concave_polygon_triangulates_without_dropping_area(self) -> None:
        # A U-shaped footprint exercises the ear-clipping path.
        points = [
            (0.0, 0.0),
            (10.0, 0.0),
            (10.0, 10.0),
            (7.0, 10.0),
            (7.0, 3.0),
            (3.0, 3.0),
            (3.0, 10.0),
            (0.0, 10.0),
        ]
        triangles = triangulate_polygon(points)
        self.assertEqual(len(triangles), len(points) - 2)
        triangle_area = 0.0
        for a, b, c in triangles:
            ax, ay = points[a]
            bx, by = points[b]
            cx, cy = points[c]
            triangle_area += abs((bx - ax) * (cy - ay) - (by - ay) * (cx - ax)) * 0.5
        self.assertAlmostEqual(triangle_area, 72.0, places=6)


class GeoJsonImporterTests(unittest.TestCase):
    def test_importer_writes_tiles_metadata_coverage_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_path = root / "buildings.geojson"
            scene_root = root / "scene"
            _write_geojson(input_path, 51.389, 35.6892)

            result = import_geojson_buildings(
                GeoJsonBuildOptions(
                    input_path=input_path,
                    scene_root=scene_root,
                    tile_size_m=1500.0,
                    replace=True,
                    include_ground=True,
                )
            )

            self.assertEqual(result.building_count, 3)
            self.assertEqual(result.skipped_count, 0)
            self.assertGreaterEqual(result.tile_count, 2)
            self.assertTrue(result.default_tile_ids)

            for tile_id in result.default_tile_ids:
                mesh_path = scene_root / "meshes" / tile_id / "BUILDING" / f"{tile_id}_buildings.ply"
                self.assertTrue(mesh_path.is_file())
                payload = mesh_path.read_bytes()
                self.assertTrue(payload.startswith(b"ply\nformat binary_little_endian 1.0\n"))
                self.assertIn(b"property float x", payload)

            coverage = load_scene_coverage(scene_root)
            self.assertEqual(coverage["scene_id"], "tehran_buildings")
            self.assertEqual(coverage["tile_count"], len(coverage["tiles"]))
            self.assertTrue(coverage["tiles"][0]["wgs84_corners"])

            metadata = load_scene_metadata(scene_root)
            self.assertEqual(metadata["tile_size_m"], 1500.0)
            self.assertEqual(metadata["default_tile_ids"], list(result.default_tile_ids))
            self.assertIsNone(metadata["fallback_image"])

            manifest = load_scene_manifest(scene_root)
            self.assertEqual(manifest.scene_id, "tehran_buildings")
            self.assertEqual(manifest.metadata["building_count"], 3)
            self.assertIn(result.default_tile_ids[0], manifest.tiles)
            api_payload = manifest.to_api_dict()
            self.assertEqual(api_payload["scene"]["tile_size_m"], 1500.0)
            for bundle in api_payload["bundles"]:
                self.assertEqual(bundle["asset_format"], "glb")
                self.assertGreater(bundle["mesh_count"], 0)

    def test_default_tile_stays_available_when_no_building_lands_in_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_path = root / "buildings.geojson"
            scene_root = root / "scene"
            _write_geojson(input_path, 51.389, 35.6892)
            document = json.loads(input_path.read_text(encoding="utf-8"))
            # Move every footprint far away from the local origin.
            def shift(value):
                if isinstance(value, list) and value and isinstance(value[0], (int, float)):
                    value[0] += 20000.0
                    return
                if isinstance(value, list):
                    for item in value:
                        shift(item)

            for feature in document["features"]:
                shift(feature["geometry"]["coordinates"])
            input_path.write_text(json.dumps(document), encoding="utf-8")

            result = import_geojson_buildings(
                GeoJsonBuildOptions(
                    input_path=input_path,
                    scene_root=scene_root,
                    tile_size_m=1500.0,
                    replace=True,
                )
            )
            default_tile_id = result.default_tile_ids[0]
            manifest = load_scene_manifest(scene_root)
            self.assertIn(default_tile_id, manifest.tiles)
            self.assertEqual(manifest.tiles[default_tile_id]["TERRAIN_TB"], 1)

    def test_existing_scene_requires_replace_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            input_path = root / "buildings.geojson"
            scene_root = root / "scene"
            _write_geojson(input_path, 51.389, 35.6892)
            options = GeoJsonBuildOptions(input_path=input_path, scene_root=scene_root, replace=True)
            import_geojson_buildings(options)

            with self.assertRaises(FileExistsError):
                import_geojson_buildings(
                    GeoJsonBuildOptions(input_path=input_path, scene_root=scene_root, replace=False)
                )


if __name__ == "__main__":
    unittest.main()
