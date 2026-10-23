from __future__ import annotations

import json
from pathlib import Path

from backend.scene.open3dhk_coverage import OPEN3DHK_COVERAGE_PATH


SCENE_METADATA_FILENAME = "scene_metadata.json"
SCENE_COVERAGE_FILENAME = "coverage.json"


def load_scene_metadata(scene_root: Path) -> dict:
    """Read optional scene metadata written by the GeoJSON importer."""

    path = Path(scene_root) / SCENE_METADATA_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_json_object(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def load_scene_coverage(scene_root: Path) -> dict:
    """Return the scene's tile coverage, falling back to bundled Open3DHK data."""

    payload = _load_json_object(Path(scene_root) / SCENE_COVERAGE_FILENAME)
    if payload is not None and isinstance(payload.get("tiles"), list):
        return payload
    fallback = _load_json_object(OPEN3DHK_COVERAGE_PATH)
    if fallback is None:
        return {"tile_count": 0, "tiles": []}
    return fallback
