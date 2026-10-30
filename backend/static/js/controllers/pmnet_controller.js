import {requestFailureState} from "/js/api.js";

const POLL_INTERVAL_MS = 250;

export function createPmnetController({
  state,
  createPmnetJob,
  getPmnetJob,
  getPmnetResult,
  readPmnetInputs,
  pmnetJobPayload,
  pmnetColorRange,
  showOverlay,
  hideOverlay,
  renderPmnetResult,
  renderPmnetScene,
  clearPmnetScene,
}) {
  let runOwner = null;

  function invalidatePmnetResult({clearOverlay = true} = {}) {
    state.pmnet.generation += 1;
    state.pmnet.jobId = null;
    state.pmnet.result = null;
    state.pmnet.status = "Idle";
    state.pmnet.failureKind = null;
    if (clearOverlay && runOwner) {
      hideOverlay(runOwner);
    }
    runOwner = null;
    if (clearOverlay) {
      clearPmnetScene();
    }
  }

  async function pollPmnet(jobId, token = state.pmnet.generation, overlayOwner = runOwner) {
    while (state.pmnet.jobId === jobId && token === state.pmnet.generation) {
      const job = await getPmnetJob(jobId);
      if (state.pmnet.jobId !== jobId || token !== state.pmnet.generation) {
        return;
      }
      state.pmnet.status = job.status;
      renderPmnetResult();

      if (job.status === "succeeded") {
        const result = await getPmnetResult(jobId);
        if (state.pmnet.jobId !== jobId || token !== state.pmnet.generation) {
          return;
        }
        state.pmnet.result = result;
        state.pmnet.jobId = null;
        if (state.mode === "pmnet") {
          renderPmnetScene();
        }
        renderPmnetResult();
        hideOverlay(overlayOwner);
        if (runOwner === overlayOwner) {
          runOwner = null;
        }
        return;
      }

      if (job.status === "failed") {
        const overlayWasCurrent = hideOverlay(overlayOwner);
        if (runOwner === overlayOwner) {
          runOwner = null;
        }
        if (!overlayWasCurrent) {
          return;
        }
        throw new Error(job.error || job.message || "PMNet pathloss job failed");
      }

      showOverlay({
        title: "Predicting Pathloss Map",
        message: job.message || "Running the PMNet pathloss surrogate...",
        indeterminate: true,
        owner: overlayOwner,
      });
      await new Promise((resolve) => window.setTimeout(resolve, POLL_INTERVAL_MS));
    }
  }

  async function runPmnet() {
    readPmnetInputs();
    pmnetColorRange();
    clearPmnetScene();
    const token = ++state.pmnet.generation;
    const overlayOwner = `pmnet:${token}`;
    runOwner = overlayOwner;

    state.pmnet.status = "Queued";
    state.pmnet.failureKind = null;
    state.pmnet.jobId = null;
    state.pmnet.result = null;
    renderPmnetResult();
    showOverlay({
      title: "Predicting Pathloss Map",
      message: "Submitting PMNet job...",
      indeterminate: true,
      owner: overlayOwner,
      force: true,
    });

    try {
      const job = await createPmnetJob(pmnetJobPayload());
      if (token !== state.pmnet.generation) {
        return;
      }
      state.pmnet.jobId = job.job_id;
      await pollPmnet(job.job_id, token, overlayOwner);
    } catch (error) {
      if (token !== state.pmnet.generation) {
        return;
      }
      state.pmnet.jobId = null;
      const failure = requestFailureState(error);
      state.pmnet.status = failure.status;
      state.pmnet.failureKind = failure.kind;
      state.pmnet.result = null;
      renderPmnetResult();
      const overlayWasCurrent = hideOverlay(overlayOwner);
      if (runOwner === overlayOwner) {
        runOwner = null;
      }
      if (!overlayWasCurrent) {
        return;
      }
      throw error;
    } finally {
      if (token !== state.pmnet.generation && runOwner === overlayOwner) {
        runOwner = null;
      }
    }
  }

  return {
    invalidatePmnetResult,
    runPmnet,
  };
}
