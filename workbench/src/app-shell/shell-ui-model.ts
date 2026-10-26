import { ObservableStateAdapter } from "../runtime/observable-state.ts";

export interface EntryPlaceResultViewModel {
  readonly index: number;
  readonly title: string;
  readonly detail: string;
  readonly meta: string;
  readonly active: boolean;
}

export interface TileSelectionViewModel {
  readonly id: string;
  readonly title: string;
  readonly detail: string;
  readonly selected: boolean;
  readonly loaded: boolean;
  readonly pendingAdd: boolean;
  readonly pendingRemove: boolean;
  readonly status: string;
  readonly statusClassName: string;
  readonly disabled: boolean;
}

export interface PerformanceCategoryViewModel {
  readonly name: string;
  readonly stats: string;
  readonly visible: boolean;
}

export interface OfflineRegionViewModel {
  readonly regionId: string;
  readonly name: string;
  readonly status: string;
  readonly tileCount: number;
  readonly bounds: Readonly<{
    west: number;
    east: number;
    south: number;
    north: number;
  }>;
  readonly mapBounds: Readonly<{
    west: number;
    east: number;
    south: number;
    north: number;
  }>;
  readonly bytesTotal: number;
  readonly assetCount: number;
  readonly missingCount: number;
  readonly progress: number;
  readonly message: string;
  readonly error: string | null;
  readonly basemap: boolean;
  readonly basemapUrl: string | null;
  readonly offlineReady: boolean;
  readonly active: boolean;
}

export interface OfflineBounds {
  readonly west: number;
  readonly east: number;
  readonly south: number;
  readonly north: number;
}

export interface OfflineUiSnapshot {
  readonly available: boolean;
  readonly mode: "online" | "offline" | "automatic";
  readonly activeRegionId: string | null;
  readonly activeRegionName: string | null;
  readonly regions: readonly OfflineRegionViewModel[];
  readonly totalBytes: number;
  readonly freeBytes: number;
  readonly storageRoot: string;
  readonly sceneRoot: string;
  readonly busyRegionId: string | null;
  readonly busy: boolean;
  readonly error: string | null;
  readonly notice: string;
  readonly draftBounds: OfflineBounds;
  readonly estimatedBytes: number | null;
  readonly estimating: boolean;
}

export interface SceneUiSnapshot {
  readonly sceneId: string;
  readonly label: string;
  readonly tileCount: number;
  readonly loadedTileCount: number;
  readonly source: string;
  readonly connectivity: string;
  readonly offlineRegion: string | null;
}

export interface ShellUiSnapshot {
  readonly entryPlaces: readonly EntryPlaceResultViewModel[];
  readonly tiles: readonly TileSelectionViewModel[];
  readonly performanceCategories: readonly PerformanceCategoryViewModel[];
  readonly offline: OfflineUiSnapshot;
  readonly scene: SceneUiSnapshot;
}

export interface ShellUiModel {
  readonly store: ObservableStateAdapter<ShellUiSnapshot>;
  readonly snapshot: () => ShellUiSnapshot;
  readonly updateEntryPlaces: (
    places: readonly EntryPlaceResultViewModel[],
  ) => void;
  readonly updateTiles: (tiles: readonly TileSelectionViewModel[]) => void;
  readonly updatePerformanceCategories: (
    categories: readonly PerformanceCategoryViewModel[],
  ) => void;
  readonly updateOffline: (patch: Partial<OfflineUiSnapshot>) => void;
  readonly updateScene: (patch: Partial<SceneUiSnapshot>) => void;
  readonly dispose: () => void;
}

export function createShellUiModel(): ShellUiModel {
  let snapshot: ShellUiSnapshot = {
    entryPlaces: [],
    tiles: [],
    performanceCategories: [],
    offline: {
      available: false,
      mode: "automatic",
      activeRegionId: null,
      activeRegionName: null,
      regions: [],
      totalBytes: 0,
      freeBytes: 0,
      storageRoot: "",
      sceneRoot: "",
      busyRegionId: null,
      busy: false,
      error: null,
      notice: "",
      draftBounds: {
        west: -180,
        east: 180,
        south: -90,
        north: 90,
      },
      estimatedBytes: null,
      estimating: false,
    },
    scene: {
      sceneId: "",
      label: "",
      tileCount: 0,
      loadedTileCount: 0,
      source: "",
      connectivity: "automatic",
      offlineRegion: null,
    },
  };
  const store = new ObservableStateAdapter(() => snapshot);
  let disposed = false;
  const update = (patch: Partial<ShellUiSnapshot>) => {
    if (disposed) return;
    snapshot = { ...snapshot, ...patch };
    store.refresh();
  };
  return {
    store,
    snapshot() {
      return snapshot;
    },
    updateEntryPlaces(entryPlaces) {
      update({ entryPlaces: [...entryPlaces] });
    },
    updateTiles(tiles) {
      update({ tiles: [...tiles] });
    },
    updatePerformanceCategories(performanceCategories) {
      update({ performanceCategories: [...performanceCategories] });
    },
    updateOffline(patch) {
      update({ offline: { ...snapshot.offline, ...patch } });
    },
    updateScene(patch) {
      update({ scene: { ...snapshot.scene, ...patch } });
    },
    dispose() {
      if (disposed) return;
      disposed = true;
      store.dispose();
    },
  };
}
