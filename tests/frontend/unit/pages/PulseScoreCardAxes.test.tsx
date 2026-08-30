// @featuretrace:dashboard-v2 — An unavailable pulse axis reads NA and draws no bar.
// Layer: test
// Data flow: /community-dashboard/health-score -> pulseAxesFrom -> PulseScoreCard axes (building-scoped).
// Related: frontend/src/components/dashboard/PulseScoreCard.tsx
//          backend/services/health_score_service.py
/**
 * Two failures this pins, both seen live on East Gate.
 *
 * 1. A null axis rendered the raw value — an empty cell — and set `width: null%`, which
 *    is invalid CSS, so the track kept whatever width it last had and the five axes no
 *    longer lined up.
 * 2. The tempting fix is to substitute 0. That is the worse bug: a fabricated zero on a
 *    health axis is exactly what was removed from the engagement pulse on 2026-08-24,
 *    where "no volunteer events recorded" scored 0/100 and drew a lone full-red axis
 *    beside four blank ones.
 */
import React from "react";
import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";

import PulseScoreCard from "@/components/dashboard/PulseScoreCard";

const AXES = [
  { k: "Financial", v: 25, color: "#16A34A" },
  { k: "Maintenance", v: 100, color: "#F59E0B" },
  { k: "Compliance", v: null, color: "#0EA5E9" },
  { k: "Engagement", v: null, color: "#E11D48" },
  { k: "Disputes", v: null, color: "#7C3AED" },
];

const renderCard = () =>
  render(
    <PulseScoreCard
      score={59}
      grade="C"
      breakdown={AXES as any}
      unavailableComponents={["compliance", "engagement", "dispute"]}
    />,
  );

describe("PulseScoreCard axes", () => {
  it("shows NA for an axis with no data", () => {
    renderCard();
    // Three unavailable axes, three NA labels.
    expect(screen.getAllByText("NA")).toHaveLength(3);
  });

  it("never substitutes 0 for missing data", () => {
    renderCard();
    // A literal "0" would mean a measured zero, which is a different claim entirely.
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("still renders the axes that do have values", () => {
    renderCard();
    expect(screen.getByText("25")).toBeInTheDocument();
    expect(screen.getByText("100")).toBeInTheDocument();
  });

  it("explains an unavailable axis on hover rather than leaving it blank", () => {
    renderCard();
    const na = screen.getAllByText("NA")[0];
    expect(na).toHaveAttribute("title", expect.stringContaining("not available"));
    expect(na.getAttribute("title")).toContain("rather than counted as zero");
  });

  it("explains what a populated axis measures", () => {
    renderCard();
    const financial = screen.getByText("25");
    expect(financial.getAttribute("title")).toContain("25/100");
    // Sourced from health_score_service, so the tooltip cannot drift from the formula.
    expect(financial.getAttribute("title")).toContain("arrears");
  });
});
