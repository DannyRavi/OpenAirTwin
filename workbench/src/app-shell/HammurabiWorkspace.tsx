import { useEffect, useMemo, useRef, useState } from "react";

import { useUiCommand } from "../app/use-ui-command.ts";
import type {
  OfflineBounds,
  OfflineRegionViewModel,
  OfflineUiSnapshot,
  SceneUiSnapshot,
  ShellUiModel,
} from "./shell-ui-model.ts";
import { useFeatureSnapshot } from "../runtime/observable-state.ts";

export type WorkspaceView = "scene" | "analysis" | "offline" | "project";

const NAV_ITEMS: readonly {
  readonly id: WorkspaceView;
  readonly label: string;
  readonly icon: string;
}[] = [
  {
    id: "scene",
    label: "Scene",
    icon: "M3 21h18M5 21V8l7-5 7 5v13M9 21v-6h6v6",
  },
  {
    id: "analysis",
    label: "Analysis",
    icon: "M4 19V5m0 14h16M7 15l4-5 3 3 5-7",
  },
  {
    id: "offline",
    label: "Offline Maps",
    icon: "M12 3a9 9 0 1 0 9 9M3 12h18M12 3c2.5 3 2.5 15 0 18M12 3c-2.5 3-2.5 15 0 18",
  },
  { id: "project", label: "Project", icon: "M4 6h16M4 12h10M4 18h7" },
];

const STATUS_LABELS: Readonly<Record<string, string>> = {
  ready: "Ready offline",
  preparing: "Downloading",
  verifying: "Verifying",
  queued: "Queued",
  partial: "Incomplete",
  missing: "Missing assets",
  failed: "Failed",
  cancelled: "Cancelled",
  cancelling: "Cancelling",
};

function formatBytes(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0 MB";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  return `${amount.toFixed(unit >= 2 ? 1 : 0)} ${units[unit] ?? "B"}`;
}

function statusLabel(region: OfflineRegionViewModel): string {
  return STATUS_LABELS[region.status] ?? region.status;
}

function RegionRow({
  region,
  busy,
}: {
  readonly region: OfflineRegionViewModel;
  readonly busy: boolean;
}) {
  const dispatch = useUiCommand();
  const active = region.active;
  const working = ["queued", "preparing", "verifying", "cancelling"].includes(
    region.status,
  );
  return (
    <li className="hx-region" data-status={region.status}>
      <div className="hx-region-head">
        <span className="hx-region-name" title={region.name}>
          {region.name}
        </span>
        <span className={`hx-badge hx-badge--${region.status}`}>
          {statusLabel(region)}
        </span>
      </div>
      <dl className="hx-region-meta">
        <div>
          <dt>Tiles</dt>
          <dd>{region.tileCount}</dd>
        </div>
        <div>
          <dt>Size</dt>
          <dd>{formatBytes(region.bytesTotal)}</dd>
        </div>
        <div>
          <dt>Assets</dt>
          <dd>{region.assetCount}</dd>
        </div>
        <div>
          <dt>Map</dt>
          <dd>{region.basemap ? "Cached" : "Not cached"}</dd>
        </div>
      </dl>
      {working ? (
        <div className="hx-progress" aria-label="Region preparation progress">
          <div
            className="hx-progress-bar"
            style={{ width: `${String(Math.round(region.progress * 100))}%` }}
          />
        </div>
      ) : null}
      <p className="hx-region-message">
        {region.error
          ? region.error
          : region.missingCount > 0
            ? `${String(region.missingCount)} asset(s) need repair`
            : region.message || statusLabel(region)}
      </p>
      <div className="hx-region-actions">
        {active ? (
          <button
            className="hx-btn hx-btn--ghost"
            type="button"
            onClick={() => {
              void dispatch({ name: "offline.region.deactivate", payload: {} });
            }}
          >
            Leave
          </button>
        ) : (
          <button
            className="hx-btn hx-btn--primary"
            type="button"
            disabled={!region.offlineReady || busy}
            onClick={() => {
              void dispatch({
                name: "offline.region.open",
                payload: { regionId: region.regionId },
              });
            }}
          >
            Open offline
          </button>
        )}
        {working ? (
          <button
            className="hx-btn hx-btn--ghost"
            type="button"
            disabled={busy}
            onClick={() => {
              void dispatch({
                name: "offline.region.cancel",
                payload: { regionId: region.regionId },
              });
            }}
          >
            Cancel
          </button>
        ) : null}
        {region.status === "partial" || region.status === "failed" ? (
          <button
            className="hx-btn hx-btn--ghost"
            type="button"
            disabled={busy}
            onClick={() => {
              void dispatch({
                name: "offline.region.retry",
                payload: { regionId: region.regionId },
              });
            }}
          >
            Retry
          </button>
        ) : null}
        {region.offlineReady && !region.basemap ? (
          <button
            className="hx-btn hx-btn--ghost"
            type="button"
            disabled={busy}
            onClick={() => {
              void dispatch({
                name: "offline.region.basemap",
                payload: { regionId: region.regionId },
              });
            }}
          >
            Cache map
          </button>
        ) : null}
        <button
          className="hx-btn hx-btn--ghost"
          type="button"
          disabled={busy}
          onClick={() => {
            void dispatch({
              name: "offline.region.verify",
              payload: { regionId: region.regionId },
            });
          }}
        >
          Verify
        </button>
        <button
          className="hx-btn hx-btn--danger"
          type="button"
          disabled={busy}
          onClick={() => {
            void dispatch({
              name: "offline.region.delete",
              payload: { regionId: region.regionId },
            });
          }}
        >
          Delete
        </button>
      </div>
    </li>
  );
}

function BoundsField({
  axis,
  label,
  value,
  onChange,
}: {
  readonly axis: keyof OfflineBounds;
  readonly label: string;
  readonly value: number;
  readonly onChange: (axis: keyof OfflineBounds, value: number) => void;
}) {
  return (
    <label className="hx-field">
      <span>{label}</span>
      <input
        className="hx-input"
        type="number"
        step="0.0001"
        value={Number.isFinite(value) ? value : 0}
        onChange={(event) => {
          const next = Number(event.target.value);
          if (Number.isFinite(next)) onChange(axis, next);
        }}
      />
    </label>
  );
}

export function OfflinePanel({
  offline,
}: {
  readonly offline: OfflineUiSnapshot;
}) {
  const dispatch = useUiCommand();
  const [name, setName] = useState("Offline area");
  const [layers, setLayers] = useState<readonly string[]>([
    "buildings",
    "terrain",
    "map",
  ]);
  const [bounds, setBounds] = useState<OfflineBounds>(offline.draftBounds);
  const draftKey = [
    offline.draftBounds.west,
    offline.draftBounds.east,
    offline.draftBounds.south,
    offline.draftBounds.north,
  ].join(":");
  useEffect(() => {
    setBounds(offline.draftBounds);
  }, [draftKey]);

  const updateBounds = (axis: keyof OfflineBounds, value: number) => {
    setBounds((current) => ({ ...current, [axis]: value }));
  };
  const toggleLayer = (layer: string) => {
    setLayers((current) =>
      current.includes(layer)
        ? current.filter((item) => item !== layer)
        : [...current, layer],
    );
  };
  const busy = offline.busy;
  return (
    <div className="hx-offline">
      <section className="hx-section">
        <h3 className="hx-section-title">Connectivity</h3>
        <div className="hx-segment" role="group" aria-label="Connectivity mode">
          {(["online", "automatic", "offline"] as const).map((mode) => (
            <button
              key={mode}
              className={`hx-segment-btn${
                offline.mode === mode ? " is-active" : ""
              }`}
              type="button"
              aria-pressed={offline.mode === mode}
              disabled={busy}
              onClick={() => {
                void dispatch({ name: "offline.mode.set", payload: { mode } });
              }}
            >
              {mode === "online"
                ? "Online"
                : mode === "automatic"
                  ? "Automatic"
                  : "Strict offline"}
            </button>
          ))}
        </div>
        <p className="hx-hint">
          {offline.mode === "offline"
            ? "Only local regions are loaded. Remote map and scene requests are blocked."
            : offline.mode === "automatic"
              ? "Local regions are preferred; remote providers may be used when available."
              : "Remote providers are available for downloads and updates."}
        </p>
      </section>

      <section className="hx-section">
        <h3 className="hx-section-title">Download area</h3>
        <label className="hx-field">
          <span>Region name</span>
          <input
            className="hx-input"
            type="text"
            maxLength={80}
            value={name}
            onChange={(event) => {
              setName(event.target.value);
            }}
          />
        </label>
        <div className="hx-coord-grid">
          <BoundsField
            axis="west"
            label="West"
            value={bounds.west}
            onChange={updateBounds}
          />
          <BoundsField
            axis="east"
            label="East"
            value={bounds.east}
            onChange={updateBounds}
          />
          <BoundsField
            axis="south"
            label="South"
            value={bounds.south}
            onChange={updateBounds}
          />
          <BoundsField
            axis="north"
            label="North"
            value={bounds.north}
            onChange={updateBounds}
          />
        </div>
        <div className="hx-inline">
          <button
            className="hx-btn hx-btn--ghost"
            type="button"
            disabled={busy}
            onClick={() => {
              void dispatch({ name: "offline.bounds.useMap", payload: {} });
            }}
          >
            Use current map view
          </button>
          <button
            className="hx-btn hx-btn--ghost"
            type="button"
            disabled={busy || offline.estimating}
            onClick={() => {
              void dispatch({
                name: "offline.region.estimate",
                payload: { bounds },
              });
            }}
          >
            {offline.estimating ? "Estimating..." : "Estimate size"}
          </button>
        </div>
        {offline.estimatedBytes !== null ? (
          <p className="hx-hint">
            Selected area needs about {formatBytes(offline.estimatedBytes)} for
            scene geometry and render bundles.
          </p>
        ) : null}
        <fieldset className="hx-layers">
          <legend>Layers</legend>
          {[
            { value: "buildings", label: "Buildings" },
            { value: "terrain", label: "Terrain" },
            { value: "map", label: "Map underlay snapshot" },
          ].map(({ value, label }) => (
            <label key={value} className="hx-check">
              <input
                type="checkbox"
                checked={layers.includes(value)}
                onChange={() => {
                  toggleLayer(value);
                }}
              />
              <span>{label}</span>
            </label>
          ))}
        </fieldset>
        <button
          className="hx-btn hx-btn--primary hx-btn--block"
          type="button"
          disabled={busy || !offline.available || layers.length === 0}
          onClick={() => {
            void dispatch({
              name: "offline.region.create",
              payload: { name, bounds, layers },
            });
          }}
        >
          {busy ? "Preparing offline region..." : "Download offline region"}
        </button>
        {offline.error ? (
          <p className="hx-error" role="alert">
            {offline.error}
          </p>
        ) : null}
        {offline.notice ? (
          <p className="hx-hint" role="status">
            {offline.notice}
          </p>
        ) : null}
      </section>

      <section className="hx-section">
        <div className="hx-section-head">
          <h3 className="hx-section-title">Stored regions</h3>
          <button
            className="hx-btn hx-btn--ghost hx-btn--compact"
            type="button"
            onClick={() => {
              void dispatch({ name: "offline.refresh", payload: {} });
            }}
          >
            Refresh
          </button>
        </div>
        {offline.regions.length === 0 ? (
          <p className="hx-empty">
            No offline regions yet. Pick an area on the entry map, then use the
            current map view here.
          </p>
        ) : (
          <ul className="hx-region-list">
            {offline.regions.map((region) => (
              <RegionRow key={region.regionId} region={region} busy={busy} />
            ))}
          </ul>
        )}
      </section>

      <section className="hx-section">
        <h3 className="hx-section-title">Storage</h3>
        <p className="hx-metric-line">
          <strong>{formatBytes(offline.totalBytes)}</strong> stored in{" "}
          {offline.regions.length} region(s)
          {offline.freeBytes > 0
            ? ` · ${formatBytes(offline.freeBytes)} free on disk`
            : ""}
        </p>
        <p className="hx-path" title={offline.storageRoot}>
          {offline.storageRoot || "Storage root unavailable"}
        </p>
        <p className="hx-hint">
          Regions are stored by the local Hammurabi backend. Basemap snapshots
          use OpenFreeMap data and must retain OpenStreetMap attribution.
        </p>
      </section>
    </div>
  );
}

export function ScenePanel({
  scene,
  offline,
  onOpenOffline,
}: {
  readonly scene: SceneUiSnapshot;
  readonly offline: OfflineUiSnapshot;
  readonly onOpenOffline: () => void;
}) {
  const dispatch = useUiCommand();
  const activeRegion = offline.regions.find((region) => region.active) ?? null;
  return (
    <div className="hx-scene-panel">
      <section className="hx-section">
        <h3 className="hx-section-title">Loaded scene</h3>
        <dl className="hx-key-values">
          <div>
            <dt>Scene</dt>
            <dd>{scene.label || scene.sceneId || "No scene"}</dd>
          </div>
          <div>
            <dt>Tiles</dt>
            <dd>
              {scene.loadedTileCount} loaded / {scene.tileCount} available
            </dd>
          </div>
          <div>
            <dt>Source</dt>
            <dd>{scene.source || "Local scene root"}</dd>
          </div>
          <div>
            <dt>Connectivity</dt>
            <dd>{scene.connectivity}</dd>
          </div>
          <div>
            <dt>Offline region</dt>
            <dd>{activeRegion?.name ?? scene.offlineRegion ?? "None"}</dd>
          </div>
        </dl>
      </section>
      <section className="hx-section">
        <h3 className="hx-section-title">Viewport</h3>
        <div className="hx-inline hx-inline--wrap">
          <button
            className="hx-btn hx-btn--ghost"
            type="button"
            onClick={() => {
              void dispatch({ name: "viewer.camera.fit", payload: {} });
            }}
          >
            Fit selected tiles
          </button>
          <button
            className="hx-btn hx-btn--ghost"
            type="button"
            onClick={() => {
              void dispatch({ name: "viewer.camera.reset", payload: {} });
            }}
          >
            Reset camera
          </button>
          <button
            className="hx-btn hx-btn--ghost"
            type="button"
            onClick={() => {
              void dispatch({ name: "entry.scene.open", payload: {} });
            }}
          >
            Choose tiles on map
          </button>
        </div>
      </section>
      <section className="hx-section">
        <h3 className="hx-section-title">Offline scene</h3>
        <p className="hx-hint">
          Download the current area, then switch to strict offline mode to open
          it without external map requests.
        </p>
        <button
          className="hx-btn hx-btn--primary hx-btn--block"
          type="button"
          onClick={onOpenOffline}
        >
          Manage offline maps
        </button>
      </section>
      <p className="hx-attribution">
        Basemap data {"\u00a9"} OpenStreetMap contributors, OpenFreeMap and
        OpenMapTiles. Scene geometry keeps its source dataset terms.
      </p>
    </div>
  );
}

export function ProjectPanel({ scene }: { readonly scene: SceneUiSnapshot }) {
  return (
    <div className="hx-project">
      <section className="hx-section">
        <h3 className="hx-section-title">Hammurabi</h3>
        <p className="hx-hint">
          Geospatial RF engineering workspace for urban digital twins, radio
          propagation and network planning. Built on the open-source OpenAirTwin
          research platform by the HKU authors; this is an independent
          derivative and is not endorsed by the upstream project.
        </p>
        <dl className="hx-key-values">
          <div>
            <dt>Scene</dt>
            <dd>{scene.sceneId || "n/a"}</dd>
          </div>
          <div>
            <dt>Runtime</dt>
            <dd>Python + Sionna RT + Three.js</dd>
          </div>
          <div>
            <dt>License</dt>
            <dd>Apache-2.0; third-party data excluded</dd>
          </div>
        </dl>
      </section>
      <section className="hx-section">
        <h3 className="hx-section-title">Data notices</h3>
        <p className="hx-hint">
          Basemap snapshots use OpenFreeMap/OpenMapTiles data {"\u00a9"}{" "}
          OpenStreetMap contributors. Offline region manifests retain the source
          scene metadata and attribution. Review the scene data license before
          redistributing geometry.
        </p>
      </section>
    </div>
  );
}

export function BrandMark({
  variant = "compact",
}: {
  readonly variant?: "compact" | "full";
}) {
  return (
    <span className={`hx-brand hx-brand--${variant}`} aria-label="Hammurabi">
      <svg className="hx-brand-mark" viewBox="0 0 96 96" aria-hidden="true">
        <rect width="96" height="96" rx="21" />
        <rect x="19" y="64" width="58" height="9" rx="2" />
        <rect x="26" y="51" width="44" height="9" rx="2" />
        <rect x="33" y="38" width="30" height="9" rx="2" />
        <path d="M36 29a12 12 0 0 1 24 0" />
        <path d="M30 29a18 18 0 0 1 36 0" opacity=".55" />
        <circle cx="48" cy="28" r="3.5" />
      </svg>
      <span className="hx-brand-copy">
        <strong>HAMMURABI</strong>
        {variant === "full" ? <small>Geospatial RF Workspace</small> : null}
      </span>
    </span>
  );
}

export function WorkspaceNav({
  active,
  onChange,
}: {
  readonly active: WorkspaceView;
  readonly onChange: (view: WorkspaceView) => void;
}) {
  return (
    <nav className="hx-nav" aria-label="Workspace sections">
      {NAV_ITEMS.map((item) => (
        <button
          key={item.id}
          className={`hx-nav-btn${active === item.id ? " is-active" : ""}`}
          type="button"
          aria-current={active === item.id ? "page" : undefined}
          onClick={() => {
            onChange(item.id);
          }}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d={item.icon} />
          </svg>
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  );
}

export function MapToolbar({
  onOpenOffline,
}: {
  readonly onOpenOffline: () => void;
}) {
  const dispatch = useUiCommand();
  const fullscreenRef = useRef(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  useEffect(() => {
    const onChange = () => {
      fullscreenRef.current = Boolean(document.fullscreenElement);
      setIsFullscreen(fullscreenRef.current);
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => {
      document.removeEventListener("fullscreenchange", onChange);
    };
  }, []);
  return (
    <div className="hx-toolbar" role="toolbar" aria-label="Map tools">
      <button
        className="hx-tool"
        type="button"
        title="Choose city tiles on the map"
        aria-label="Choose city tiles on the map"
        onClick={() => {
          void dispatch({ name: "entry.scene.open", payload: {} });
        }}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M9 18 3 21V6l6-3 6 3 6-3v15l-6 3-6-3Z" />
          <path d="M9 3v15M15 6v15" />
        </svg>
      </button>
      <button
        className="hx-tool"
        type="button"
        title="Fit selected tiles"
        aria-label="Fit selected tiles"
        onClick={() => {
          void dispatch({ name: "viewer.camera.fit", payload: {} });
        }}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
        </svg>
      </button>
      <button
        className="hx-tool"
        type="button"
        title="Reset camera"
        aria-label="Reset camera"
        onClick={() => {
          void dispatch({ name: "viewer.camera.reset", payload: {} });
        }}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M3 12a9 9 0 1 0 3-6.7" />
          <path d="M3 4v5h5" />
        </svg>
      </button>
      <button
        className="hx-tool"
        type="button"
        title="Layer and category visibility"
        aria-label="Layer and category visibility"
        onClick={() => {
          void dispatch({ name: "performance.dock.toggle", payload: {} });
        }}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="m12 3 9 5-9 5-9-5 9-5Z" />
          <path d="m3 13 9 5 9-5" />
        </svg>
      </button>
      <button
        className="hx-tool"
        type="button"
        title="Offline maps"
        aria-label="Offline maps"
        onClick={onOpenOffline}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M12 21a9 9 0 1 1 9-9" />
          <path d="M3 12h9M12 3c2.4 2.8 2.4 15.2 0 18" />
        </svg>
      </button>
      <button
        className="hx-tool"
        type="button"
        title={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
        aria-label={isFullscreen ? "Exit fullscreen" : "Enter fullscreen"}
        aria-pressed={isFullscreen}
        onClick={() => {
          void dispatch({ name: "app.fullscreen.toggle", payload: {} });
        }}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          {isFullscreen ? (
            <path d="M9 4v5H4M15 4v5h5M9 20v-5H4M15 20v-5h5" />
          ) : (
            <path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" />
          )}
        </svg>
      </button>
    </div>
  );
}

export function WorkspaceStatus({
  offline,
  scene,
}: {
  readonly offline: OfflineUiSnapshot;
  readonly scene: SceneUiSnapshot;
}) {
  const active = offline.regions.find((region) => region.active) ?? null;
  const working = offline.regions.find(
    (region) =>
      ["queued", "preparing", "verifying", "cancelling"].includes(
        region.status,
      ) && region.progress < 1,
  );
  return (
    <div className="hx-status" aria-live="polite">
      <span className={`hx-status-dot hx-status-dot--${offline.mode}`} />
      <span className="hx-status-mode">
        {offline.mode === "offline"
          ? "Offline"
          : offline.mode === "automatic"
            ? "Auto"
            : "Online"}
      </span>
      <span className="hx-status-divider" />
      <span className="hx-status-detail">
        {working
          ? `${working.name}: ${String(Math.round(working.progress * 100))}%`
          : active
            ? active.name
            : `${String(scene.loadedTileCount)} tile(s)`}
      </span>
    </div>
  );
}

export function useOfflineSnapshot(model: ShellUiModel) {
  const snapshot = useFeatureSnapshot(model.store);
  return useMemo(() => snapshot.offline, [snapshot.offline]);
}
