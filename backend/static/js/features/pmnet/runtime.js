export function createPmnetFeature(context) {
  const {state} = context;
  const {controller, resultView} = context;
  const shared = context.featureServices.solver;
  const solver = () => context.controllers.solver;
  const scene = () => context.controllers.scene;
  const picking = () => context.controllers.devicePicking;

  function runRequirementMessage() {
    return Array.isArray(state.pmnet.tx) ? "" : "Place a PMNet Tx before predicting the pathloss map.";
  }

  function attachEvents() {
    // PMNet fields and actions are emitted by the React control surface.
  }

  const deviceIds = new Set(["pmnetTxX", "pmnetTxY", "pmnetTxZ"]);
  const roiIds = new Set([
    "pmnetSizeX", "pmnetSizeY", "pmnetGrid", "pmnetHeightOffset",
    "pmnetWallLoss", "pmnetRxHeight", "pmnetEngine", "pmnetTxPower",
  ]);
  const displayIds = new Set(["pmnetColormap", "pmnetColorMin", "pmnetColorMax", "pmnetShowBuildings"]);

  function handleControlCommit(controlId) {
    if (deviceIds.has(controlId)) {
      solver().readPmnetInputs();
      controller.invalidatePmnetResult();
      picking().refreshPickStatus("pmnet");
      scene().renderAll();
      return true;
    }
    if (roiIds.has(controlId)) {
      solver().readPmnetInputs();
      controller.invalidatePmnetResult();
      scene().renderAll();
      return true;
    }
    if (!displayIds.has(controlId)) {
      return false;
    }
    try {
      solver().readPmnetInputs();
      solver().rerenderPmnetOverlay();
      resultView.renderPmnetResult();
    } catch (error) {
      context.utilities.showErrorDialog("PMNet Display Failed", error);
    }
    return true;
  }

  function handleControlAction(actionId) {
    if (actionId === "btnPickPmnetTx") {
      picking().openDevicePrecision("pmnet-tx");
      return true;
    }
    if (actionId !== "btnRunPmnet") {
      return false;
    }
    return context.utilities.runSolveFromDock(
      "btnRunPmnet",
      () => controller.runPmnet(),
    ).catch((error) => {
      scene().hideOverlay(null, true);
      return context.utilities.showErrorDialog("PMNet Failed", error);
    }).then(() => true);
  }

  return {
    attachEvents,
    handleControlAction,
    handleControlCommit,
    applyPick(pick, target) {
      const position = shared.pickPositionWithSurfaceClearance(pick, target.scope);
      shared.setLogicalAndVisual(state.pmnet, "tx", position);
      controller.invalidatePmnetResult();
    },
    markerPositions() {
      return {tx: state.pmnet.txVisual, rx: null};
    },
    readInputs() {
      solver().readPmnetInputs();
    },
    runRequirementMessage,
    activate() {
      context.viewerRef.current.clearOverlay();
    },
    onSettingsChanged() {
      controller.invalidatePmnetResult();
    },
    render() {
      resultView.renderPmnetResult();
    },
  };
}
