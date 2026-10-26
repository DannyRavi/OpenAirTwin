import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AppProviders } from "../app/AppProviders.tsx";
import { OfflinePanel } from "../app-shell/HammurabiWorkspace.tsx";
import type { OfflineUiSnapshot } from "../app-shell/shell-ui-model.ts";
import type { RootErrorReporter } from "../runtime/error-reporting.ts";
import { CommandBus } from "../runtime/ui-command.ts";
import type { AnyUiCommand } from "../runtime/ui-command.ts";

const reportError: RootErrorReporter = vi.fn();

function snapshot(patch: Partial<OfflineUiSnapshot> = {}): OfflineUiSnapshot {
  return {
    available: true,
    mode: "automatic",
    activeRegionId: null,
    activeRegionName: null,
    regions: [],
    totalBytes: 0,
    freeBytes: 0,
    storageRoot: "/tmp/offline",
    sceneRoot: "/tmp/scene",
    busyRegionId: null,
    busy: false,
    error: null,
    notice: "",
    draftBounds: { west: 51.38, east: 51.4, south: 35.68, north: 35.7 },
    estimatedBytes: null,
    estimating: false,
    ...patch,
  };
}

function renderPanel(
  offline: OfflineUiSnapshot,
  commandBus = new CommandBus(),
) {
  return {
    commandBus,
    ...render(
      <AppProviders
        commandBus={commandBus}
        reportError={reportError}
        rootId="offline-panel-test"
      >
        <OfflinePanel offline={offline} />
      </AppProviders>,
    ),
  };
}

describe("Hammurabi offline maps panel", () => {
  it("dispatches the selected connectivity mode", async () => {
    const commandBus = new CommandBus();
    const received: AnyUiCommand[] = [];
    commandBus.subscribe("offline.mode.set", (command) => {
      received.push(command);
    });
    renderPanel(snapshot(), commandBus);

    fireEvent.click(screen.getByRole("button", { name: "Strict offline" }));
    await waitFor(() => {
      expect(received).toEqual([
        { name: "offline.mode.set", payload: { mode: "offline" } },
      ]);
    });
  });

  it("creates a region from the current bounds and selected layers", async () => {
    const commandBus = new CommandBus();
    const received: AnyUiCommand[] = [];
    commandBus.subscribe("offline.region.create", (command) => {
      received.push(command);
    });
    renderPanel(snapshot(), commandBus);

    fireEvent.change(screen.getByLabelText("Region name"), {
      target: { value: "Campus" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Download offline region" }),
    );
    await waitFor(() => {
      expect(received).toHaveLength(1);
      expect(received[0]).toMatchObject({
        name: "offline.region.create",
        payload: {
          name: "Campus",
          bounds: { west: 51.38, east: 51.4, south: 35.68, north: 35.7 },
          layers: ["buildings", "terrain", "map"],
        },
      });
    });
  });

  it("shows real region status and opens only ready regions", async () => {
    const commandBus = new CommandBus();
    const received: AnyUiCommand[] = [];
    commandBus.subscribe("offline.region.open", (command) => {
      received.push(command);
    });
    renderPanel(
      snapshot({
        regions: [
          {
            regionId: "ready-region",
            name: "Ready region",
            status: "ready",
            tileCount: 2,
            bounds: { west: 51.38, east: 51.4, south: 35.68, north: 35.7 },
            mapBounds: { west: 51.38, east: 51.4, south: 35.68, north: 35.7 },
            bytesTotal: 1048576,
            assetCount: 8,
            missingCount: 0,
            progress: 1,
            message: "Ready for offline use",
            error: null,
            basemap: true,
            basemapUrl: "/api/offline/regions/ready-region/basemap.png",
            offlineReady: true,
            active: false,
          },
        ],
      }),
      commandBus,
    );

    expect(screen.getByText("Ready offline")).toBeTruthy();
    expect(screen.getByText("1.0 MB")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Open offline" }));
    await waitFor(() => {
      expect(received).toEqual([
        {
          name: "offline.region.open",
          payload: { regionId: "ready-region" },
        },
      ]);
    });
  });
});
