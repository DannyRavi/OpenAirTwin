import {defineFeature} from "/js/core/feature_registry.js";
import {createPmnetController} from "/js/controllers/pmnet_controller.js";
import {clearPmnetScene, createPmnetRenderer} from "/js/features/pmnet/renderer.js";
import {createPmnetFeature} from "/js/features/pmnet/runtime.js";
import {createPmnetState} from "/js/features/pmnet/state.js";
import {createPmnetTransport} from "/js/features/pmnet/transport.js";
import {createPmnetResultView} from "/js/ui/pmnet_result_view.js";

function createResultView(context) {
  const shared = context.featureServices.solver;
  return createPmnetResultView({
    state: context.state,
    ui: context.ui,
    getViewer: shared.getViewer,
    pmnetColorRange: shared.pmnetColorRange,
    syncLivePreviewStatusUi: shared.syncLivePreviewStatusUi,
    resultDock: context.featureServices.resultDock,
  });
}

function createController(context) {
  const shared = context.featureServices.solver;
  return createPmnetController({
    state: context.state,
    createPmnetJob: context.transport.createPmnetJob,
    getPmnetJob: context.transport.getPmnetJob,
    getPmnetResult: context.transport.getPmnetResult,
    readPmnetInputs: shared.readPmnetInputs,
    pmnetJobPayload: shared.pmnetJobPayload,
    pmnetColorRange: shared.pmnetColorRange,
    showOverlay: shared.showOverlay,
    hideOverlay: shared.hideOverlay,
    renderPmnetResult: context.resultView.renderPmnetResult,
    renderPmnetScene: context.resultView.renderPmnetSceneForResult,
    clearPmnetScene: () => clearPmnetScene(shared.getViewer()),
  });
}

export const pmnetFeature = defineFeature({
  id: "pmnet",
  order: 60,
  title: "PMNet Pathloss",
  createState: createPmnetState,
  createTransport: createPmnetTransport,
  createController,
  createResultView,
  createRenderer: createPmnetRenderer,
  createFeature: createPmnetFeature,
  inputReader: "readPmnetInputs",
  settingsDependencies: ["common-solver"],
  sharedControlPolicy: {tx: true, rx: false, antenna: false, channel: false},
  ui: {
    tabRef: "tabPmnet",
    panelRef: "pmnetPanel",
    runButtonRef: "btnRunPmnet",
    disableDuringTileLoad: true,
    parameterGroups: ["pmnetOnlyParams"],
    resultMethod: "renderPmnetResult",
  },
  pickingTargets: [
    {id: "pmnet-tx", role: "tx", scope: "pmnet", prompt: "Click any surface to place PMNet Tx", buttonRef: "btnPickPmnetTx", cardRef: "pmnetTxDeviceCard", precisionTitle: "PMNet Tx", precision: true, clearance: true, readMethod: "readPmnetInputs", applyMethod: "applyPmnetPick"},
  ],
  renderLayers: ["pmnet"],
});
