from __future__ import annotations

from dataclasses import dataclass

from backend import config
from backend.jobs.inprocess_jobs import (
    InProcessJob,
    InProcessJobManager,
    InProcessQueueFull,
)
from backend.pmnet.params import parse_pmnet_payload
from backend.pmnet.solver import solve_pmnet


@dataclass
class PmnetJob(InProcessJob):
    pass


class PmnetQueueFull(InProcessQueueFull):
    def __init__(self, max_pending_jobs: int) -> None:
        super().__init__(
            "PMNet pathloss job queue is full; try again later", max_pending_jobs
        )


class PmnetJobManager(InProcessJobManager):
    def __init__(
        self,
        rt_runtime,
        *,
        solver=solve_pmnet,
        validate_payload=parse_pmnet_payload,
        max_pending_jobs: int = config.PMNET_MAX_PENDING_JOBS,
        max_stored_jobs: int = config.PMNET_MAX_STORED_JOBS,
        job_ttl_seconds: float = config.PMNET_JOB_TTL_SECONDS,
        start_worker: bool = True,
    ) -> None:
        super().__init__(
            rt_runtime,
            solver=solver,
            validate_payload=validate_payload,
            job_type=PmnetJob,
            queue_full_type=PmnetQueueFull,
            id_prefix="pm",
            success_message="Pathloss map ready",
            failure_message="PMNet pathloss job failed",
            max_pending_jobs=max_pending_jobs,
            max_stored_jobs=max_stored_jobs,
            job_ttl_seconds=job_ttl_seconds,
            start_worker=start_worker,
        )
