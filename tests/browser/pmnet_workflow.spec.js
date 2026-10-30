import { expect, test } from "@playwright/test";

import {
  activateMode,
  configureMainDeviceFixture,
  enableRealViewer,
  openDeterministicApp,
} from "./workbench-fixture.js";

const PMNET_RESULT = Object.freeze({
  job_id: "pmnet-snapshot",
  status: "succeeded",
  schema_version: 1,
  metric: "path_gain_db",
  unit: "dB",
  engine: {
    id: "physics",
    label: "Geometry surrogate",
    checkpoint: null,
    device: null,
    reason: "No PMNet checkpoint is configured",
    preference: "auto",
  },
  tx: { position: [72, 37, 40], power_dbm: 30 },
  grid: {
    nx: 4,
    ny: 4,
    cell_size: [50, 50],
    bounds: { min: [22, 12], max: [122, 112] },
    frequency_hz: 3.5e9,
    height_offset: 1.5,
    vertex_shape: [5, 5],
    vertex_z: new Array(25).fill(0),
  },
  values: {
    count: 16,
    data: [
      -100, -101, -102, -103,
      -104, -105, -106, -107,
      -108, -109, -110, -111,
      -112, -113, -114, -115,
    ],
  },
  range: { min: -115, max: -100 },
  buildings: {
    coverage: 0.25,
    cell_count: 4,
    rendered_cell_count: 4,
    truncated: false,
    max_cells: 20000,
    height: { min: 8, max: 24, mean: 16 },
    cells: [
      [1, 1, 20, 0, -90, -100],
      [2, 1, 20, 0, -92, -102],
      [1, 2, 12, 0, -95, -105],
      [2, 2, 12, 0, -96, -106],
    ],
    grid: { nx: 4, ny: 4 },
  },
  stats: {
    mean_db: -107.5,
    median_db: -107.5,
    los_fraction: 0.5,
    shadow_fraction: 0.5,
    mean_wall_crossings: 0.5,
    building_coverage: 0.25,
    building_cells: 4,
    ground_cells: 12,
    max_obstruction: 3.2,
  },
  timings: { geometry_s: 0.01, surface_s: 0.02, inference_s: 0.03, total_s: 0.06 },
});

test("PMNet pathloss workflow posts the request and renders map, dock and buildings", async ({
  page,
}) => {
  test.setTimeout(120_000);
  let submitted = null;
  await page.route("**/api/pmnet/jobs", async (route) => {
    submitted = route.request().postDataJSON();
    await route.fulfill({
      status: 202,
      json: { ok: true, job_id: "pmnet-snapshot", status: "queued" },
    });
  });
  await page.route("**/api/pmnet/jobs/pmnet-snapshot", (route) =>
    route.fulfill({
      json: {
        job_id: "pmnet-snapshot",
        status: "succeeded",
        progress: 1,
        message: "Pathloss map ready",
      },
    }),
  );
  await page.route("**/api/pmnet/jobs/pmnet-snapshot/result", (route) =>
    route.fulfill({ json: PMNET_RESULT }),
  );

  await openDeterministicApp(page);
  await enableRealViewer(page);
  await configureMainDeviceFixture(page);
  await activateMode(page, "pmnet");

  await expect(page.locator("#btnRunPmnet")).toBeEnabled();
  await page.locator("#btnRunPmnet").click();

  await expect(page.locator("#resultDockTitle")).toHaveText(
    "PMNet Pathloss Results",
  );
  await expect(page.locator("#resultDockSubtitle")).toHaveText(
    "Pathloss map / Building-aware surrogate",
  );
  await expect(page.locator("#pmStatus")).toHaveText("Succeeded");
  await expect(page.locator("#pmEngine")).toHaveText("Geometry surrogate");
  await expect(page.locator("#pmGrid")).toHaveText("4 x 4 cells");
  await expect(page.locator("#pmCoverage")).toHaveText("25.0% of ROI");
  await expect(page.locator("#pmArea")).toHaveText("200 x 200 m");
  await expect(page.locator("#pmCellSize")).toHaveText("50.00 x 50.00 m");
  await expect(page.locator("#pmRange")).toHaveText("-115.0 .. -100.0 dB");
  await expect(page.locator("#pmEngineNote")).toHaveText(
    "No PMNet checkpoint is configured",
  );
  await expect(page.locator("#pmnetColorbarSection")).toBeVisible();

  expect(submitted).toEqual({
    tx: { position: [72, 37, 40], orientation: [0, 0, 0] },
    roi: { size: [200, 200], grid: 128, height_offset: 1.5 },
    solver: { frequency_hz: 3.5e9 },
    model: { engine: "auto", wall_loss_db: 6 },
    signal: { tx_power_dbm: 30 },
  });

  const scene = await page.evaluate(async () => {
    const { viewerRef } =
      await import("/js/app_state.js?v=20260723-radar-shared-groups");
    const viewer = viewerRef.current;
    const triangles = (group) =>
      group.children.reduce(
        (total, mesh) =>
          total + (mesh.geometry?.attributes?.position?.count ?? 0) / 3,
        0,
      );
    return {
      groundTriangles: triangles(viewer.pmnetGroundLayer.group),
      buildingTriangles: triangles(viewer.pmnetBuildingLayer.group),
    };
  });
  expect(scene.groundTriangles).toBe(32);
  expect(scene.buildingTriangles).toBeGreaterThan(0);

  // Leaving the mode releases the Feature-owned layers.
  await activateMode(page, "link");
  await expect
    .poll(() =>
      page.evaluate(async () => {
        const { viewerRef } =
          await import("/js/app_state.js?v=20260723-radar-shared-groups");
        return viewerRef.current.pmnetGroundLayer.group.children.length;
      }),
    )
    .toBe(0);
});

test("PMNet toggling the building overlay keeps the ground prediction", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await page.route("**/api/pmnet/jobs", (route) =>
    route.fulfill({
      status: 202,
      json: { ok: true, job_id: "pmnet-snapshot", status: "queued" },
    }),
  );
  await page.route("**/api/pmnet/jobs/pmnet-snapshot", (route) =>
    route.fulfill({
      json: {
        job_id: "pmnet-snapshot",
        status: "succeeded",
        progress: 1,
        message: "Pathloss map ready",
      },
    }),
  );
  await page.route("**/api/pmnet/jobs/pmnet-snapshot/result", (route) =>
    route.fulfill({ json: PMNET_RESULT }),
  );

  await openDeterministicApp(page);
  await enableRealViewer(page);
  await configureMainDeviceFixture(page);
  await activateMode(page, "pmnet");
  await page.locator("#btnRunPmnet").click();
  await expect(page.locator("#pmStatus")).toHaveText("Succeeded");

  await page.evaluate(() => {
    const input = document.getElementById("pmnetShowBuildings");
    input.click();
    input.dispatchEvent(new Event("change", { bubbles: true }));
    input.blur();
  });

  await expect
    .poll(() =>
      page.evaluate(async () => {
        const { viewerRef } =
          await import("/js/app_state.js?v=20260723-radar-shared-groups");
        const viewer = viewerRef.current;
        return {
          buildings: viewer.pmnetBuildingLayer.group.children.length,
          ground: viewer.pmnetGroundLayer.group.children.length,
        };
      }),
    )
    .toEqual({ buildings: 0, ground: 1 });
});

test("PMNet Tx map pick fills the precision coordinates", async ({ page }) => {
  test.setTimeout(120_000);
  await openDeterministicApp(page);
  await enableRealViewer(page);
  await activateMode(page, "pmnet");

  // The deterministic fixture has no scene bundles, so the surface hit is
  // stubbed. Everything after the raycast is the production pick path.
  await page.evaluate(async () => {
    const { viewerRef } =
      await import("/js/app_state.js?v=20260723-radar-shared-groups");
    viewerRef.current.pickOnSurface = () => ({
      surfacePosition: [12.5, 34.0, 5.5],
      surfaceNormal: [0, 0, 1],
      logicalPosition: [12.5, 34.0, 5.5],
      markerPosition: [12.5, 34.0, 6.0],
    });
  });

  await page.locator("#btnPickPmnetTx").click();
  await expect(page.locator("#btnPickPmnetTx")).toHaveClass(/active/);

  const box = await page.locator("#view").boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);

  await expect(page.locator("#pmnetTxX")).toHaveValue("12.5");
  await expect(page.locator("#pmnetTxY")).toHaveValue("34.0");
  // The pick applies the shared 1.5 m surface clearance to the picked point.
  await expect(page.locator("#pmnetTxZ")).toHaveValue("7.0");
  await expect(page.locator("#btnRunPmnet")).toBeEnabled();

  const state = await page.evaluate(async () => {
    const { state } = await import(
      "/js/app_state.js?v=20260723-radar-shared-groups"
    );
    return { tx: state.pmnet.tx, activeTarget: state.deviceControl.activeTarget };
  });
  expect(state.activeTarget).toBe("pmnet-tx");
  expect(state.tx).toEqual([12.5, 34.0, 7.0]);

  // Closing the picker keeps the committed coordinates and the run action.
  await page.locator("#btnPickPmnetTx").click();
  await expect(page.locator("#btnPickPmnetTx")).not.toHaveClass(/active/);
  await expect(page.locator("#pmnetTxX")).toHaveValue("12.5");
  await expect(page.locator("#btnRunPmnet")).toBeEnabled();
  expect(
    await page.evaluate(async () => {
      const { state } = await import(
        "/js/app_state.js?v=20260723-radar-shared-groups"
      );
      return state.pmnet.tx;
    }),
  ).toEqual([12.5, 34.0, 7.0]);
});
