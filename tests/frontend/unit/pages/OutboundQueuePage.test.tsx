// @featuretrace:outbound-message-queue — Console renders honest state and gates actions.
// Layer: test
// Data flow: /outbound-messages -> OutboundQueuePage -> table + tiles + help panel (building-scoped).
// Related: frontend/src/pages/dashboard/admin/OutboundQueuePage.jsx
/**
 * The console's job is to tell an operator the truth about held mail.
 *
 * The mocked auth object is hoisted to a module-level const, not rebuilt per call: the
 * page lists `api` in a useCallback dependency array, and a fresh object each render
 * re-fires the loader forever. That pattern hung two suites indefinitely in this repo
 * before (tasks/lessons.md, 2026-08-25).
 */
import React from "react";
import "@testing-library/jest-dom";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const HELD_ROW = {
  id: "m-1", to_email: "owner@example.com", subject: "Quarterly levy notice",
  context: "levy_reminder", status: "held", created_at: "2026-08-27T01:00:00Z",
  hold_reason: "queue disabled for this building", will_send_next_tick: false,
};

const listResponse = {
  data: {
    messages: [HELD_ROW], total: 1, limit: 200, offset: 0,
    queue_settings: { enabled: false, hold_seconds: 30, expiry_hours: 48, disabled_categories: [] },
    unknown_fields: [],
    search_help: { syntax: [{ example: "status:held", means: "only held messages" }], fields: ["status", "to"] },
  },
};
const summaryResponse = {
  data: { counts: { held: 1, sent: 0, cancelled: 0, failed: 0, sending: 0, expired: 0 }, queue_settings: {} },
};

const api = {
  get: jest.fn((url: string) => Promise.resolve(url.includes("summary") ? summaryResponse : listResponse)),
  post: jest.fn(() => Promise.resolve({ data: { success: true } })),
  put: jest.fn(() => Promise.resolve({ data: { success: true } })),
};
const AUTH = { api, isManager: () => true, loading: false };

jest.mock("@/contexts/AuthContext", () => ({ useAuth: () => AUTH }));
jest.mock("next/navigation", () => ({ useRouter: () => ({ replace: jest.fn(), push: jest.fn() }) }));
jest.mock("sonner", () => ({
  toast: Object.assign(jest.fn(), { success: jest.fn(), error: jest.fn() }),
}));

import OutboundQueuePage from "@/pages/dashboard/admin/OutboundQueuePage";

describe("OutboundQueuePage", () => {
  beforeEach(() => jest.clearAllMocks());

  it("warns that sending is paused and says held mail is not discarded", async () => {
    render(<OutboundQueuePage />);
    // The badge exists on first paint showing the optimistic default, so wait for the
    // loaded settings rather than asserting on whatever rendered first.
    await waitFor(() =>
      expect(screen.getByTestId("queue-state-badge")).toHaveTextContent(/paused/i));
    // The distinction an operator needs: held is recoverable, dropped is not.
    expect(screen.getByText(/held, not discarded/i)).toBeInTheDocument();
    expect(screen.getByText(/48-hour window/i)).toBeInTheDocument();
  });

  it("shows which gate is holding a message, not a bare 'pending'", async () => {
    render(<OutboundQueuePage />);
    expect(await screen.findByText(/queue disabled for this building/i)).toBeInTheDocument();
  });

  it("offers drop and send-now only while a message is still held", async () => {
    render(<OutboundQueuePage />);
    expect(await screen.findByTestId("cancel-m-1")).toBeInTheDocument();
    expect(screen.getByTestId("release-m-1")).toBeInTheDocument();
  });

  it("renders the help panel from the backend parser's own vocabulary", async () => {
    render(<OutboundQueuePage />);
    await screen.findByTestId("queue-search");
    await userEvent.click(screen.getByTestId("search-help-toggle"));
    const panel = await screen.findByTestId("search-help-panel");
    // Sourced from search_help so the documented syntax cannot drift from the parser.
    expect(panel).toHaveTextContent("status:held");
    expect(panel).toHaveTextContent("only held messages");
  });

  it("dropping a message posts a cancel for that id", async () => {
    render(<OutboundQueuePage />);
    await userEvent.click(await screen.findByTestId("cancel-m-1"));
    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith("/outbound-messages/m-1/cancel", expect.any(Object)));
  });

  it("never prefixes calls with /api — the axios instance already has it", async () => {
    render(<OutboundQueuePage />);
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    for (const call of api.get.mock.calls) {
      expect(String(call[0]).startsWith("/api/")).toBe(false);
    }
  });
});
