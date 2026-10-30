from __future__ import annotations

from backend.features.core import BackendFeatureDefinition
from backend.features.inprocess_routes import (
    InProcessFeatureService,
    register_inprocess_job_routes,
)
from backend.jobs.pmnet_jobs import PmnetJobManager


def create_service(resources):
    return InProcessFeatureService(PmnetJobManager(resources["rt_runtime"]))


def register_routes(routes) -> None:
    register_inprocess_job_routes(
        routes,
        feature_id="pmnet",
        path_prefix="/api/pmnet/jobs",
        legacy_attribute="pmnet_job_manager",
    )


PMNET_FEATURE = BackendFeatureDefinition(
    id="pmnet",
    create_service=create_service,
    register_routes=register_routes,
    resources=("rt_runtime",),
)
