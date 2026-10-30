import { useUiCommand } from "../app/use-ui-command.ts";
import type { MeasureUiSnapshot } from "./shell-ui-model.ts";

export function MeasurePanel({
  measure,
}: {
  readonly measure: MeasureUiSnapshot;
}) {
  const dispatch = useUiCommand();
  const unitName = measure.unit === "in" ? "Imperial (in)" : "Metric (m)";
  return (
    <div className="hx-measure-panel">
      <section className="hx-section">
        <h3 className="hx-section-title">What to measure</h3>
        <div
          className="hx-segment hx-segment--two"
          role="group"
          aria-label="Measurement type"
        >
          <button
            className={`hx-segment-btn${measure.mode === "distance" ? " is-active" : ""}`}
            id="measureModeDistance"
            type="button"
            aria-pressed={measure.mode === "distance"}
            onClick={() => {
              void dispatch({
                name: "measure.mode.set",
                payload: { mode: "distance" },
              });
            }}
          >
            {"Distance"}
          </button>
          <button
            className={`hx-segment-btn${measure.mode === "size" ? " is-active" : ""}`}
            id="measureModeSize"
            type="button"
            aria-pressed={measure.mode === "size"}
            onClick={() => {
              void dispatch({
                name: "measure.mode.set",
                payload: { mode: "size" },
              });
            }}
          >
            {"Size"}
          </button>
        </div>
        <p className="hx-hint" id="measureModeHint">
          {measure.mode === "size"
            ? "Pick two opposite building corners to read width, depth and height."
            : "Pick any two surface points to read the straight-line distance and its X/Y/Z components."}
        </p>
      </section>

      <section className="hx-section">
        <h3 className="hx-section-title">Units</h3>
        <div
          className="hx-segment hx-segment--two"
          role="group"
          aria-label="Measurement units"
        >
          <button
            className={`hx-segment-btn${measure.unit === "m" ? " is-active" : ""}`}
            id="measureUnitMetric"
            type="button"
            aria-pressed={measure.unit === "m"}
            onClick={() => {
              void dispatch({
                name: "measure.unit.set",
                payload: { unit: "m" },
              });
            }}
          >
            {"Metric (m)"}
          </button>
          <button
            className={`hx-segment-btn${measure.unit === "in" ? " is-active" : ""}`}
            id="measureUnitImperial"
            type="button"
            aria-pressed={measure.unit === "in"}
            onClick={() => {
              void dispatch({
                name: "measure.unit.set",
                payload: { unit: "in" },
              });
            }}
          >
            {"Imperial (in)"}
          </button>
        </div>
        <p className="hx-hint">
          {`Active unit: ${unitName}. Coordinates stay editable in the device bar.`}
        </p>
      </section>

      <section className="hx-section">
        <h3 className="hx-section-title">
          {measure.mode === "size" ? "Building size" : "Distance"}
        </h3>
        <p className="hx-measure-summary" id="measurePanelSummary">
          {measure.summary}
        </p>
        <p className="hx-hint" id="measurePanelSecondary">
          {measure.secondary}
        </p>
        <dl className="hx-key-values" id="measurePanelRows">
          {measure.rows.map((row) => (
            <div key={row.id}>
              <dt>{row.label}</dt>
              <dd id={`measureRow-${row.id}`}>{row.value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="hx-section">
        <h3 className="hx-section-title">Points</h3>
        <dl className="hx-key-values">
          <div>
            <dt>Point A</dt>
            <dd>{measure.hasA ? "Placed" : "Not placed"}</dd>
          </div>
          <div>
            <dt>Point B</dt>
            <dd>{measure.hasB ? "Placed" : "Not placed"}</dd>
          </div>
        </dl>
        <p className="hx-hint" id="measurePanelHint">
          {measure.hint}
        </p>
        <button
          className="hx-btn hx-btn--block hx-btn--ghost"
          id="btnMeasureClearPanel"
          type="button"
          disabled={!measure.hasA && !measure.hasB}
          onClick={() => {
            void dispatch({
              name: "measure.point.clear",
              payload: { point: "all" },
            });
          }}
        >
          {measure.mode === "size" ? "Clear points" : "Clear measurement"}
        </button>
      </section>
    </div>
  );
}
