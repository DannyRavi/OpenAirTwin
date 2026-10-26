from __future__ import annotations

import base64
import json
from pathlib import Path
import tempfile
import time
import unittest

from backend.offline.region_store import OfflineRegionError, OfflineRegionManager


COMMON_XML = """<?xml version="1.0" encoding="utf-8"?>
<scene version="3.0.0">
  <integrator type="path" />
  <bsdf type="itu-radio-material" id="itu_concrete"><string name="type" value="concrete" /></bsdf>
</scene>
"""

TILE_XML = """<?xml version="1.0" encoding="utf-8"?>
<scene version="3.0.0">
  <shape type="ply" id="{tile}_building_1">
    <string name="filename" value="meshes/{tile}/BUILDING/building_1.ply" />
    <ref name="bsdf" id="itu_concrete" />
  </shape>
</scene>
"""


def write_scene(root: Path, tiles: tuple[str, ...] = ("TILE_A",)) -> None:
    (root / "common").mkdir(parents=True)
    (root / "tiles").mkdir(parents=True)
    (root / "meshes").mkdir(parents=True)
    (root / "cache" / "render_bundles").mkdir(parents=True)
    (root / "common" / "scene_common.xml").write_text(COMMON_XML, encoding="utf-8")
    coverage = {"schema_version": 1, "scene_id": "offline_test", "tile_count": len(tiles), "tiles": []}
    for index, tile in enumerate(tiles):
        (root / "tiles" / f"{tile}.xml").write_text(TILE_XML.format(tile=tile), encoding="utf-8")
        mesh_dir = root / "meshes" / tile / "BUILDING"
        mesh_dir.mkdir(parents=True)
        (mesh_dir / "building_1.ply").write_bytes(b"ply\n")
        coverage["tiles"].append(
            {
                "id": tile,
                "wgs84_corners": [
                    [51.0 + index, 35.0],
                    [51.0 + index + 0.1, 35.0],
                    [51.0 + index + 0.1, 35.1],
                    [51.0 + index, 35.1],
                ],
            }
        )
    (root / "coverage.json").write_text(json.dumps(coverage), encoding="utf-8")
    (root / "scene_metadata.json").write_text(
        json.dumps({"scene_id": "offline_test", "label": "Offline Test"}), encoding="utf-8"
    )


def fake_bundle_builder(_scene_root: Path, bundle):
    path = _scene_root / bundle.relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"glTF-offline-bundle")
    return type("BundleResult", (), {"bundle_path": path})()


def wait_for_job(manager: OfflineRegionManager, job_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = manager.get_job(job_id)
        if job is not None and job.status not in {"queued", "preparing", "verifying", "cancelling"}:
            return job
        time.sleep(0.02)
    raise AssertionError("offline job did not finish")


class OfflineRegionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.scene = self.root / "scene"
        write_scene(self.scene)
        self.manager = OfflineRegionManager(
            root=self.root / "regions",
            scene_root=self.scene,
            tile_bundle_builder=fake_bundle_builder,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_region_becomes_ready_only_after_validated_manifest(self) -> None:
        job = self.manager.create_region(
            {"name": "Test Area", "tile_ids": ["TILE_A"], "layers": ["buildings", "terrain", "map"]}
        )
        finished = wait_for_job(self.manager, job.job_id)
        self.assertEqual(finished.status, "ready")
        record = self.manager.get_region(job.region_id)
        assert record is not None
        self.assertEqual(record.status, "ready")
        self.assertTrue(record.to_api_dict()["offline_ready"])
        self.assertGreater(record.asset_count, 0)
        self.assertEqual(record.missing_count, 0)
        manifest = json.loads((record.directory / "region.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertTrue(all(len(asset["sha256"]) == 64 for asset in manifest["assets"]))
        self.assertIn("attribution", manifest)

    def test_region_persists_across_manager_restart_and_reopens(self) -> None:
        job = self.manager.create_region({"name": "Persistent", "tile_ids": ["TILE_A"]})
        finished = wait_for_job(self.manager, job.job_id)
        self.assertEqual(finished.status, "ready")
        self.manager.open_region(job.region_id)

        restarted = OfflineRegionManager(
            root=self.root / "regions",
            scene_root=self.scene,
            tile_bundle_builder=fake_bundle_builder,
        )
        startup = restarted.startup_region()
        assert startup is not None
        record, scene_root = startup
        self.assertEqual(record.region_id, job.region_id)
        self.assertTrue((scene_root / "tiles" / "TILE_A.xml").is_file())
        self.assertTrue((scene_root / "meshes" / "TILE_A" / "BUILDING" / "building_1.ply").is_file())
        self.assertEqual(record.status, "ready")

    def test_corrupted_asset_is_not_reported_as_ready(self) -> None:
        job = self.manager.create_region({"name": "Corrupt", "tile_ids": ["TILE_A"]})
        finished = wait_for_job(self.manager, job.job_id)
        self.assertEqual(finished.status, "ready")
        record = self.manager.get_region(job.region_id)
        assert record is not None
        mesh = record.directory / "scene" / "meshes" / "TILE_A" / "BUILDING" / "building_1.ply"
        mesh.write_bytes(b"tampered")
        refreshed = self.manager.get_region(job.region_id)
        assert refreshed is not None
        self.assertEqual(refreshed.status, "missing")
        self.assertFalse(refreshed.to_api_dict()["offline_ready"])
        with self.assertRaises(OfflineRegionError):
            self.manager.verify_region(job.region_id)

    def test_strict_offline_mode_blocks_remote_downloads(self) -> None:
        missing_scene = self.root / "missing"
        write_scene(missing_scene)
        (missing_scene / "tiles" / "TILE_A.xml").unlink()
        called = []

        def downloader(*_args, **_kwargs):
            called.append(True)

        manager = OfflineRegionManager(
            root=self.root / "regions-strict",
            scene_root=missing_scene,
            downloader=downloader,
            tile_bundle_builder=fake_bundle_builder,
        )
        manager.set_mode("offline")
        with self.assertRaisesRegex(OfflineRegionError, "not present in the local scene"):
            manager.create_region({"name": "Strict", "tile_ids": ["TILE_A"]})
        self.assertEqual(called, [])

    def test_failed_preparation_keeps_partial_state_without_ready_manifest(self) -> None:
        broken_scene = self.root / "broken"
        write_scene(broken_scene, ("TILE_A", "TILE_B"))
        (broken_scene / "meshes" / "TILE_B").rename(broken_scene / "meshes" / "TILE_B_hidden")
        manager = OfflineRegionManager(
            root=self.root / "regions-broken",
            scene_root=broken_scene,
            tile_bundle_builder=fake_bundle_builder,
        )
        job = manager.create_region({"name": "Broken", "tile_ids": ["TILE_A", "TILE_B"]})
        finished = wait_for_job(manager, job.job_id)
        self.assertEqual(finished.status, "failed")
        record = manager.get_region(job.region_id)
        assert record is not None
        self.assertEqual(record.status, "failed")
        self.assertFalse((record.directory / "region.json").exists())
        self.assertTrue((record.directory / "state.json").is_file())
        self.assertFalse(record.to_api_dict()["offline_ready"])

    def test_delete_removes_region_data_only(self) -> None:
        job = self.manager.create_region({"name": "Delete", "tile_ids": ["TILE_A"]})
        wait_for_job(self.manager, job.job_id)
        record = self.manager.get_region(job.region_id)
        assert record is not None
        self.assertTrue(record.directory.is_dir())
        self.manager.delete_region(job.region_id)
        self.assertFalse(record.directory.exists())
        self.assertIsNone(self.manager.get_region(job.region_id))
        self.assertTrue((self.scene / "tiles" / "TILE_A.xml").is_file())

    def test_basemap_snapshot_is_validated_and_served(self) -> None:
        job = self.manager.create_region({"name": "Map", "tile_ids": ["TILE_A"]})
        wait_for_job(self.manager, job.job_id)
        png = b"\x89PNG\r\n\x1a\n" + b"offline-map"
        record = self.manager.save_basemap(
            job.region_id, {"png_base64": base64.b64encode(png).decode("ascii")}
        )
        self.assertTrue(record.basemap)
        self.assertEqual(record.to_api_dict()["basemap_url"], f"/api/offline/regions/{job.region_id}/basemap.png")
        self.assertEqual(self.manager.basemap_path(job.region_id).read_bytes(), png)
        with self.assertRaises(OfflineRegionError):
            self.manager.save_basemap(job.region_id, {"png_base64": base64.b64encode(b"not png").decode()})

    def test_connectivity_mode_persists(self) -> None:
        self.manager.set_mode("offline")
        restarted = OfflineRegionManager(
            root=self.root / "regions",
            scene_root=self.scene,
            tile_bundle_builder=fake_bundle_builder,
        )
        self.assertEqual(restarted.mode, "offline")
        with self.assertRaises(OfflineRegionError):
            restarted.set_mode("airplane")


if __name__ == "__main__":
    unittest.main()
