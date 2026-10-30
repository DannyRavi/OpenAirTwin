"""PMNet pathloss-map prediction.

The package implements the PMNet inference contract as a *static* surrogate:
it reads the already-loaded Sionna RT scene geometry once, rasterizes the
building mask around the transmitter and predicts a pathloss map. It never
launches ray tracing, so a prediction is orders of magnitude cheaper than a
Radio Map solve and can be repeated interactively.

Public entry points:

``parse_pmnet_payload``
    Validate a request body and return normalized parameters.
``solve_pmnet``
    Predict one pathloss map for a ready Sionna RT runtime.
"""

from backend.pmnet.params import PMNET_SCHEMA_VERSION, parse_pmnet_payload
from backend.pmnet.solver import solve_pmnet

__all__ = ["PMNET_SCHEMA_VERSION", "parse_pmnet_payload", "solve_pmnet"]
