import {colormapGradient} from "/js/colormaps.js";
import {renderPmnetScene} from "/js/features/pmnet/renderer.js";
import {formatCount, formatFixed, formatStatus} from "/js/ui/result_formatters.js?v=20260519-mode-isolation";

function formatArea(size) {
  const [width, height] = Array.isArray(size) ? size : [];
  return `${formatFixed(Number(width), 0)} x ${formatFixed(Number(height), 0, " m")}`;
}

function statusType(status) {
  if (status === "Succeeded") return "success";
  if (["Failed", "Timed Out", "Offline", "Server Error"].includes(status)) return "error";
  if (status === "Cancelled") return "warning";
  if (status === "Idle") return "empty";
  return "loading";
}

function engineLabel(engine) {
  if (!engine || typeof engine !== "object") return "Pending";
  return engine.id === "pmnet" ? "PMNet checkpoint" : "Geometry surrogate";
}

function engineDetail(engine) {
  if (!engine || typeof engine !== "object") {
    return "Select a checkpoint or use the built-in surrogate.";
  }
  if (engine.id === "pmnet") {
    return `Checkpoint ${String(engine.checkpoint || "").split("/").pop() || "loaded"} on ${engine.device || "cpu"}`;
  }
  return engine.reason || "Deterministic multi-wall and knife-edge prediction.";
}

function formatRange(range) {
  const min = Number(range?.min);
  const max = Number(range?.max);
  return Number.isFinite(min) && Number.isFinite(max)
    ? `${min.toFixed(1)} .. ${max.toFixed(1)} dB`
    : "N/A";
}

export function createPmnetResultView({
  state,
  ui,
  getViewer,
  pmnetColorRange,
  resultDock,
  syncLivePreviewStatusUi,
}) {
  function sceneOptions() {
    return {showBuildings: Boolean(state.pmnet.display.showBuildings)};
  }

  function createViewModel(visible) {
    const {minDb, maxDb, colormap} = pmnetColorRange();
    const result = state.pmnet.result;
    const summary = [
      {id: "status", label: "Status", value: formatStatus(state.pmnet.status), valueId: "pmStatus"},
      {id: "engine", label: "Engine", value: engineLabel(result?.engine), valueId: "pmEngine"},
      {id: "grid", label: "Grid", value: "--", valueId: "pmGrid"},
      {id: "coverage", label: "Buildings", value: "--", valueId: "pmCoverage"},
    ];
    const resolution = [
      {id: "area", label: "ROI", value: formatArea(state.pmnet.roi.size), valueId: "pmArea"},
      {id: "cell-size", label: "Cell Size", value: "--", valueId: "pmCellSize"},
      {id: "wall-loss", label: "Wall Loss", value: "--", valueId: "pmWallLoss"},
      {id: "range", label: "Result Range", value: "--", valueId: "pmRange"},
    ];

    if (result) {
      const grid = result.grid || {};
      const [nx, ny] = [Number(grid.nx), Number(grid.ny)];
      summary[2].value = Number.isFinite(nx) && Number.isFinite(ny)
        ? `${nx} x ${ny} cells`
        : "--";
      const coverage = Number(result.buildings?.coverage);
      summary[3].value = Number.isFinite(coverage)
        ? `${(coverage * 100).toFixed(1)}% of ROI`
        : "--";
      const cellSize = Array.isArray(grid.cell_size) ? grid.cell_size : null;
      resolution[1].value = cellSize
        ? `${formatFixed(Number(cellSize[0]), 2)} x ${formatFixed(Number(cellSize[1]), 2, " m")}`
        : "--";
      resolution[2].value = `${formatFixed(Number(state.pmnet.model.wallLossDb), 1, " dB")} | Rx ${formatFixed(Number(grid.height_offset), 1, " m")}`;
      resolution[3].value = formatRange(result.range);
    } else {
      summary[2].value = `${formatCount(state.pmnet.roi.grid)} x ${formatCount(state.pmnet.roi.grid)} cells (pending)`;
      resolution[1].value = "Pending";
      resolution[2].value = `${formatFixed(Number(state.pmnet.model.wallLossDb), 1, " dB")} | Rx ${formatFixed(Number(state.pmnet.roi.heightOffset), 1, " m")}`;
    }

    return {
      status: statusType(state.pmnet.status),
      visible,
      engineDetail: engineDetail(result?.engine),
      summary,
      resolution,
      colorbar: {
        visible,
        colormapLabel: `Colormap: ${colormap}`,
        rangeLabel: `Display limits: ${minDb.toFixed(0)} .. ${maxDb.toFixed(0)} dB`,
        minLabel: `${minDb.toFixed(0)} dB`,
        maxLabel: `${maxDb.toFixed(0)} dB`,
        gradient: colormapGradient(colormap),
      },
    };
  }

  function renderPmnetSceneForResult() {
    if (!state.pmnet.result) {
      return null;
    }
    return renderPmnetScene(
      getViewer(),
      state.pmnet.result,
      pmnetColorRange(),
      sceneOptions(),
    );
  }

  function renderPmnetResult() {
    const shouldShow = state.mode === "pmnet"
      && (state.pmnet.status !== "Idle" || Boolean(state.pmnet.result));
    syncLivePreviewStatusUi();
    if (
      state.mode === "pmnet"
      && state.pmnet.result
      && !getViewer().hasPmnetSurface?.("ground")
    ) {
      renderPmnetSceneForResult();
    }
    if (state.mode === "pmnet") {
      ui.linkChannelSection.classList.toggle("hidden", !shouldShow);
      ui.linkChannelSection.setAttribute("aria-hidden", String(!shouldShow));
      ui.linkChannelSection.classList.remove("radarResultMode");
      if (shouldShow) {
        ui.resultDockTitle.textContent = "PMNet Pathloss Results";
        ui.resultDockSubtitle.textContent = resultSubtitle();
      }
    }
    resultDock.update("pmnet", createViewModel(shouldShow), state.mode);
  }

  function resultSubtitle() {
    const engine = state.pmnet.result?.engine;
    if (engine?.id === "pmnet") {
      return "Pathloss map / PMNet checkpoint";
    }
    return "Pathloss map / Building-aware surrogate";
  }

  return Object.freeze({
    renderPmnetResult,
    renderPmnetSceneForResult,
  });
}
