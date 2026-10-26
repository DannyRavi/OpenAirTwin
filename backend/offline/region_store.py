"""Persistent offline scene regions for Hammurabi.

A region is a self-contained snapshot of the selected city tiles. It contains
the common Sionna scene XML, the per-tile XML, every referenced PLY mesh and the
preprocessed render bundles. The manifest with SHA-256 digests is written last,
so an interrupted preparation remains explicitly incomplete instead of being
reported as ready.

The store deliberately uses only the filesystem. Hammurabi runs with a local
Python backend, so the backend filesystem is the persistent store for both the
desktop and browser deployments. Browser-side IndexedDB is not required for the
scene itself and would duplicate several hundred megabytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import threading
import traceback
from uuid import uuid4

from backend import config
from backend.scene.scene_catalog import (
    SCENE_COVERAGE_FILENAME,
    SCENE_METADATA_FILENAME,
    load_scene_coverage,
    load_scene_metadata,
)
from backend.scene.tile_bundles import build_tile_bundle_records, ensure_tile_bundle
from backend.scene.tile_scene_xml import (
    COMMON_SCENE_RELATIVE_PATH,
    TILE_SCENE_RELATIVE_DIR,
    load_tile_scene_xml_source,
    resolve_scene_filename,
)
from backend.scene.xml_catalog import load_scene_manifest


OFFLINE_SCHEMA_VERSION = 1
REGION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
CONNECTIVITY_MODES = {"online", "offline", "automatic"}
ACTIVE_STATUSES = {"queued", "preparing", "verifying", "cancelling"}
BASEMAP_MAX_BYTES = 8 * 1024 * 1024
ATTRIBUTION = {
    "basemap": (
        "Basemap snapshot: OpenFreeMap (OpenMapTiles) data \u00a9 "
        "OpenStreetMap contributors."
    ),
    "scene": "Scene geometry remains subject to the source dataset terms.",
}


class OfflineRegionError(RuntimeError):
    pass


class OfflineRegionBusy(OfflineRegionError):
    def __init__(self, region_id: str, job_id: str) -> None:
        super().__init__(f"An offline region job is already active: {region_id}")
        self.region_id = region_id
        self.job_id = job_id


@dataclass
class OfflineJob:
    job_id: str
    region_id: str
    status: str = "queued"
    progress: float = 0.0
    message: str = "Queued"
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "region_id": self.region_id,
            "status": self.status,
            "progress": round(max(0.0, min(1.0, self.progress)), 4),
            "message": self.message,
            "error": self.error,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return cleaned[:40] or "region"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    temp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    temp_path.replace(path)


def _bounds_from_corners(corners: object) -> dict | None:
    if not isinstance(corners, list) or not corners:
        return None
    west = east = south = north = None
    for corner in corners:
        if not isinstance(corner, (list, tuple)) or len(corner) < 2:
            continue
        try:
            lon = float(corner[0])
            lat = float(corner[1])
        except (TypeError, ValueError):
            continue
        west = lon if west is None else min(west, lon)
        east = lon if east is None else max(east, lon)
        south = lat if south is None else min(south, lat)
        north = lat if north is None else max(north, lat)
    if west is None or east is None or south is None or north is None:
        return None
    return {"west": west, "east": east, "south": south, "north": north}


def _bounds_valid(bounds: object) -> dict | None:
    if not isinstance(bounds, dict):
        return None
    try:
        west = float(bounds["west"])
        east = float(bounds["east"])
        south = float(bounds["south"])
        north = float(bounds["north"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (west < east and south < north):
        return None
    if west < -180 or east > 180 or south < -90 or north > 90:
        return None
    return {"west": west, "east": east, "south": south, "north": north}


def _bounds_intersect(left: dict, right: dict) -> bool:
    return not (
        left["east"] <= right["west"]
        or left["west"] >= right["east"]
        or left["north"] <= right["south"]
        or left["south"] >= right["north"]
    )


def _copy_or_link(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


@dataclass(frozen=True)
class RegionRecord:
    region_id: str
    name: str
    status: str
    tile_ids: tuple[str, ...]
    bounds: dict
    map_bounds: dict
    layers: tuple[str, ...]
    created_at: str
    updated_at: str
    bytes_total: int
    asset_count: int
    missing_count: int
    basemap: bool
    directory: Path
    manifest: dict | None = None
    progress: float = 0.0
    message: str = ""
    error: str | None = None

    def to_api_dict(self, *, include_assets: bool = False) -> dict:
        payload = {
            "region_id": self.region_id,
            "name": self.name,
            "status": self.status,
            "tile_ids": list(self.tile_ids),
            "tile_count": len(self.tile_ids),
            "bounds": dict(self.bounds),
            "map_bounds": dict(self.map_bounds),
            "layers": list(self.layers),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "bytes_total": int(self.bytes_total),
            "asset_count": int(self.asset_count),
            "missing_count": int(self.missing_count),
            "progress": round(max(0.0, min(1.0, self.progress)), 4),
            "message": self.message,
            "error": self.error,
            "basemap": bool(self.basemap),
            "basemap_url": (
                f"/api/offline/regions/{self.region_id}/basemap.png"
                if self.basemap
                else None
            ),
            "attribution": ATTRIBUTION,
            "offline_ready": self.status == "ready"
            and self.missing_count == 0
            and self.asset_count > 0,
        }
        if include_assets and self.manifest is not None:
            payload["assets"] = self.manifest.get("assets", [])
            payload["source"] = self.manifest.get("source", {})
        return payload


class OfflineRegionManager:
    """Prepare, verify and open persistent offline scene regions."""

    def __init__(
        self,
        *,
        root: Path,
        scene_root: Path,
        downloader=None,
        tile_bundle_builder=None,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.scene_root = Path(scene_root).resolve()
        self.downloader = downloader
        self.tile_bundle_builder = tile_bundle_builder or ensure_tile_bundle
        self.settings_path = self.root / "settings.json"
        self._lock = threading.Lock()
        self._jobs: dict[str, OfflineJob] = {}
        self._active_job_by_region: dict[str, str] = {}
        self._settings = self._load_settings()

    # ---------------------------------------------------------------- settings
    def _load_settings(self) -> dict:
        fallback = {
            "schema_version": OFFLINE_SCHEMA_VERSION,
            "mode": "automatic",
            "active_region": None,
        }
        payload = _json(self.settings_path)
        if payload is None:
            return fallback
        mode = payload.get("mode")
        active = payload.get("active_region")
        return {
            "schema_version": OFFLINE_SCHEMA_VERSION,
            "mode": mode if mode in CONNECTIVITY_MODES else "automatic",
            "active_region": active if isinstance(active, str) else None,
        }

    @property
    def mode(self) -> str:
        return str(self._settings["mode"])

    @property
    def active_region_id(self) -> str | None:
        value = self._settings.get("active_region")
        return value if isinstance(value, str) else None

    def set_mode(self, mode: object) -> str:
        if mode not in CONNECTIVITY_MODES:
            raise OfflineRegionError("Connectivity mode must be online, offline or automatic")
        with self._lock:
            self._settings["mode"] = mode
            _write_json_atomic(self.settings_path, self._settings)
        return mode

    def set_active_region(self, region_id: str | None) -> None:
        with self._lock:
            self._settings["active_region"] = region_id
            _write_json_atomic(self.settings_path, self._settings)

    # ------------------------------------------------------------------ paths
    def region_dir(self, region_id: str) -> Path:
        if REGION_ID_PATTERN.fullmatch(str(region_id)) is None:
            raise OfflineRegionError(f"Invalid offline region id: {region_id}")
        return self.root / str(region_id)

    def scene_dir(self, region_id: str) -> Path:
        return self.region_dir(region_id) / "scene"

    # -------------------------------------------------------------- inventory
    def list_regions(self) -> list[RegionRecord]:
        records: list[RegionRecord] = []
        for entry in sorted(self.root.iterdir()):
            if not entry.is_dir() or REGION_ID_PATTERN.fullmatch(entry.name) is None:
                continue
            record = self._read_region(entry.name)
            if record is not None:
                records.append(record)
        records.sort(key=lambda record: record.updated_at, reverse=True)
        return records

    def get_region(self, region_id: str) -> RegionRecord | None:
        return self._read_region(region_id)

    def _read_region(self, region_id: str) -> RegionRecord | None:
        directory = self.region_dir(region_id)
        manifest_path = directory / "region.json"
        state_path = directory / "state.json"
        manifest = _json(manifest_path)
        state = _json(state_path) or {}
        if manifest is None and not state:
            return None
        source = manifest or state
        assets = manifest.get("assets", []) if manifest else []
        missing = 0
        if manifest and manifest.get("status") == "ready":
            for asset in assets:
                candidate = directory / str(asset.get("path", ""))
                if not candidate.is_file() or candidate.stat().st_size != int(asset.get("bytes", -1)):
                    missing += 1
        status = str(source.get("status") or "partial")
        if missing and status == "ready":
            status = "missing"
        active_job = self._active_job_for(region_id)
        if active_job is not None:
            status = active_job.status
        basemap = (directory / "basemap.png").is_file()
        return RegionRecord(
            region_id=region_id,
            name=str(source.get("name") or region_id),
            status=status,
            tile_ids=tuple(str(item) for item in source.get("tile_ids", [])),
            bounds=_bounds_valid(source.get("bounds")) or {
                "west": -180.0,
                "east": 180.0,
                "south": -90.0,
                "north": 90.0,
            },
            map_bounds=_bounds_valid(source.get("map_bounds"))
            or _bounds_valid(source.get("bounds"))
            or {
                "west": -180.0,
                "east": 180.0,
                "south": -90.0,
                "north": 90.0,
            },
            layers=tuple(str(item) for item in source.get("layers", [])),
            created_at=str(source.get("created_at") or ""),
            updated_at=str(source.get("updated_at") or ""),
            bytes_total=int(source.get("bytes_total") or 0),
            asset_count=int(source.get("asset_count") or len(assets)),
            missing_count=missing,
            basemap=basemap,
            directory=directory,
            manifest=manifest,
            progress=float(active_job.progress if active_job else (source.get("progress") or 0.0)),
            message=str(active_job.message if active_job else (source.get("message") or "")),
            error=(active_job.error if active_job else source.get("error")),
        )

    # ---------------------------------------------------------------- estimate
    def estimate(self, payload: dict) -> dict:
        tile_ids = self._resolve_tile_ids(payload)
        bounds = self._region_bounds(payload, tile_ids)
        sizes = self._tile_sizes(tile_ids)
        total = sum(sizes.values())
        return {
            "ok": True,
            "tile_ids": list(tile_ids),
            "tile_count": len(tile_ids),
            "bounds": bounds,
            "bytes": total,
            "tiles": [{"tile_id": tile_id, "bytes": sizes[tile_id]} for tile_id in tile_ids],
            "ready_megabytes": round(total / (1024 * 1024), 2),
        }

    def _tile_sizes(self, tile_ids: tuple[str, ...]) -> dict[str, int]:
        sizes: dict[str, int] = {}
        for tile_id in tile_ids:
            total = 0
            tile_xml = self.scene_root / TILE_SCENE_RELATIVE_DIR / f"{tile_id}.xml"
            if tile_xml.is_file():
                total += tile_xml.stat().st_size
            mesh_root = self.scene_root / "meshes" / tile_id
            if mesh_root.is_dir():
                for path in mesh_root.rglob("*"):
                    if path.is_file():
                        total += path.stat().st_size
            bundle_root = self.scene_root / "cache" / "render_bundles" / tile_id
            if bundle_root.is_dir():
                for path in bundle_root.rglob("*"):
                    if path.is_file():
                        total += path.stat().st_size
            sizes[tile_id] = total
        return sizes

    # ----------------------------------------------------------- tile helpers
    def _resolve_tile_ids(self, payload: dict) -> tuple[str, ...]:
        explicit = payload.get("tile_ids")
        coverage = load_scene_coverage(self.scene_root)
        coverage_tiles = {
            str(tile.get("id")): tile
            for tile in coverage.get("tiles", [])
            if isinstance(tile, dict) and tile.get("id")
        }
        scene_source = load_tile_scene_xml_source(self.scene_root)
        available = scene_source.available_tile_ids
        if isinstance(explicit, list) and explicit:
            requested = tuple(sorted({str(item).strip() for item in explicit if str(item).strip()}))
            unknown = [tile_id for tile_id in requested if tile_id not in available]
            if unknown:
                raise OfflineRegionError(
                    "These tiles are not present in the local scene: " + ", ".join(unknown[:8])
                )
            return requested
        bounds = _bounds_valid(payload.get("bounds"))
        if bounds is None:
            raise OfflineRegionError("Provide tile_ids or valid geographic bounds")
        matched: list[str] = []
        for tile_id, tile in coverage_tiles.items():
            if tile_id not in available:
                continue
            tile_bounds = _bounds_from_corners(tile.get("wgs84_corners"))
            if tile_bounds is not None and _bounds_intersect(tile_bounds, bounds):
                matched.append(tile_id)
        if not matched:
            raise OfflineRegionError(
                "No loaded scene tiles intersect this area. Select a different area or load the tiles first."
            )
        return tuple(sorted(matched))

    def _region_bounds(self, payload: dict, tile_ids: tuple[str, ...]) -> dict:
        bounds = _bounds_valid(payload.get("bounds"))
        if bounds is not None:
            return bounds
        coverage = load_scene_coverage(self.scene_root)
        values = [
            _bounds_from_corners(tile.get("wgs84_corners"))
            for tile in coverage.get("tiles", [])
            if isinstance(tile, dict) and str(tile.get("id")) in tile_ids
        ]
        valid = [value for value in values if value is not None]
        if not valid:
            metadata = load_scene_metadata(self.scene_root)
            metadata_bounds = _bounds_valid(metadata.get("bounds_wgs84"))
            if metadata_bounds is not None:
                return metadata_bounds
            return {"west": -180.0, "east": 180.0, "south": -90.0, "north": 90.0}
        return {
            "west": min(value["west"] for value in valid),
            "east": max(value["east"] for value in valid),
            "south": min(value["south"] for value in valid),
            "north": max(value["north"] for value in valid),
        }

    def _tile_union_bounds(self, tile_ids: tuple[str, ...]) -> dict | None:
        coverage = load_scene_coverage(self.scene_root)
        values = [
            _bounds_from_corners(tile.get("wgs84_corners"))
            for tile in coverage.get("tiles", [])
            if isinstance(tile, dict) and str(tile.get("id")) in tile_ids
        ]
        valid = [value for value in values if value is not None]
        if not valid:
            return None
        return {
            "west": min(value["west"] for value in valid),
            "east": max(value["east"] for value in valid),
            "south": min(value["south"] for value in valid),
            "north": max(value["north"] for value in valid),
        }

    # ---------------------------------------------------------------- job API
    def _active_job_for(self, region_id: str) -> OfflineJob | None:
        job_id = self._active_job_by_region.get(region_id)
        if job_id is None:
            return None
        job = self._jobs.get(job_id)
        if job is None or job.status not in ACTIVE_STATUSES:
            return None
        return job

    def create_region(self, payload: dict, *, region_id: str | None = None, resume: bool = False) -> OfflineJob:
        tile_ids = self._resolve_tile_ids(payload)
        bounds = self._region_bounds(payload, tile_ids)
        map_bounds = self._tile_union_bounds(tile_ids) or bounds
        name = str(payload.get("name") or "Offline region").strip()[:80] or "Offline region"
        layers = payload.get("layers")
        if not isinstance(layers, list) or not layers:
            layers = ["buildings", "terrain", "map"]
        normalized_layers = tuple(sorted({str(item) for item in layers if str(item)}))
        target_id = region_id or f"{_slug(name)}-{uuid4().hex[:8]}"
        with self._lock:
            active = self._active_job_for(target_id)
            if active is not None:
                raise OfflineRegionBusy(target_id, active.job_id)
            existing = self._read_region(target_id) if (self.region_dir(target_id) / "region.json").exists() else None
            if existing is not None and existing.status == "ready" and not resume:
                raise OfflineRegionError(f"Offline region already exists: {target_id}")
            job = OfflineJob(
                job_id=f"offline_{uuid4().hex[:12]}",
                region_id=target_id,
                status="queued",
                message="Queued offline scene preparation",
            )
            self._jobs[job.job_id] = job
            self._active_job_by_region[target_id] = job.job_id
        metadata = {
            "schema_version": OFFLINE_SCHEMA_VERSION,
            "region_id": target_id,
            "name": name,
            "status": "preparing",
            "tile_ids": list(tile_ids),
            "bounds": bounds,
            "map_bounds": map_bounds,
            "layers": list(normalized_layers),
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "bytes_total": 0,
            "asset_count": 0,
            "progress": 0.0,
            "message": "Preparing offline scene",
            "error": None,
        }
        directory = self.region_dir(target_id)
        if not resume or not (directory / "state.json").exists():
            _write_json_atomic(directory / "state.json", metadata)
        thread = threading.Thread(
            target=self._run_prepare,
            args=(job.job_id, target_id, name, tile_ids, bounds, map_bounds, normalized_layers),
            daemon=True,
        )
        thread.start()
        return job

    def get_job(self, job_id: str) -> OfflineJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel_region(self, region_id: str) -> OfflineJob:
        with self._lock:
            job = self._active_job_for(region_id)
            if job is None:
                raise OfflineRegionError(f"No active preparation for {region_id}")
            job.cancel_event.set()
            job.status = "cancelling"
            job.message = "Cancelling; partial data is kept for a safe retry"
            job.updated_at = datetime.now(timezone.utc)
            return job

    def _update_job(self, job_id: str, **fields) -> OfflineJob | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            for key, value in fields.items():
                setattr(job, key, value)
            job.updated_at = datetime.now(timezone.utc)
            return job

    def _report(self, job_id: str, progress: float, message: str) -> None:
        self._update_job(job_id, status="preparing", progress=progress, message=message)

    def _write_state(self, region_id: str, **patch) -> None:
        state_path = self.region_dir(region_id) / "state.json"
        current = _json(state_path) or {
            "schema_version": OFFLINE_SCHEMA_VERSION,
            "region_id": region_id,
            "created_at": _utc_now(),
        }
        current.update(patch)
        current["updated_at"] = _utc_now()
        _write_json_atomic(state_path, current)

    # ------------------------------------------------------------ preparation
    def _download_tile_if_needed(self, region_id: str, tile_id: str, job: OfflineJob) -> None:
        tile_xml = self.scene_root / TILE_SCENE_RELATIVE_DIR / f"{tile_id}.xml"
        if tile_xml.is_file():
            return
        if self.mode == "offline":
            raise OfflineRegionError(
                f"Tile {tile_id} is not stored locally and strict offline mode blocks remote downloads"
            )
        if self.downloader is None:
            raise OfflineRegionError(
                f"Tile {tile_id} is missing locally and no downloader is configured"
            )
        self._update_job(job.job_id, message=f"Downloading tile {tile_id}")
        result = self.downloader(
            tile_id,
            progress_cb=lambda progress, message: self._report(
                job.job_id, 0.05 + progress * 0.35, f"{tile_id}: {message}"
            ),
            cancel_check=job.cancel_event.is_set,
        )
        if not tile_xml.is_file() and isinstance(result, dict) and result.get("tile_xml"):
            candidate = Path(str(result["tile_xml"]))
            if candidate.is_file():
                return

    def _run_prepare(
        self,
        job_id: str,
        region_id: str,
        name: str,
        tile_ids: tuple[str, ...],
        bounds: dict,
        map_bounds: dict,
        layers: tuple[str, ...],
    ) -> None:
        job = self._update_job(job_id, status="preparing", progress=0.01, message="Preparing")
        if job is None:
            return
        directory = self.region_dir(region_id)
        scene_target = self.scene_dir(region_id)
        try:
            if self.mode == "offline":
                for tile_id in tile_ids:
                    if not (self.scene_root / TILE_SCENE_RELATIVE_DIR / f"{tile_id}.xml").is_file():
                        raise OfflineRegionError(
                            f"Strict offline mode: tile {tile_id} is not available locally"
                        )
            scene_target.mkdir(parents=True, exist_ok=True)
            (scene_target / TILE_SCENE_RELATIVE_DIR).mkdir(parents=True, exist_ok=True)
            (scene_target / "meshes").mkdir(parents=True, exist_ok=True)
            (scene_target / "cache" / "render_bundles").mkdir(parents=True, exist_ok=True)
            (scene_target / COMMON_SCENE_RELATIVE_PATH).parent.mkdir(parents=True, exist_ok=True)
            if job.cancel_event.is_set():
                raise OfflineRegionError("Cancelled")

            self._report(job_id, 0.03, "Copying common scene definition")
            common = self.scene_root / COMMON_SCENE_RELATIVE_PATH
            if not common.is_file():
                raise OfflineRegionError("The source scene has no common scene definition")
            _copy_or_link(common, scene_target / COMMON_SCENE_RELATIVE_PATH)

            metadata = load_scene_metadata(self.scene_root)
            if metadata:
                _write_json_atomic(scene_target / SCENE_METADATA_FILENAME, metadata)

            total_tiles = max(1, len(tile_ids))
            for index, tile_id in enumerate(tile_ids):
                if job.cancel_event.is_set():
                    raise OfflineRegionError("Cancelled")
                base = 0.05 + (index / total_tiles) * 0.65
                self._report(job_id, base, f"Preparing {tile_id} ({index + 1}/{total_tiles})")
                self._download_tile_if_needed(region_id, tile_id, job)
                tile_xml = self.scene_root / TILE_SCENE_RELATIVE_DIR / f"{tile_id}.xml"
                if not tile_xml.is_file():
                    raise OfflineRegionError(f"Tile XML is missing after download: {tile_id}")
                _copy_or_link(tile_xml, scene_target / TILE_SCENE_RELATIVE_DIR / tile_xml.name)

                mesh_source = self.scene_root / "meshes" / tile_id
                if not mesh_source.is_dir():
                    raise OfflineRegionError(f"Tile {tile_id} has no mesh directory")
                mesh_target = scene_target / "meshes" / tile_id
                mesh_target.mkdir(parents=True, exist_ok=True)
                for mesh_path in mesh_source.rglob("*"):
                    if job.cancel_event.is_set():
                        raise OfflineRegionError("Cancelled")
                    if mesh_path.is_file():
                        relative = mesh_path.relative_to(mesh_source)
                        _copy_or_link(mesh_path, mesh_target / relative)

                manifest = load_scene_manifest(self.scene_root)
                bundles = [bundle for bundle in manifest.bundles if bundle.tile == tile_id]
                for bundle in bundles:
                    if job.cancel_event.is_set():
                        raise OfflineRegionError("Cancelled")
                    result = self.tile_bundle_builder(self.scene_root, bundle)
                    if not result.bundle_path.is_file():
                        raise OfflineRegionError(
                            f"Render bundle was not produced for {tile_id}: {bundle.bundle_id}"
                        )
                    _copy_or_link(
                        result.bundle_path,
                        scene_target / bundle.relative_path,
                    )

            self._report(job_id, 0.74, "Writing region coverage")
            coverage = load_scene_coverage(self.scene_root)
            selected = [
                tile for tile in coverage.get("tiles", [])
                if isinstance(tile, dict) and str(tile.get("id")) in tile_ids
            ]
            _write_json_atomic(
                scene_target / SCENE_COVERAGE_FILENAME,
                {
                    "schema_version": coverage.get("schema_version", 1),
                    "scene_id": f"{coverage.get('scene_id', 'scene')}_{region_id}",
                    "label": f"{name} (offline)",
                    "source_crs": coverage.get("source_crs"),
                    "tile_size_m": coverage.get("tile_size_m"),
                    "tile_count": len(selected),
                    "tiles": selected,
                },
            )

            if job.cancel_event.is_set():
                raise OfflineRegionError("Cancelled")
            self._update_job(job_id, status="verifying", progress=0.8, message="Verifying downloaded assets")
            assets = self._collect_assets(scene_target)
            if not assets:
                raise OfflineRegionError("No scene assets were prepared")
            bytes_total = sum(int(asset["bytes"]) for asset in assets)
            now = _utc_now()
            manifest_payload = {
                "schema_version": OFFLINE_SCHEMA_VERSION,
                "region_id": region_id,
                "name": name,
                "status": "ready",
                "tile_ids": list(tile_ids),
                "bounds": bounds,
                "map_bounds": map_bounds,
                "layers": list(layers),
                "created_at": now,
                "updated_at": now,
                "bytes_total": bytes_total,
                "asset_count": len(assets),
                "progress": 1.0,
                "message": "Ready for offline use",
                "error": None,
                "attribution": ATTRIBUTION,
                "source": {
                    "scene_root": str(self.scene_root),
                    "scene_metadata": metadata,
                    "coverage_url": "/api/scene/coverage",
                },
                "assets": assets,
            }
            _write_json_atomic(directory / "region.json", manifest_payload)
            self._write_state(
                region_id,
                status="ready",
                bytes_total=bytes_total,
                asset_count=len(assets),
                progress=1.0,
                message="Ready for offline use",
                error=None,
            )
            self._update_job(job_id, status="ready", progress=1.0, message="Ready for offline use")
        except OfflineRegionError as exc:
            cancelled = job.cancel_event.is_set() or str(exc) == "Cancelled"
            message = "Cancelled; partial data kept for resume" if cancelled else str(exc)
            self._write_state(
                region_id,
                status="partial" if cancelled else "failed",
                message=message,
                error=None if cancelled else str(exc),
            )
            self._update_job(
                job_id,
                status="cancelled" if cancelled else "failed",
                progress=1.0,
                message=message,
                error=None if cancelled else str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            traceback.print_exception(type(exc), exc, exc.__traceback__)
            self._write_state(
                region_id,
                status="failed",
                message="Offline preparation failed",
                error=str(exc),
            )
            self._update_job(
                job_id,
                status="failed",
                progress=1.0,
                message="Offline preparation failed",
                error=str(exc),
            )
        finally:
            with self._lock:
                if self._active_job_by_region.get(region_id) == job_id:
                    self._active_job_by_region.pop(region_id, None)

    def _collect_assets(self, scene_target: Path) -> list[dict]:
        assets: list[dict] = []
        for path in sorted(scene_target.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(scene_target).as_posix()
            assets.append(
                {
                    "path": f"scene/{relative}",
                    "bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
        return assets

    # ------------------------------------------------------------ open/verify
    def verify_region(self, region_id: str) -> RegionRecord:
        record = self._read_region(region_id)
        if record is None or record.manifest is None:
            raise OfflineRegionError(f"Offline region is incomplete: {region_id}")
        directory = record.directory
        missing: list[str] = []
        mismatched: list[str] = []
        for asset in record.manifest.get("assets", []):
            path = directory / str(asset.get("path", ""))
            if not path.is_file():
                missing.append(str(asset.get("path")))
                continue
            if path.stat().st_size != int(asset.get("bytes", -1)):
                mismatched.append(str(asset.get("path")))
                continue
            if _sha256_file(path) != asset.get("sha256"):
                mismatched.append(str(asset.get("path")))
        if missing or mismatched:
            raise OfflineRegionError(
                "Offline region verification failed: "
                f"{len(missing)} missing and {len(mismatched)} corrupted assets"
            )
        return record

    def open_region(self, region_id: str) -> RegionRecord:
        record = self.verify_region(region_id)
        self.set_active_region(region_id)
        return record

    def deactivate_region(self) -> None:
        self.set_active_region(None)
        for record in self.list_regions():
            if record.status == "ready":
                continue

    def delete_region(self, region_id: str) -> None:
        with self._lock:
            active = self._active_job_for(region_id)
            if active is not None:
                active.cancel_event.set()
        if self.active_region_id == region_id:
            self.set_active_region(None)
        directory = self.region_dir(region_id)
        if directory.exists():
            shutil.rmtree(directory)
        with self._lock:
            self._active_job_by_region.pop(region_id, None)

    def startup_region(self) -> tuple[RegionRecord, Path] | None:
        region_id = self.active_region_id
        if not region_id:
            return None
        try:
            record = self._read_region(region_id)
        except OfflineRegionError:
            return None
        if record is None or record.status != "ready" or record.missing_count:
            return None
        return record, self.scene_dir(region_id)

    # ---------------------------------------------------------------- basemap
    def save_basemap(self, region_id: str, payload: dict) -> RegionRecord:
        record = self._read_region(region_id)
        if record is None:
            raise OfflineRegionError(f"Unknown offline region: {region_id}")
        if record.status != "ready":
            raise OfflineRegionError("The offline region is not ready yet")
        encoded = payload.get("png_base64")
        if not isinstance(encoded, str) or not encoded:
            raise OfflineRegionError("A base64 PNG payload is required")
        try:
            data = base64.b64decode(encoded.split(",", 1)[-1], validate=True)
        except Exception as exc:  # noqa: BLE001
            raise OfflineRegionError("Invalid base64 image payload") from exc
        if len(data) > BASEMAP_MAX_BYTES:
            raise OfflineRegionError("The basemap snapshot exceeds the 8 MB limit")
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise OfflineRegionError("The basemap snapshot must be a PNG image")
        directory = record.directory
        target = directory / "basemap.png"
        temp = directory / f"basemap.{uuid4().hex}.tmp"
        temp.write_bytes(data)
        temp.replace(target)
        manifest = record.manifest or {}
        assets = [
            asset for asset in manifest.get("assets", [])
            if not str(asset.get("path", "")).startswith("basemap")
        ]
        assets.append(
            {
                "path": "basemap.png",
                "bytes": target.stat().st_size,
                "sha256": _sha256_file(target),
            }
        )
        manifest["assets"] = assets
        manifest["basemap"] = {
            "path": "basemap.png",
            "bounds": record.map_bounds,
            "attribution": ATTRIBUTION["basemap"],
        }
        manifest["updated_at"] = _utc_now()
        _write_json_atomic(directory / "region.json", manifest)
        self._write_state(region_id, basemap=True, updated_at=_utc_now())
        updated = self._read_region(region_id)
        if updated is None:
            raise OfflineRegionError("Could not read the updated region")
        return updated

    def basemap_path(self, region_id: str) -> Path:
        path = self.region_dir(region_id) / "basemap.png"
        if not path.is_file():
            raise OfflineRegionError(f"No basemap snapshot is stored for {region_id}")
        return path

    # ----------------------------------------------------------------- status
    def storage_summary(self) -> dict:
        regions = self.list_regions()
        total = sum(region.bytes_total for region in regions)
        subdirectories: dict[str, int] = {}
        for region in regions:
            subdirectories[region.region_id] = region.bytes_total
        usage = shutil.disk_usage(self.root)
        return {
            "storage_root": str(self.root),
            "scene_root": str(self.scene_root),
            "total_bytes": total,
            "free_bytes": int(usage.free),
            "disk_total_bytes": int(usage.total),
            "region_count": len(regions),
            "regions": subdirectories,
            "active_region": self.active_region_id,
            "mode": self.mode,
            "modes": sorted(CONNECTIVITY_MODES),
            "attribution": ATTRIBUTION,
        }
