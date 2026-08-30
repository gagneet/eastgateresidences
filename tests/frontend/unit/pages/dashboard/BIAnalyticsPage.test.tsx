import React from "react";
import {render, screen, waitFor} from "@testing-library/react";

import BIAnalyticsPage from "@/pages/dashboard/BIAnalyticsPage";
import {useAuth} from "@/contexts/AuthContext";

jest.mock("@/contexts/AuthContext", () => ({
    useAuth: jest.fn(),
}));

const mockPush = jest.fn();
const mockReplace = jest.fn();
jest.mock("next/navigation", () => ({
    useRouter: () => ({
        push: mockPush,
        replace: mockReplace,
    }),
}));

const mockUseAuth = useAuth as jest.Mock;

// Mock the bi/access response — gating flag for toggleOn
const biAccessResponse = {
    enabled: true,
    scope: "building",
    source: "postgres_bi",
    pg_primary: false,
    reason: "enabled",
    warnings: [],
};

// Responses use the shape: api.get().then(r => r.data?.data), so mock wraps in { data: { data: {...} } }
const apiGet = jest.fn((url: string) => {
    if (url.includes("/bi/access")) {
        return Promise.resolve({data: biAccessResponse});
    }
    if (url.includes("/financial-summary")) {
        return Promise.resolve({
            data: {
                data: {
                    levy_collection_rate: 0.85,
                    total_paid: 180000,
                    total_levied: 212000,
                    total_arrears: 12000,
                    lots_in_arrears: 3,
                    admin_fund_balance: 120000,
                    sinking_fund_balance: 210000,
                },
            },
        });
    }
    if (url.includes("/alerts")) {
        // alerts uses r.data (not r.data.data)
        return Promise.resolve({data: {alert_count: 0}});
    }
    if (url.includes("/sinking-fund-projection")) {
        return Promise.resolve({data: {data: {projection: [], shortfall_risk: false}}});
    }
    if (url.includes("/maintenance-costs")) {
        return Promise.resolve({
            data: {
                data: {
                    by_category: [],
                    total_work_orders: 0,
                    total_cost: 0,
                    open_work_orders: 0,
                    sla_breaches: 0,
                },
            },
        });
    }
    // Generic empty list for all other /bi/building/* endpoints
    return Promise.resolve({data: {data: []}});
});

describe("BIAnalyticsPage", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockUseAuth.mockReturnValue({
            api: {get: apiGet},
            availableYears: ["2025", "2026"],
            financialYearStartMonth: 7,
            hasPermission: (permission: string) => permission === "can_view_finances",
            hasFeatureAccess: (_key: string) => true,
            loading: false,
            // Real shape from GET /buildings/me: `id` is the Postgres scheme UUID,
            // `building_id` is the plan number the JWT claim and capability scope use.
            selectedBuilding: {id: "d565e4fa-bd11-4fb1-a213-82100ddc78ff", building_id: "13195", name: "East Gate Residences"},
            selectedYear: "2026",
            setSelectedYear: jest.fn(),
            user: {role: "super_admin"},
        });
    });

    it("renders the BI Analytics workspace with panel titles", async () => {
        render(<BIAnalyticsPage/>);

        // Main heading
        await waitFor(() => {
            expect(screen.getByText("BI Analytics")).toBeTruthy();
        });

        // The access check is made immediately
        await waitFor(() => {
            expect(apiGet).toHaveBeenCalledWith(expect.stringContaining("/bi/access?building_id=13195"));
        });

        // Data panels appear
        await waitFor(() => {
            expect(apiGet).toHaveBeenCalledWith(expect.stringContaining("/bi/building/13195/financial-summary"));
            expect(apiGet).toHaveBeenCalledWith(expect.stringContaining("/bi/building/13195/levy-collection-trend"));
        });

        // Key panel titles should render
        await waitFor(() => {
            expect(screen.getByText("Levy Collection Trend")).toBeTruthy();
            expect(screen.getByText("Sinking Fund Runway")).toBeTruthy();
        });
    });

    it("does not render page content for users without finance access", async () => {
        mockUseAuth.mockReturnValue({
            api: {get: apiGet},
            hasPermission: () => false,
            hasFeatureAccess: () => false,
            loading: false,
            // No selectedBuilding → bid = "" → all useBI fetchers get null → no API calls
            selectedBuilding: null,
            selectedYear: "2026",
            user: {role: "owner"},
        });

        render(<BIAnalyticsPage/>);

        // Page content must not appear
        expect(screen.queryByText("BI Analytics")).toBeNull();
        // With no selectedBuilding, bid is empty → useBI fetchers are null → no API calls made
        expect(apiGet).not.toHaveBeenCalled();
    });
});
