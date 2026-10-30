export function createPmnetState() {
  return {
    generation: 0,
    failureKind: null,
    tx: null,
    txVisual: null,
    surfaceClearanceM: 1.5,
    roi: {
      size: [200.0, 200.0],
      grid: 128,
      heightOffset: 1.5,
    },
    model: {
      engine: "auto",
      wallLossDb: 6.0,
      rxHeightM: 1.5,
    },
    signal: {txPowerDbm: 30.0},
    display: {
      colorMinDb: -140,
      colorMaxDb: -60,
      colormap: "jet",
      showBuildings: true,
    },
    jobId: null,
    result: null,
    status: "Idle",
  };
}
