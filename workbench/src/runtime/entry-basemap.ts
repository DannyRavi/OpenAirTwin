import { maplibreGL } from "@maplibre/maplibre-gl-leaflet";
import type { Map as LeafletMap } from "leaflet";
import { Map as MapLibreMap, setWorkerUrl } from "maplibre-gl";
import basemapWorkerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

import { createEntryBasemapStyle } from "./entry-basemap.style.ts";
import leaflet from "./leaflet-runtime.ts";
import "./entry-basemap.css";

interface EntryBasemapOptions {
  style: string;
  attribution: string;
  onReady: () => void;
  onError: () => void;
}

interface BasemapSnapshotBounds {
  west: number;
  east: number;
  south: number;
  north: number;
}

interface BasemapSnapshotOptions {
  style: string;
  bounds: BasemapSnapshotBounds;
  maxSize?: number;
  applyEntryStyle?: boolean;
}

type ManagedBasemapLayer = ReturnType<typeof maplibreGL> & {
  _transitionEnd: () => void;
  _resizeContainer: () => void;
  _zoomEnd: () => void;
};

function waitForMapEvent(
  map: MapLibreMap,
  eventName: "load" | "idle",
  timeoutMs: number,
) {
  return new Promise<void>((resolve, reject) => {
    let settled = false;
    const finish = (error?: Error) => {
      if (settled) return;
      settled = true;
      map.off(eventName, onEvent);
      window.clearTimeout(timer);
      if (error) reject(error);
      else resolve();
    };
    const onEvent = () => {
      finish();
    };
    const timer = window.setTimeout(() => {
      finish(new Error(`MapLibre ${eventName} timed out`));
    }, timeoutMs);
    map.once(eventName, onEvent);
  });
}

function mercatorAspect(bounds: BasemapSnapshotBounds) {
  const mercatorX = (lon: number) => (lon + 180) / 360;
  const mercatorY = (lat: number) => {
    const clamped = Math.max(-85.05112878, Math.min(85.05112878, lat));
    const radians = (clamped * Math.PI) / 180;
    return (
      (1 - Math.log(Math.tan(radians) + 1 / Math.cos(radians)) / Math.PI) / 2
    );
  };
  return {
    width: Math.abs(mercatorX(bounds.east) - mercatorX(bounds.west)),
    height: Math.abs(mercatorY(bounds.south) - mercatorY(bounds.north)),
  };
}

export async function renderBasemapSnapshot({
  style,
  bounds,
  maxSize = 1536,
  applyEntryStyle = true,
}: BasemapSnapshotOptions): Promise<HTMLCanvasElement> {
  const context = document.createElement("canvas").getContext("webgl2");
  if (!context) throw new Error("WebGL2 is unavailable");
  context.getExtension("WEBGL_lose_context")?.loseContext();
  setWorkerUrl(basemapWorkerUrl);

  const aspect = mercatorAspect(bounds);
  if (!(aspect.width > 0) || !(aspect.height > 0)) {
    throw new Error("Invalid basemap bounds");
  }
  let width = Math.max(256, maxSize);
  let height = Math.round(width * (aspect.height / aspect.width));
  if (height > maxSize) {
    height = maxSize;
    width = Math.round(height * (aspect.width / aspect.height));
  }
  width = Math.max(256, Math.min(maxSize, width));
  height = Math.max(256, Math.min(maxSize, height));

  const container = document.createElement("div");
  container.setAttribute("aria-hidden", "true");
  container.style.cssText = `position:fixed;left:-100000px;top:0;width:${String(width)}px;height:${String(height)}px;overflow:hidden;pointer-events:none;`;
  document.body.appendChild(container);
  let map: MapLibreMap | null = null;
  try {
    map = new MapLibreMap({
      container,
      interactive: false,
      attributionControl: false,
      fadeDuration: 0,
      canvasContextAttributes: { preserveDrawingBuffer: true },
    });
    const loaded = waitForMapEvent(map, "load", 15000);
    map.setStyle(
      style,
      applyEntryStyle
        ? { transformStyle: (_previous, next) => createEntryBasemapStyle(next) }
        : undefined,
    );
    await loaded;
    const activeMap = map;
    activeMap.fitBounds(
      [
        [bounds.west, bounds.south],
        [bounds.east, bounds.north],
      ],
      { padding: 0, animate: false, duration: 0 },
    );
    await waitForMapEvent(activeMap, "idle", 10000).catch(() => {});
    const tileDeadline = performance.now() + 8000;
    while (!activeMap.areTilesLoaded() && performance.now() < tileDeadline) {
      await new Promise<void>((resolve) => window.setTimeout(resolve, 100));
    }
    // Capture after a completed MapLibre render. preserveDrawingBuffer keeps
    // the WebGL frame readable after the render pass.
    await new Promise<void>((resolve) => {
      let settled = false;
      const finish = () => {
        if (settled) return;
        settled = true;
        resolve();
      };
      activeMap.once("render", finish);
      activeMap.triggerRepaint();
      window.setTimeout(finish, 2000);
    });
    await new Promise<void>((resolve) => {
      window.requestAnimationFrame(() => {
        window.requestAnimationFrame(() => {
          resolve();
        });
      });
    });
    const source = activeMap.getCanvas();
    const output = document.createElement("canvas");
    const scale = Math.min(1, 2048 / source.width, 2048 / source.height);
    output.width = Math.max(1, Math.round(source.width * scale));
    output.height = Math.max(1, Math.round(source.height * scale));
    const outputContext = output.getContext("2d");
    if (!outputContext)
      throw new Error("Could not create basemap snapshot canvas");
    // The 3D ground plane is viewed at a grazing angle and downsampled, which
    // otherwise washes out road and label contrast.
    outputContext.filter = "saturate(1.65) contrast(1.28) brightness(0.96)";
    outputContext.drawImage(source, 0, 0, output.width, output.height);
    outputContext.filter = "none";
    return output;
  } finally {
    map?.remove();
    container.remove();
  }
}

export function createEntryBasemap(
  map: LeafletMap,
  { style, attribution, onReady, onError }: EntryBasemapOptions,
) {
  // MapLibre 6 leaves a partially constructed map when WebGL2 is unavailable.
  const context = document.createElement("canvas").getContext("webgl2");
  if (!context) throw new Error("WebGL2 is unavailable");
  context.getExtension("WEBGL_lose_context")?.loseContext();
  setWorkerUrl(basemapWorkerUrl);
  let active = true;
  let transitionFrame: number | undefined;
  const layer = maplibreGL({
    attributionControl: { customAttribution: attribution },
    interactive: false,
  }) as ManagedBasemapLayer;
  // The pinned adapter does not retain/cancel its deferred resize/zoom frame.
  layer._transitionEnd = () => {
    if (!active) return;
    if (transitionFrame !== undefined)
      leaflet.Util.cancelAnimFrame(transitionFrame);
    transitionFrame = leaflet.Util.requestAnimFrame(() => {
      transitionFrame = undefined;
      if (!active) return;
      const renderer = layer.getMaplibreMap();
      const offset = map.latLngToContainerPoint(map.getBounds().getNorthWest());
      layer._resizeContainer();
      leaflet.DomUtil.setTransform(renderer.getCanvas(), offset, 1);
      void renderer.once("moveend", () => {
        if (active) layer._zoomEnd();
      });
      renderer.jumpTo({ center: map.getCenter(), zoom: map.getZoom() - 1 });
    });
  };
  // The upstream adapter assumes WebGL construction succeeded when removing.
  const remove = layer.onRemove.bind(layer);
  layer.onRemove = (owner) => {
    active = false;
    if (transitionFrame !== undefined)
      leaflet.Util.cancelAnimFrame(transitionFrame);
    transitionFrame = undefined;
    if (layer.getMaplibreMap() as MapLibreMap | undefined) remove(owner);
    else layer.getContainer().remove();
    return layer;
  };
  try {
    layer.addTo(map);
  } catch (error) {
    map.removeLayer(layer);
    throw error;
  }
  const renderer = layer.getMaplibreMap();
  const ready = () => {
    if (active) onReady();
  };
  const failed = () => {
    // Removing a layer during a renderer event can interrupt its own cleanup.
    queueMicrotask(() => {
      if (active) onError();
    });
  };
  void renderer.once("load", ready);
  renderer.on("error", failed);
  renderer.on("webglcontextlost", failed);
  layer.once("remove", () => {
    active = false;
    renderer.off("load", ready);
    renderer.off("error", failed);
    renderer.off("webglcontextlost", failed);
  });
  renderer.setStyle(style, {
    transformStyle: (_previous, next) => createEntryBasemapStyle(next),
  });
  return layer;
}
