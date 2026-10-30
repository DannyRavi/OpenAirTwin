import {
  cancelOfflineRegion,
  createOfflineRegion,
  createTileDownloadJob,
  cancelTileDownloadJob,
  deactivateOfflineRegion,
  deleteOfflineRegion,
  estimateOfflineRegion,
  getManifest,
  getOfflineStatus,
  getOpen3dHkTileCoverage,
  getRtCapabilities,
  getRtSceneSelection,
  getTileDownloadJob,
  openOfflineRegion,
  resumeOfflineRegion,
  saveOfflineBasemap,
  setOfflineMode,
  setRtSceneSelection,
  verifyOfflineRegion,
} from "/js/api.js";
import {entryMap, featureStore, PERFORMANCE_MODES, state, viewerRef} from "/js/app_state.js?v=20260723-radar-shared-groups";
import {FeatureRegistry, PickingRegistry, SettingsBus} from "/js/core/feature_registry.js";
import {bindAppShellRefs, bindControlSurfaceRefs, inputs, ui} from "/js/dom_refs.js?v=20260519-mode-isolation";
import {FEATURE_CATALOG} from "/js/features/feature_catalog.js?v=20260723-radar-shared-groups";
import {createAppDialogController} from "/js/controllers/app_dialog_controller.js?v=20260604-app-dialog";
import {createDevicePickingController} from "/js/controllers/device_picking_controller.js?v=20260519-mode-isolation";
import {createEntryMapController} from "/js/entry_map.js";
import {createMeasureController} from "/js/measure/measure_controller.js";
import {createParamTooltipController} from "/js/param_tooltips.js";
import {createPerformancePanelController} from "/js/performance_panel.js";
import {createSceneRenderStateController} from "/js/scene_render_state.js?v=20260723-empty-devices";
import {createSolverControlsController} from "/js/solver_controls.js?v=20260723-empty-devices";
import {mountAppShell} from "/@oat/app-shell/app-shell-runtime.tsx";

const settings = new SettingsBus();
const pickingRegistry = new PickingRegistry();
const featureRegistry = new FeatureRegistry({
  definitions: FEATURE_CATALOG,
  store: featureStore,
  settings,
  picking: pickingRegistry,
});

const context = {
  state,
  entryMap,
  ui,
  inputs,
  viewerRef,
  features: featureRegistry,
  settings,
  picking: pickingRegistry,
  featureServices: {},
  api: {
    createTileDownloadJob,
    cancelTileDownloadJob,
    getManifest,
    getOpen3dHkTileCoverage,
    getRtCapabilities,
    getRtSceneSelection,
    getTileDownloadJob,
    setRtSceneSelection,
  },
  controllers: {},
  utilities: {},
};
let appShellRuntime = null;
let resultDockModel = null;
let controlSurfaceModel = null;
let dialogController = null;
let performancePanel = null;
let entryMapController = null;
let solverControls = null;
let sceneRenderState = null;
let paramTooltips = null;
let devicePicking = null;
let measureController = null;

function initializeControllers() {
  dialogController = createAppDialogController(context);
  performancePanel = createPerformancePanelController(context);
  entryMapController = createEntryMapController(context);
  solverControls = createSolverControlsController(context);
  sceneRenderState = createSceneRenderStateController(context);
  paramTooltips = createParamTooltipController(context);
  devicePicking = createDevicePickingController(context);
  measureController = createMeasureController({
    state,
    ui,
    inputs,
    getViewer: currentViewer,
    shellUi: appShellRuntime.shell,
    controls: appShellRuntime.controls,
    renderAll: () => sceneRenderState.renderAll(),
    devicePicking,
    viewElement: appShellRuntime.element("view"),
  });
  measureController.attachPointerEvents();
  Object.assign(context.controllers, {
    dialogs: dialogController,
    performance: performancePanel,
    entry: entryMapController,
    solver: solverControls,
    scene: sceneRenderState,
    tooltips: paramTooltips,
    devicePicking,
    measure: measureController,
  });
}

function errorMessage(error) {
  return error?.message || String(error || "Unknown error");
}

function showErrorDialog(title, error) {
  return dialogController.alert({
    title,
    message: errorMessage(error),
    variant: "error",
  });
}

function currentViewer() {
  return viewerRef.current;
}

function setModeMenuOpen(open) {
  ui.modeSelector.open = open;
  ui.modeSelectButton.setAttribute("aria-expanded", String(open));
}

function closeModeMenu() {
  setModeMenuOpen(false);
}

function closeFeatureTransientUi() {
  for (const definition of featureRegistry.definitions()) {
    featureRegistry.instance(definition.id)?.closeTransientUi?.();
  }
}

const activeSolveActions = new Set();
let offlinePollTimer = null;
const basemapJobs = new Set();

function offlineRegionView(region, activeRegionId) {
  return {
    regionId: region.region_id,
    name: region.name,
    status: region.status,
    tileCount: region.tile_count,
    bounds: region.bounds,
    mapBounds: region.map_bounds || region.bounds,
    bytesTotal: region.bytes_total,
    assetCount: region.asset_count,
    missingCount: region.missing_count,
    progress: region.progress,
    message: region.message || "",
    error: region.error || null,
    basemap: Boolean(region.basemap),
    basemapUrl: region.basemap_url || null,
    offlineReady: Boolean(region.offline_ready),
    active: region.region_id === activeRegionId,
  };
}

function offlineDraftBounds(status) {
  const active = status.active_region;
  if (active?.bounds) return active.bounds;
  const sceneBounds = state.manifest?.scene?.bounds_wgs84;
  if (sceneBounds && Number.isFinite(sceneBounds.west)) return sceneBounds;
  const overviewBounds = state.entry.overview?.latLngBounds;
  if (overviewBounds?.isValid()) {
    return {
      west: overviewBounds.getWest(),
      east: overviewBounds.getEast(),
      south: overviewBounds.getSouth(),
      north: overviewBounds.getNorth(),
    };
  }
  return {west: -180, east: 180, south: -90, north: 90};
}

function applyOfflineStatus(status, {initial = false} = {}) {
  const shell = context.featureServices.shellUi;
  if (!shell) return;
  const previous = shellSnapshotOffline();
  const regions = (status.regions || []).map((region) =>
    offlineRegionView(region, status.active_region_id),
  );
  const busyRegion = (status.regions || []).find((region) =>
    ["queued", "preparing", "verifying", "cancelling"].includes(region.status),
  );
  const activeRegion = regions.find((region) => region.active) || null;
  const patch = {
    available: true,
    mode: status.mode,
    activeRegionId: status.active_region_id || null,
    activeRegionName: activeRegion?.name || null,
    regions,
    totalBytes: status.storage?.total_bytes || 0,
    freeBytes: status.storage?.free_bytes || 0,
    storageRoot: status.storage?.storage_root || "",
    sceneRoot: status.scene_root || "",
    busyRegionId: busyRegion?.region_id || null,
    busy: Boolean(busyRegion),
    error: null,
    notice: previous.notice || "",
    draftBounds:
      initial || !previous.draftBounds
        ? offlineDraftBounds(status)
        : previous.draftBounds,
  };
  shell.updateOffline(patch);
  window.__HAMMURABI_OFFLINE__ = {
    mode: status.mode,
    activeRegionId: status.active_region_id || null,
    activeRegion: activeRegion
      ? {
          regionId: activeRegion.regionId,
          name: activeRegion.name,
          bounds: activeRegion.bounds,
          mapBounds: activeRegion.mapBounds,
          basemapUrl: activeRegion.basemapUrl,
        }
      : null,
    regions: regions.map((region) => ({
      regionId: region.regionId,
      name: region.name,
      bounds: region.bounds,
      basemapUrl: region.basemapUrl,
      offlineReady: region.offlineReady,
    })),
  };
  if (status.mode === "offline" && document.body) {
    document.body.dataset.connectivity = "offline";
  } else if (document.body) {
    document.body.dataset.connectivity = status.mode;
  }
  syncHammurabiSceneUi();
}

function shellSnapshotOffline() {
  return context.featureServices.shellUi?.snapshot?.().offline || {};
}

async function refreshOfflineStatus({initial = false} = {}) {
  try {
    const status = await getOfflineStatus();
    applyOfflineStatus(status, {initial});
    return status;
  } catch (error) {
    context.featureServices.shellUi?.updateOffline({
      available: false,
      error: error?.message || String(error),
    });
    return null;
  }
}

function syncHammurabiSceneUi() {
  const shell = context.featureServices.shellUi;
  if (!shell) return;
  const manifest = state.manifest || {};
  const scene = manifest.scene || {};
  const offline = shellSnapshotOffline();
  const active = (offline.regions || []).find((region) => region.active);
  shell.updateScene({
    sceneId: manifest.scene_id || "",
    label: scene.label || manifest.scene_id || "",
    tileCount: Array.isArray(manifest.tiles) ? manifest.tiles.length : 0,
    loadedTileCount: sceneRenderState ? sceneRenderState.tileSelections().length : 0,
    source: active ? "Offline region" : "Local scene root",
    connectivity: offline.mode || "automatic",
    offlineRegion: active?.name || null,
  });
}

function stopOfflinePolling() {
  if (offlinePollTimer !== null) {
    window.clearInterval(offlinePollTimer);
    offlinePollTimer = null;
  }
}

function startOfflinePolling() {
  stopOfflinePolling();
  offlinePollTimer = window.setInterval(async () => {
    const status = await refreshOfflineStatus();
    if (!status) return;
    const hasWork = (status.regions || []).some((region) =>
      ["queued", "preparing", "verifying", "cancelling"].includes(region.status),
    );
    if (!hasWork) {
      stopOfflinePolling();
      const ready = (status.regions || []).filter(
        (region) => region.status === "ready" && !region.basemap,
      );
      for (const region of ready) {
        if (!basemapJobs.has(region.region_id)) {
          void cacheOfflineBasemap(region.region_id, region.map_bounds || region.bounds, region.layers || []);
        }
      }
    }
  }, 1200);
}

async function cacheOfflineBasemap(regionId, bounds, layers = []) {
  if (basemapJobs.has(regionId)) return;
  if (!layers.includes("map")) return;
  basemapJobs.add(regionId);
  const shell = context.featureServices.shellUi;
  try {
    if (!bounds) throw new Error("Region bounds are unknown");
    const {renderBasemapSnapshot} = await import("/@oat/runtime/entry-basemap.ts");
    const canvas = await renderBasemapSnapshot({
      style: "https://tiles.openfreemap.org/styles/bright",
      bounds,
      maxSize: 1536,
      applyEntryStyle: false,
    });
    const dataUrl = canvas.toDataURL("image/png");
    await saveOfflineBasemap(regionId, dataUrl);
    shell?.updateOffline({
      notice: "Offline map snapshot cached. Scene buildings and terrain are ready without internet.",
    });
    await refreshOfflineStatus();
  } catch (error) {
    shell?.updateOffline({
      notice:
        "The offline map snapshot could not be cached. Buildings and terrain still open offline; the local fallback map is used.",
    });
    console.warn("[offline] basemap snapshot failed", error);
  } finally {
    basemapJobs.delete(regionId);
  }
}

async function openOfflineRegionAndEnter(regionId) {
  await openOfflineRegion(regionId);
  state.manifest = await getManifest();
  state.entry.coverage = await getOpen3dHkTileCoverage();
  sceneRenderState.populateTileList(state.manifest);
  performancePanel.populatePerformanceControls(state.manifest);
  state.entry.overview = entryMapController.buildEntryOverview(
    state.manifest,
    state.entry.coverage,
  );
  entryMapController.renderEntryOverview();
  const status = await refreshOfflineStatus();
  const active = status?.active_region;
  const tileIds = active?.tile_ids || [];
  sceneRenderState.setTileSelection(tileIds);
  await handleEnterScene();
  syncHammurabiSceneUi();
}

async function runSolveFromDock(actionId, run) {
  if (activeSolveActions.has(actionId)) return;
  activeSolveActions.add(actionId);
  devicePicking.clearActiveDevice({render: false});
  solverControls.cancelLivePreview();
  sceneRenderState.renderAll();
  controlSurfaceModel.setActionBusy(actionId, true);
  try {
    await run();
  } finally {
    activeSolveActions.delete(actionId);
    controlSurfaceModel.setActionBusy(actionId, false);
    sceneRenderState.renderAll();
  }
}

Object.assign(context.utilities, {
  closeModeMenu,
  runSolveFromDock,
  showErrorDialog,
});

function sceneViewStorageKey() {
  const offline = shellSnapshotOffline();
  const active = (offline.regions || []).find((region) => region.active);
  const sceneId = state.manifest?.scene_id || "scene";
  return `hammurabi:camera:${active?.regionId || sceneId}`;
}

function saveSceneView() {
  const viewer = currentViewer();
  if (!viewer?.__ready) return;
  try {
    window.localStorage.setItem(
      sceneViewStorageKey(),
      JSON.stringify(viewer.getCameraState()),
    );
  } catch (error) {
    console.warn("[view] camera state could not be saved", error);
  }
}

function restoreSceneView() {
  const viewer = currentViewer();
  if (!viewer?.__ready) return false;
  try {
    const raw = window.localStorage.getItem(sceneViewStorageKey());
    if (!raw) return false;
    return viewer.setCameraState(JSON.parse(raw));
  } catch (error) {
    console.warn("[view] camera state could not be restored", error);
    return false;
  }
}

async function handleEnterScene() {
  devicePicking.clearActiveDevice({render: false, status: false});
  try {
    const result = await sceneRenderState.enterScene();
    restoreSceneView();
    syncHammurabiSceneUi();
    return result;
  } catch (error) {
    sceneRenderState.hideOverlay(null, true);
    state.tileLoadBusy = false;
    sceneRenderState.syncTileListUi();
    return showErrorDialog("Enter Scene Failed", error);
  }
}

function handleReturnToScene() {
  devicePicking.clearActiveDevice({render: false});
  sceneRenderState.resetSelectionToLoadedTiles();
  entryMapController.hideEntryScreen();
  sceneRenderState.renderAll();
}

async function handleAppShellCommand(command) {
  const payload = command.payload || {};
  if (command.name === "workbench.controls.toggle") {
    paramTooltips.hideTooltip();
    state.panelCollapsed = !state.panelCollapsed;
    sceneRenderState.syncControlSidebarUi();
    return;
  }
  if (command.name === "entry.sidebar.toggle") {
    state.entry.sidebarCollapsed = !state.entry.sidebarCollapsed;
    entryMapController.syncEntrySidebarUi();
    if (!state.entry.sidebarCollapsed) {
      window.setTimeout(() => ui.entryPlaceInput.focus(), 120);
    }
    return;
  }
  if (command.name === "entry.scene.return") return handleReturnToScene();
  if (command.name === "entry.search.submit") return entryMapController.runEntryPlaceSearch();
  if (command.name === "entry.place.select") return entryMapController.focusEntryPlaceResult(payload.index);
  if (command.name === "entry.map.fit") return entryMapController.fitEntryMapToView();
  if (command.name === "entry.map.focusSelection") {
    const selected = sceneRenderState.tileSelections();
    if (selected.length) {
      entryMapController.focusEntryMapTiles(selected, selected.length > 1 ? 0.98 : 1.08);
    }
    return;
  }
  if (command.name === "entry.map.zoomIn") return entryMap.map?.zoomIn(0.5);
  if (command.name === "entry.map.zoomOut") return entryMap.map?.zoomOut(0.5);
  if (command.name === "entry.map.pointerLeave") return entryMapController.hideEntryMapTooltip();
  if (command.name === "entry.scene.enter") return handleEnterScene();
  if (command.name === "entry.scene.open") {
    saveSceneView();
    devicePicking.clearActiveDevice({render: false});
    solverControls.cancelLivePreview();
    entryMapController.showEntryScreen();
    return;
  }
  if (command.name === "entry.tile.toggle") {
    return sceneRenderState.setTileChecked(payload.tileId, Boolean(payload.checked));
  }
  if (command.name === "performance.mode.select") {
    if (!PERFORMANCE_MODES.has(payload.mode)) return;
    state.performance.mode = payload.mode;
    currentViewer().setPerformanceMode(payload.mode);
    performancePanel.syncPerformanceUi();
    return;
  }
  if (command.name === "performance.lightMaterials.toggle") {
    state.performance.lightweightMaterials = Boolean(payload.checked);
    currentViewer().setLightweightMaterials(state.performance.lightweightMaterials);
    performancePanel.syncPerformanceUi();
    return;
  }
  if (command.name === "performance.dock.toggle") {
    state.performance.dockExpanded = !state.performance.dockExpanded;
    performancePanel.syncPerformanceUi();
    return;
  }
  if (command.name === "results.dock.toggle") {
    state.resultDock.expanded = !state.resultDock.expanded;
    sceneRenderState.syncResultDockUi();
    return;
  }
  if (command.name === "performance.categories.showAll") return performancePanel.showAllCategories();
  if (command.name === "performance.categories.hideHeavy") return performancePanel.hideHeavyCategories();
  if (command.name === "performance.category.toggle") {
    return performancePanel.setCategoryVisibility(payload.category, payload.checked);
  }
  if (command.name === "performance.tick") {
    if (currentViewer().__ready && !state.entry.visible) performancePanel.syncPerformanceUi();
    return;
  }
  if (command.name === "workbench.mode.toggle") {
    ui.modeSelectButton.setAttribute("aria-expanded", String(Boolean(payload.open)));
    return;
  }
  if (command.name === "workbench.mode.select") {
    const definition = featureRegistry.get(payload.mode);
    if (!definition) return;
    closeModeMenu();
    paramTooltips.hideTooltip();
    solverControls.cancelLivePreview();
    devicePicking.stopTxOrbit();
    devicePicking.clearActiveDevice({render: false});
    featureRegistry.activate(definition.id, context);
    sceneRenderState.renderAll();
    return;
  }
  if (command.name === "workbench.view.select") {
    state.workspaceView = String(payload.view || "analysis");
    if (state.workspaceView === "measure") {
      devicePicking.clearActiveDevice({render: false});
      measureController.setActive(true);
    } else {
      measureController.setActive(false);
    }
    sceneRenderState.renderAll();
    measureController.refresh();
    return;
  }
  if (command.name === "measure.mode.set") {
    measureController.setMode(payload.mode);
    return;
  }
  if (command.name === "measure.unit.set") {
    measureController.setUnit(payload.unit);
    return;
  }
  if (command.name === "measure.point.pick") {
    measureController.armPick(payload.point);
    return;
  }
  if (command.name === "measure.point.clear") {
    measureController.clearPoint(payload.point || "all");
    return;
  }
  if (command.name === "workbench.transient.dismiss") {
    closeModeMenu();
    closeFeatureTransientUi();
    return;
  }
  if (command.name === "viewer.precision.escape") {
    if (measureController.cancelPick()) return;
    devicePicking.handleDevicePrecisionEscape({key: "Escape", preventDefault() {}});
    return;
  }
  if (command.name === "workbench.resize") {
    paramTooltips.hideTooltip();
    if (!state.entry.visible) return;
    const selected = sceneRenderState.tileSelections();
    if (selected.length) {
      entryMapController.focusEntryMapTiles(selected, selected.length > 1 ? 0.98 : 1.08);
    } else {
      entryMapController.fitEntryMapToView();
    }
    return;
  }
  if (command.name === "viewer.camera.reset") {
    if (!currentViewer().__ready) return;
    currentViewer().resetView();
    sceneRenderState.renderAll();
    return;
  }
  if (command.name === "viewer.camera.fit") {
    if (!currentViewer().__ready) return;
    const selected = sceneRenderState.tileSelections();
    currentViewer().focusOnTiles(selected.length ? selected : [...currentViewer().loadedTileIds]);
    sceneRenderState.renderAll();
    return;
  }
  if (command.name === "app.fullscreen.toggle") {
    if (document.fullscreenElement) {
      await document.exitFullscreen();
    } else {
      await document.documentElement.requestFullscreen();
    }
    return;
  }
  if (command.name === "offline.refresh") {
    await refreshOfflineStatus();
    return;
  }
  if (command.name === "offline.bounds.useMap") {
    const bounds = entryMap.map?.getBounds?.();
    if (!bounds?.isValid()) {
      context.featureServices.shellUi?.updateOffline({
        notice: "Open the map to capture a viewport, or enter bounds manually.",
      });
      return;
    }
    context.featureServices.shellUi?.updateOffline({
      draftBounds: {
        west: bounds.getWest(),
        east: bounds.getEast(),
        south: bounds.getSouth(),
        north: bounds.getNorth(),
      },
      notice: "Bounds captured from the current map viewport.",
    });
    return;
  }
  if (command.name === "offline.mode.set") {
    try {
      const response = await setOfflineMode(payload.mode);
      applyOfflineStatus(response.status);
      const shell = context.featureServices.shellUi;
      shell?.updateOffline({
        notice:
          payload.mode === "offline"
            ? "Strict offline mode enabled. Remote map and scene assets are blocked."
            : "Connectivity mode updated.",
      });
    } catch (error) {
      context.featureServices.shellUi?.updateOffline({error: errorMessage(error)});
    }
    return;
  }
  if (command.name === "offline.region.estimate") {
    const shell = context.featureServices.shellUi;
    shell?.updateOffline({estimating: true, error: null});
    try {
      const estimate = await estimateOfflineRegion({bounds: payload.bounds});
      shell?.updateOffline({
        estimating: false,
        estimatedBytes: estimate.bytes,
        notice: `${estimate.tile_count} tile(s) selected.`,
      });
    } catch (error) {
      shell?.updateOffline({estimating: false, error: errorMessage(error)});
    }
    return;
  }
  if (command.name === "offline.region.create") {
    const shell = context.featureServices.shellUi;
    shell?.updateOffline({
      busy: true,
      error: null,
      notice: "Preparing offline scene assets...",
      estimatedBytes: null,
    });
    try {
      const response = await createOfflineRegion(payload);
      shell?.updateOffline({busyRegionId: response.region_id});
      await refreshOfflineStatus();
      startOfflinePolling();
    } catch (error) {
      shell?.updateOffline({busy: false, error: errorMessage(error)});
    }
    return;
  }
  if (command.name === "offline.region.open") {
    const shell = context.featureServices.shellUi;
    shell?.updateOffline({busy: true, error: null, notice: "Opening offline region..."});
    try {
      await openOfflineRegionAndEnter(payload.regionId);
      await refreshOfflineStatus();
      shell?.updateOffline({busy: false, notice: "Offline region loaded from local assets."});
    } catch (error) {
      shell?.updateOffline({busy: false, error: errorMessage(error)});
      await showErrorDialog("Open Offline Region Failed", error);
    }
    return;
  }
  if (command.name === "offline.region.deactivate") {
    const shell = context.featureServices.shellUi;
    shell?.updateOffline({busy: true, error: null});
    try {
      await deactivateOfflineRegion();
      state.manifest = await getManifest();
      state.entry.coverage = await getOpen3dHkTileCoverage();
      sceneRenderState.populateTileList(state.manifest);
      performancePanel.populatePerformanceControls(state.manifest);
      state.entry.overview = entryMapController.buildEntryOverview(
        state.manifest,
        state.entry.coverage,
      );
      entryMapController.renderEntryOverview();
      const sceneTileIds = new Set(state.manifest.tiles.map((tile) => tile.id));
      const defaults = (state.manifest.scene?.default_tile_ids || []).filter((tileId) =>
        sceneTileIds.has(tileId),
      );
      sceneRenderState.setTileSelection(defaults);
      if (!state.entry.visible) await handleEnterScene();
      await refreshOfflineStatus();
      shell?.updateOffline({busy: false, notice: "Returned to the local scene library."});
    } catch (error) {
      shell?.updateOffline({busy: false, error: errorMessage(error)});
    }
    return;
  }
  if (command.name === "offline.region.verify") {
    const shell = context.featureServices.shellUi;
    shell?.updateOffline({busy: true, error: null});
    try {
      await verifyOfflineRegion(payload.regionId);
      shell?.updateOffline({notice: "All region assets match their hashes."});
      await refreshOfflineStatus();
    } catch (error) {
      shell?.updateOffline({error: errorMessage(error)});
    } finally {
      shell?.updateOffline({busy: false});
    }
    return;
  }
  if (command.name === "offline.region.cancel") {
    try {
      await cancelOfflineRegion(payload.regionId);
      await refreshOfflineStatus();
    } catch (error) {
      context.featureServices.shellUi?.updateOffline({error: errorMessage(error)});
    }
    return;
  }
  if (command.name === "offline.region.retry") {
    try {
      await resumeOfflineRegion(payload.regionId);
      await refreshOfflineStatus();
      startOfflinePolling();
    } catch (error) {
      context.featureServices.shellUi?.updateOffline({error: errorMessage(error)});
    }
    return;
  }
  if (command.name === "offline.region.basemap") {
    const status = await refreshOfflineStatus();
    const region = (status?.regions || []).find(
      (candidate) => candidate.region_id === payload.regionId,
    );
    if (region) await cacheOfflineBasemap(region.region_id, region.map_bounds || region.bounds, ["map"]);
    return;
  }
  if (command.name === "offline.region.delete") {
    const status = await refreshOfflineStatus();
    const region = (status?.regions || []).find(
      (candidate) => candidate.region_id === payload.regionId,
    );
    if (!region) return;
    const confirmed = await dialogController.confirm({
      title: "Delete offline region",
      message: `Delete "${region.name}" and its ${(region.bytes_total / (1024 * 1024)).toFixed(1)} MB of local scene assets?`,
      confirmLabel: "Delete region",
      variant: "warning",
    });
    if (!confirmed) return;
    try {
      await deleteOfflineRegion(payload.regionId);
      await refreshOfflineStatus();
      context.featureServices.shellUi?.updateOffline({
        notice: `Deleted ${region.name}. No unrelated data was removed.`,
      });
    } catch (error) {
      context.featureServices.shellUi?.updateOffline({error: errorMessage(error)});
    }
    return;
  }
  if (command.name === "dialog.primary") return dialogController.confirmActiveDialog();
  if (command.name === "dialog.secondary") return dialogController.finishDialog(false);
  if (command.name === "dialog.close") return dialogController.cancelActiveDialog();
  if (command.name === "loading.cancel") return sceneRenderState.cancelOverlay();
}

function attachRuntimeEvents() {
  paramTooltips.attach();

  featureRegistry.attachEvents(context);

  const commonSolverIds = new Set([
    "cfgFrequency", "cfgMaxDepth", "cfgLos", "cfgSpecular",
    "cfgDiffuse", "cfgRefraction", "cfgSeed",
  ]);
  const antennaIds = new Set([
    "txArrayPattern", "txArrayPolarization", "txArrayRows", "txArrayCols",
    "txArrayVerticalSpacing", "txArrayHorizontalSpacing", "rxArrayPattern",
    "rxArrayPolarization", "rxArrayRows", "rxArrayCols",
    "rxArrayVerticalSpacing", "rxArrayHorizontalSpacing",
  ]);
  controlSurfaceModel.setCommandHandler(async (command) => {
    if (command.name === "workbench.control.commit") {
      const {controlId} = command.payload;
      if (commonSolverIds.has(controlId)) {
        settings.publish("common-solver");
        sceneRenderState.renderAll();
        return;
      }
      if (antennaIds.has(controlId)) {
        solverControls.readAntennaArrayInputs();
        settings.publish("antenna");
        sceneRenderState.renderAll();
        return;
      }
      if (measureController.handleControlCommit(controlId)) return;
      for (const definition of featureRegistry.definitions()) {
        if (await featureRegistry.instance(definition.id)?.handleControlCommit?.(controlId)) return;
      }
      return;
    }
    if (command.name === "workbench.control.action") {
      const {actionId, value} = command.payload;
      if (actionId === "btnOrbitTx") {
        devicePicking.toggleTxOrbit();
        return;
      }
      if (measureController.handleControlAction(actionId)) return;
      for (const definition of featureRegistry.definitions()) {
        if (await featureRegistry.instance(definition.id)?.handleControlAction?.(actionId, value)) return;
      }
      return;
    }
    if (command.name === "workbench.control.group.toggle") {
      const {controlId, open} = command.payload;
      for (const definition of featureRegistry.definitions()) {
        if (await featureRegistry.instance(definition.id)?.handleControlGroupToggle?.(controlId, open)) return;
      }
    }
  });

  devicePicking.attachPointerEvents(appShellRuntime.element("view"));
}

async function bootstrap() {
  appShellRuntime = mountAppShell({
    activeMode: state.mode,
    container: document.body,
    reportError: ({error}) => {
      console.error("[app-shell]", error);
    },
  });
  bindAppShellRefs(appShellRuntime);
  controlSurfaceModel = appShellRuntime.controls;
  resultDockModel = appShellRuntime.results;
  bindControlSurfaceRefs(controlSurfaceModel);
  context.featureServices.controls = controlSurfaceModel;
  context.featureServices.resultDock = resultDockModel;
  context.featureServices.shellUi = appShellRuntime.shell;
  context.featureServices.deepMimoDatasets = {
    update: appShellRuntime.datasets.update,
    setToggleHandler: appShellRuntime.setDatasetToggleHandler,
  };
  initializeControllers();
  appShellRuntime.setCommandHandler(handleAppShellCommand);
  appShellRuntime.setDatasetToggleHandler(null);
  controlSurfaceModel.setCommandHandler(null);
  featureRegistry.initialize(context);
  featureRegistry.activate(state.mode, context);
  sceneRenderState.showOverlay({title: "Loading Scene", message: "Loading scene manifest...", percent: 10, force: true});
  attachRuntimeEvents();
  sceneRenderState.showOverlay({title: "Loading Scene", message: "Loading RT capabilities...", percent: 14, force: true});
  const [rtCapabilities, manifest, coverage, offlineStatus] = await Promise.all([
    getRtCapabilities(),
    getManifest(),
    getOpen3dHkTileCoverage(),
    getOfflineStatus().catch(() => null),
  ]);
  state.rtCapabilities = rtCapabilities;
  solverControls.applyRtCapabilities(state.rtCapabilities);
  sceneRenderState.showOverlay({title: "Loading Scene", message: "Loading scene manifest...", percent: 18, force: true});
  state.manifest = manifest;
  sceneRenderState.showOverlay({title: "Loading Scene", message: "Loading tile coverage...", percent: 22, force: true});
  state.entry.coverage = coverage;
  sceneRenderState.populateTileList(state.manifest);
  performancePanel.populatePerformanceControls(state.manifest);
  const sceneTileIds = new Set(state.manifest.tiles.map((tile) => tile.id));
  const defaultTileIds = Array.isArray(state.manifest.scene?.default_tile_ids)
    ? state.manifest.scene.default_tile_ids.filter((tileId) => sceneTileIds.has(tileId))
    : [];
  sceneRenderState.setTileSelection(defaultTileIds);
  if (offlineStatus) {
    applyOfflineStatus(offlineStatus, {initial: true});
    const hasActiveJob = (offlineStatus.regions || []).some((region) =>
      ["queued", "preparing", "verifying", "cancelling"].includes(region.status),
    );
    if (hasActiveJob) startOfflinePolling();
  }
  state.entry.overview = entryMapController.buildEntryOverview(state.manifest, state.entry.coverage);
  entryMapController.renderEntryOverview();
  sceneRenderState.hideOverlay(null, true);
  syncHammurabiSceneUi();
  if (offlineStatus) {
    const shell = context.featureServices.shellUi;
    if (offlineStatus.active_region?.status === "ready") {
      shell?.updateOffline({
        notice: `Offline region "${offlineStatus.active_region.name}" is active and served from local storage.`,
      });
    }
    if (offlineStatus.mode === "offline" && !offlineStatus.active_region) {
      shell?.updateOffline({
        notice: "Strict offline mode is active. Open a downloaded region to load buildings.",
      });
    }
  }

  sceneRenderState.renderAll();
  if (state.entry.overview) {
    entryMapController.showEntryScreen({syncOverview: false});
  } else {
    ui.panel.style.display = "flex";
    sceneRenderState.syncControlSidebarUi();
  }
  window.__OPENAIRTWIN_UI_READY_MS__ = window.performance.now();
  window.dispatchEvent(new CustomEvent("hammurabi:ui-ready"));
}

window.addEventListener("pagehide", () => {
  saveSceneView();
  featureRegistry.dispose(context);
  devicePicking?.dispose?.();
  measureController?.dispose?.();
  paramTooltips?.dispose?.();
  sceneRenderState?.dispose?.();
  entryMapController?.dispose?.();
  performancePanel?.dispose?.();
  dialogController?.dispose?.();
  appShellRuntime?.dispose();
  appShellRuntime = null;
  resultDockModel = null;
  controlSurfaceModel = null;
}, {once: true});

bootstrap().catch((error) => {
  try {
    sceneRenderState?.hideOverlay(null, true);
  } catch (cleanupError) {
    console.error("Failed to restore the UI after startup error", cleanupError);
    if (ui.loadingScreen) ui.loadingScreen.style.display = "none";
  }
  if (dialogController) return showErrorDialog("Startup Failed", error);
  console.error("Startup Failed", error);
});
