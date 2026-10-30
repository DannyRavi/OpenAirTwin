import {normalizeColormapName} from "/js/colormaps.js";

function finiteOr(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function gridAxis(bounds, key, fallback) {
  const pair = bounds?.[key];
  if (!Array.isArray(pair) || pair.length !== 2) {
    return fallback;
  }
  return [finiteOr(pair[0], fallback[0]), finiteOr(pair[1], fallback[1])];
}

export function pmnetColorRange(state) {
  const minDb = finiteOr(state.display.colorMinDb, -140);
  const maxDb = finiteOr(state.display.colorMaxDb, -60);
  const colormap = normalizeColormapName(state.display.colormap);
  state.display.colormap = colormap;
  if (!(minDb < maxDb)) {
    return {minDb: -140, maxDb: -60, colormap};
  }
  return {minDb, maxDb, colormap};
}

/** Build the ground drape and building envelope triangle soups. */
export function buildPmnetGeometry(result) {
  const grid = result?.grid || {};
  const nx = Math.max(1, Math.floor(finiteOr(grid.nx, 1)));
  const ny = Math.max(1, Math.floor(finiteOr(grid.ny, 1)));
  const min = gridAxis(grid.bounds, "min", [0, 0]);
  const max = gridAxis(grid.bounds, "max", [min[0] + nx, min[1] + ny]);
  const dx = (max[0] - min[0]) / nx;
  const dy = (max[1] - min[1]) / ny;
  const lift = finiteOr(grid.height_offset, 0);
  const stride = nx + 1;
  const vertexZ = Array.isArray(grid.vertex_z) ? grid.vertex_z : [];
  const cellValues = Array.isArray(result?.values?.data) ? result.values.data : [];

  const groundPositions = new Float32Array(nx * ny * 18);
  const groundValues = new Float32Array(nx * ny * 2);
  const zAt = (index, fallback) => finiteOr(vertexZ[index], fallback);
  let cursor = 0;
  let valueCursor = 0;
  for (let row = 0; row < ny; row += 1) {
    const y0 = min[1] + row * dy;
    const y1 = y0 + dy;
    for (let col = 0; col < nx; col += 1) {
      const x0 = min[0] + col * dx;
      const x1 = x0 + dx;
      const base = row * stride + col;
      const z00 = zAt(base, lift) + lift;
      const z10 = zAt(base + 1, lift) + lift;
      const z01 = zAt(base + stride, lift) + lift;
      const z11 = zAt(base + stride + 1, lift) + lift;
      groundPositions.set(
        [
          x0, y0, z00,
          x1, y0, z10,
          x1, y1, z11,
          x0, y0, z00,
          x1, y1, z11,
          x0, y1, z01,
        ],
        cursor,
      );
      cursor += 18;
      const value = finiteOr(cellValues[row * nx + col], Number.NaN);
      groundValues[valueCursor] = value;
      groundValues[valueCursor + 1] = value;
      valueCursor += 2;
    }
  }

  const buildingPositions = [];
  const buildingValues = [];
  const cells = Array.isArray(result?.buildings?.cells) ? result.buildings.cells : [];
  const covered = new Set();
  for (const cell of cells) {
    if (!Array.isArray(cell) || cell.length < 6) {
      continue;
    }
    covered.add(`${Math.round(cell[0])}:${Math.round(cell[1])}`);
  }
  const roofLift = 0.3;
  for (const cell of cells) {
    if (!Array.isArray(cell) || cell.length < 6) {
      continue;
    }
    const [rawCol, rawRow, roofZ, groundZ, roofGain, groundGain] = cell;
    const col = Math.round(Number(rawCol));
    const row = Math.round(Number(rawRow));
    const x0 = min[0] + col * dx;
    const x1 = x0 + dx;
    const y0 = min[1] + row * dy;
    const y1 = y0 + dy;
    const top = finiteOr(roofZ, 0) + roofLift;
    const bottom = finiteOr(groundZ, 0);
    const roofValue = finiteOr(roofGain, Number.NaN);
    const wallValue = finiteOr(groundGain, Number.NaN);
    buildingPositions.push(
      x0, y0, top, x1, y0, top, x1, y1, top,
      x0, y0, top, x1, y1, top, x0, y1, top,
    );
    buildingValues.push(roofValue, roofValue);
    const neighbors = [
      [col - 1, row, "west"],
      [col + 1, row, "east"],
      [col, row - 1, "south"],
      [col, row + 1, "north"],
    ];
    for (const [neighborCol, neighborRow, side] of neighbors) {
      if (covered.has(`${neighborCol}:${neighborRow}`)) {
        continue;
      }
      if (side === "west") {
        buildingPositions.push(x0, y0, bottom, x0, y1, bottom, x0, y1, top, x0, y0, bottom, x0, y1, top, x0, y0, top);
      } else if (side === "east") {
        buildingPositions.push(x1, y0, bottom, x1, y0, top, x1, y1, top, x1, y0, bottom, x1, y1, top, x1, y1, bottom);
      } else if (side === "south") {
        buildingPositions.push(x0, y0, bottom, x0, y0, top, x1, y0, top, x0, y0, bottom, x1, y0, top, x1, y0, bottom);
      } else {
        buildingPositions.push(x0, y1, bottom, x1, y1, bottom, x1, y1, top, x0, y1, bottom, x1, y1, top, x0, y1, top);
      }
      buildingValues.push(wallValue, wallValue);
    }
  }

  return {
    ground: {positions: groundPositions, values: groundValues},
    buildings: {
      positions: new Float32Array(buildingPositions),
      values: new Float32Array(buildingValues),
    },
    cellCount: nx * ny,
    buildingCellCount: cells.length,
  };
}

export function renderPmnetScene(viewer, result, colorRange, {showBuildings = true} = {}) {
  const geometry = buildPmnetGeometry(result);
  viewer.renderPmnetSurface("ground", {
    positions: geometry.ground.positions,
    values: geometry.ground.values,
    min: colorRange.minDb,
    max: colorRange.maxDb,
    colormap: colorRange.colormap,
    opacity: 0.9,
    renderOrder: 13,
  });
  if (showBuildings && geometry.buildings.values.length) {
    viewer.renderPmnetSurface("buildings", {
      positions: geometry.buildings.positions,
      values: geometry.buildings.values,
      min: colorRange.minDb,
      max: colorRange.maxDb,
      colormap: colorRange.colormap,
      opacity: 0.97,
      renderOrder: 14,
    });
  } else {
    viewer.clearPmnetLayer("buildings");
  }
  return geometry;
}

export function clearPmnetScene(viewer) {
  viewer.clearPmnet();
}

export function createPmnetRenderer({viewerRef}) {
  const viewer = () => viewerRef.current;
  return {
    renderPmnetResult(result, colorRange, options) {
      return renderPmnetScene(viewer(), result, colorRange, options);
    },
    clearPmnetResult() {
      clearPmnetScene(viewer());
    },
  };
}
