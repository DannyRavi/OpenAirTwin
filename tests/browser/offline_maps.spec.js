import { expect, test } from "@playwright/test";

const enabled = process.env.OAT_RUN_OFFLINE_BROWSER_TEST === "1";

test.describe("offline region persistence", () => {
  test.skip(
    !enabled,
    "Set OAT_RUN_OFFLINE_BROWSER_TEST=1 to run the real scene offline check.",
  );

  test("a downloaded region reopens with external requests blocked", async ({
    page,
    request,
  }) => {
    test.setTimeout(180_000);
    const manifest = await (await request.get("/api/scene/manifest")).json();
    const tiles = manifest.tiles ?? [];
    const defaults = manifest.scene?.default_tile_ids ?? [];
    const tile = tiles.find((candidate) => defaults.includes(candidate.id)) ?? tiles[0];
    if (!tile) test.skip(true, "The server has no loaded scene tiles.");
    let regionId = null;
    try {
      await request.post("/api/offline/mode", { data: { mode: "automatic" } });
      const created = await request.post("/api/offline/regions", {
        data: {
          name: `Browser Offline ${Date.now()}`,
          tile_ids: [tile.id],
          layers: ["buildings", "terrain"],
        },
      });
      expect(created.status()).toBe(202);
      const body = await created.json();
      regionId = body.region_id;
      await expect
        .poll(
          async () => {
            const response = await request.get(`/api/offline/regions/${regionId}`);
            if (response.status() !== 200) return "pending";
            const payload = await response.json();
            return payload.region?.status ?? "pending";
          },
          { timeout: 120_000, intervals: [500, 1000, 2000] },
        )
        .toBe("ready");

      const region = await (
        await request.get(`/api/offline/regions/${regionId}`)
      ).json();
      expect(region.region.offline_ready).toBe(true);
      expect(region.region.asset_count).toBeGreaterThan(0);
      expect(region.region.missing_count).toBe(0);

      await request.post("/api/offline/mode", { data: { mode: "offline" } });
      const opened = await request.post(`/api/offline/regions/${regionId}/open`);
      expect(opened.ok()).toBe(true);
      const openedPayload = await opened.json();
      expect(openedPayload.tile_ids).toEqual([tile.id]);

      const external = [];
      await page.route("**/*", (route) => {
        const url = route.request().url();
        if (
          url.startsWith("http://127.0.0.1:8090") ||
          url.startsWith("data:") ||
          url.startsWith("blob:")
        ) {
          return route.continue();
        }
        external.push(url);
        return route.abort("internetdisconnected");
      });
      await page.goto("/", { waitUntil: "domcontentloaded" });
      await expect(page).toHaveTitle("Hammurabi");
      await expect(page.locator("#entryScreen")).toBeVisible();
      expect(
        await page.evaluate(() => window.__HAMMURABI_OFFLINE__?.mode),
      ).toBe("offline");
      await expect(page.locator("#btnEnterScene")).toBeEnabled();
      await page.locator("#btnEnterScene").click();
      await expect(page.locator("#entryScreen")).toHaveClass(/hidden/, {
        timeout: 60_000,
      });
      await expect(page.locator(".hx-toolbar")).toBeVisible();
      expect(external).toEqual([]);
      const canvas = await page.locator("#view").evaluate((element) => ({
        width: element.width,
        height: element.height,
      }));
      expect(canvas.width).toBeGreaterThan(0);
      expect(canvas.height).toBeGreaterThan(0);
    } finally {
      if (regionId) {
        await request.post(`/api/offline/regions/${regionId}/delete`);
      }
      await request.post("/api/offline/mode", { data: { mode: "automatic" } });
    }
  });
});
