from __future__ import annotations

from collections.abc import Callable
from time import perf_counter

import numpy as np

from backend import config
from backend.pmnet.geometry import (
    RoiGrid,
    SurfaceGrid,
    build_surface,
    split_scene_triangles,
)
from backend.pmnet.model import (
    build_normalized_inputs,
    checkpoint_loss_field,
    load_checkpoint_model,
    resolve_checkpoint,
    run_checkpoint_prediction,
    torch_status,
)
from backend.pmnet.params import PMNET_SCHEMA_VERSION, parse_pmnet_payload
from backend.pmnet.predictor import (
    PredictionField,
    predict_pathloss,
    round_prediction,
)
from backend.rt.runtime import log_timing, require_scene_generation


ProgressCallback = Callable[[float, str], None]

ROOF_RECEIVER_CLEARANCE_M = 0.5
_SCENE_DIRECT_GEOMETRY_ENGINE = "geometry-surrogate"


def _report(progress_cb: ProgressCallback | None, progress: float, message: str) -> None:
    if progress_cb is not None:
        progress_cb(progress, message)


def _ground_materials() -> tuple[str, ...]:
    return tuple(
        {
            str(config.RADIOMAP_MEASUREMENT_MATERIAL),
            "itu_wet_ground",
        }
    )


def _collect_scene_geometry(rt_runtime, expected_scene_generation: int | None) -> tuple[np.ndarray, np.ndarray]:
    require_scene_generation(rt_runtime, expected_scene_generation)
    scene = rt_runtime.require_ready()
    return split_scene_triangles(
        scene,
        obstacle_materials=config.PMNET_OBSTACLE_MATERIALS,
        ground_materials=_ground_materials(),
    )


def _resolve_engine(params: dict) -> dict:
    """Decide between a PMNet checkpoint and the deterministic surrogate."""

    preference = params["engine_preference"]
    status = torch_status()
    if preference == "physics":
        return {
            "id": "physics",
            "label": "Geometry surrogate",
            "checkpoint": None,
            "device": None,
            "reason": "model.engine=physics requested the built-in surrogate",
            "preference": preference,
        }
    if not status["available"]:
        if preference == "checkpoint":
            raise ValueError(
                "model.engine=checkpoint requires PyTorch, which is not installed in "
                "this environment"
            )
        return {
            "id": "physics",
            "label": "Geometry surrogate",
            "checkpoint": None,
            "device": None,
            "reason": f"PyTorch is unavailable ({status['error']})",
            "preference": preference,
        }

    checkpoint = resolve_checkpoint(params["checkpoint_path"])
    if checkpoint is None:
        if preference == "checkpoint":
            raise ValueError(
                "model.engine=checkpoint requires a PMNet checkpoint; set "
                "OAT_PMNET_CHECKPOINT or place a .pt file under models/pmnet"
            )
        return {
            "id": "physics",
            "label": "Geometry surrogate",
            "checkpoint": None,
            "device": None,
            "reason": "No PMNet checkpoint is configured",
            "preference": preference,
        }

    try:
        entry = load_checkpoint_model(checkpoint, device=config.PMNET_DEVICE)
    except Exception as exc:
        if preference == "checkpoint":
            raise ValueError(f"Could not load the PMNet checkpoint: {exc}") from exc
        return {
            "id": "physics",
            "label": "Geometry surrogate",
            "checkpoint": None,
            "device": None,
            "reason": f"PMNet checkpoint could not be loaded ({exc})",
            "preference": preference,
        }
    return {
        "id": "pmnet",
        "label": "PMNet checkpoint",
        "checkpoint": str(entry.checkpoint),
        "device": entry.device,
        "reason": None,
        "preference": preference,
        "_entry": entry,
    }


def _checkpoint_ground_field(
    entry,
    surface: SurfaceGrid,
    tx_position,
    params: dict,
    ground_field: PredictionField,
) -> PredictionField:
    prediction = run_checkpoint_prediction(entry, surface, tx_position)
    loss = checkpoint_loss_field(
        prediction,
        min_loss_db=params["checkpoint_min_loss_db"],
        max_loss_db=params["checkpoint_max_loss_db"],
        shape=(int(surface.roi.ny), int(surface.roi.nx)),
    )
    return PredictionField(
        loss_db=loss,
        wall_crossings=ground_field.wall_crossings,
        fresnel_parameter=ground_field.fresnel_parameter,
        los_mask=ground_field.los_mask,
    )


def _building_cells(surface: SurfaceGrid, ground: PredictionField, roof: PredictionField) -> dict:
    roi = surface.roi
    rows, cols = np.nonzero(surface.building_mask)
    cells: list[list[float]] = []
    limit = int(config.MAX_PMNET_BUILDING_CELLS)
    truncated = rows.size > limit
    for index in range(min(rows.size, limit)):
        row = int(rows[index])
        col = int(cols[index])
        cells.append(
            [
                col,
                row,
                round(float(surface.roof_z[row, col]), 3),
                round(float(surface.ground_z[row, col]), 3),
                round(float(roof.path_gain_db[row, col]), 3),
                round(float(ground.path_gain_db[row, col]), 3),
            ]
        )
    return {
        "coverage": float(surface.building_coverage),
        "cell_count": int(rows.size),
        "rendered_cell_count": len(cells),
        "truncated": bool(truncated),
        "max_cells": limit,
        "height": surface.height_summary(),
        "cells": cells,
        "grid": {"nx": int(roi.nx), "ny": int(roi.ny)},
    }


def _field_stats(field: PredictionField, surface: SurfaceGrid) -> dict:
    loss = np.asarray(field.loss_db, dtype=np.float64)
    finite = loss[np.isfinite(loss)]
    return {
        "mean_db": float(np.mean(-finite)) if finite.size else 0.0,
        "median_db": float(np.median(-finite)) if finite.size else 0.0,
        "los_fraction": float(np.count_nonzero(field.los_mask)) / float(max(loss.size, 1)),
        "shadow_fraction": float(np.count_nonzero(field.wall_crossings > 0.0))
        / float(max(loss.size, 1)),
        "mean_wall_crossings": float(np.mean(field.wall_crossings)) if loss.size else 0.0,
        "building_coverage": float(surface.building_coverage),
        "building_cells": int(surface.building_cell_count),
        "ground_cells": int(surface.roi.cell_count - surface.building_cell_count),
        "max_obstruction": float(np.max(field.fresnel_parameter))
        if field.fresnel_parameter.size
        else 0.0,
    }


def solve_pmnet(
    rt_runtime,
    payload: dict,
    progress_cb: ProgressCallback | None = None,
    *,
    expected_scene_generation: int | None = None,
) -> dict:
    """Predict one PMNet pathloss map for a ready Sionna RT runtime."""

    params = parse_pmnet_payload(payload)
    total_started_at = perf_counter()

    _report(progress_cb, 0.04, "Reading scene geometry")
    geometry_started_at = perf_counter()
    with rt_runtime.lock:
        ground_triangles, obstacle_triangles = _collect_scene_geometry(
            rt_runtime, expected_scene_generation
        )
    geometry_seconds = perf_counter() - geometry_started_at
    log_timing(
        "pmnet_geometry",
        geometry_started_at,
        ground=int(ground_triangles.shape[0]),
        obstacles=int(obstacle_triangles.shape[0]),
    )

    _report(progress_cb, 0.18, "Rasterizing building mask")
    surface_started_at = perf_counter()
    roi = RoiGrid.from_center(
        params["tx_position"][:2],
        params["roi_size"],
        params["grid_cells"],
    )
    surface = build_surface(
        ground_triangles=ground_triangles,
        obstacle_triangles=obstacle_triangles,
        roi=roi,
    )
    surface_seconds = perf_counter() - surface_started_at
    log_timing(
        "pmnet_surface",
        surface_started_at,
        cells=roi.cell_count,
        buildings=surface.building_cell_count,
    )

    _report(progress_cb, 0.34, "Predicting ground pathloss")
    inference_started_at = perf_counter()
    ground_field = predict_pathloss(
        params["tx_position"],
        surface,
        rx_z=surface.ground_z + float(params["height_offset"]),
        frequency_hz=params["frequency_hz"],
        wall_loss_db=params["wall_loss_db"],
    )

    roof_field = predict_pathloss(
        params["tx_position"],
        surface,
        rx_z=surface.roof_z + ROOF_RECEIVER_CLEARANCE_M,
        frequency_hz=params["frequency_hz"],
        wall_loss_db=params["wall_loss_db"],
    )

    engine = _resolve_engine(params)
    entry = engine.pop("_entry", None)
    if entry is not None:
        _report(progress_cb, 0.62, "Running PMNet checkpoint")
        ground_field = _checkpoint_ground_field(
            entry, surface, params["tx_position"], params, ground_field
        )
    inference_seconds = perf_counter() - inference_started_at
    log_timing("pmnet_inference", inference_started_at, engine=engine["id"])

    _report(progress_cb, 0.88, "Finalizing pathloss map")
    finalize_started_at = perf_counter()
    ground_gain = ground_field.path_gain_db
    finite_gain = ground_gain[np.isfinite(ground_gain)]
    buildings = _building_cells(surface, ground_field, roof_field)
    stats = _field_stats(ground_field, surface)
    log_timing("pmnet_finalize", finalize_started_at, cells=roi.cell_count)

    _report(progress_cb, 1.0, "Pathloss map ready")
    log_timing("pmnet_total", total_started_at, cells=roi.cell_count, engine=engine["id"])
    grid_payload = roi.describe()
    grid_payload.update(
        {
            "frequency_hz": float(params["frequency_hz"]),
            "height_offset": float(params["height_offset"]),
            "vertex_z": round_prediction(surface.vertex_z),
            "vertex_shape": [int(roi.ny) + 1, int(roi.nx) + 1],
        }
    )

    return {
        "schema_version": PMNET_SCHEMA_VERSION,
        "metric": "path_gain_db",
        "unit": "dB",
        "engine": engine,
        "tx": {
            "position": [float(value) for value in params["tx_position"]],
            "power_dbm": float(params["tx_power_dbm"]),
        },
        "grid": grid_payload,
        "values": {
            "count": int(roi.cell_count),
            "data": round_prediction(ground_gain),
        },
        "range": {
            "min": float(np.min(finite_gain)) if finite_gain.size else 0.0,
            "max": float(np.max(finite_gain)) if finite_gain.size else 0.0,
        },
        "buildings": buildings,
        "stats": stats,
        "timings": {
            "geometry_s": float(geometry_seconds),
            "surface_s": float(surface_seconds),
            "inference_s": float(inference_seconds),
            "total_s": float(perf_counter() - total_started_at),
        },
    }


__all__ = ["solve_pmnet", "build_normalized_inputs", "ROOF_RECEIVER_CLEARANCE_M"]
