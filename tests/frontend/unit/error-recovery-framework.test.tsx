// @featuretrace:error-recovery-framework — Frontend tests for recovery pages, role routing, and API error display.
// Layer: test
// Data flow: Jest render -> recovery components/classifier -> safe user-facing states (global).
// Related: frontend/src/components/shared/RecoveryPanel.tsx
//          frontend/src/lib/api-error.ts

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";

import NotFound from "@/app/not-found";
import AppError from "@/app/error";
import SafetyFeedPage from "@/app/(app)/intelligence/safety-feed/page";
import APIErrorMessage from "@/components/shared/APIErrorMessage";
import {
  FeatureDisabled,
  InternalPreview,
  PermissionDenied,
  ServerError,
} from "@/components/shared/RecoveryStates";
import { classifyApiError } from "@/lib/api-error";
import { getRoleAwareDashboardPath } from "@/lib/recovery";
import { mockPush } from "@/__tests__/__mocks__/next/navigation";

let mockUser: any = null;
// Safety Feed now reads the toggle itself instead of being a hardcoded disabled
// shell, so the mock has to supply the rest of the AuthContext surface it uses.
let mockFeatureEnabled = false;
const mockApiGet = jest.fn();

jest.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: mockUser,
    loading: false,
    api: { get: mockApiGet, patch: jest.fn() },
    hasFeatureAccess: () => mockFeatureEnabled,
    isManager: () => false,
    isECMember: () => false,
  }),
}));

jest.mock("sonner", () => ({ toast: { success: jest.fn(), error: jest.fn() } }));

describe("error recovery framework", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockUser = null;
    mockFeatureEnabled = false;
    mockApiGet.mockResolvedValue({ data: [] });
  });

  it("not-found page renders a friendly recovery page", () => {
    render(<NotFound />);
    expect(screen.getByRole("heading", { name: /we could not find that page/i })).toBeInTheDocument();
    expect(screen.getByTestId("not-found-recovery")).toBeInTheDocument();
    expect(screen.queryByText(/traceback|stack trace/i)).not.toBeInTheDocument();
  });

  it("error page renders without exposing stack traces", () => {
    render(<AppError error={Object.assign(new Error("secret stack detail"), { digest: "req-123" })} reset={jest.fn()} />);
    expect(screen.getByRole("heading", { name: /this page could not load/i })).toBeInTheDocument();
    expect(screen.getByText("req-123")).toBeInTheDocument();
    expect(screen.queryByText(/secret stack detail/i)).not.toBeInTheDocument();
  });

  it("all roles resolve to /dashboard (route handles its own role logic)", () => {
    for (const role of ["super_admin", "strata_admin", "strata_manager", "ec_member", "owner", "tenant", "guest"]) {
      expect(getRoleAwareDashboardPath({ role })).toBe("/dashboard");
    }
    expect(getRoleAwareDashboardPath(null)).toBe("/dashboard");
    expect(getRoleAwareDashboardPath(undefined)).toBe("/dashboard");
  });

  it("unauthenticated dashboard action links to /dashboard (auth guard handles redirect to login)", () => {
    render(<NotFound />);
    fireEvent.click(screen.getByRole("button", { name: /go to dashboard/i }));
    expect(mockPush).toHaveBeenCalledWith("/dashboard");
  });

  it("feature-disabled, internal-preview, and permission-denied components render", () => {
    const { rerender } = render(<FeatureDisabled featureKey="powerhouse_conversations" />);
    expect(screen.getByTestId("feature-disabled-recovery")).toHaveTextContent("powerhouse_conversations");
    rerender(<InternalPreview featureKey="powerhouse_conversations" />);
    expect(screen.getByTestId("internal-preview-recovery")).toHaveTextContent("Internal preview");
    rerender(<PermissionDenied requestId="req-403" />);
    expect(screen.getByTestId("permission-denied-recovery")).toHaveTextContent("req-403");
  });

  it("server-error component shows retry", () => {
    render(<ServerError requestId="req-500" onRetry={jest.fn()} />);
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("API error component renders each major type and only shows retry for retryable errors", () => {
    const serverError = classifyApiError({
      response: { status: 500, data: { error: { code: "SERVER_ERROR", message: "Safe server message", retryable: true } } },
      config: { method: "get", url: "/x" },
    });
    const { rerender } = render(<APIErrorMessage error={serverError} onRetry={jest.fn()} />);
    expect(screen.getByTestId("api-error-server_error")).toHaveTextContent("Safe server message");
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();

    const forbidden = classifyApiError({
      response: { status: 403, data: { error: { code: "FORBIDDEN", message: "No access", retryable: false } } },
      config: { method: "get", url: "/x" },
    });
    rerender(<APIErrorMessage error={forbidden} onRetry={jest.fn()} />);
    expect(screen.getByTestId("api-error-forbidden")).toHaveTextContent("No access");
    expect(screen.queryByRole("button", { name: /retry/i })).not.toBeInTheDocument();

    expect(classifyApiError({ response: { status: 401, data: { error: { code: "UNAUTHENTICATED" } } } }).category).toBe("unauthenticated");
    expect(classifyApiError({ response: { status: 404, data: { error: { code: "NOT_FOUND" } } } }).category).toBe("not_found");
    expect(classifyApiError({ response: { status: 422, data: { error: { code: "VALIDATION_ERROR" } } } }).category).toBe("validation");
    expect(classifyApiError({ response: { status: 409, data: { error: { code: "CONFLICT" } } } }).category).toBe("conflict");
    expect(classifyApiError({ response: { status: 429, data: { error: { code: "RATE_LIMITED" } } } }).category).toBe("rate_limited");
    expect(classifyApiError({ response: { status: 503, data: { error: { code: "SERVICE_UNAVAILABLE" } } } }).category).toBe("service_unavailable");
    expect(classifyApiError({ response: { status: 403, data: { error: { code: "FEATURE_DISABLED" } } } }).category).toBe("feature_disabled");
    expect(classifyApiError({ code: "ECONNABORTED" }).category).toBe("network_error");
  });

  it("Powerhouse disabled state shows friendly recovery, not a raw 404", () => {
    render(<FeatureDisabled featureKey="powerhouse_conversations" />);
    expect(screen.getByRole("heading", { name: /this feature is not available yet/i })).toBeInTheDocument();
  });

  it("safety feed shows the recovery page when the toggle is off", () => {
    mockFeatureEnabled = false;
    render(<SafetyFeedPage />);
    expect(screen.getByTestId("feature-disabled-recovery")).toHaveTextContent("safety_feed");
  });

  it("safety feed renders the real feed when the toggle is on", async () => {
    // Regression: this route used to render the disabled shell unconditionally,
    // so an enabled feature still reported "feature_disabled" — while
    // backend/routers/safety.py was implemented and registered the whole time.
    mockFeatureEnabled = true;
    render(<SafetyFeedPage />);
    expect(await screen.findByTestId("safety-feed-page")).toBeInTheDocument();
    expect(screen.queryByTestId("feature-disabled-recovery")).not.toBeInTheDocument();
    expect(mockApiGet).toHaveBeenCalledWith("/safety/events");
  });

  it("safety feed distinguishes an empty feed from an unreachable one", async () => {
    mockFeatureEnabled = true;
    mockApiGet.mockRejectedValueOnce({ response: { data: { detail: "boom" } } });
    render(<SafetyFeedPage />);
    // A failed load must not render as "no safety events" — missing and zero are
    // distinct states, and on a safety log that distinction matters.
    expect(await screen.findByTestId("safety-feed-error")).toBeInTheDocument();
    expect(screen.queryByTestId("safety-feed-empty")).not.toBeInTheDocument();
  });
});
