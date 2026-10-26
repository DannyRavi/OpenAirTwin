import * as THREE from "/lib/three.module.js";

const BASEMAP_STYLE = "https://tiles.openfreemap.org/styles/bright";
const BASEMAP_ELEVATION_M = 0.02;
const SNAPSHOT_MAX_SIZE = 1536;
const ATTRIBUTION_HTML =
  '&copy; <a href="https://openfreemap.org/" target="_blank" rel="noreferrer">OpenFreeMap</a> &copy; <a href="https://www.openmaptiles.org/" target="_blank" rel="noreferrer">OpenMapTiles</a> Data from <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap</a>';

function coverageBounds(corners) {
  if (!Array.isArray(corners) || corners.length < 3) {
    return null;
  }
  let west = Infinity;
  let east = -Infinity;
  let south = Infinity;
  let north = -Infinity;
  for (const corner of corners) {
    const lon = Number(corner?.[0]);
    const lat = Number(corner?.[1]);
    if (!Number.isFinite(lon) || !Number.isFinite(lat)) {
      continue;
    }
    west = Math.min(west, lon);
    east = Math.max(east, lon);
    south = Math.min(south, lat);
    north = Math.max(north, lat);
  }
  if (![west, east, south, north].every(Number.isFinite) || west >= east || south >= north) {
    return null;
  }
  return {west, east, south, north};
}

function localTileBounds(modelEntries, tileId) {
  const box = new THREE.Box3();
  let hasGround = false;
  for (const entry of modelEntries.values()) {
    if (entry.bundle.tile !== tileId) {
      continue;
    }
    if (entry.bundle.category !== "TERRAIN_TB") {
      continue;
    }
    const geometry = entry.object.geometry;
    if (!geometry?.boundingBox) {
      geometry?.computeBoundingBox?.();
    }
    if (geometry?.boundingBox) {
      box.union(geometry.boundingBox);
      hasGround = true;
    }
  }
  if (!hasGround) {
    for (const entry of modelEntries.values()) {
      if (entry.bundle.tile !== tileId) {
        continue;
      }
      const geometry = entry.object.geometry;
      if (!geometry?.boundingBox) {
        geometry?.computeBoundingBox?.();
      }
      if (geometry?.boundingBox) {
        box.union(geometry.boundingBox);
      }
    }
  }
  if (box.isEmpty()) {
    return null;
  }
  return {
    west: box.min.x,
    east: box.max.x,
    south: box.min.y,
    north: box.max.y,
  };
}

/**
 * Renders a decorative OpenFreeMap Positron snapshot in the Three.js scene.
 *
 * This group is deliberately separate from the model group: it is never
 * ray-cast, never sent to Sionna RT, and never included in scene bounds or
 * performance estimates. It exists only to give the 3D view a map underlay.
 */
export class ViewerBasemap {
  constructor({scene, renderer} = {}) {
    this.scene = scene;
    this.renderer = renderer;
    this.group = new THREE.Group();
    this.group.name = "viewer-basemap";
    this.group.renderOrder = 1;
    this.scene.add(this.group);
    this.generation = 0;
    this.planes = [];
    this.active = false;
    this.attribution = null;
    this.snapshotCache = new Map();
  }

  #ensureAttribution() {
    if (this.attribution) {
      return this.attribution;
    }
    const existing = document.querySelector(".viewerBasemapAttribution");
    if (existing) {
      this.attribution = existing;
      return this.attribution;
    }
    const element = document.createElement("div");
    element.className = "viewerBasemapAttribution hidden";
    element.innerHTML = ATTRIBUTION_HTML;
    document.body.appendChild(element);
    this.attribution = element;
    return this.attribution;
  }

  setAttributionVisible(visible) {
    const shouldShow = Boolean(visible);
    if (!shouldShow && !this.attribution) {
      return;
    }
    this.#ensureAttribution().classList.toggle("hidden", !shouldShow || !this.active);
  }

  #clearPlanes() {
    for (const plane of this.planes) {
      this.group.remove(plane.mesh);
      plane.mesh.geometry?.dispose();
      plane.mesh.material?.map?.dispose?.();
      plane.mesh.material?.dispose?.();
    }
    this.planes = [];
  }

  clear() {
    this.generation += 1;
    this.#clearPlanes();
    this.active = false;
    this.setAttributionVisible(false);
  }

  async #snapshot(bounds) {
    const key = [
      bounds.west.toFixed(6),
      bounds.east.toFixed(6),
      bounds.south.toFixed(6),
      bounds.north.toFixed(6),
    ].join(":");
    const cached = this.snapshotCache.get(key);
    if (cached) {
      return cached;
    }
    const {renderBasemapSnapshot} = await import("/@oat/runtime/entry-basemap.ts");
    const snapshot = await renderBasemapSnapshot({
      style: BASEMAP_STYLE,
      bounds,
      maxSize: SNAPSHOT_MAX_SIZE,
      applyEntryStyle: false,
    });
    this.snapshotCache.set(key, snapshot);
    while (this.snapshotCache.size > 4) {
      const oldest = this.snapshotCache.keys().next().value;
      if (oldest === undefined) break;
      this.snapshotCache.delete(oldest);
    }
    return snapshot;
  }

  async #offlineSnapshot(url) {
    const key = `offline:${url}`;
    const cached = this.snapshotCache.get(key);
    if (cached) {
      return cached;
    }
    const image = await new Promise((resolve, reject) => {
      const texture = new THREE.TextureLoader().load(
        url,
        () => resolve(texture),
        undefined,
        () => reject(new Error(`Offline basemap unavailable: ${url}`)),
      );
      texture.colorSpace = THREE.SRGBColorSpace;
    });
    this.snapshotCache.set(key, image);
    while (this.snapshotCache.size > 4) {
      const oldest = this.snapshotCache.keys().next().value;
      if (oldest === undefined) break;
      this.snapshotCache.delete(oldest);
    }
    return image;
  }

  async update({tileIds = [], coverageTiles = [], modelEntries = new Map()} = {}) {
    const generation = ++this.generation;
    this.#clearPlanes();
    this.active = false;
    this.setAttributionVisible(false);

    const coverageById = new Map(
      (Array.isArray(coverageTiles) ? coverageTiles : [])
        .filter((tile) => tile?.id)
        .map((tile) => [tile.id, tile]),
    );
    const tasks = [];
    for (const tileId of [...new Set(tileIds)]) {
      const coverage = coverageById.get(tileId);
      const wgs84 = coverageBounds(coverage?.wgs84_corners);
      const local = localTileBounds(modelEntries, tileId);
      if (!wgs84 || !local || local.east <= local.west || local.north <= local.south) {
        continue;
      }
      tasks.push({tileId, wgs84, local});
    }
    if (!tasks.length) {
      return false;
    }

    const localUnion = tasks.reduce(
      (acc, task) => ({
        west: Math.min(acc.west, task.local.west),
        east: Math.max(acc.east, task.local.east),
        south: Math.min(acc.south, task.local.south),
        north: Math.max(acc.north, task.local.north),
      }),
      {west: Infinity, east: -Infinity, south: Infinity, north: -Infinity},
    );
    const wgs84Union = tasks.reduce(
      (acc, task) => ({
        west: Math.min(acc.west, task.wgs84.west),
        east: Math.max(acc.east, task.wgs84.east),
        south: Math.min(acc.south, task.wgs84.south),
        north: Math.max(acc.north, task.wgs84.north),
      }),
      {west: 180, east: -180, south: 90, north: -90},
    );

    const offlineRuntime = window.__HAMMURABI_OFFLINE__;
    const offlineMode = offlineRuntime?.mode === "offline";
    const activeRegion = offlineRuntime?.activeRegion;
    const cachedBasemap = activeRegion?.basemapUrl;
    let snapshot = null;
    if (cachedBasemap && (offlineMode || offlineRuntime?.mode === "automatic")) {
      try {
        snapshot = await this.#offlineSnapshot(cachedBasemap);
      } catch (error) {
        console.warn("Offline 3D basemap unavailable", error);
      }
    } else if (!offlineMode) {
      try {
        snapshot = await this.#snapshot(wgs84Union);
      } catch (error) {
        console.warn("3D basemap snapshot unavailable", error);
      }
    }
    if (generation !== this.generation) {
      return false;
    }
    if (!snapshot) {
      return false;
    }

    const texture = snapshot.isTexture ? snapshot : new THREE.CanvasTexture(snapshot);
    texture.colorSpace = THREE.SRGBColorSpace;
    texture.anisotropy = Math.min(
      8,
      this.renderer?.capabilities?.getMaxAnisotropy?.() || 1,
    );
    texture.needsUpdate = true;

    const width = localUnion.east - localUnion.west;
    const height = localUnion.north - localUnion.south;
    const geometry = new THREE.PlaneGeometry(width, height);
    const material = new THREE.MeshBasicMaterial({
      map: texture,
      side: THREE.DoubleSide,
      transparent: false,
      depthWrite: true,
      depthTest: true,
      polygonOffset: true,
      polygonOffsetFactor: -4,
      polygonOffsetUnits: -4,
    });
    material.toneMapped = false;
    const mesh = new THREE.Mesh(geometry, material);
    mesh.position.set(
      (localUnion.west + localUnion.east) / 2,
      (localUnion.south + localUnion.north) / 2,
      BASEMAP_ELEVATION_M,
    );
    mesh.renderOrder = 1;
    mesh.frustumCulled = false;
    mesh.userData.viewerBasemap = true;
    this.group.add(mesh);
    this.planes.push({mesh});

    this.active = true;
    this.setAttributionVisible(true);
    return true;
  }

  dispose() {
    this.clear();
    this.snapshotCache.clear();
    this.scene.remove(this.group);
    this.attribution?.remove?.();
    this.attribution = null;
  }
}
