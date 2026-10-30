import { expect, test } from "@playwright/test";

import {
  enableRealViewer,
  openDeterministicApp,
} from "./workbench-fixture.js";

const STATE_SPECIFIER = "/js/app_state.js?v=20260723-radar-shared-groups";

async function openMeasureView(page) {
  await page.locator('nav[aria-label="Workspace sections"] button', {
    hasText: "Measure",
  }).click();
  await expect
    .poll(() =>
      page.evaluate(async (spec) => {
        const { state } = await import(spec);
        return state.workspaceView;
      }, STATE_SPECIFIER),
    )
    .toBe("measure");
}

async function stubSurfacePick(page, position) {
  await page.evaluate(
    async ({ spec, next }) => {
      const { viewerRef } = await import(spec);
      viewerRef.current.pickOnSurface = () => ({
        surfacePosition: [...next],
        surfaceNormal: [0, 0, 1],
        logicalPosition: [...next],
        markerPosition: [...next],
      });
    },
    { spec: STATE_SPECIFIER, next: position },
  );
}

async function pickPoint(page, buttonId, position) {
  await stubSurfacePick(page, position);
  await page.locator(`#${buttonId}`).click();
  const box = await page.locator("#view").boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
}

test("Measure section measures a distance in metric and imperial units", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await openDeterministicApp(page);
  await enableRealViewer(page);
  await openMeasureView(page);

  await expect(page.locator("#measurePanelSummary")).toHaveText("--");
  await expect(page.locator("#measureModeDistance")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.locator("#measureUnitMetric")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.locator("#btnMeasurePickA")).toBeVisible();
  await expect(page.locator("#btnMeasurePickB")).toBeVisible();
  await expect(page.locator("#btnMeasureClear")).toBeVisible();
  await expect(page.locator("#measureCoordPanel")).toBeVisible();
  // Analysis feature actions stay out of the measurement workflow.
  await expect(page.locator("#btnSolveLink")).toBeHidden();

  await pickPoint(page, "btnMeasurePickA", [0, 0, 0]);
  await expect(page.locator("#measureAX")).toHaveValue("0.00");
  await expect(page.locator("#measureAY")).toHaveValue("0.00");
  await expect(page.locator("#measureAZ")).toHaveValue("0.00");

  await pickPoint(page, "btnMeasurePickB", [3, 4, 12]);
  await expect(page.locator("#measureBX")).toHaveValue("3.00");
  await expect(page.locator("#measureBY")).toHaveValue("4.00");
  await expect(page.locator("#measureBZ")).toHaveValue("12.00");

  // 3-4-12 right triangle: 13 m exactly.
  await expect(page.locator("#measurePanelSummary")).toHaveText("13.00 m");
  await expect(page.locator("#measureDistance")).toHaveText("13.00 m");
  await expect(page.locator("#measureRow-dx")).toHaveText("3.00 m");
  await expect(page.locator("#measureRow-dy")).toHaveText("4.00 m");
  await expect(page.locator("#measureRow-dz")).toHaveText("12.00 m");

  const scene = await page.evaluate(async (spec) => {
    const { viewerRef } = await import(spec);
    const layer = viewerRef.current.measureLayer.group;
    return {
      children: layer.children.length,
      lines: layer.children.filter(
        (child) => child.type !== "Mesh" || child.material?.type === "LineMaterial",
      ).length,
      markers: layer.children.filter((child) => child.type === "Mesh").length,
    };
  }, STATE_SPECIFIER);
  // Two point markers plus four guide lines (diagonal and three axis edges).
  expect(scene.markers).toBe(2);
  expect(scene.children).toBeGreaterThanOrEqual(6);

  // Imperial inches.
  await page.locator("#measureUnitImperial").click();
  await expect(page.locator("#measureUnitImperial")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await expect(page.locator("#measurePanelSummary")).toHaveText("511.8 in");
  await expect(page.locator("#measureAX")).toHaveValue("0.0");
  await expect(page.locator("#measureBX")).toHaveValue("118.1");
  await expect(page.locator("#measureBZ")).toHaveValue("472.4");

  // Size mode reports width, depth and height of the picked box.
  await page.locator("#measureModeSize").click();
  await expect(page.locator("#measureRow-width")).toHaveText("118.1 in");
  await expect(page.locator("#measureRow-depth")).toHaveText("157.5 in");
  await expect(page.locator("#measureRow-height")).toHaveText("472.4 in");
  await expect(page.locator("#measurePanelSummary")).toContainText("×");

  // Editable coordinates stay authoritative in the active unit.
  await page.locator("#measureBX").fill("236.2");
  await page.locator("#measureBX").blur();
  await expect
    .poll(() =>
      page.evaluate(async (spec) => {
        const { state } = await import(spec);
        return state.measure.b?.[0];
      }, STATE_SPECIFIER),
    )
    .toBeCloseTo(6, 1);
  await expect(page.locator("#measureRow-width")).toHaveText("236.2 in");

  // Switching back to metric re-renders the same geometry in metres.
  await page.locator("#measureUnitMetric").click();
  await expect(page.locator("#measureRow-width")).toHaveText("6.00 m");

  // Clearing removes both points and the guides.
  await page.locator("#btnMeasureClear").click();
  await expect(page.locator("#measurePanelSummary")).toHaveText("--");
  await expect(page.locator("#measureAX")).toHaveValue("");
  await expect(page.locator("#measureBX")).toHaveValue("");
  await expect
    .poll(() =>
      page.evaluate(async (spec) => {
        const { viewerRef } = await import(spec);
        return viewerRef.current.measureLayer.group.children.length;
      }, STATE_SPECIFIER),
    )
    .toBe(0);
});

test("Measure section releases picking, guides and device actions on exit", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await openDeterministicApp(page);
  await enableRealViewer(page);
  await openMeasureView(page);
  await pickPoint(page, "btnMeasurePickA", [1, 2, 3]);
  await pickPoint(page, "btnMeasurePickB", [4, 6, 8]);

  const before = await page.evaluate(async (spec) => {
    const { state, viewerRef } = await import(spec);
    return {
      pickTarget: state.measure.pickTarget,
      guides: viewerRef.current.measureLayer.group.children.length,
    };
  }, STATE_SPECIFIER);
  expect(before.pickTarget).toBeNull();
  expect(before.guides).toBeGreaterThan(0);

  await page.locator('nav[aria-label="Workspace sections"] button', {
    hasText: "Analysis",
  }).click();
  await expect
    .poll(() =>
      page.evaluate(async (spec) => {
        const { state, viewerRef } = await import(spec);
        return {
          view: state.workspaceView,
          guides: viewerRef.current.measureLayer.group.children.length,
        };
      }, STATE_SPECIFIER),
    )
    .toEqual({ view: "analysis", guides: 0 });
  await expect(page.locator("#btnMeasurePickA")).toBeHidden();
  await expect(page.locator("#measureCoordPanel")).toBeHidden();
  await expect(page.locator("#btnSolveLink")).toBeVisible();

  // The measurement points survive the view switch for a later return.
  await openMeasureView(page);
  await expect(page.locator("#measureAX")).toHaveValue("1.00");
  await expect(page.locator("#measureBX")).toHaveValue("4.00");
  await expect(page.locator("#measurePanelSummary")).toHaveText("7.07 m");
});

test("Measure section cancels an armed pick with Escape", async ({ page }) => {
  await openDeterministicApp(page);
  await enableRealViewer(page);
  await openMeasureView(page);
  await page.locator("#btnMeasurePickA").click();
  await expect(page.locator("#btnMeasurePickA")).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.keyboard.press("Escape");
  await expect
    .poll(() =>
      page.evaluate(async (spec) => {
        const { state } = await import(spec);
        return state.measure.pickTarget;
      }, STATE_SPECIFIER),
    )
    .toBeNull();
  await expect(page.locator("#btnMeasurePickA")).toHaveAttribute(
    "aria-pressed",
    "false",
  );
});
