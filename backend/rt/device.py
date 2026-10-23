from __future__ import annotations

import os
import subprocess

from backend import config


MIN_OPTIX_COMPUTE_CAPABILITY = 7.0
_SUPPORTED_DEVICE_VALUES = frozenset({"auto", "cpu", "cuda", "gpu"})
_configured = False


def _gpu_compute_capabilities() -> list[float]:
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=compute_cap", "--format=csv,noheader"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return []
    if completed.returncode != 0:
        return []
    capabilities: list[float] = []
    for line in completed.stdout.splitlines():
        text = line.strip().split(",")[0].strip()
        try:
            capabilities.append(float(text))
        except ValueError:
            continue
    return capabilities


def _log(message: str) -> None:
    print(f"[rt] {message}", flush=True)


def configure_rt_device() -> str:
    """Select a Dr.Jit/Mitsuba device that can actually run the RT solver.

    Sionna RT uses OptiX for CUDA devices, which requires at least compute
    capability 7.0 (SM70). Older GPUs are still exposed by ``nvidia-smi`` but
    crash inside Dr.Jit, so automatically fall back to the LLVM CPU variant
    instead of aborting the process. ``OAT_RT_DEVICE`` can force ``cpu`` or
    ``cuda``.
    """

    global _configured
    requested = str(os.environ.get("OAT_RT_DEVICE", "auto")).strip().lower() or "auto"
    if requested not in _SUPPORTED_DEVICE_VALUES:
        _log(f"ignoring unsupported OAT_RT_DEVICE={requested!r}; using auto")
        requested = "auto"

    if _configured and requested == "auto":
        return "cpu" if os.environ.get("CUDA_VISIBLE_DEVICES") == "" else "auto"
    _configured = True

    if os.environ.get("CUDA_VISIBLE_DEVICES") == "" and requested == "auto":
        _log("RT device auto-selected CPU (CUDA_VISIBLE_DEVICES is empty)")
        return "cpu"

    if requested == "cpu":
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        _log("RT device forced to CPU")
        return "cpu"

    if requested in {"cuda", "gpu"}:
        _log("RT device forced to CUDA")
        return "cuda"

    capabilities = _gpu_compute_capabilities()
    if capabilities and max(capabilities) < MIN_OPTIX_COMPUTE_CAPABILITY:
        os.environ["CUDA_VISIBLE_DEVICES"] = ""
        detected = ", ".join(f"{value:g}" for value in capabilities)
        _log(
            "Detected CUDA compute capability "
            f"{detected}, below OptiX's required {MIN_OPTIX_COMPUTE_CAPABILITY:g}; "
            "falling back to the LLVM CPU backend"
        )
        return "cpu"
    if capabilities:
        detected = ", ".join(f"{value:g}" for value in capabilities)
        _log(f"RT device auto-selected CUDA (compute capability {detected})")
    else:
        _log("RT device auto-selected the available Mitsuba/Dr.Jit backend")
    return "auto"


def rt_device_status() -> dict[str, object]:
    capabilities = _gpu_compute_capabilities()
    return {
        "requested": str(os.environ.get("OAT_RT_DEVICE", "auto")),
        "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
        "gpu_compute_capabilities": capabilities,
        "minimum_optix_compute_capability": MIN_OPTIX_COMPUTE_CAPABILITY,
    }
