// @featuretrace:dashboard-v2 — First visit says "Welcome"; a return says how long it has been.
// Layer: test
// Data flow: user.last_login_at -> daysSinceLastVisit -> SinceLastVisit heading (building-scoped).
// Related: frontend/src/components/dashboard/SinceLastVisit.tsx
//          frontend/src/app/(dashboard)/dashboard/ManagementDashboard.tsx
/**
 * The card defaulted daysSince to 1, so an account with no previous login read
 * "Since your last visit · 1 day ago … here's what changed while you were away" — three
 * false claims in one heading: that they had visited, when, and that the items below
 * postdated it.
 *
 * The second defect was quieter: every elapsed time rendered as days, so a manager
 * returning after a long absence read "417 days ago".
 */
import React from "react";
import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";

import SinceLastVisit from "@/components/dashboard/SinceLastVisit";

describe("SinceLastVisit heading", () => {
  it("greets a first visit instead of inventing an elapsed time", () => {
    render(<SinceLastVisit daysSince={null} items={[]} />);
    expect(screen.getByText(/1st visit · Welcome/i)).toBeInTheDocument();
    // The "while you were away" subtitle asserts a visit that never happened.
    expect(screen.queryByText(/while you were away/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Recent activity in your building/i)).toBeInTheDocument();
  });

  it("never claims a last visit when there was none", () => {
    render(<SinceLastVisit daysSince={null} items={[]} />);
    expect(screen.queryByText(/Since your last visit/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/1 day ago/i)).not.toBeInTheDocument();
  });

  it.each([
    [1, /1 day ago/i],
    [3, /3 days ago/i],
    [13, /13 days ago/i],
    [21, /3 weeks ago/i],
    [90, /3 months ago/i],
    [417, /1 year ago/i],
    [800, /2 years ago/i],
  ])("renders %i days as a readable interval", (days, expected) => {
    render(<SinceLastVisit daysSince={days as number} items={[]} />);
    expect(screen.getByText(expected)).toBeInTheDocument();
    expect(screen.getByText(/Since your last visit/i)).toBeInTheDocument();
  });

  it("keeps the returning-visitor subtitle for a real gap", () => {
    render(<SinceLastVisit daysSince={5} items={[]} />);
    expect(screen.getByText(/while you were away/i)).toBeInTheDocument();
  });

  it("does not render days for a multi-week gap", () => {
    // The specific regression: 21 days once read "21 days ago".
    render(<SinceLastVisit daysSince={21} items={[]} />);
    expect(screen.queryByText(/21 days ago/i)).not.toBeInTheDocument();
  });
});
