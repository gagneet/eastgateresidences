import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";

import PowerhouseConversationCenterPage from "@/pages/dashboard/powerhouse/PowerhouseConversationCenterPage";
import PowerhouseInboxSettingsPage from "@/pages/dashboard/powerhouse/PowerhouseInboxSettingsPage";

const mockApi = { get: jest.fn(), post: jest.fn() };
const mockAuth = {
  api: mockApi,
  loading: false,
  isManager: () => true,
  isOwner: () => false,
  isTenant: () => false,
  isGuest: () => false,
  hasFeatureAccess: (_key: string) => true,
};

jest.mock("@/contexts/AuthContext", () => ({
  useAuth: () => mockAuth,
}));

describe("Powerhouse shell pages", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockAuth.loading = false;
    mockAuth.isManager = () => true;
    mockAuth.isOwner = () => false;
    mockAuth.isTenant = () => false;
    mockAuth.isGuest = () => false;
    mockAuth.hasFeatureAccess = (_key: string) => true;
  });

  it("renders conversation center empty state", async () => {
    mockApi.get.mockResolvedValueOnce({ data: { items: [] } });
    render(<PowerhouseConversationCenterPage />);
    expect(screen.getByTestId("powerhouse-conversation-center")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("powerhouse-thread-list-empty")).toBeInTheDocument();
    });
  });

  it("renders conversation center error state", async () => {
    mockApi.get.mockRejectedValueOnce({ response: { data: { detail: "boom" } } });
    render(<PowerhouseConversationCenterPage />);
    await waitFor(() => {
      expect(screen.getByTestId("powerhouse-thread-list-error")).toHaveTextContent("boom");
    });
  });

  it("shows feature-disabled state when powerhouse_conversations toggle is off", () => {
    mockAuth.hasFeatureAccess = (key: string) => key !== "powerhouse_conversations";
    render(<PowerhouseConversationCenterPage />);
    expect(screen.getByTestId("powerhouse-center-feature-disabled")).toBeInTheDocument();
    expect(screen.queryByTestId("powerhouse-conversation-center")).not.toBeInTheDocument();
  });

  it("does not call API when powerhouse_conversations toggle is off", async () => {
    mockAuth.hasFeatureAccess = (key: string) => key !== "powerhouse_conversations";
    render(<PowerhouseConversationCenterPage />);
    expect(mockApi.get).not.toHaveBeenCalled();
  });

  it("blocks inbox settings for non-manager", () => {
    mockAuth.isManager = () => false;
    render(<PowerhouseInboxSettingsPage />);
    expect(screen.getByTestId("powerhouse-inbox-settings-forbidden")).toBeInTheDocument();
  });

  it("shows feature-disabled state for inbox settings when powerhouse_shared_inbox toggle is off", () => {
    mockAuth.hasFeatureAccess = (key: string) => key !== "powerhouse_shared_inbox";
    render(<PowerhouseInboxSettingsPage />);
    expect(screen.getByTestId("powerhouse-inbox-settings-feature-disabled")).toBeInTheDocument();
    expect(screen.queryByTestId("powerhouse-inbox-settings-page")).not.toBeInTheDocument();
  });

  it("does not call inbox API when powerhouse_shared_inbox toggle is off", () => {
    mockAuth.hasFeatureAccess = (key: string) => key !== "powerhouse_shared_inbox";
    render(<PowerhouseInboxSettingsPage />);
    expect(mockApi.get).not.toHaveBeenCalled();
  });

  it("shows inbox settings empty state for manager", async () => {
    mockApi.get.mockResolvedValueOnce({ data: [] });
    render(<PowerhouseInboxSettingsPage />);
    await waitFor(() => {
      expect(screen.getByTestId("powerhouse-inbox-settings-empty")).toBeInTheDocument();
    });
  });

  it("one building toggle off does not affect another building — conversation center visible with toggle on", async () => {
    // Building A has toggle on: full page renders
    mockAuth.hasFeatureAccess = (_key: string) => true;
    mockApi.get.mockResolvedValueOnce({ data: { items: [] } });
    const { unmount } = render(<PowerhouseConversationCenterPage />);
    expect(screen.queryByTestId("powerhouse-center-feature-disabled")).not.toBeInTheDocument();
    unmount();

    // Building B has toggle off: disabled state shown, no API call
    jest.clearAllMocks();
    mockAuth.hasFeatureAccess = (key: string) => key !== "powerhouse_conversations";
    render(<PowerhouseConversationCenterPage />);
    expect(screen.getByTestId("powerhouse-center-feature-disabled")).toBeInTheDocument();
    expect(mockApi.get).not.toHaveBeenCalled();
  });
});

