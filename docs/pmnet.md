# PMNet pathloss maps

PMNet ([`abman23/pmnet`](https://github.com/abman23/pmnet), MIT, Lee and
Molisch) predicts a two-dimensional pathloss map from a building map and a
transmitter location. Hammurabi integrates it as the **PMNet Pathloss** mode:
the same Sionna RT scene that drives Link, Radio Map and DeepMIMO is
rasterized into a building-height image, the prediction is returned as a
path-gain field, and the result is drawn as a ground heatmap plus a building
envelope on top of the 3D scene.

PMNet is a *static* surrogate. It never launches path tracing, so a prediction
takes well under a second on a normal CPU while a comparable Radio Map solve
takes minutes. Use it for interactive "what if I move the Tx" exploration and
use Radio Map when you need physically traced path gain.

## Workflow

1. Select map tiles and enter the scene so Sionna RT reports `ready`.
2. Choose **PMNet Pathloss** in the mode menu.
3. Pick a Tx surface (the same device picker as Radio Map) or type X/Y/Z.
4. Set the ROI size, grid resolution and receiver height.
5. Select **Predict Map**. The result dock reports the engine, grid, building
   coverage, display range and prediction budget; the 3D scene shows the
   ground heatmap and the building envelope.

Changing a prediction parameter invalidates the current map, exactly like the
other analysis modes. Display-only parameters (colormap and colour limits) and
the building-envelope toggle redraw without a new job.

## Request contract

`POST /api/pmnet/jobs` accepts:

```json
{
  "tx": {"position": [72.0, 37.0, 40.0], "orientation": [0.0, 0.0, 0.0]},
  "roi": {"size": [200.0, 200.0], "grid": 128, "height_offset": 1.5},
  "solver": {"frequency_hz": 3500000000.0},
  "model": {
    "engine": "auto",
    "checkpoint": null,
    "wall_loss_db": 6.0,
    "checkpoint_min_loss_db": 60.0,
    "checkpoint_max_loss_db": 160.0
  },
  "signal": {"tx_power_dbm": 30.0}
}
```

| Field | Bounds |
| --- | --- |
| `roi.size` | 10 m to 2000 m per axis |
| `roi.grid` | 16 to 256 cells per side, at most 40 000 cells |
| `roi.height_offset` | 0 m to 200 m above terrain |
| `solver.frequency_hz` | the shared solver frequency bounds |
| `model.wall_loss_db` | 0 dB to 60 dB per building wall |
| `signal.tx_power_dbm` | -40 dBm to 80 dBm |

The job routes mirror the shared in-process feature contract:

- `POST /api/pmnet/jobs` creates a job (429 when the queue is full, 409 when no
  scene is ready);
- `GET /api/pmnet/jobs/{job_id}` returns progress;
- `GET /api/pmnet/jobs/{job_id}/result` returns the map once the job succeeded
  and 409 otherwise.

Results are held in memory, bounded by `OAT_PMNET_MAX_PENDING_JOBS` (default 2)
and `OAT_PMNET_MAX_STORED_JOBS` (default 8), and expire after
`OAT_PMNET_JOB_TTL_SECONDS` (default 3600). PMNet has no download route and
writes no generated files.

## Result contract

The result extends the job payload with:

- `metric: "path_gain_db"` and `unit: "dB"`; values are negative propagation
  path gain, matching the Radio Map convention, so the same colormaps and
  display ranges apply;
- `engine`: `{id, label, checkpoint, device, reason, preference}`. `id` is
  `pmnet` for a checkpoint forward pass and `physics` for the deterministic
  surrogate; `reason` always explains a fallback;
- `grid`: `nx`, `ny`, `cell_size`, `bounds`, `vertex_z` (drape corners,
  row-major `(ny + 1) x (nx + 1)`) and `height_offset`;
- `values.data`: `nx * ny` path-gain samples in row-major cell order;
- `buildings.cells`: `[column, row, roof_z, ground_z, roof_gain, ground_gain]`
  for every rasterized building cell, capped by `OAT_MAX_PMNET_BUILDING_CELLS`;
- `stats` and `timings` for the result dock and for diagnosing slow scenes.

The frontend builds both meshes from this contract: a terrain-following ground
drape from `grid.vertex_z` and `values.data`, and rooftop plus facade
triangles from `buildings.cells`. Facades are emitted only where a building
cell touches a non-building cell, which keeps the envelope cheap even for
thousands of cells.

## Prediction engines

### Geometry surrogate (default)

`backend/pmnet/predictor.py` implements a COST-231-style multi-wall model
combined with an ITU-R P.526 single knife-edge term:

1. free-space loss from the 3D Tx-to-cell distance;
2. the number of building walls crossed along the ray (each wall costs
   `model.wall_loss_db`);
3. the largest Fresnel obstruction over the roofline along the same ray.

The dominant (least-loss) path wins, and no prediction can beat free-space
loss. Building cells are additionally predicted on the rooftop plane, which
is what makes the envelope show strong Rooftop values and shadowed facades.

The surrogate is deterministic: the same scene, Tx and parameters always give
the same map, which is what the unit and browser suites assert.

### PMNet checkpoint

`backend/pmnet/model.py` reproduces `models/pmnet_v1.py` and
`models/pmnet_v3.py` from the upstream repository (the two differ only in
`output_stride`). A checkpoint is used when PyTorch is importable **and** a
checkpoint resolves:

1. `model.checkpoint` in the request body;
2. `OAT_PMNET_CHECKPOINT`;
3. a `.pt`/`.pth` file under `models/pmnet/`.

```bash
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
mkdir -p models/pmnet
# Place the downloaded pmnet_v3 checkpoint here, then restart the server.
cp ~/Downloads/mymodel.pt models/pmnet/pmnet_v3.pt
```

`model.engine` selects the behaviour:

- `auto` (default) uses a checkpoint when available and otherwise records why
  the surrogate was used;
- `checkpoint` fails the request when PyTorch or a checkpoint is missing;
- `physics` always uses the surrogate.

The checkpoint contract follows the upstream dataloader: channel 0 is the
normalized building-height image, channel 1 is a transmitter proximity map,
and the single output channel is a value in `[0, 1]` where 1 is the strongest
cell. Hammurabi maps that value to loss with
`checkpoint_min_loss_db` .. `checkpoint_max_loss_db` (60 dB .. 160 dB by
default). Tune these two values to the normalization used by the checkpoint's
training dataset; the upstream repository normalizes each map independently.

Only the ground plane uses the checkpoint. The building envelope always uses
the geometry surrogate because PMNet predicts a single 2D map at one receiver
height. The result dock states this split.

## Building extraction

`backend/pmnet/geometry.py` reads the already-loaded Sionna RT scene without
tracing rays:

- objects whose radio material is a ground material form the terrain;
- every other material (concrete, wood, unknown) is treated as an obstacle, so
  a new material never silently disappears from a map;
- near-horizontal triangles are rasterized into a corner height field (the
  drape) and a cell-centre roof field; a cell is a building when its roof is at
  least `OAT_MIN_PMNET_BUILDING_HEIGHT_M` (default 1 m) above the ground.

Materials are configured with `OAT_PMNET_OBSTACLE_MATERIALS` (default
`itu_concrete,itu_wood,itu_marble`) and the existing
`OAT_RADIOMAP_MEASUREMENT_MATERIAL` ground material.

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `OAT_PMNET_MAX_PENDING_JOBS` | `2` | Concurrent queued/running jobs |
| `OAT_PMNET_MAX_STORED_JOBS` | `8` | Retained finished jobs |
| `OAT_PMNET_JOB_TTL_SECONDS` | `3600` | Finished-job lifetime |
| `OAT_DEFAULT_PMNET_GRID_CELLS` | `128` | Default cells per side |
| `OAT_MAX_PMNET_CELLS` | `40000` | Hard cell budget |
| `OAT_MAX_PMNET_RAY_SAMPLES` | `192` | Ray samples per cell for the surrogate |
| `OAT_MAX_PMNET_BUILDING_CELLS` | `20000` | Envelope cells returned to the browser |
| `OAT_PMNET_CHECKPOINT` | empty | Default checkpoint path |
| `OAT_PMNET_DEVICE` | `auto` | `auto`, `cpu` or `cuda` for a checkpoint |
| `OAT_PMNET_DEFAULT_MIN_LOSS_DB` | `60` | Checkpoint output floor |
| `OAT_PMNET_DEFAULT_MAX_LOSS_DB` | `160` | Checkpoint output ceiling |
| `OAT_PMNET_INFERENCE_SIZE` | `256` | Checkpoint input resolution |

## Limitations

- A checkpoint prediction is only as good as its training scenarios. The
  upstream models were trained on USC, UCLA, Boston and Rural ray-traced maps;
  a Hong Kong or Tehran tile is an out-of-distribution input unless the model
  was fine-tuned on comparable geometry.
- The checkpoint normalization window is a documented assumption, not a
  calibrated property of an arbitrary `.pt` file.
- The surrogate is a two-mechanism empirical model. It does not resolve
  diffraction orders, diffuse scattering, material properties or antenna
  patterns, and it uses one receiver height per field.
- The ROI is a square window centred on the Tx; it is not drawn interactively
  like the DeepMIMO ROI.
- The interactive tutorial covers the original workflows. A PMNet tutorial
  step needs reviewed screenshots and is tracked as a follow-up.
