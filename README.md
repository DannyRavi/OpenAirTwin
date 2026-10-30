<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="backend/static/assets/hammurabi_logo_dark.png">
    <source media="(prefers-color-scheme: light)" srcset="backend/static/assets/hammurabi_logo.png">
    <img src="backend/static/assets/hammurabi_logo.png" alt="Hammurabi" width="620">
  </picture>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-green"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="Sionna RT" src="https://img.shields.io/badge/Sionna_RT-2.0.1-brightgreen">
  <img alt="React 19" src="https://img.shields.io/badge/React-19.2.8-61DAFB?logo=react&logoColor=black">
  <img alt="WebGL rendering" src="https://img.shields.io/badge/rendering-WebGL-4b8bbe">
  <a href="https://github.com/DannyRavi/OpenAirTwin/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/DannyRavi/OpenAirTwin/actions/workflows/ci.yml/badge.svg"></a>
</p>

<p align="center">
  <a href="https://hkuopensource.github.io/OpenAirTwin/">Tutorial</a> ·
  <a href="https://hkuopensource.github.io/OpenAirTwin/architecture/">Architecture map</a> ·
  <a href="docs/development.md">Development guide</a> ·
  <a href="CHANGELOG.md">Changelog</a> ·
  <a href="docs/data-licenses.md">Scene data notice</a> ·
  <a href="LICENSE">License</a>
</p>

Hammurabi is a professional 3D geospatial and RF simulation workspace for urban
digital twins, wireless network planning and radio propagation. This repository
is an independent derivative of the open-source
[OpenAirTwin](https://github.com/HKUOpenSource/OpenAirTwin) research platform.
It retains the Python/Sionna RT backend, the tile-based Three.js city viewer and
all existing Link, Mobility, Radio Map, DeepMIMO and Radar workflows, adds a
PMNet pathloss-map predictor that renders predictions over the map and its
buildings, and adds a map-first workspace plus persistent offline regions that
reopen without internet access.

<p align="center">
  <img src="backend/static/assets/hammurabi_workspace.png" alt="Hammurabi 3D geospatial workspace" width="100%">
</p>

> [!IMPORTANT]
> Hammurabi inherits OpenAirTwin's research-software security model. The server
> has no authentication and binds to `127.0.0.1` by default. Do not expose it to
> an untrusted network without an appropriate authenticated reverse proxy and
> additional hardening.

## Quick Start

### macOS or Linux

```bash
git clone https://github.com/DannyRavi/OpenAirTwin.git
cd Hammurabi
git checkout gba
python3 install.py --with-sample-scene
set -a; . ./.oat-env; set +a
./.venv/bin/python -m backend.server
```

### Windows PowerShell

```powershell
git clone https://github.com/DannyRavi/OpenAirTwin.git
Set-Location OpenAirTwin
git checkout gba
py -3.11 install.py --with-sample-scene
. .\.oat-env.ps1
.\.venv\Scripts\python.exe -m backend.server
```

The installer creates `.oat-env` on every platform and also creates
`.oat-env.ps1` on Windows. These files preserve the selected CPU/GPU and local
runtime configuration. Hammurabi keeps the `OAT_*` environment namespace for
backward compatibility with existing installations.

Open [http://127.0.0.1:8090](http://127.0.0.1:8090), then verify the
installation. The upstream tutorial remains useful for the shared workflows:
[OpenAirTwin tutorial](https://hkuopensource.github.io/OpenAirTwin/).

## Features

| Feature | Workflow | Main output |
| --- | --- | --- |
| Map and scene management | Select map tiles, download scene data, and load only the required city geometry | Incremental 3D scene |
| Offline maps | Define a geographic area, persist its tile snapshots, meshes, render bundles and map image, then reopen it after a restart with the network disabled | Self-contained offline region |
| Link analysis | Place Tx/Rx points and configure the shared RT solver | Paths, received power, channel taps, CIR and frequency response |
| Radar sensing | Place monostatic or bistatic Radar Tx/Rx, add moving drone targets, and configure OFDM, CA-CFAR and propagation parameters | Range–Doppler and range-profile plots, detections, target truth and classified propagation paths |
| Mobility | Define Rx waypoints, speed and sampling parameters | Trajectory, sample markers and time-varying channel results |
| Radio map | Configure a receiver patch, resolution and display range | Scalar heatmap over the 3D scene |
| PMNet pathloss | Pick a Tx, set the prediction window, grid and wall loss, and predict without path tracing | Ground pathloss heatmap plus a rooftop and facade building envelope |
| DeepMIMO export | Draw a receiver ROI and configure the receiver grid | Downloadable DeepMIMO-compatible dataset |


## Documentation

- [Interactive architecture map](docs/openairtwin-architecture.html):
  current frontend, transport, backend service, job and renderer relationships;
  its versioned source is [`docs/openairtwin-architecture.html`](docs/openairtwin-architecture.html).
- [Development guide](docs/development.md): repository structure, tests,
  architectural boundaries and the process for adding a Feature.
- [Changelog](CHANGELOG.md) and [release checklist](docs/release-checklist.md):
  the v1.1.0 release record, production gates and maintainer
  publication sequence.
- [PMNet pathloss maps](docs/pmnet.md): prediction engines, checkpoint setup,
  building extraction, request/result contracts and limitations.
- [Offline maps and persistent scenes](docs/offline-maps.md): storage layout,
  connectivity modes, download pipeline and what remains online-only.
- [Scene data and third-party terms](docs/data-licenses.md): Open3Dhk/CSDI data
  responsibilities, bundled sample-scene attribution and Radar drone-model
  licensing.
- [Interactive tutorial](https://hkuopensource.github.io/OpenAirTwin/):
  screenshot-led walkthroughs for all six workflows, with stable deep links
  and saved progress.

## Installation Details

### Requirements

- Python 3.11 or newer.
- A modern browser with WebGL support.
- A GPU-capable environment is recommended for practical Sionna RT workloads.
- CPU-only machines need LLVM for the Dr.Jit backend.

Sionna RT, Mitsuba, Dr.Jit, CUDA and GPU-driver compatibility are environment
specific. The installer creates or reuses `.venv/`, installs the pinned packages
from `requirements.txt`, writes local runtime configuration, and reports
system-level issues that still need action.

Run the interactive installer from the repository root:

```bash
python3 install.py
```

It can detect NVIDIA GPUs, select a GPU when multiple devices are present,
download the sample scene, run environment diagnostics, and print the matching
launch command.

### CPU-only Install

Install LLVM first, then force CPU mode.

macOS:

```bash
brew install llvm
python3 install.py --cpu
```

Ubuntu or Debian:

```bash
sudo apt update
sudo apt install llvm
python3 install.py --cpu
```

Windows users should install LLVM from the official
[LLVM download page](https://releases.llvm.org/download.html), reopen PowerShell,
and run:

```powershell
py -3.11 install.py --cpu
```

### Installer Flags

- `--with-sample-scene`: download the bundled `11_SW_7A-D` sample scene.
- `--no-sample-scene`: skip the sample-scene prompt.
- `--gpu <index-or-uuid>`: pin a specific GPU.
- `--cpu`: force CPU mode by clearing `CUDA_VISIBLE_DEVICES`.
- `--yes`: use non-interactive defaults; combine it with
  `--with-sample-scene` when the scene is required.
- `--doctor`: run diagnostics without reinstalling packages.
- `--recreate-venv`: remove and rebuild `.venv/`.
- `--dry-run`: print planned installer actions without changing local files.

Run `python3 install.py --help` for the authoritative option list.

## Scene Data

Link, Radar Sensing, Radiomap, Mobility and DeepMIMO workflows require scene data. The sample
archive contains four Open3Dhk tiles: `11_SW_7A`, `11_SW_7B`, `11_SW_7C` and
`11_SW_7D`. Install it with:

```bash
python3 install.py --with-sample-scene
```

Runtime scene assets use this layout by default:

```text
scene/
|-- common/
|   `-- scene_common.xml
|-- tiles/
|   `-- <tile_id>.xml
|-- meshes/
|   `-- <tile_id>/<category>/*.ply
`-- cache/
    `-- render_bundles/
```

Use a different scene root with:

```bash
OAT_SCENE_ROOT=/path/to/scene python3 -m backend.server
```

Build or refresh frontend tile bundles with:

```bash
python3 -m backend.tools.build_tile_bundles --help
```

Hammurabi's Apache-2.0 license does not cover third-party government spatial
data. Review [Scene data and third-party terms](docs/data-licenses.md) before
redistributing scene data or derived products.

### Building scenes from GeoJSON

You can build a complete Sionna RT scene from a building-footprint GeoJSON
FeatureCollection expressed in EPSG:3857. The importer extrudes every footprint,
tiles the result in true metres, and writes the standard per-tile XML/PLY layout
plus `coverage.json` and `scene_metadata.json`:

```bash
python3 -m backend.tools.import_geojson_buildings \
  --input gba/tehran-building-formatted.geojson \
  --scene-root scene \
  --replace
```

The bundled Tehran dataset is generated this way. The default tile contains the
scene origin and is selected automatically, so pressing **Load Selected Tiles**
immediately opens 3D buildings around central Tehran. Every Link, Mobility,
Radiomap, DeepMIMO and Radar workflow then uses that scene. Larger areas can be
selected on the entry map; tile bundles are built lazily on first request.

Useful importer options include `--tile-size-m`, `--ground-cell-size-m`,
`--center-lon`, `--center-lat`, `--max-features` and `--no-ground`. See
[GeoJSON building scenes](docs/geojson-scenes.md) for the output contract and
radio-map/device-coordinate details.

## Configuration

Runtime behavior is configured with `OAT_*` environment variables. Common
options include:

| Variable | Purpose |
| --- | --- |
| `OAT_HOST`, `OAT_PORT` | Backend bind address; defaults to `127.0.0.1:8090` |
| `OAT_SCENE_ROOT` | Runtime scene root |
| `OAT_GENERATED_ROOT` | Generated job and runtime XML output root |
| `OAT_DEFAULT_FREQUENCY_HZ` | Default carrier frequency |
| `OAT_DEFAULT_MAX_DEPTH` | Default RT path depth |
| `OAT_DEFAULT_LINK_SAMPLES` | Default Link sample budget |
| `OAT_DEFAULT_RADIOMAP_SAMPLES` | Default Radiomap sample budget |
| `OAT_RT_DEVICE` | RT backend: `auto` (default), `cpu` or `cuda`; `auto` falls back to LLVM when a GPU is below OptiX's compute-capability requirement |
| `OAT_DEEPMIMO_ENV_PYTHON` | Python executable used by DeepMIMO workers |
| `OAT_MAP_DOWNLOAD_BASE_URL`, `OAT_MAP_DOWNLOAD_FORMAT`, `OAT_MAP_DOWNLOAD_KEY` | Optional tile-download source |

The complete and authoritative list, including validation and queue limits, is
defined in [`backend/config.py`](backend/config.py). Restart the server after
changing runtime environment values.

## Development and Testing

Run the Python suite from the repository root:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

Run Playwright feature and visual regressions:

```bash
cd tests/browser
npm ci
npx playwright install chromium
npm test
```

Build the tutorial website:

```bash
cd website
npm ci
npm test
npm run build
```

for run first time:

```bash
cd workbench
npx -y npm@11.9.0 ci
npx -y npm@11.9.0 run build
cd ..
set -a; . ./.oat-env; set +a
./.venv/bin/python -m backend.server
```


See the [development guide](docs/development.md) before changing Feature
registration, REST contracts, viewer layers or shared settings.

## Repository Layout

```text
.
|-- backend/
|   |-- features/          # Backend Feature definitions and route catalog
|   |-- jobs/              # In-process and subprocess job managers
|   |-- rt/                # Sionna RT solvers and scene runtime
|   |-- scene/             # Tile scene generation and bundle tooling
|   `-- static/
|       `-- js/
|           |-- core/      # Feature registry, store, settings and picking
|           |-- features/  # Feature-owned state, transport and renderers
|           `-- viewer/    # Layer, primitive and asset infrastructure
|-- docs/                  # Architecture and contributor documentation
|-- tests/                 # Python unit, contract and regression tests
|   `-- browser/           # Playwright behavior and visual baselines
|-- workbench/             # React/TypeScript production Workbench source
|-- website/               # React/Vite tutorial website
|-- CHANGELOG.md           # Release history and pending user-visible changes
|-- CITATION.cff           # Machine-readable software citation metadata
|-- install.py             # Cross-platform installer and environment doctor
|-- requirements-test.txt  # Minimal dependencies for automated test jobs
|-- requirements.txt       # Pinned Python runtime dependencies
|-- LICENSE
`-- README.md
```

## Troubleshooting

- Run `python3 install.py --doctor` after changing Python, CUDA, drivers or LLVM.
- Missing `nvidia-smi` is not necessarily a failure; CPU mode can run when the
  Dr.Jit LLVM backend passes diagnostics.
- CUDA warnings commonly indicate an NVIDIA driver, CUDA,
  `CUDA_VISIBLE_DEVICES`, or Mitsuba CUDA variant mismatch.
- If the browser opens but analysis cannot run, confirm that a scene is loaded
  and that all four sample tile XML/mesh directories are present.
- On Windows, start through `.oat-env.ps1` so GPU selection and cache paths are
  applied to the current PowerShell process.

## Contributing

Issues and pull requests are welcome. Keep existing REST contracts and UI
behavior backward compatible unless the change explicitly introduces a versioned
contract. New analysis capabilities should use the frontend and backend Feature
catalogs instead of adding mode-specific branches to core entry points. Run the
relevant Python and Playwright suites before submitting a change.

## Citation

Hammurabi is a derivative of OpenAirTwin. If the platform contributes to your
work, cite the upstream software version this fork is based on:

> Wang, Z., Zhou, Z., & Zhao, Z. (2026). OpenAirTwin (Version 1.1.0)
> [Computer software]. https://github.com/HKUOpenSource/OpenAirTwin/releases/tag/v1.1.0

Machine-readable citation metadata is available in [`CITATION.cff`](CITATION.cff).

## License

Hammurabi source code is released under the Apache License 2.0. See
[`LICENSE`](LICENSE) for details. Upstream OpenAirTwin copyright and attribution
notices are preserved. Scene data and other third-party assets remain
subject to their respective terms.
