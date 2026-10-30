"""PMNet (``pmnet_v1`` / ``pmnet_v3``) inference for pre-trained checkpoints.

The network definition mirrors ``models/pmnet_v1.py`` and
``models/pmnet_v3.py`` from https://github.com/abman23/pmnet (the only
difference is ``output_stride``). PyTorch is optional: every public helper
imports it lazily and reports a typed reason when it is unavailable, so the
feature keeps working with the deterministic surrogate in
:mod:`backend.pmnet.predictor`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading

import numpy as np

from backend import config
from backend.pmnet.geometry import SurfaceGrid


_BOTTLENECK_EXPANSION = 4
_CHECKPOINT_SUFFIXES = (".pt", ".pth")
_TORCH_LOCK = threading.Lock()
_MODEL_CACHE: dict[tuple[str, str, int], object] = {}


@dataclass(frozen=True)
class CheckpointModel:
    model: object
    device: str
    checkpoint: Path
    output_stride: int


def torch_status() -> dict:
    """Report PyTorch availability without importing it eagerly."""

    try:
        import torch  # noqa: F401
    except Exception as exc:  # pragma: no cover - depends on the interpreter
        return {
            "available": False,
            "version": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "available": True,
        "version": str(getattr(torch, "__version__", "unknown")),
        "error": None,
    }


def resolve_checkpoint(requested: str | None) -> Path | None:
    """Resolve an explicit checkpoint or the configured default."""

    candidates: list[Path] = []
    if requested:
        candidates.append(Path(requested).expanduser())
    if config.PMNET_CHECKPOINT_PATH:
        candidates.append(Path(config.PMNET_CHECKPOINT_PATH).expanduser())
    candidates.append(config.PROJECT_ROOT / "models" / "pmnet")

    for candidate in candidates:
        if candidate.is_file() and candidate.suffix.lower() in _CHECKPOINT_SUFFIXES:
            return candidate.resolve()
        if candidate.is_dir():
            matches = sorted(
                path
                for path in candidate.rglob("*")
                if path.is_file() and path.suffix.lower() in _CHECKPOINT_SUFFIXES
            )
            if matches:
                return matches[0].resolve()
    return None


def _resolve_device(torch, requested: str) -> str:
    token = (requested or "auto").strip().lower()
    if token in {"cpu", "cuda"}:
        if token == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("OAT_PMNET_DEVICE=cuda but CUDA is not available")
        return token
    return "cuda" if torch.cuda.is_available() else "cpu"


def _build_module(torch, output_stride: int):
    import torch.nn as nn
    import torch.nn.functional as F

    class ConvBnReLU(nn.Sequential):
        def __init__(self, in_ch, out_ch, kernel_size, stride, padding, dilation, relu=True):
            super().__init__()
            self.add_module(
                "conv",
                nn.Conv2d(in_ch, out_ch, kernel_size, stride, padding, dilation, bias=False),
            )
            self.add_module("bn", nn.BatchNorm2d(out_ch, eps=1e-5, momentum=1 - 0.999))
            if relu:
                self.add_module("relu", nn.ReLU())

    class Bottleneck(nn.Module):
        def __init__(self, in_ch, out_ch, stride, dilation, downsample):
            super().__init__()
            mid_ch = out_ch // _BOTTLENECK_EXPANSION
            self.reduce = ConvBnReLU(in_ch, mid_ch, 1, stride, 0, 1, True)
            self.conv3x3 = ConvBnReLU(mid_ch, mid_ch, 3, 1, dilation, dilation, True)
            self.increase = ConvBnReLU(mid_ch, out_ch, 1, 1, 0, 1, False)
            self.shortcut = (
                ConvBnReLU(in_ch, out_ch, 1, stride, 0, 1, False)
                if downsample
                else nn.Identity()
            )

        def forward(self, x):
            h = self.reduce(x)
            h = self.conv3x3(h)
            h = self.increase(h)
            h = h + self.shortcut(x)
            return F.relu(h)

    class ResLayer(nn.Sequential):
        def __init__(self, n_layers, in_ch, out_ch, stride, dilation, multi_grids=None):
            super().__init__()
            grids = multi_grids if multi_grids is not None else [1] * n_layers
            if len(grids) != n_layers:
                raise ValueError("multi_grids must match n_layers")
            for index in range(n_layers):
                self.add_module(
                    f"block{index + 1}",
                    Bottleneck(
                        in_ch=(in_ch if index == 0 else out_ch),
                        out_ch=out_ch,
                        stride=(stride if index == 0 else 1),
                        dilation=dilation * grids[index],
                        downsample=(index == 0),
                    ),
                )

    class Stem(nn.Sequential):
        def __init__(self, out_ch, in_ch=2):
            super().__init__()
            self.add_module("conv1", ConvBnReLU(in_ch, out_ch, 7, 2, 3, 1))
            self.add_module("pool", nn.MaxPool2d(in_ch, 2, 1, ceil_mode=True))

    class ImagePool(nn.Module):
        def __init__(self, in_ch, out_ch):
            super().__init__()
            self.pool = nn.AdaptiveAvgPool2d(1)
            self.conv = ConvBnReLU(in_ch, out_ch, 1, 1, 0, 1)

        def forward(self, x):
            _, _, height, width = x.shape
            h = self.conv(self.pool(x))
            return F.interpolate(h, size=(height, width), mode="bilinear", align_corners=False)

    class ASPP(nn.Module):
        def __init__(self, in_ch, out_ch, rates):
            super().__init__()
            self.stages = nn.Module()
            self.stages.add_module("c0", ConvBnReLU(in_ch, out_ch, 1, 1, 0, 1))
            for index, rate in enumerate(rates):
                self.stages.add_module(
                    f"c{index + 1}",
                    ConvBnReLU(in_ch, out_ch, 3, 1, padding=rate, dilation=rate),
                )
            self.stages.add_module("imagepool", ImagePool(in_ch, out_ch))

        def forward(self, x):
            return torch.cat([stage(x) for stage in self.stages.children()], dim=1)

    def conv_relu(in_channels, out_channels, kernel, padding):
        return nn.Sequential(
            nn.Conv2d(in_channels, out_channels, kernel, padding=padding),
            nn.ReLU(inplace=True),
        )

    def conv_relu_transpose(in_channels, out_channels, kernel, padding):
        return nn.Sequential(
            nn.ConvTranspose2d(in_channels, out_channels, kernel, stride=2, padding=padding),
            nn.ReLU(inplace=True),
        )

    class PMNet(nn.Module):
        def __init__(self, n_blocks, atrous_rates, multi_grids, stride):
            super().__init__()
            if stride == 8:
                strides = [1, 2, 1, 1]
                dilations = [1, 1, 2, 4]
            elif stride == 16:
                strides = [1, 2, 2, 1]
                dilations = [1, 1, 1, 2]
            else:
                raise ValueError("PMNet output_stride must be 8 or 16")

            channels = [64 * 2 ** power for power in range(6)]
            self.layer1 = Stem(channels[0])
            self.layer2 = ResLayer(n_blocks[0], channels[0], channels[2], strides[0], dilations[0])
            self.layer3 = ResLayer(n_blocks[1], channels[2], channels[3], strides[1], dilations[1])
            self.layer4 = ResLayer(n_blocks[2], channels[3], channels[3], strides[2], dilations[2])
            self.layer5 = ResLayer(
                n_blocks[3], channels[3], channels[4], strides[3], dilations[3], multi_grids
            )
            self.aspp = ASPP(channels[4], 256, atrous_rates)
            concat_ch = 256 * (len(atrous_rates) + 2)
            self.add_module("fc1", ConvBnReLU(concat_ch, 512, 1, 1, 0, 1))
            self.reduce = ConvBnReLU(256, 256, 1, 1, 0, 1)
            self.conv_up5 = conv_relu(512, 512, 3, 1)
            self.conv_up4 = conv_relu(512 + 512, 512, 3, 1)
            self.conv_up3 = conv_relu_transpose(512 + 512, 256, 3, 1)
            self.conv_up2 = conv_relu(256 + 256, 256, 3, 1)
            self.conv_up1 = conv_relu(256 + 256, 256, 3, 1)
            self.conv_up0 = conv_relu(256 + 64, 128, 3, 1)
            self.conv_up00 = nn.Sequential(
                nn.Conv2d(128 + 2, 64, kernel_size=3, padding=1),
                nn.BatchNorm2d(64),
                nn.ReLU(),
                nn.Conv2d(64, 64, kernel_size=3, padding=1),
                nn.BatchNorm2d(64),
                nn.ReLU(),
                nn.Conv2d(64, 1, kernel_size=3, padding=1),
            )

        def forward(self, x):
            x1 = self.layer1(x)
            x2 = self.layer2(x1)
            x3 = self.reduce(x2)
            x4 = self.layer3(x3)
            x5 = self.layer4(x4)
            x6 = self.layer5(x5)
            x8 = self.fc1(self.aspp(x6))
            xup5 = self.conv_up5(x8)
            xup5 = torch.cat([xup5, x5], dim=1)
            xup4 = self.conv_up4(xup5)
            xup4 = torch.cat([xup4, x4], dim=1)
            xup3 = self.conv_up3(xup4)
            xup3 = torch.cat([xup3, x3], dim=1)
            xup2 = self.conv_up2(xup3)
            xup2 = torch.cat([xup2, x2], dim=1)
            xup1 = self.conv_up1(xup2)
            xup1 = torch.cat([xup1, x1], dim=1)
            xup0 = self.conv_up0(xup1)
            xup0 = F.interpolate(
                xup0, size=x.shape[2:], mode="bilinear", align_corners=False
            )
            xup0 = torch.cat([xup0, x], dim=1)
            return self.conv_up00(xup0)

    return PMNet(
        n_blocks=[3, 3, 27, 3],
        atrous_rates=[6, 12, 18],
        multi_grids=[1, 2, 4],
        stride=output_stride,
    )


def load_checkpoint_model(
    checkpoint: Path,
    *,
    output_stride: int = 8,
    device: str = "auto",
) -> CheckpointModel:
    """Load and cache one PMNet checkpoint."""

    import torch

    resolved = Path(checkpoint).resolve()
    if not resolved.is_file():
        raise ValueError(f"PMNet checkpoint not found: {resolved}")
    target_device = _resolve_device(torch, device)
    cache_key = (str(resolved), target_device, int(output_stride))
    with _TORCH_LOCK:
        cached = _MODEL_CACHE.get(cache_key)
        if cached is not None:
            return cached
        model = _build_module(torch, int(output_stride))
        try:
            state = torch.load(str(resolved), map_location=target_device, weights_only=True)
        except TypeError:  # pragma: no cover - older torch releases
            state = torch.load(str(resolved), map_location=target_device)
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]
        if not isinstance(state, dict):
            raise ValueError("PMNet checkpoint does not contain a state dict")
        cleaned = {
            (key[7:] if key.startswith("module.") else key): value
            for key, value in state.items()
        }
        model.load_state_dict(cleaned, strict=True)
        model.eval()
        model.to(target_device)
        entry = CheckpointModel(
            model=model,
            device=target_device,
            checkpoint=resolved,
            output_stride=int(output_stride),
        )
        _MODEL_CACHE[cache_key] = entry
        return entry


def clear_model_cache() -> None:
    with _TORCH_LOCK:
        _MODEL_CACHE.clear()


def _resize_nearest(array: np.ndarray, size: int) -> np.ndarray:
    rows, cols = array.shape
    if rows == 0 or cols == 0:
        return np.zeros((size, size), dtype=np.float64)
    row_index = np.minimum((np.arange(size) * rows) // size, rows - 1)
    col_index = np.minimum((np.arange(size) * cols) // size, cols - 1)
    return array[row_index[:, None], col_index[None, :]]


def resize_bilinear(array: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """Bilinear resample of a 2D array to ``shape`` using numpy only."""

    source = np.asarray(array, dtype=np.float64)
    target_rows, target_cols = int(shape[0]), int(shape[1])
    rows, cols = source.shape
    if rows == 0 or cols == 0:
        return np.zeros((target_rows, target_cols), dtype=np.float64)
    if (rows, cols) == (target_rows, target_cols):
        return source.copy()

    def axis(source_size: int, target_size: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        scale = source_size / float(target_size)
        positions = (np.arange(target_size) + 0.5) * scale - 0.5
        positions = np.clip(positions, 0.0, source_size - 1.0)
        lower = np.floor(positions).astype(np.int64)
        upper = np.minimum(lower + 1, source_size - 1)
        weight = positions - lower
        return lower, upper, weight

    r0, r1, rw = axis(rows, target_rows)
    c0, c1, cw = axis(cols, target_cols)
    top = source[r0][:, c0] * (1 - cw)[None, :] + source[r0][:, c1] * cw[None, :]
    bottom = source[r1][:, c0] * (1 - cw)[None, :] + source[r1][:, c1] * cw[None, :]
    return top * (1 - rw)[:, None] + bottom * rw[:, None]


def build_normalized_inputs(
    surface: SurfaceGrid,
    tx_position,
    *,
    size: int | None = None,
) -> np.ndarray:
    """Build the 2-channel PMNet input for one ROI.

    Channel 0 is the normalized building-height image and channel 1 is a
    transmitter proximity map. Both match the layout used by
    ``dataloader/loader_USC.py`` (``building`` and ``Tx`` images).
    """

    grid_size = int(size or config.PMNET_INFERENCE_SIZE)
    grid_size = max(32, grid_size)

    heights = np.asarray(surface.building_height, dtype=np.float64)
    positive = heights[heights > 0.0]
    scale = float(np.percentile(positive, 99)) if positive.size else 1.0
    scale = max(scale, float(config.MIN_PMNET_BUILDING_HEIGHT_M))
    building_channel = np.clip(heights / scale, 0.0, 1.0)
    building_channel = _resize_nearest(building_channel, grid_size)

    roi = surface.roi
    width = float(roi.bounds_max[0] - roi.bounds_min[0])
    height = float(roi.bounds_max[1] - roi.bounds_min[1])
    fx = ((float(tx_position[0]) - float(roi.bounds_min[0])) / max(width, 1e-6)) * (grid_size - 1)
    fy = ((float(tx_position[1]) - float(roi.bounds_min[1])) / max(height, 1e-6)) * (grid_size - 1)
    fx = float(np.clip(fx, 0.0, grid_size - 1))
    fy = float(np.clip(fy, 0.0, grid_size - 1))
    yy, xx = np.mgrid[0:grid_size, 0:grid_size]
    sigma = max(grid_size / 32.0, 1.0)
    tx_channel = np.exp(-((xx - fx) ** 2 + (yy - fy) ** 2) / (2.0 * sigma ** 2))
    tx_channel = tx_channel.reshape(grid_size, grid_size)

    return np.stack([building_channel, tx_channel], axis=0).astype(np.float32)


def checkpoint_loss_field(
    prediction: np.ndarray,
    *,
    min_loss_db: float,
    max_loss_db: float,
    shape: tuple[int, int],
) -> np.ndarray:
    """Map a clipped PMNet prediction in ``[0, 1]`` to propagation loss in dB."""

    clipped = np.clip(np.asarray(prediction, dtype=np.float64), 0.0, 1.0)
    resized = resize_bilinear(clipped, shape)
    low = float(min_loss_db)
    high = float(max_loss_db)
    # PMNet predicts a normalized pathloss map: 0 is the weakest cell and 1 is
    # the strongest, so the strongest cell gets the smallest loss.
    return low + (1.0 - resized) * (high - low)


def run_checkpoint_prediction(
    entry: CheckpointModel,
    surface: SurfaceGrid,
    tx_position,
    *,
    inference_size: int | None = None,
) -> np.ndarray:
    """Run one forward pass and return the prediction at ROI resolution."""

    import torch

    size = int(inference_size or config.PMNET_INFERENCE_SIZE)
    inputs = build_normalized_inputs(surface, tx_position, size=size)
    tensor = torch.from_numpy(inputs).unsqueeze(0).to(entry.device)
    with torch.no_grad():
        prediction = entry.model(tensor)
    array = prediction.detach().to("cpu").numpy().reshape(size, size)
    return np.clip(array, 0.0, 1.0)
