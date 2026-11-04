import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";
import type { Withheld } from "../domain/gates";
import type { GateOutcome } from "../lib/analysis";
import { WithheldBlock, WithheldFigure } from "./Withheld";

const withheld: Withheld = {
  check: {
    gate_id: "cv-int2-v2",
    gate_sha256: "7ab3a2b0e1836b1f21892ac11993bc77",
    metric: "entering_count_error_share",
    scope: "site",
    split: "held_out",
    measured: 0.2033,
    comparator: "<=",
    threshold: 0.2,
    n: 551,
    passed: false,
  } as GateOutcome,
  gateId: "cv-int2-v2",
  versus: "20.33% count error, limit 20%",
  limitNote: "The limit was fixed before the read and was not moved, so what it covers is withheld.",
  reason: "withheld: failed cv-int2-v2",
};

afterEach(cleanup);

function figure() {
  return render(
    <MemoryRouter>
      <WithheldFigure what="Entering flow" withheld={withheld} />
    </MemoryRouter>,
  );
}

describe("a withheld figure", () => {
  it("says so where the number would be, with the measured value and the limit", () => {
    figure();
    expect(screen.getByText("Withheld")).toBeTruthy();
    expect(screen.getByText("20.33% count error, limit 20%")).toBeTruthy();
    expect(screen.getByText("Limit fixed before the read")).toBeTruthy();
  });

  it("gives the whole check one interaction away, and closes on Escape", () => {
    figure();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { expanded: false }));
    const panel = screen.getByRole("dialog", { name: "Why Entering flow is withheld" });
    expect(panel.textContent).toContain("Entering count error");
    expect(panel.textContent).toContain("0.2033");
    expect(panel.textContent).toContain("at most 0.20");
    expect(panel.textContent).toContain("held-out read");
    expect(panel.textContent).toContain("fixed before the read");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("button").getAttribute("aria-expanded")).toBe("false");
  });

  it("puts no flow figure in the value's place", () => {
    const { container } = figure();
    expect(container.textContent).not.toMatch(/veh/);
  });

  it("fills a chart's place with the case for it, not an empty plot", () => {
    render(
      <MemoryRouter>
        <WithheldBlock what="Entering flow" withheld={withheld} note="Two different checks." />
      </MemoryRouter>,
    );
    expect(screen.getByText(/none is estimated in its place/)).toBeTruthy();
    expect(screen.getByText("Two different checks.")).toBeTruthy();
    expect(screen.getByText("at most 0.20")).toBeTruthy();
  });
});
