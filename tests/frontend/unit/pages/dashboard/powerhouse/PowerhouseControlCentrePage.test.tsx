// @featuretrace:powerhouse-ui-navigation — Jest coverage for Powerhouse Control Centre visibility and safe links.
// Layer: test
// Data flow: test -> PowerhouseControlCentrePage -> mocked /features/powerhouse/status payload (building-scoped).
// Related: frontend/src/pages/powerhouse/PowerhouseControlCentrePage.tsx

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import { usePathname } from "next/navigation";

import PowerhouseControlCentrePage from "@/pages/dashboard/powerhouse/PowerhouseControlCentrePage";
import { POWERHOUSE_FEATURES } from "@/lib/powerhouseFeatureCatalogue";

const mockApi = { get: jest.fn() };

const mockAuth: {
  api: typeof mockApi;
  loading: boolean;
  user: { role: string; ec_position?: string };
  isAdmin: () => boolean;
  isManager: () => boolean;
} = {
  api: mockApi,
  loading: false,
  user: { role: "super_admin" },
  isAdmin: () => true,
  isManager: () => true,
};

jest.mock("@/contexts/AuthContext", () => ({
  useAuth: () => mockAuth,
}));

jest.mock("next/link", () => ({ children, href, ...props }: any) => <a href={href} {...props}>{children}</a>);

function statusPayload(role = "super_admin") {
  return {
    building_id: "13195",
    role,
    diagnostic: role === "super_admin" || role === "strata_manager",
    features: POWERHOUSE_FEATURES
      .filter((feature) => feature.allowedRoles.includes(role as any))
      .map((feature) => ({
        ...feature,
        enabled: feature.key === "powerhouse_conversations",
        currentStatus: feature.key === "powerhouse_conversations" ? "enabled" : "disabled",
        visibleForRole: true,
        reason: feature.disabledReason,
        globalDefault: false,
        buildingOverride: feature.key === "powerhouse_conversations",
      })),
  };
}

describe("PowerhouseControlCentrePage", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockAuth.loading = false;
    mockAuth.user = { role: "super_admin" };
    mockAuth.isAdmin = () => true;
    mockAuth.isManager = () => true;
    // Default fallback for the command-foundation-health follow-up call each test doesn't
    // explicitly assert on; individual tests still override the first call via mockResolvedValueOnce.
    mockApi.get.mockResolvedValue({ data: { scheme_found: false, domains: [], outbox: null, idempotency: null } });
  });

  it("renders the Powerhouse control centre and diagnostic status panel", async () => {
    mockApi.get.mockResolvedValueOnce({ data: statusPayload() });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getByTestId("powerhouse-control-centre")).toBeInTheDocument());
    expect(screen.getByTestId("powerhouse-toggle-status-panel")).toBeInTheDocument();
  });

  it("disabled feature tile does not link to a 404 route", async () => {
    mockApi.get.mockResolvedValueOnce({ data: statusPayload() });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getByTestId("powerhouse-disabled-link-powerhouse_shared_inbox")).toBeInTheDocument());
    expect(screen.queryByTestId("powerhouse-open-powerhouse_shared_inbox")).not.toBeInTheDocument();
  });

  it("internal-preview feature shows warning", async () => {
    mockApi.get.mockResolvedValueOnce({ data: statusPayload() });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getByTestId("powerhouse-warning-cutover_control_plane")).toBeInTheDocument());
  });

  it("owner cannot see admin or cutover features", async () => {
    mockAuth.user = { role: "owner" };
    mockAuth.isAdmin = () => false;
    mockAuth.isManager = () => false;
    render(<PowerhouseControlCentrePage />);
    expect(screen.getByTestId("powerhouse-control-forbidden")).toBeInTheDocument();
    expect(mockApi.get).not.toHaveBeenCalled();
  });

  it("tenant cannot see admin or cutover features", async () => {
    mockAuth.user = { role: "tenant" };
    mockAuth.isAdmin = () => false;
    mockAuth.isManager = () => false;
    render(<PowerhouseControlCentrePage />);
    expect(screen.getByTestId("powerhouse-control-forbidden")).toBeInTheDocument();
    expect(mockApi.get).not.toHaveBeenCalled();
  });

  it("strata manager sees allowed subset without cutover control", async () => {
    mockAuth.user = { role: "strata_manager" };
    mockAuth.isAdmin = () => false;
    mockAuth.isManager = () => true;
    mockApi.get.mockResolvedValueOnce({ data: statusPayload("strata_manager") });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getByTestId("powerhouse-feature-powerhouse_conversations")).toBeInTheDocument());
    expect(screen.queryByTestId("powerhouse-feature-cutover_control_plane")).not.toBeInTheDocument();
  });

  it("chairman (role=ec_member) can access the control centre via isManager()", async () => {
    // 'chairman' is never a top-level user.role value (see rules/post-compact-critical.md) —
    // a real chairman has role='ec_member' and isManager() returns true for that role, which
    // is what actually grants access here (there is no chairman-literal special case).
    mockAuth.user = { role: "ec_member", ec_position: "CHAIRMAN" };
    mockAuth.isAdmin = () => false;
    mockAuth.isManager = () => true;
    mockApi.get.mockResolvedValueOnce({ data: statusPayload("ec_member") });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getByTestId("powerhouse-control-centre")).toBeInTheDocument());
  });

  it("never grants control-centre access via a literal 'chairman' role string (regression guard)", async () => {
    // Backend never issues role: 'chairman'. isAdmin/isManager both false simulates the
    // (impossible) case where an actual role='chairman' user reaches the frontend — access
    // must be denied since isInternal no longer has a role === 'chairman' fallback.
    mockAuth.user = { role: "chairman" };
    mockAuth.isAdmin = () => false;
    mockAuth.isManager = () => false;
    render(<PowerhouseControlCentrePage />);
    expect(screen.getByTestId("powerhouse-control-forbidden")).toBeInTheDocument();
    expect(mockApi.get).not.toHaveBeenCalled();
  });

  it("docs links route through the in-app tech-docs viewer, not the raw static page", async () => {
    // Navigating straight to /tech-docs/index.html is a full page load out of the SPA;
    // most individual tech-docs pages have no way back into the app from there. The
    // in-app viewer at /admin/tech-docs keeps the DashboardLayout sidebar.
    mockApi.get.mockResolvedValueOnce({ data: statusPayload() });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getAllByText("Docs").length).toBeGreaterThan(0));
    expect(screen.getAllByText("Docs")[0].closest("a")).toHaveAttribute("href", "/admin/tech-docs");
  });

  it("super_admin sees diagnostic status", async () => {
    mockApi.get.mockResolvedValueOnce({ data: statusPayload() });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getByText("Global default")).toBeInTheDocument());
  });

  it("enabled feature links to existing page", async () => {
    mockApi.get.mockResolvedValueOnce({ data: statusPayload() });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getByTestId("powerhouse-open-powerhouse_conversations")).toHaveAttribute("href", "/powerhouse/conversations"));
  });

  it("disabled feature links to disabled/recovery state", async () => {
    mockApi.get.mockResolvedValueOnce({ data: statusPayload() });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getByTestId("powerhouse-disabled-link-powerhouse_automation_rules")).toHaveTextContent("Unavailable"));
  });

  it("navigation does not include missing routes as open links", async () => {
    mockApi.get.mockResolvedValueOnce({ data: statusPayload() });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getByTestId("powerhouse-disabled-link-powerhouse_email_intake")).toHaveTextContent("No UI route yet"));
  });

  it("does not render a dead Open link back to the page the user is already on", async () => {
    // powerhouse_control_centre and feature_toggle_status both route to
    // /powerhouse — the page itself. Regression guard for the bug
    // where clicking "Open" appeared to do nothing because it linked to the
    // current URL.
    (usePathname as jest.Mock).mockReturnValue("/powerhouse");
    mockApi.get.mockResolvedValueOnce({ data: statusPayload() });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getByTestId("powerhouse-current-page-powerhouse_control_centre")).toHaveTextContent("You are here"));
    expect(screen.queryByTestId("powerhouse-open-powerhouse_control_centre")).not.toBeInTheDocument();
    (usePathname as jest.Mock).mockReturnValue("/");
  });

  it("hides the Docs link for internal roles the tech-docs auth-guard would bounce (regression guard)", async () => {
    // frontend/public/tech-docs/auth-guard.js hard-redirects any non-super_admin
    // session straight back to /dashboard. Showing "Docs" to a strata_manager or
    // ec_member here previously looked like the app crashing on click.
    mockAuth.user = { role: "strata_manager" };
    mockAuth.isAdmin = () => false;
    mockAuth.isManager = () => true;
    mockApi.get.mockResolvedValueOnce({ data: statusPayload("strata_manager") });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getByTestId("powerhouse-control-centre")).toBeInTheDocument());
    expect(screen.queryByText("Docs")).not.toBeInTheDocument();
  });

  it("groups feature tiles under category headings", async () => {
    mockApi.get.mockResolvedValueOnce({ data: statusPayload() });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getByTestId("powerhouse-control-centre")).toBeInTheDocument());
    expect(screen.getByText("Control & Visibility")).toBeInTheDocument();
    expect(screen.getByText("Communications")).toBeInTheDocument();
    expect(screen.getByText("Workflow & Automation")).toBeInTheDocument();
  });

  it("renders the command foundation health panel for the new diagnostic card", async () => {
    mockApi.get.mockResolvedValueOnce({ data: statusPayload() });
    mockApi.get.mockResolvedValueOnce({
      data: {
        building_id: "13195",
        scheme_found: true,
        domains: [{ domain: "powerhouse_conversations", mode: "mongo_primary", readiness_status: "unknown" }],
        outbox: { pending: 2, oldest_pending: null, dead_lettered: 0 },
        idempotency: { incomplete_records: 1 },
      },
    });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getByTestId("powerhouse-command-foundation-panel")).toBeInTheDocument());
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("shows a friendly message when no PostgreSQL scheme exists for the building yet", async () => {
    mockApi.get.mockResolvedValueOnce({ data: statusPayload() });
    mockApi.get.mockResolvedValueOnce({ data: { building_id: "UP-DEMO-001", scheme_found: false, domains: [], outbox: null, idempotency: null } });
    render(<PowerhouseControlCentrePage />);
    await waitFor(() => expect(screen.getByTestId("powerhouse-command-foundation-no-scheme")).toBeInTheDocument());
  });
});
