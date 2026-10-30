from __future__ import annotations

from backend import config
from backend.rt.common import (
    parse_bounded_float,
    parse_bounded_int,
    parse_object,
    parse_vector,
)


PMNET_SCHEMA_VERSION = 1
SUPPORTED_PMNET_ENGINES = ("auto", "checkpoint", "physics")
DEFAULT_PMNET_ENGINE = "auto"
MAX_PMNET_CHECKPOINT_LOSS_DB = 1000.0


def _parse_engine(value: object) -> str:
    if value is None:
        return DEFAULT_PMNET_ENGINE
    if not isinstance(value, str):
        raise ValueError("model.engine must be a string")
    normalized = value.strip().lower()
    if normalized not in SUPPORTED_PMNET_ENGINES:
        raise ValueError(
            "model.engine must be one of: " + ", ".join(SUPPORTED_PMNET_ENGINES)
        )
    return normalized


def _parse_optional_text(value: object, *, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string or null")
    text = value.strip()
    return text or None


def parse_pmnet_payload(payload: dict) -> dict:
    """Validate one ``/api/pmnet/jobs`` request body."""

    payload = parse_object(payload, name="payload")
    tx = parse_object(payload.get("tx", {}), name="tx")
    roi = parse_object(payload.get("roi", {}), name="roi")
    solver = parse_object(payload.get("solver", {}), name="solver")
    model = parse_object(payload.get("model", {}), name="model")
    signal = parse_object(payload.get("signal", {}), name="signal")

    size = parse_vector(
        roi.get("size", config.DEFAULT_PMNET_ROI_SIZE),
        size=2,
        name="roi.size",
    )
    for axis, value in zip(("x", "y"), size):
        if value < config.MIN_PMNET_ROI_SIZE_M or value > config.MAX_PMNET_ROI_SIZE_M:
            raise ValueError(
                f"roi.size {axis} must be between {config.MIN_PMNET_ROI_SIZE_M:g} "
                f"and {config.MAX_PMNET_ROI_SIZE_M:g} meters"
            )

    grid = parse_bounded_int(
        roi.get("grid", config.DEFAULT_PMNET_GRID_CELLS),
        name="roi.grid",
        min_value=config.MIN_PMNET_GRID_CELLS,
        max_value=config.MAX_PMNET_GRID_CELLS,
    )
    if grid * grid > config.MAX_PMNET_CELLS:
        raise ValueError(
            f"roi.grid of {grid} creates {grid * grid} cells, which exceeds the "
            f"configured limit of {config.MAX_PMNET_CELLS}"
        )

    height_offset = parse_bounded_float(
        roi.get("height_offset", config.DEFAULT_PMNET_HEIGHT_OFFSET),
        name="roi.height_offset",
        min_value=0.0,
    )
    if height_offset > config.MAX_PMNET_RX_HEIGHT_M:
        raise ValueError(
            "roi.height_offset must be at most "
            f"{config.MAX_PMNET_RX_HEIGHT_M:g} meters"
        )

    min_loss_db = parse_bounded_float(
        model.get("checkpoint_min_loss_db", config.PMNET_DEFAULT_MIN_LOSS_DB),
        name="model.checkpoint_min_loss_db",
        min_value=0.0,
        max_value=MAX_PMNET_CHECKPOINT_LOSS_DB,
    )
    max_loss_db = parse_bounded_float(
        model.get("checkpoint_max_loss_db", config.PMNET_DEFAULT_MAX_LOSS_DB),
        name="model.checkpoint_max_loss_db",
        min_value=0.0,
        max_value=MAX_PMNET_CHECKPOINT_LOSS_DB,
    )
    if max_loss_db - min_loss_db < 1.0:
        raise ValueError(
            "model.checkpoint_max_loss_db must exceed model.checkpoint_min_loss_db "
            "by at least 1 dB"
        )

    return {
        "tx_position": parse_vector(
            tx.get("position", config.DEFAULT_TX_POSITION),
            size=3,
            name="tx.position",
        ),
        "tx_orientation": parse_vector(
            tx.get("orientation", (0.0, 0.0, 0.0)),
            size=3,
            name="tx.orientation",
        ),
        "roi_size": (float(size[0]), float(size[1])),
        "grid_cells": int(grid),
        "height_offset": float(height_offset),
        "frequency_hz": parse_bounded_float(
            solver.get("frequency_hz", config.DEFAULT_FREQUENCY_HZ),
            name="solver.frequency_hz",
            min_value=config.MIN_FREQUENCY_HZ,
            max_value=config.MAX_FREQUENCY_HZ,
        ),
        "engine_preference": _parse_engine(model.get("engine")),
        "checkpoint_path": _parse_optional_text(
            model.get("checkpoint"), name="model.checkpoint"
        ),
        "wall_loss_db": parse_bounded_float(
            model.get("wall_loss_db", config.DEFAULT_PMNET_WALL_LOSS_DB),
            name="model.wall_loss_db",
            min_value=config.MIN_PMNET_WALL_LOSS_DB,
            max_value=config.MAX_PMNET_WALL_LOSS_DB,
        ),
        "checkpoint_min_loss_db": min_loss_db,
        "checkpoint_max_loss_db": max_loss_db,
        "tx_power_dbm": parse_bounded_float(
            signal.get("tx_power_dbm", config.DEFAULT_PMNET_TX_POWER_DBM),
            name="signal.tx_power_dbm",
            min_value=config.MIN_PMNET_TX_POWER_DBM,
            max_value=config.MAX_PMNET_TX_POWER_DBM,
        ),
    }
