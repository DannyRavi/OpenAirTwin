"""Deterministic pathloss surrogate used when no PMNet checkpoint is loaded.

The model is a COST-231-style multi-wall prediction combined with a Fresnel
knife-edge term for the over-roof path. It is intentionally simple and fully
deterministic, so the PMNet UI stays usable on machines without PyTorch while
keeping the same output contract as a checkpoint prediction.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from backend import config
from backend.pmnet.geometry import RoiGrid, SurfaceGrid


SPEED_OF_LIGHT_MPS = 299_792_458.0


@dataclass(frozen=True)
class PredictionField:
    """One predicted pathloss field at a fixed receiver height."""

    loss_db: np.ndarray        # (ny, nx), positive propagation loss
    wall_crossings: np.ndarray  # (ny, nx) float, buildings entered
    fresnel_parameter: np.ndarray  # (ny, nx) max knife-edge v
    los_mask: np.ndarray       # (ny, nx) bool, unobstructed line of sight

    @property
    def path_gain_db(self) -> np.ndarray:
        return -self.loss_db


def free_space_path_loss_db(distance_m: np.ndarray, frequency_hz: float) -> np.ndarray:
    distance = np.maximum(np.asarray(distance_m, dtype=np.float64), 1e-3)
    return 20.0 * np.log10(4.0 * np.pi * distance * float(frequency_hz) / SPEED_OF_LIGHT_MPS)


def knife_edge_loss_db(v: np.ndarray) -> np.ndarray:
    """ITU-R P.526 single knife-edge diffraction loss for parameter ``v``."""

    v = np.asarray(v, dtype=np.float64)
    loss = np.zeros_like(v)
    blocked = v > -0.78
    if np.any(blocked):
        vv = v[blocked]
        term = np.sqrt(np.maximum((vv - 0.1) ** 2 + 1.0, 0.0)) + vv - 0.1
        loss[blocked] = 6.9 + 20.0 * np.log10(np.maximum(term, 1e-6))
    return np.maximum(loss, 0.0)


def _cell_indices(
    x: np.ndarray,
    y: np.ndarray,
    roi: RoiGrid,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Nearest-cell indices plus an inside-ROI mask for arbitrary XY points."""

    fx = (x - roi.bounds_min[0]) / roi.cell_size[0]
    fy = (y - roi.bounds_min[1]) / roi.cell_size[1]
    ix = np.floor(fx).astype(np.int64)
    iy = np.floor(fy).astype(np.int64)
    inside = (
        (x >= roi.bounds_min[0])
        & (x <= roi.bounds_max[0])
        & (y >= roi.bounds_min[1])
        & (y <= roi.bounds_max[1])
    )
    ix = np.clip(ix, 0, int(roi.nx) - 1)
    iy = np.clip(iy, 0, int(roi.ny) - 1)
    return ix, iy, inside


def _sample_field(field: np.ndarray, ix: np.ndarray, iy: np.ndarray, inside: np.ndarray) -> np.ndarray:
    sampled = np.asarray(field)[iy, ix]
    return np.where(inside, sampled, 0.0)


def _sample_building(field: np.ndarray, ix: np.ndarray, iy: np.ndarray, inside: np.ndarray) -> np.ndarray:
    sampled = np.asarray(field, dtype=bool)[iy, ix]
    return sampled & inside


def predict_pathloss(
    tx_position,
    surface: SurfaceGrid,
    *,
    rx_z: np.ndarray,
    frequency_hz: float,
    wall_loss_db: float,
    max_samples: int | None = None,
    block_size: int = 2048,
) -> PredictionField:
    """Predict propagation loss for every cell of ``surface.roi``."""

    roi = surface.roi
    nx, ny = int(roi.nx), int(roi.ny)
    tx = np.asarray(tx_position, dtype=np.float64).reshape(3)
    rx_z = np.asarray(rx_z, dtype=np.float64).reshape(ny, nx)

    xx, yy = np.meshgrid(roi.center_xs(), roi.center_ys())
    cell_x = xx.reshape(-1)
    cell_y = yy.reshape(-1)
    cell_z = rx_z.reshape(-1)

    dx = cell_x - tx[0]
    dy = cell_y - tx[1]
    dz = cell_z - tx[2]
    horizontal = np.hypot(dx, dy)
    distance = np.maximum(np.sqrt(horizontal ** 2 + dz ** 2), 1e-3)

    wavelength = SPEED_OF_LIGHT_MPS / float(frequency_hz)
    fspl = free_space_path_loss_db(distance, frequency_hz)

    roof_top = np.asarray(surface.roof_z, dtype=np.float64)
    obstacle = np.asarray(surface.building_mask, dtype=bool)

    limit = int(config.MAX_PMNET_RAY_SAMPLES if max_samples is None else max_samples)
    limit = max(2, limit)

    wall_crossings = np.zeros(distance.shape[0], dtype=np.float64)
    max_v = np.full(distance.shape[0], -np.inf, dtype=np.float64)

    for start in range(0, distance.shape[0], block_size):
        stop = min(start + block_size, distance.shape[0])
        h = horizontal[start:stop]
        d = distance[start:stop]
        reach = float(np.max(h)) if h.size else 0.0
        step = max(float(np.min(roi.cell_size)) * 0.5, 1e-3)
        samples = int(min(limit, max(4, np.ceil(reach / step) + 1)))
        fractions = np.linspace(0.0, 1.0, samples, dtype=np.float64)[1:-1]
        if fractions.size == 0:
            fractions = np.asarray([0.5], dtype=np.float64)

        sx = tx[0] + (cell_x[start:stop, None] - tx[0]) * fractions[None, :]
        sy = tx[1] + (cell_y[start:stop, None] - tx[1]) * fractions[None, :]
        sz = tx[2] + (cell_z[start:stop, None] - tx[2]) * fractions[None, :]

        ix, iy, inside = _cell_indices(sx, sy, roi)
        local_roof = _sample_field(roof_top, ix, iy, inside)
        local_obstacle = _sample_building(obstacle, ix, iy, inside)
        blocked = local_obstacle & (sz < local_roof)

        # Every building boundary along the path costs one wall; counting both
        # entry and exit keeps the model sane when the transmitter itself sits
        # inside a footprint.
        transitions = np.abs(np.diff(blocked.astype(np.int8), axis=1))
        wall_crossings[start:stop] = transitions.sum(axis=1).astype(np.float64)

        # Fresnel parameter: positive when the ray passes below the roof.
        path = np.maximum(d[:, None], 1e-3)
        d1 = np.maximum(fractions[None, :] * path, 1e-3)
        d2 = np.maximum((1.0 - fractions[None, :]) * path, 1e-3)
        obstruction = np.where(local_obstacle, local_roof - sz, -np.inf)
        fresnel_radius = np.sqrt(
            np.maximum(wavelength * d1 * d2 / (d1 + d2), 0.0)
        )
        v = np.sqrt(2.0) * obstruction / np.maximum(fresnel_radius, 1e-6)
        v = np.where(np.isfinite(v), v, -np.inf)
        max_v[start:stop] = v.max(axis=1)

    max_v = np.where(np.isfinite(max_v), max_v, -np.inf)

    penetration_loss = fspl + wall_crossings * float(wall_loss_db)
    diffraction_loss = fspl + knife_edge_loss_db(max_v)
    loss = np.minimum(penetration_loss, diffraction_loss)
    # A predicted path can never beat free-space propagation.
    loss = np.maximum(loss, fspl)

    los = (wall_crossings <= 0.0) & (max_v <= 0.0)
    return PredictionField(
        loss_db=loss.reshape(ny, nx),
        wall_crossings=wall_crossings.reshape(ny, nx),
        fresnel_parameter=max_v.reshape(ny, nx),
        los_mask=los.reshape(ny, nx),
    )


def round_prediction(values: np.ndarray, *, places: int = 3) -> list[float | None]:
    """Serialize a prediction field without leaking ``NaN`` into JSON."""

    flat = np.asarray(values, dtype=np.float64).reshape(-1)
    rounded = np.round(flat, places)
    return [
        float(value) if np.isfinite(value) else None
        for value in rounded
    ]
