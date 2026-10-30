const INCHES_PER_METER = 39.37007874015748;
const PICK_TAP_MAX_MOVE_PX = 6;
const MEASURE_POINT_IDS = Object.freeze([
  "measureAX", "measureAY", "measureAZ",
  "measureBX", "measureBY", "measureBZ",
]);
const MEASURE_FIELD_IDS = new Set(MEASURE_POINT_IDS);
const MEASURE_UNITS = new Set(["m", "in"]);
const MEASURE_MODES = new Set(["distance", "size"]);

export function metersToUnit(meters, unit) {
  return unit === "in" ? Number(meters) * INCHES_PER_METER : Number(meters);
}

export function unitToMeters(value, unit) {
  return unit === "in" ? Number(value) / INCHES_PER_METER : Number(value);
}

export function measureUnitLabel(unit) {
  return unit === "in" ? "in" : "m";
}

/** Format a length in the active unit; values are always stored in metres. */
export function formatLength(meters, unit, {digits = null, unitSuffix = true} = {}) {
  const numeric = Number(meters);
  if (!Number.isFinite(numeric)) {
    return "--";
  }
  const converted = metersToUnit(numeric, unit);
  const precision = digits ?? (unit === "in" ? 1 : 2);
  const text = converted.toFixed(precision);
  return unitSuffix ? `${text} ${measureUnitLabel(unit)}` : text;
}

/** Raw editable value for the active unit (no suffix). */
export function measureFieldValue(meters, unit) {
  if (!Number.isFinite(Number(meters))) {
    return "";
  }
  return metersToUnit(meters, unit).toFixed(unit === "in" ? 1 : 2);
}

export function formatArea(squareMeters, unit) {
  const numeric = Number(squareMeters);
  if (!Number.isFinite(numeric)) {
    return "--";
  }
  if (unit === "in") {
    const squareInches = numeric * INCHES_PER_METER * INCHES_PER_METER;
    return `${squareInches.toFixed(0)} in²`;
  }
  return `${numeric.toFixed(1)} m²`;
}

export function formatVolume(cubicMeters, unit) {
  const numeric = Number(cubicMeters);
  if (!Number.isFinite(numeric)) {
    return "--";
  }
  if (unit === "in") {
    const cubicInches = numeric * INCHES_PER_METER ** 3;
    return `${cubicInches.toFixed(0)} in³`;
  }
  return `${numeric.toFixed(2)} m³`;
}

function normalizePoint(values) {
  if (!Array.isArray(values) || values.length !== 3) {
    return null;
  }
  const point = values.map(Number);
  return point.every(Number.isFinite) ? point : null;
}

function pointSummary(point) {
  if (!point) {
    return null;
  }
  return {
    x: Number(point[0]),
    y: Number(point[1]),
    z: Number(point[2]),
  };
}

export function measureReadout(state) {
  const a = normalizePoint(state.a);
  const b = normalizePoint(state.b);
  const unit = MEASURE_UNITS.has(state.unit) ? state.unit : "m";
  const mode = MEASURE_MODES.has(state.mode) ? state.mode : "distance";
  const empty = {a, b, unit, mode, ready: false};

  if (!a || !b) {
    return {
      ...empty,
      summary: "--",
      dockSummary: "--",
      secondary:
        a || b
          ? "Place the remaining point to read the measurement."
          : "Pick point A and point B to start measuring.",
      rows: [],
      hint: a
        ? "Click Pick B in the device bar, then click the 3D scene."
        : "Click Pick A in the device bar, then click the 3D scene.",
    };
  }

  const dx = Math.abs(b[0] - a[0]);
  const dy = Math.abs(b[1] - a[1]);
  const dz = Math.abs(b[2] - a[2]);
  const distance = Math.hypot(dx, dy, dz);
  const footprint = dx * dy;
  const volume = dx * dy * dz;
  const otherUnit = unit === "in" ? "m" : "in";

  const axisText = (value) => formatLength(value, unit, {unitSuffix: false});
  if (mode === "size") {
    return {
      ...empty,
      ready: true,
      summary: `${formatLength(dx, unit)} × ${formatLength(dy, unit)} × ${formatLength(dz, unit)}`,
      dockSummary: `${axisText(dx)} × ${axisText(dy)} × ${axisText(dz)} ${measureUnitLabel(unit)}`,
      secondary: `Footprint ${formatArea(footprint, unit)} · Volume ${formatVolume(volume, unit)} · Diagonal ${formatLength(distance, unit)}`,
      rows: [
        {id: "width", label: "Width (X)", value: formatLength(dx, unit)},
        {id: "depth", label: "Depth (Y)", value: formatLength(dy, unit)},
        {id: "height", label: "Height (Z)", value: formatLength(dz, unit)},
        {id: "area", label: "Footprint", value: formatArea(footprint, unit)},
        {id: "volume", label: "Volume", value: formatVolume(volume, unit)},
        {id: "other", label: otherUnit === "in" ? "Diagonal (in)" : "Diagonal (m)", value: formatLength(distance, otherUnit)},
      ],
      hint: "Adjust the numeric fields to refine the measured extents.",
    };
  }

  return {
    ...empty,
    ready: true,
    summary: formatLength(distance, unit),
    dockSummary: formatLength(distance, unit),
    secondary: `≈ ${formatLength(distance, otherUnit)} · ΔX ${formatLength(dx, unit)} · ΔY ${formatLength(dy, unit)} · ΔZ ${formatLength(dz, unit)}`,
    rows: [
      {id: "dx", label: "ΔX", value: formatLength(dx, unit)},
      {id: "dy", label: "ΔY", value: formatLength(dy, unit)},
      {id: "dz", label: "ΔZ", value: formatLength(dz, unit)},
      {id: "distance", label: "Distance", value: formatLength(distance, unit)},
      {id: "other", label: otherUnit === "in" ? "Distance (in)" : "Distance (m)", value: formatLength(distance, otherUnit)},
      {id: "slope", label: "Rise / Run", value: `${formatLength(dz, unit)} / ${formatLength(Math.hypot(dx, dy), unit)}`},
    ],
    hint: "Adjust the numeric fields to refine the measurement.",
  };
}

export function measureShellSnapshot(state) {
  const readout = measureReadout(state);
  return {
    mode: readout.mode,
    unit: readout.unit,
    pickTarget: state.pickTarget === "a" || state.pickTarget === "b" ? state.pickTarget : null,
    hint:
      state.pickTarget === "a" || state.pickTarget === "b"
        ? `Click the 3D scene to place point ${state.pickTarget.toUpperCase()}.`
        : readout.hint,
    summary: readout.summary,
    secondary: readout.secondary,
    rows: readout.rows,
    hasA: Boolean(readout.a),
    hasB: Boolean(readout.b),
    ready: readout.ready,
    a: pointSummary(readout.a),
    b: pointSummary(readout.b),
  };
}

export function createMeasureController({
  state,
  ui,
  inputs,
  getViewer,
  shellUi,
  renderAll,
  controls,
  devicePicking,
  viewElement,
}) {
  let pointerAttached = false;
  let tapCandidate = null;

  function isActive() {
    return state.workspaceView === "measure";
  }

  function measureState() {
    return state.measure;
  }

  function pointValues(point) {
    return normalizePoint(measureState()[point]);
  }

  function setPoint(point, position) {
    const normalized = normalizePoint(position);
    if (!normalized) {
      return false;
    }
    measureState()[point] = normalized;
    measureState()[`${point}Visual`] = normalized;
    measureState().pickTarget = null;
    commit();
    return true;
  }

  function clearPoint(point = "all") {
    const target = measureState();
    if (point === "a" || point === "all") {
      target.a = null;
      target.aVisual = null;
    }
    if (point === "b" || point === "all") {
      target.b = null;
      target.bVisual = null;
    }
    target.pickTarget = null;
    commit();
  }

  function setMode(mode) {
    if (!MEASURE_MODES.has(mode)) {
      return;
    }
    measureState().mode = mode;
    commit();
  }

  function setUnit(unit) {
    if (!MEASURE_UNITS.has(unit)) {
      return;
    }
    measureState().unit = unit;
    commit();
  }

  function armPick(point) {
    if (point !== "a" && point !== "b") {
      return;
    }
    measureState().pickTarget = measureState().pickTarget === point ? null : point;
    if (measureState().pickTarget) {
      devicePicking?.clearActiveDevice?.({render: false});
    }
    commit();
  }

  function cancelPick() {
    if (!measureState().pickTarget) {
      return false;
    }
    measureState().pickTarget = null;
    tapCandidate = null;
    commit();
    return true;
  }

  function readMeasureInputs() {
    for (const [point, prefix] of [["a", "A"], ["b", "B"]]) {
      const refs = [inputs[`measure${prefix}X`], inputs[`measure${prefix}Y`], inputs[`measure${prefix}Z`]];
      if (refs.some((ref) => !ref)) {
        continue;
      }
      const texts = refs.map((ref) => String(ref.value).trim());
      if (texts.some((text) => text === "")) {
        continue;
      }
      const values = texts.map((text) => Number(text));
      if (!values.every(Number.isFinite)) {
        continue;
      }
      const position = values.map((value) => unitToMeters(value, measureState().unit));
      measureState()[point] = position;
      measureState()[`${point}Visual`] = position;
    }
  }

  function handleControlCommit(controlId) {
    if (!MEASURE_FIELD_IDS.has(controlId)) {
      return false;
    }
    readMeasureInputs();
    refresh();
    return true;
  }

  function handleControlAction(actionId) {
    if (actionId === "btnMeasurePickA") {
      armPick("a");
      return true;
    }
    if (actionId === "btnMeasurePickB") {
      armPick("b");
      return true;
    }
    if (actionId === "btnMeasureClear") {
      clearPoint("all");
      return true;
    }
    return false;
  }

  function updateReadoutUi() {
    const readout = measureReadout(measureState());
    if (ui.measureDistance) {
      ui.measureDistance.textContent = readout.ready ? readout.dockSummary : "--";
      ui.measureDistance.title = readout.ready ? readout.secondary : "";
    }
    if (ui.hintText) {
      ui.hintText.textContent = measureState().pickTarget
        ? `Click the 3D scene to place point ${measureState().pickTarget.toUpperCase()}.`
        : measureState().ready
          ? "Edit A/B coordinates or pick new points."
          : "Click Pick A or Pick B, then click the 3D scene.";
    }
    const patches = [];
    for (const [point, prefix] of [["a", "A"], ["b", "B"]]) {
      const refs = [inputs[`measure${prefix}X`], inputs[`measure${prefix}Y`], inputs[`measure${prefix}Z`]];
      const values = pointValues(point);
      refs.forEach((ref, index) => {
        if (!ref) return;
        ref.placeholder = values ? "" : "—";
        ref.disabled = !measureState().active;
        if (document.activeElement !== ref) {
          ref.value = values ? measureFieldValue(values[index], readout.unit) : "";
          patches.push({id: ref.id, value: ref.value, disabled: ref.disabled});
        }
      });
    }
    if (patches.length) {
      controls?.updateFields?.(patches);
    }
    for (const [id, active] of [
      ["btnMeasurePickA", measureState().pickTarget === "a"],
      ["btnMeasurePickB", measureState().pickTarget === "b"],
    ]) {
      const button = ui[id];
      if (!button) continue;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    }
  }

  function draw() {
    const viewer = getViewer();
    if (!isActive() || !viewer) {
      viewer?.clearMeasure?.();
      return;
    }
    const a = pointValues("a");
    const b = pointValues("b");
    if (!a && !b) {
      viewer.clearMeasure?.();
      return;
    }
    viewer.renderMeasure?.({
      a,
      b,
      mode: measureState().mode,
      pointALabel: "A",
      pointBLabel: "B",
    });
  }

  function publishShellState() {
    shellUi?.updateMeasure?.(measureShellSnapshot(measureState()));
  }

  function refresh() {
    updateReadoutUi();
    draw();
    publishShellState();
  }

  function commit() {
    renderAll?.();
    refresh();
  }

  function setActive(active) {
    measureState().active = Boolean(active);
    if (!active) {
      measureState().pickTarget = null;
      tapCandidate = null;
      getViewer()?.clearMeasure?.();
    }
    refresh();
  }

  function pickAt(clientX, clientY) {
    const point = measureState().pickTarget;
    if (!point) {
      return false;
    }
    const pick = getViewer()?.pickOnSurface?.(clientX, clientY, 0);
    const position = pick?.surfacePosition || pick?.logicalPosition || null;
    if (!position) {
      return false;
    }
    return setPoint(point, position);
  }

  function handlePointerDown(event) {
    if (!isActive() || !measureState().pickTarget) {
      return;
    }
    if (!event.isPrimary || event.button !== 0) {
      return;
    }
    tapCandidate = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      canceled: false,
    };
    event.preventDefault();
    event.stopPropagation();
  }

  function handlePointerMove(event) {
    if (!tapCandidate || event.pointerId !== tapCandidate.pointerId) {
      return;
    }
    const dx = event.clientX - tapCandidate.startX;
    const dy = event.clientY - tapCandidate.startY;
    if (dx * dx + dy * dy > PICK_TAP_MAX_MOVE_PX * PICK_TAP_MAX_MOVE_PX) {
      tapCandidate.canceled = true;
    }
  }

  function handlePointerUp(event) {
    if (!tapCandidate || event.pointerId !== tapCandidate.pointerId) {
      return;
    }
    const candidate = tapCandidate;
    tapCandidate = null;
    if (candidate.canceled) {
      return;
    }
    if (pickAt(event.clientX, event.clientY)) {
      event.preventDefault();
      event.stopPropagation();
    }
  }

  function attachPointerEvents() {
    if (pointerAttached || !viewElement) {
      return;
    }
    pointerAttached = true;
    viewElement.addEventListener("pointerdown", handlePointerDown, {capture: true});
    window.addEventListener("pointermove", handlePointerMove, {capture: true});
    window.addEventListener("pointerup", handlePointerUp, {capture: true});
    window.addEventListener("pointercancel", () => {
      tapCandidate = null;
    });
    window.addEventListener("blur", () => {
      tapCandidate = null;
    });
  }

  function dispose() {
    if (!pointerAttached || !viewElement) {
      return;
    }
    pointerAttached = false;
    viewElement.removeEventListener("pointerdown", handlePointerDown, {capture: true});
    window.removeEventListener("pointermove", handlePointerMove, {capture: true});
    window.removeEventListener("pointerup", handlePointerUp, {capture: true});
  }

  return {
    attachPointerEvents,
    armPick,
    cancelPick,
    clearPoint,
    dispose,
    handleControlAction,
    handleControlCommit,
    isActive,
    readMeasureInputs,
    refresh,
    setActive,
    setMode,
    setUnit,
  };
}
