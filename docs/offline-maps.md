# Offline Maps and Persistent 3D Scenes

Hammurabi can persist a geographic area and reopen its buildings, terrain and
render bundles after a full application restart without internet access. This
document describes the storage model, the download pipeline, connectivity modes
and the limits of offline operation.

## What is stored

An offline region is a self-contained directory under the backend storage root
(default `generated/offline_regions/`, override with `OAT_OFFLINE_REGION_ROOT`):

```text
generated/offline_regions/<region-id>/
|-- region.json          # immutable verified manifest, written last
|-- state.json           # mutable progress/status, atomic writes
|-- basemap.png          # optional cached map snapshot for the region
`-- scene/
    |-- common/scene_common.xml
    |-- tiles/<tile-id>.xml
    |-- meshes/<tile-id>/<category>/*.ply
    |-- cache/render_bundles/<tile-id>/*.glb
    |-- coverage.json
    `-- scene_metadata.json
```

The manifest records the requested geographic bounds, the exact union of the
selected tile bounds used for map-image alignment (`map_bounds`), tile ids,
source metadata, attribution, asset byte sizes and SHA-256 digests. `region.json` is written only
after every asset has been copied and hashed. A region is reported as
`offline_ready` only when the manifest is present, the asset count is non-zero
and no asset is missing. A separate verification action re-hashes every asset.

The local Python backend is the persistent store. This is the appropriate
deployment model for Hammurabi: the browser talks to a local server, and the
scene cache is expected to be hundreds of megabytes. Duplicating it into
IndexedDB would double storage and still require the backend for the Sionna RT
scene. No temporary browser HTTP cache is relied on.

## Download pipeline

1. The user captures the current map viewport or enters a bounding box.
2. The backend intersects the bounds with `/api/scene/coverage` and selects the
   loaded scene tiles. There is no invented geometry for uncovered areas.
3. Each tile XML and its referenced PLY meshes are hard-linked when possible, or
   copied. Missing tiles are fetched through the existing tile downloader when
   online and the deployment supports it.
4. Render bundles are built with the existing `ensure_tile_bundle` pipeline and
   stored inside the region, so a later launch does not need to preprocess
   geometry again.
5. Assets are hashed and written into `region.json` atomically.
6. If the map layer was selected and the network is available, the workspace
   renders a map snapshot with MapLibre and uploads the PNG to the region.

Cancelling keeps `state.json` and partial files. The region is shown as
incomplete and can be retried; the manifest is never written for a partial
download. Deleting a region removes only its own directory.

## Connectivity modes

| Mode | Behaviour |
| --- | --- |
| Online | Remote providers are allowed. Downloads and updates are available. |
| Automatic | Stored regions and cached map snapshots are preferred; remote providers are used only when needed. |
| Strict offline | Only local regions are opened. The entry basemap is not requested, the 3D map underlay uses the cached region image only, and the backend rejects remote tile downloads. |

The mode is persisted in `settings.json` inside the offline storage root and is
read during startup. If a region was active when Hammurabi stopped, the backend
serves that region's scene root on the next launch. The workspace also reports
missing or corrupted assets instead of substituting an unrelated scene.

## Offline map requests

The 2D entry map normally uses OpenFreeMap Positron vector tiles. In strict
offline mode Hammurabi does not import that provider: it shows the stored region
snapshot, or the packaged local overview image when no snapshot exists. The 3D
decorative underlay likewise uses only the stored PNG. The actual 3D buildings,
terrain and RF scene are served by the local backend from the region snapshot.

Map snapshots are derived from OpenFreeMap/OpenMapTiles data and must retain
OpenStreetMap attribution. Tile-provider terms still apply; only download areas
where bulk caching is permitted.

## Saved view state

The 3D camera position and orbit target are stored in browser `localStorage` per
active offline region (or per scene id) when leaving the 3D workspace or the
page, and restored after the scene finishes loading. This is a UI preference,
not scene data; clearing site storage resets it.

## What works offline

These workflows run against the locally stored scene, as long as the local
Python/Sionna RT runtime is installed:

- Scene loading, camera navigation, picking and layer visibility.
- Link analysis, Mobility, Radio Map, Radar Sensing and DeepMIMO export when
  their runtime dependencies are present locally. These workflows share the
  active region's scene root; DeepMIMO workers receive it explicitly instead of
  the original scene library.
- Existing results, saved device/transmitter state and exports that use local
  files.

The following are not made offline-capable by a map download:

- Installing Python packages, CUDA/LLVM drivers or downloading a missing Sionna
  runtime.
- Nominatim place search, which is a remote geocoding service.
- Updating from a remote tile provider that is not stored locally.
- The tutorial website, unless it is separately hosted or cached.

## Local data import

Hammurabi does not accept arbitrary filesystem paths from the browser and does
not upload arbitrary archives. To use a different dataset offline, prepare it as
a normal Hammurabi scene with the existing tools (for example
`backend.tools.import_geojson_buildings`), then create an offline region from its
tiles. Unsupported formats are rejected rather than silently accepted.

## Storage management

The Offline Maps panel shows the storage root, total bytes, per-region size,
asset counts, verification state and cached-map state. Each region exposes Open,
Verify, Retry, Cache map and Delete actions. Deletion asks for confirmation and
removes only the selected region directory.

## Limitations

- Pause is not implemented. Running preparation can be cancelled; partial data
  is retained and "Retry" resumes the region safely.
- Existing ready regions cannot be updated in place. Delete and recreate the
  region to refresh its snapshot.
- Automatic mode prefers a cached region but does not subscribe to operating
  system connectivity events; mode changes are applied immediately and persist,
  while a changed network condition is handled lazily on the next request.
- The offline map underlay is a raster snapshot of the selected area, not a full
  vector tile archive. Panning far outside the snapshot shows the local fallback
  image; the 3D buildings and RF scene remain complete for the region.
- The browser application shell is served by the local Hammurabi backend. There
  is no service worker; if the local backend is not running, neither the shell
  nor the scene is available.
- The UI imports existing Hammurabi scene formats through region creation. It
  does not upload OSM PBF, GeoTIFF or MBTiles archives directly. Prepare those
  datasets with the repository tools first, then create a region from the
  resulting scene.
- Visual regression baselines were regenerated for Linux; the macOS baseline
  images in the repository still show the prior interface and need regeneration
  on macOS before cross-platform visual comparison.

## API summary

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/api/offline/status` | Mode, regions, active region and storage |
| POST | `/api/offline/mode` | Persist `online`, `automatic` or `offline` |
| POST | `/api/offline/estimate` | Size and tile estimate for bounds |
| POST | `/api/offline/regions` | Start a region preparation job |
| GET | `/api/offline/regions/{id}` | Region manifest and status |
| POST | `/api/offline/regions/{id}/cancel` | Cancel preparation |
| POST | `/api/offline/regions/{id}/resume` | Retry an incomplete region |
| POST | `/api/offline/regions/{id}/verify` | Re-hash every asset |
| POST | `/api/offline/regions/{id}/open` | Verify and make the region active |
| POST | `/api/offline/regions/{id}/delete` | Delete the region directory |
| POST | `/api/offline/regions/{id}/basemap` | Store a PNG map snapshot |
| GET | `/api/offline/regions/{id}/basemap.png` | Serve the cached map |
| POST | `/api/offline/deactivate` | Return to the source scene library |

All routes are local-only, like the rest of the Hammurabi backend. The backend
has no authentication; keep it bound to `127.0.0.1` unless it is placed behind an
authenticated reverse proxy.
