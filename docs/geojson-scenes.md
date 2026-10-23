# GeoJSON building scenes

`backend.tools.import_geojson_buildings` converts a building-footprint GeoJSON
FeatureCollection into a tile-based OpenAirTwin / Sionna RT scene. It is the same
format the rest of the application consumes, so no special runtime mode is
needed.

## Input

- A GeoJSON `FeatureCollection`.
- Polygon or MultiPolygon footprints in **EPSG:3857** (Web Mercator).
- An optional numeric `height` property in metres; missing or invalid values
  default to 3 m.

The importer multiplies Web Mercator deltas by `cos(latitude)` so tile sizes,
radio-map cells and device coordinates are true metres at the site.

## Command

```bash
python3 -m backend.tools.import_geojson_buildings \
  --input gba/tehran-building-formatted.geojson \
  --scene-root scene \
  --replace \
  --tile-size-m 1500 \
  --ground-cell-size-m 20
```

| Option | Meaning |
| --- | --- |
| `--input` | Input FeatureCollection |
| `--scene-root` | Output scene root (defaults to `OAT_SCENE_ROOT` / `scene`) |
| `--tile-size-m` | Square tile size in true metres |
| `--ground-cell-size-m` | Ground-mesh cell size; `0` writes one quad per tile |
| `--center-lon`, `--center-lat` | Local scene origin (default central Tehran) |
| `--scene-id`, `--label` | Metadata identity shown by the entry map |
| `--replace` | Replace an existing generated scene |
| `--max-features` | Import only the first N features (smoke tests) |
| `--no-ground` | Do not write ground meshes |

## Output

```text
scene/
|-- common/scene_common.xml
|-- tiles/<tile_id>.xml
|-- meshes/<tile_id>/BUILDING/<tile_id>_buildings.ply
|-- meshes/<tile_id>/TERRAIN_TB/<tile_id>_ground.ply
|-- coverage.json
`-- scene_metadata.json
```

- Each tile has at most one merged building mesh and one tessellated ground mesh.
- `coverage.json` is served by `GET /api/scene/coverage` and drives the entry
  map, including WGS84 corners for every tile.
- `scene_metadata.json` is included in `GET /api/scene/manifest` under `scene`.
  It carries the scene identity, bounds, default tile selection and search
  settings.

## Coordinates and defaults

The local scene origin is the configured `--center-lon/--center-lat`. Tile
boundaries are centred on that origin, so the default tile spans
`[-tile/2, +tile/2]` in both axes. The application's existing default devices
(`Link` Tx/Rx, `Radar` target) are local coordinates near `(72, 37, 40)` and
therefore fall inside the default tile.

The entry map selects the default tile automatically. Additional tiles can be
toggled on the map. Render bundles (`.glb`) and gzip copies are built lazily on
first request and cached under `scene/cache/render_bundles/`.

## 3D map underlay

The viewer places a decorative OpenFreeMap basemap snapshot under the selected
3D tiles. It lives in a separate Three.js group and is intentionally excluded
from the model group, so it is never ray-cast, never included in scene bounds or
performance face estimates, and never sent to Sionna RT. Pressing **Return to
3D** from the entry map reveals the underlay; it is generated asynchronously
after the selected tiles load. OpenFreeMap/OpenMapTiles/OpenStreetMap attribution
is shown in the top-right corner while the underlay is visible.

## Radio maps

Ground meshes are tessellated (`--ground-cell-size-m`, default 20 m) so the
radiomap terrain-patch builder can select and subdivide measurement cells around
the transmitter. A single tile-sized ground quad would force the `Auto` density
level to produce a nearly tile-sized measurement cell. Users can still set an
explicit `Cell Size` in the Radiomap controls.

## Large datasets

The Tehran file contains 524k footprints. Importing it produces roughly 690
tiles, 7M building vertices and 360 MB of scene assets. The viewer and Sionna RT
only load the selected tiles, so keep the default single-tile selection for
interactive work and add neighbours only when a workflow needs them.
