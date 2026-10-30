from __future__ import annotations

import json
from types import SimpleNamespace
import time
import unittest
from unittest.mock import patch

import numpy as np

from backend import config
from backend.features.catalog import BACKEND_FEATURE_CATALOG, FEATURE_ROUTES
from backend.features.core import FeatureServiceRegistry
from backend.features.pmnet import PMNET_FEATURE
from backend.jobs.pmnet_jobs import PmnetJobManager, PmnetQueueFull
from backend.pmnet.geometry import (
    RoiGrid,
    build_surface,
    rasterize_grid,
    triangles_in_bounds,
    upward_triangles,
)
from backend.pmnet.model import build_normalized_inputs, checkpoint_loss_field, resize_bilinear
from backend.pmnet.params import parse_pmnet_payload
from backend.pmnet.predictor import (
    free_space_path_loss_db,
    knife_edge_loss_db,
    predict_pathloss,
)
from backend.pmnet.solver import solve_pmnet


def quad(x0: float, y0: float, x1: float, y1: float, z: float) -> np.ndarray:
    a = [x0, y0, z]
    b = [x1, y0, z]
    c = [x1, y1, z]
    d = [x0, y1, z]
    return np.asarray([[[*a], [*b], [*c]], [[*a], [*c], [*d]]], dtype=float)


def scene_geometry() -> tuple[np.ndarray, np.ndarray]:
    ground = quad(-500.0, -500.0, 500.0, 500.0, 0.0)
    building = quad(-10.0, -10.0, 10.0, 10.0, 30.0)
    return ground, building


class FakeRuntime:
    def __init__(self) -> None:
        from threading import Lock

        self.lock = Lock()
        self.generation = 1

    def require_ready(self):
        return object()


class RoiGridTests(unittest.TestCase):
    def test_from_center_builds_square_cells(self) -> None:
        roi = RoiGrid.from_center((10.0, -4.0), (100.0, 50.0), 20)
        self.assertEqual((roi.nx, roi.ny), (20, 20))
        self.assertEqual(roi.cell_count, 400)
        np.testing.assert_allclose(roi.cell_size, [5.0, 2.5])
        np.testing.assert_allclose(roi.bounds_min, [-40.0, -29.0])
        np.testing.assert_allclose(roi.bounds_max, [60.0, 21.0])

    def test_rejects_invalid_size(self) -> None:
        with self.assertRaises(ValueError):
            RoiGrid.from_center((0.0, 0.0), (0.0, 10.0), 8)


class GeometryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ground, self.building = scene_geometry()

    def test_triangle_bounds_filter(self) -> None:
        far = quad(400.0, 400.0, 410.0, 410.0, 0.0)
        kept = triangles_in_bounds(np.concatenate([self.building, far]), [-50, -50], [50, 50])
        self.assertEqual(kept.shape[0], 2)

    def test_upward_filter_drops_vertical_triangles(self) -> None:
        wall = np.asarray(
            [[[0.0, 0.0, 0.0], [0.0, 10.0, 0.0], [0.0, 10.0, 30.0]]], dtype=float
        )
        kept = upward_triangles(np.concatenate([self.building, wall]))
        self.assertEqual(kept.shape[0], 2)

    def test_rasterize_grid_returns_nan_outside_coverage(self) -> None:
        xs = np.asarray([0.5, 1.5])
        ys = np.asarray([0.5, 1.5])
        grid = rasterize_grid(xs, ys, quad(0.0, 0.0, 1.0, 1.0, 5.0))
        np.testing.assert_allclose(grid[0, 0], 5.0)
        self.assertTrue(np.isnan(grid[1, 1]))

    def test_build_surface_rasterizes_building_footprint(self) -> None:
        roi = RoiGrid.from_center((0.0, 0.0), (100.0, 100.0), 50)
        surface = build_surface(
            ground_triangles=self.ground,
            obstacle_triangles=self.building,
            roi=roi,
        )
        self.assertEqual(surface.building_cell_count, 100)
        self.assertAlmostEqual(surface.building_coverage, 100 / 2500)
        summary = surface.height_summary()
        self.assertAlmostEqual(summary["min"], 30.0, places=3)
        self.assertAlmostEqual(summary["max"], 30.0, places=3)
        self.assertTrue(np.all(np.isfinite(surface.vertex_z)))

    def test_build_surface_requires_ground(self) -> None:
        roi = RoiGrid.from_center((0.0, 0.0), (40.0, 40.0), 10)
        with self.assertRaises(ValueError):
            build_surface(
                ground_triangles=np.zeros((0, 3, 3)),
                obstacle_triangles=self.building,
                roi=roi,
            )


class PredictorTests(unittest.TestCase):
    def test_free_space_path_loss_matches_reference(self) -> None:
        # 1 km at 3.5 GHz is about 103.33 dB.
        value = free_space_path_loss_db(np.asarray([1000.0]), 3.5e9)[0]
        self.assertAlmostEqual(value, 103.33, places=2)

    def test_knife_edge_loss_is_monotonic(self) -> None:
        losses = knife_edge_loss_db(np.asarray([-1.0, 0.0, 1.0, 2.0, 4.0]))
        self.assertEqual(losses[0], 0.0)
        self.assertTrue(np.all(np.diff(losses) > 0.0))

    def test_shadow_behind_building_exceeds_line_of_sight(self) -> None:
        ground, building = scene_geometry()
        roi = RoiGrid.from_center((0.0, 0.0), (100.0, 100.0), 50)
        surface = build_surface(
            ground_triangles=ground, obstacle_triangles=building, roi=roi
        )
        field = predict_pathloss(
            (-15.0, 0.0, 35.0),
            surface,
            rx_z=surface.ground_z + 1.5,
            frequency_hz=3.5e9,
            wall_loss_db=6.0,
        )
        self.assertTrue(field.los_mask[25, 2])
        self.assertFalse(field.los_mask[25, 47])
        self.assertGreater(field.loss_db[25, 47], field.loss_db[25, 2] + 5.0)
        self.assertGreaterEqual(int(field.wall_crossings[25, 47]), 1)
        # The unobstructed cell must sit exactly on the free-space reference.
        reference = free_space_path_loss_db(
            np.asarray([float(np.hypot(30.0, 33.5))]), 3.5e9
        )[0]
        self.assertAlmostEqual(float(field.loss_db[25, 2]), reference, delta=0.5)

    def test_rooftop_prediction_is_stronger_than_street_level(self) -> None:
        ground, building = scene_geometry()
        roi = RoiGrid.from_center((0.0, 0.0), (100.0, 100.0), 50)
        surface = build_surface(
            ground_triangles=ground, obstacle_triangles=building, roi=roi
        )
        tx = (-15.0, 0.0, 35.0)
        street = predict_pathloss(
            tx,
            surface,
            rx_z=surface.ground_z + 1.5,
            frequency_hz=3.5e9,
            wall_loss_db=6.0,
        )
        roof = predict_pathloss(
            tx,
            surface,
            rx_z=surface.roof_z + 0.5,
            frequency_hz=3.5e9,
            wall_loss_db=6.0,
        )
        self.assertGreater(roof.path_gain_db[25, 25], street.path_gain_db[25, 25])


class PayloadTests(unittest.TestCase):
    def test_defaults_are_applied(self) -> None:
        params = parse_pmnet_payload({"tx": {"position": [1.0, 2.0, 3.0]}})
        self.assertEqual(params["roi_size"], config.DEFAULT_PMNET_ROI_SIZE)
        self.assertEqual(params["grid_cells"], config.DEFAULT_PMNET_GRID_CELLS)
        self.assertEqual(params["engine_preference"], "auto")
        self.assertIsNone(params["checkpoint_path"])

    def test_rejects_out_of_range_values(self) -> None:
        cases = [
            {"roi": {"size": [1.0, 100.0]}},
            {"roi": {"grid": 4}},
            {"roi": {"grid": 512}},
            {"model": {"engine": "magic"}},
            {"model": {"wall_loss_db": 5000.0}},
            {"signal": {"tx_power_dbm": 10000.0}},
            {"model": {"checkpoint_min_loss_db": 100.0, "checkpoint_max_loss_db": 100.5}},
        ]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    parse_pmnet_payload(dict(overrides))

    def test_engine_preference_is_normalized(self) -> None:
        params = parse_pmnet_payload({"model": {"engine": " PHYSICS "}})
        self.assertEqual(params["engine_preference"], "physics")


class CheckpointHelperTests(unittest.TestCase):
    def test_resize_bilinear_preserves_constant_images(self) -> None:
        source = np.full((4, 4), 0.25)
        resized = resize_bilinear(source, (9, 7))
        self.assertEqual(resized.shape, (9, 7))
        np.testing.assert_allclose(resized, 0.25)

    def test_checkpoint_loss_field_inverts_normalized_prediction(self) -> None:
        prediction = np.asarray([[1.0, 0.0]], dtype=float)
        loss = checkpoint_loss_field(
            prediction, min_loss_db=60.0, max_loss_db=160.0, shape=(1, 2)
        )
        np.testing.assert_allclose(loss, [[60.0, 160.0]])

    def test_normalized_inputs_have_two_channels(self) -> None:
        ground, building = scene_geometry()
        roi = RoiGrid.from_center((0.0, 0.0), (100.0, 100.0), 32)
        surface = build_surface(
            ground_triangles=ground, obstacle_triangles=building, roi=roi
        )
        inputs = build_normalized_inputs(surface, (0.0, 0.0, 10.0), size=64)
        self.assertEqual(inputs.shape, (2, 64, 64))
        self.assertLessEqual(float(inputs.max()), 1.0)
        self.assertGreaterEqual(float(inputs.min()), 0.0)
        self.assertAlmostEqual(float(inputs[0].max()), 1.0, places=5)
        self.assertGreater(float(inputs[1].max()), 0.9)


class SolverTests(unittest.TestCase):
    def payload(self, **overrides) -> dict:
        payload = {
            "tx": {"position": [-15.0, 0.0, 35.0]},
            "roi": {"size": [120.0, 120.0], "grid": 32},
            "solver": {"frequency_hz": 3.5e9},
            "model": {"engine": "physics", "wall_loss_db": 6.0},
            "signal": {"tx_power_dbm": 30.0},
        }
        payload.update(overrides)
        return payload

    def test_payload_is_json_serializable_and_complete(self) -> None:
        ground, building = scene_geometry()
        with patch(
            "backend.pmnet.solver.split_scene_triangles",
            return_value=(ground, building),
        ):
            result = solve_pmnet(FakeRuntime(), self.payload())

        json.dumps(result)
        self.assertEqual(result["metric"], "path_gain_db")
        self.assertEqual(result["engine"]["id"], "physics")
        self.assertEqual(result["values"]["count"], 32 * 32)
        self.assertEqual(len(result["grid"]["vertex_z"]), 33 * 33)
        self.assertGreaterEqual(result["buildings"]["cell_count"], 25)
        self.assertGreater(len(result["buildings"]["cells"]), 0)
        self.assertGreater(result["range"]["max"], result["range"]["min"])
        for key in ("mean_db", "median_db", "los_fraction", "shadow_fraction"):
            self.assertIn(key, result["stats"])

    def test_progress_reports_reach_completion(self) -> None:
        ground, building = scene_geometry()
        events: list[tuple[float, str]] = []
        with patch(
            "backend.pmnet.solver.split_scene_triangles",
            return_value=(ground, building),
        ):
            solve_pmnet(
                FakeRuntime(),
                self.payload(),
                progress_cb=lambda progress, message: events.append((progress, message)),
            )
        self.assertGreaterEqual(len(events), 4)
        self.assertEqual(events[-1][0], 1.0)
        self.assertTrue(all(0.0 <= progress <= 1.0 for progress, _ in events))

    def test_checkpoint_engine_without_torch_is_rejected(self) -> None:
        ground, building = scene_geometry()
        with patch(
            "backend.pmnet.solver.split_scene_triangles",
            return_value=(ground, building),
        ), patch(
            "backend.pmnet.solver.torch_status",
            return_value={"available": False, "version": None, "error": "missing"},
        ):
            with self.assertRaises(ValueError):
                solve_pmnet(
                    FakeRuntime(),
                    self.payload(model={"engine": "checkpoint"}),
                )

    def test_auto_engine_uses_surrogate_without_checkpoint(self) -> None:
        ground, building = scene_geometry()
        with patch(
            "backend.pmnet.solver.split_scene_triangles",
            return_value=(ground, building),
        ), patch(
            "backend.pmnet.solver.torch_status",
            return_value={"available": False, "version": None, "error": "missing"},
        ):
            result = solve_pmnet(FakeRuntime(), self.payload(model={"engine": "auto"}))
        self.assertEqual(result["engine"]["id"], "physics")
        self.assertIn("reason", result["engine"])


class JobTests(unittest.TestCase):
    def test_job_uses_shared_manager_and_routes_are_registered(self) -> None:
        self.assertIn("pmnet", [feature.id for feature in BACKEND_FEATURE_CATALOG])
        paths = {(method, path) for method, path, _name in FEATURE_ROUTES.routes()}
        self.assertIn(("POST", "/api/pmnet/jobs"), paths)
        self.assertIn(("GET", "/api/pmnet/jobs/{job_id}"), paths)
        self.assertIn(("GET", "/api/pmnet/jobs/{job_id}/result"), paths)
        self.assertFalse(any("download" in path for _method, path in paths if "pmnet" in path))

    def test_feature_service_registers_manager(self) -> None:
        services = FeatureServiceRegistry({"rt_runtime": object()})
        services.register(PMNET_FEATURE)
        self.assertIsInstance(services.get("pmnet").manager, PmnetJobManager)
        self.assertEqual(services.get("pmnet").manager.max_pending_jobs, config.PMNET_MAX_PENDING_JOBS)

    def test_result_route_rejects_unfinished_job(self) -> None:
        services = FeatureServiceRegistry({"rt_runtime": object()})
        with patch(
            "backend.features.pmnet.PmnetJobManager",
            new=lambda rt_runtime: PmnetJobManager(rt_runtime, start_worker=False),
        ):
            services.register(PMNET_FEATURE)
        manager = services.get("pmnet").manager
        job = manager.create_job(
            {"tx": {"position": [0.0, 0.0, 10.0]}, "roi": {"grid": 16}}
        )
        responses: list[tuple[dict, int]] = []
        request = SimpleNamespace(
            app_state=SimpleNamespace(
                feature_services=services,
                rt_runtime=SimpleNamespace(lock=None),
            ),
            read_json_body=lambda: {},
            send_json=lambda payload, code=200: responses.append((payload, code)),
            send_text=lambda text, code=200: responses.append(({"text": text}, code)),
        )
        self.assertTrue(
            FEATURE_ROUTES.dispatch("GET", f"/api/pmnet/jobs/{job.job_id}/result", request)
        )
        self.assertEqual(responses[-1][1], 409)
        self.assertEqual(responses[-1][0]["status"], "queued")


class JobManagerLifecycleTests(unittest.TestCase):
    def payload(self) -> dict:
        return {"tx": {"position": [0.0, 0.0, 10.0]}, "roi": {"grid": 16}}

    def wait_for_status(self, manager: PmnetJobManager, job_id: str, status: str):
        deadline = time.time() + 2.0
        while time.time() < deadline:
            job = manager.get_job(job_id)
            if job is not None and job.status == status:
                return job
            time.sleep(0.01)
        self.fail(f"Timed out waiting for {job_id} to reach {status}")

    def test_worker_completes_and_stores_result(self) -> None:
        result = {"metric": "path_gain_db", "values": {"count": 1, "data": [-90.0]}}
        manager = PmnetJobManager(
            object(),
            solver=lambda _runtime, _payload, progress_cb=None: result,
            start_worker=True,
        )
        job = manager.create_job(self.payload())
        completed = self.wait_for_status(manager, job.job_id, "succeeded")
        self.assertEqual(completed.progress, 1.0)
        self.assertEqual(completed.message, "Pathloss map ready")
        self.assertEqual(manager.get_result(job.job_id), result)

    def test_failure_is_reported_with_the_solver_message(self) -> None:
        def failing_solver(_runtime, _payload, progress_cb=None):
            raise RuntimeError("geometry unavailable")

        manager = PmnetJobManager(object(), solver=failing_solver)
        job = manager.create_job(self.payload())
        failed = self.wait_for_status(manager, job.job_id, "failed")
        self.assertIn("geometry unavailable", failed.error)
        self.assertEqual(failed.message, "PMNet pathloss job failed")

    def test_queue_is_bounded(self) -> None:
        manager = PmnetJobManager(object(), max_pending_jobs=1, start_worker=False)
        manager.create_job(self.payload())
        with self.assertRaises(PmnetQueueFull) as error:
            manager.create_job(self.payload())
        self.assertEqual(error.exception.max_pending_jobs, 1)

    def test_pending_limit_matches_configuration(self) -> None:
        manager = PmnetJobManager(object(), start_worker=False)
        self.assertEqual(manager.max_pending_jobs, config.PMNET_MAX_PENDING_JOBS)
        self.assertEqual(manager.max_stored_jobs, config.PMNET_MAX_STORED_JOBS)


if __name__ == "__main__":
    unittest.main()
