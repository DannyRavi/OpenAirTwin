from __future__ import annotations

import argparse
from pathlib import Path

from backend import config
from backend.scene.geojson_buildings import (
    DEFAULT_CENTER_LAT,
    DEFAULT_CENTER_LON,
    DEFAULT_GROUND_CELL_SIZE_M,
    DEFAULT_SCENE_ID,
    DEFAULT_SCENE_LABEL,
    DEFAULT_TILE_SIZE_M,
    GeoJsonBuildOptions,
    import_geojson_buildings,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build an OpenAirTwin / Sionna RT scene from a building GeoJSON "
            "FeatureCollection expressed in EPSG:3857."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("gba/tehran-building-formatted.geojson"),
        help="Input GeoJSON FeatureCollection (default: %(default)s)",
    )
    parser.add_argument(
        "--scene-root",
        type=Path,
        default=config.SCENE_ROOT,
        help="Scene output root (default: OAT_SCENE_ROOT or <repo>/scene)",
    )
    parser.add_argument(
        "--tile-size-m",
        type=float,
        default=DEFAULT_TILE_SIZE_M,
        help="Square tile size in true metres (default: %(default)s)",
    )
    parser.add_argument(
        "--center-lon",
        type=float,
        default=DEFAULT_CENTER_LON,
        help="Longitude of the local scene origin (default: %(default)s)",
    )
    parser.add_argument(
        "--center-lat",
        type=float,
        default=DEFAULT_CENTER_LAT,
        help="Latitude of the local scene origin (default: %(default)s)",
    )
    parser.add_argument("--scene-id", default=DEFAULT_SCENE_ID, help="Scene identifier (default: %(default)s)")
    parser.add_argument("--label", default=DEFAULT_SCENE_LABEL, help="Human-readable scene label (default: %(default)s)")
    parser.add_argument(
        "--max-features",
        type=int,
        default=None,
        help="Only import the first N features (useful for smoke tests)",
    )
    parser.add_argument(
        "--ground-cell-size-m",
        type=float,
        default=DEFAULT_GROUND_CELL_SIZE_M,
        help=(
            "Ground mesh cell size in metres; 0 writes one quad per tile "
            "(default: %(default)s)"
        ),
    )
    parser.add_argument("--replace", action="store_true", help="Replace an existing generated scene")
    parser.add_argument("--no-ground", action="store_true", help="Do not write ground meshes")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    options = GeoJsonBuildOptions(
        input_path=args.input,
        scene_root=args.scene_root,
        tile_size_m=args.tile_size_m,
        center_lon=args.center_lon,
        center_lat=args.center_lat,
        scene_id=args.scene_id,
        scene_label=args.label,
        replace=args.replace,
        include_ground=not args.no_ground,
        ground_cell_size_m=args.ground_cell_size_m,
        max_features=args.max_features,
    )

    def progress(fraction: float, message: str) -> None:
        print(f"{fraction * 100:6.2f}%  {message}", flush=True)

    result = import_geojson_buildings(options, progress_cb=progress)
    print()
    print(f"Scene root      : {result.scene_root}")
    print(f"Scene id        : {result.scene_id}")
    print(f"Tiles           : {result.tile_count:,}")
    print(f"Buildings       : {result.building_count:,}")
    print(f"Vertices        : {result.vertex_count:,}")
    print(f"Triangles       : {result.triangle_count:,}")
    print(f"Skipped features: {result.skipped_count:,}")
    print(f"Default tiles   : {', '.join(result.default_tile_ids)}")


if __name__ == "__main__":
    main()
