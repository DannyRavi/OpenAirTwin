"""Deterministic 2.5D rasterization of Sionna RT scene geometry.

The PMNet contract consumes a building-height image and a transmitter image.
This module derives both from plain triangle soup, so it is independent of
Mitsuba and unit-testable with synthetic geometry.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from backend import config


_EPS = 1e-9
_INSIDE_TOLERANCE = 1e-7


@dataclass(frozen=True)
class RoiGrid:
    """A regular XY grid whose cell centres are the prediction pixels."""

    bounds_min: np.ndarray
    bounds_max: np.ndarray
    nx: int
    ny: int
    cell_size: np.ndarray

    @classmethod
    def from_center(cls, center_xy, size_xy, grid_cells: int) -> "RoiGrid":
        center = np.asarray(center_xy, dtype=np.float64).reshape(2)
        size = np.asarray(size_xy, dtype=np.float64).reshape(2)
        cell_count = int(grid_cells)
        if cell_count <= 0:
            raise ValueError("roi.grid must be positive")
        if np.any(size <= 0.0):
            raise ValueError("roi.size values must be positive")
        return cls(
            bounds_min=center - size * 0.5,
            bounds_max=center + size * 0.5,
            nx=cell_count,
            ny=cell_count,
            cell_size=size / float(cell_count),
        )

    @property
    def cell_count(self) -> int:
        return int(self.nx) * int(self.ny)

    def center_xs(self) -> np.ndarray:
        return self.bounds_min[0] + (np.arange(self.nx) + 0.5) * self.cell_size[0]

    def center_ys(self) -> np.ndarray:
        return self.bounds_min[1] + (np.arange(self.ny) + 0.5) * self.cell_size[1]

    def edge_xs(self) -> np.ndarray:
        return np.linspace(self.bounds_min[0], self.bounds_max[0], int(self.nx) + 1)

    def edge_ys(self) -> np.ndarray:
        return np.linspace(self.bounds_min[1], self.bounds_max[1], int(self.ny) + 1)

    def cell_center_points(self) -> np.ndarray:
        xx, yy = np.meshgrid(self.center_xs(), self.center_ys())
        return np.column_stack([xx.reshape(-1), yy.reshape(-1)])

    def corner_points(self) -> np.ndarray:
        xx, yy = np.meshgrid(self.edge_xs(), self.edge_ys())
        return np.column_stack([xx.reshape(-1), yy.reshape(-1)])

    def describe(self) -> dict:
        return {
            "nx": int(self.nx),
            "ny": int(self.ny),
            "cell_size": [float(self.cell_size[0]), float(self.cell_size[1])],
            "bounds": {
                "min": [float(self.bounds_min[0]), float(self.bounds_min[1])],
                "max": [float(self.bounds_max[0]), float(self.bounds_max[1])],
            },
        }


@dataclass(frozen=True)
class SurfaceGrid:
    """Rasterized ground and building surfaces for one ROI."""

    roi: RoiGrid
    vertex_z: np.ndarray          # (ny + 1, nx + 1) ground height at grid corners
    ground_z: np.ndarray          # (ny, nx) ground height at cell centres
    roof_z: np.ndarray            # (ny, nx) highest obstacle top, ground where none
    building_mask: np.ndarray     # (ny, nx) bool
    building_height: np.ndarray   # (ny, nx) roof minus ground, metres
    obstacle_triangle_count: int
    ground_triangle_count: int

    @property
    def building_cell_count(self) -> int:
        return int(np.count_nonzero(self.building_mask))

    @property
    def building_coverage(self) -> float:
        total = self.roi.cell_count
        return 0.0 if total <= 0 else float(self.building_cell_count) / float(total)

    def height_summary(self) -> dict:
        heights = self.building_height[self.building_mask]
        if heights.size == 0:
            return {"min": 0.0, "max": 0.0, "mean": 0.0}
        return {
            "min": float(np.min(heights)),
            "max": float(np.max(heights)),
            "mean": float(np.mean(heights)),
        }


def triangles_in_bounds(triangles: np.ndarray, bounds_min, bounds_max, *, margin: float = 0.0) -> np.ndarray:
    """Return the triangles whose XY bounding box overlaps the ROI."""

    triangles = _as_triangles(triangles)
    if triangles.shape[0] == 0:
        return triangles
    lower = np.asarray(bounds_min, dtype=np.float64) - float(margin)
    upper = np.asarray(bounds_max, dtype=np.float64) + float(margin)
    xy = triangles[:, :, :2]
    tri_min = np.min(xy, axis=1)
    tri_max = np.max(xy, axis=1)
    keep = (
        (tri_min[:, 0] <= upper[0])
        & (tri_max[:, 0] >= lower[0])
        & (tri_min[:, 1] <= upper[1])
        & (tri_max[:, 1] >= lower[1])
    )
    return triangles[keep]


def _triangle_normal_z(triangles: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    normals = np.cross(
        triangles[:, 1, :] - triangles[:, 0, :],
        triangles[:, 2, :] - triangles[:, 0, :],
    )
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > _EPS
    unit_z = np.zeros(normals.shape[0], dtype=np.float64)
    unit_z[valid] = normals[valid, 2] / lengths[valid]
    return unit_z, valid


def upward_triangles(triangles: np.ndarray, *, min_normal_z: float = 0.5) -> np.ndarray:
    """Return the near-horizontal triangles that form roofs and ground."""

    triangles = _as_triangles(triangles)
    if triangles.shape[0] == 0:
        return triangles
    unit_z, valid = _triangle_normal_z(triangles)
    return triangles[valid & (np.abs(unit_z) >= float(min_normal_z))]


def vertical_triangles(triangles: np.ndarray, *, max_normal_z: float = 0.5) -> np.ndarray:
    """Return the near-vertical triangles that form walls and facades."""

    triangles = _as_triangles(triangles)
    if triangles.shape[0] == 0:
        return triangles
    unit_z, valid = _triangle_normal_z(triangles)
    return triangles[valid & (np.abs(unit_z) < float(max_normal_z))]


def rasterize_grid(
    xs: np.ndarray,
    ys: np.ndarray,
    triangles: np.ndarray,
) -> np.ndarray:
    """Maximum interpolated Z of a triangle soup on a regular grid.

    ``xs`` and ``ys`` are strictly increasing coordinate vectors. The result is
    ``(len(ys), len(xs))`` and ``NaN`` where no triangle covers a sample.
    """

    xs = np.asarray(xs, dtype=np.float64).reshape(-1)
    ys = np.asarray(ys, dtype=np.float64).reshape(-1)
    # Use -inf while accumulating: np.maximum propagates NaN, so NaN cannot be
    # the "empty" sentinel here.
    out = np.full((ys.size, xs.size), -np.inf, dtype=np.float64)
    triangles = _as_triangles(triangles)
    if triangles.shape[0] == 0 or xs.size == 0 or ys.size == 0:
        return np.full((ys.size, xs.size), np.nan, dtype=np.float64)

    a = triangles[:, 0, :]
    b = triangles[:, 1, :]
    c = triangles[:, 2, :]
    denom = (
        (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0])
        + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
    )
    tri_min = np.min(triangles[:, :, :2], axis=1)
    tri_max = np.max(triangles[:, :, :2], axis=1)

    for index in np.flatnonzero(np.abs(denom) > _EPS):
        i0 = int(np.searchsorted(xs, tri_min[index, 0] - _EPS, side="left"))
        i1 = int(np.searchsorted(xs, tri_max[index, 0] + _EPS, side="right"))
        j0 = int(np.searchsorted(ys, tri_min[index, 1] - _EPS, side="left"))
        j1 = int(np.searchsorted(ys, tri_max[index, 1] + _EPS, side="right"))
        if i0 >= i1 or j0 >= j1:
            continue
        qx, qy = np.meshgrid(xs[i0:i1], ys[j0:j1])
        ai = a[index]
        bi = b[index]
        ci = c[index]
        d = denom[index]
        w0 = ((bi[1] - ci[1]) * (qx - ci[0]) + (ci[0] - bi[0]) * (qy - ci[1])) / d
        w1 = ((ci[1] - ai[1]) * (qx - ci[0]) + (ai[0] - ci[0]) * (qy - ci[1])) / d
        w2 = 1.0 - w0 - w1
        inside = (
            (w0 >= -_INSIDE_TOLERANCE)
            & (w1 >= -_INSIDE_TOLERANCE)
            & (w2 >= -_INSIDE_TOLERANCE)
        )
        if not np.any(inside):
            continue
        z = w0 * ai[2] + w1 * bi[2] + w2 * ci[2]
        block = out[j0:j1, i0:i1]
        candidate = np.where(inside, z, -np.inf)
        np.maximum(block, candidate, out=block)

    out[~np.isfinite(out)] = np.nan
    return out


def sample_height_field(points_xy: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    """Interpolate the highest triangle surface at arbitrary XY points."""

    points = np.asarray(points_xy, dtype=np.float64).reshape(-1, 2)
    result = np.full(points.shape[0], np.nan, dtype=np.float64)
    triangles = _as_triangles(triangles)
    if points.shape[0] == 0 or triangles.shape[0] == 0:
        return result

    xs = np.unique(points[:, 0])
    ys = np.unique(points[:, 1])
    grid = rasterize_grid(xs, ys, triangles)
    column = np.searchsorted(xs, points[:, 0])
    row = np.searchsorted(ys, points[:, 1])
    values = grid[row, column]
    result[:] = values
    return result


def build_surface(
    *,
    ground_triangles: np.ndarray,
    obstacle_triangles: np.ndarray,
    roi: RoiGrid,
) -> SurfaceGrid:
    """Rasterize ground and buildings into a :class:`SurfaceGrid`."""

    roi_min = roi.bounds_min
    roi_max = roi.bounds_max
    ground = triangles_in_bounds(ground_triangles, roi_min, roi_max, margin=1.0)
    obstacle = triangles_in_bounds(obstacle_triangles, roi_min, roi_max, margin=1.0)

    ground_up = upward_triangles(ground)
    obstacle_up = upward_triangles(obstacle)

    corner_xs = roi.edge_xs()
    corner_ys = roi.edge_ys()
    corner_z = rasterize_grid(corner_xs, corner_ys, ground_up)
    if not np.any(np.isfinite(corner_z)):
        # Fall back to any ground orientation so a coarse or axis-aligned scene
        # still produces a usable drape.
        corner_z = rasterize_grid(corner_xs, corner_ys, ground)
    finite = np.isfinite(corner_z)
    fill_level: float | None = float(np.median(corner_z[finite])) if np.any(finite) else None
    if fill_level is None:
        # The ROI may extend past the loaded ground (edge of a tile or a small
        # scene). Anchor the drape on the ground under the ROI centre instead
        # of failing the whole prediction.
        centre_probe = sample_height_field(
            np.asarray(
                [
                    [
                        (roi_min[0] + roi_max[0]) * 0.5,
                        (roi_min[1] + roi_max[1]) * 0.5,
                    ]
                ],
                dtype=np.float64,
            ),
            ground,
        )
        if np.isfinite(centre_probe[0]):
            fill_level = float(centre_probe[0])
    if fill_level is None:
        raise ValueError(
            "No ground surface was found inside the PMNet ROI; select tiles that "
            "contain terrain around the transmitter"
        )
    vertex_z = np.where(finite, corner_z, fill_level)

    ground_cell_z = (
        vertex_z[:-1, :-1]
        + vertex_z[1:, :-1]
        + vertex_z[:-1, 1:]
        + vertex_z[1:, 1:]
    ) * 0.25

    roof_sample = rasterize_grid(roi.center_xs(), roi.center_ys(), obstacle_up)
    roof_z = np.where(np.isfinite(roof_sample), roof_sample, ground_cell_z)
    height = np.maximum(roof_z - ground_cell_z, 0.0)
    building_mask = height >= float(config.MIN_PMNET_BUILDING_HEIGHT_M)

    return SurfaceGrid(
        roi=roi,
        vertex_z=vertex_z,
        ground_z=ground_cell_z,
        roof_z=roof_z,
        building_mask=building_mask,
        building_height=height,
        obstacle_triangle_count=int(obstacle_up.shape[0]),
        ground_triangle_count=int(ground_up.shape[0]),
    )


def split_scene_triangles(
    scene,
    *,
    obstacle_materials,
    ground_materials,
) -> tuple[np.ndarray, np.ndarray]:
    """Collect ground and obstacle triangles from a Sionna RT scene."""

    import mitsuba as mi

    from backend.rt.common import to_numpy

    ground_blocks: list[np.ndarray] = []
    obstacle_blocks: list[np.ndarray] = []
    obstacle_tokens = {str(token).strip().lower() for token in obstacle_materials}
    ground_tokens = {str(token).strip().lower() for token in ground_materials}

    for obj in scene.objects.values():
        material = getattr(getattr(obj, "radio_material", None), "name", None)
        token = str(material).strip().lower() if material is not None else ""
        if not token or token in {"none"}:
            continue
        target = ground_blocks if token in ground_tokens else obstacle_blocks
        if token not in ground_tokens and token not in obstacle_tokens:
            # Unknown materials are treated as obstructions so a new material
            # never silently disappears from the pathloss map.
            target = obstacle_blocks
        try:
            mesh = obj.clone(as_mesh=True)
            params = mi.traverse(mesh)
            vertex_positions = np.asarray(
                to_numpy(params["vertex_positions"]), dtype=np.float64
            ).reshape(-1, 3)
            faces = np.asarray(to_numpy(params["faces"]), dtype=np.int64).reshape(-1, 3)
        except Exception:
            continue
        if faces.size == 0 or vertex_positions.size == 0:
            continue
        target.append(vertex_positions[faces])

    ground = (
        np.concatenate(ground_blocks, axis=0)
        if ground_blocks
        else np.zeros((0, 3, 3), dtype=np.float64)
    )
    obstacle = (
        np.concatenate(obstacle_blocks, axis=0)
        if obstacle_blocks
        else np.zeros((0, 3, 3), dtype=np.float64)
    )
    return ground, obstacle


def _as_triangles(triangles) -> np.ndarray:
    array = np.asarray(triangles, dtype=np.float64)
    if array.size == 0:
        return array.reshape(0, 3, 3)
    if array.ndim != 3 or array.shape[1:] != (3, 3):
        raise ValueError("triangles must have shape (n, 3, 3)")
    return array
