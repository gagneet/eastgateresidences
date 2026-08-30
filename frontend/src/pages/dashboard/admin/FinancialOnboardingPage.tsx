// @featuretrace:demo_bank — Financial-only onboarding entry for already-created buildings (building-scoped).
// @featuretrace:financial-onboarding — Select an existing building and continue historical finance setup (building-scoped).
// Layer: frontend
// Data flow: FinancialOnboardingPage → GET /buildings/me → POST /auth/switch-building →
//            HistoricalReconstructionPage → /financials/onboarding/building/{id}/reconstruction-batches/*
//            → demo_bank_transactions → /bank-feeds sync/matching/GL posting path.
// Related: frontend/src/pages/dashboard/financial/HistoricalReconstructionPage.tsx
//           backend/routers/financial_onboarding.py

"use client";

import React, {useEffect, useMemo, useState} from "react";
import {Building2, Loader2, RefreshCw, ShieldCheck} from "lucide-react";
import {toast} from "sonner";
import {useAuth} from "../../../contexts/AuthContext";
import {Badge} from "../../../components/ui/badge";
import {Button} from "../../../components/ui/button";
import {Card, CardContent, CardHeader, CardTitle} from "../../../components/ui/card";
import HistoricalReconstructionPage from "../financial/HistoricalReconstructionPage";

type BuildingOption = {
    id: string;
    building_id?: string;
    name?: string;
    tenant_name?: string;
    jurisdiction?: string;
    is_demo?: boolean;
};

const FINANCIAL_ONBOARDING_ROUTE = "/admin/financial-onboarding";

export default function FinancialOnboardingPage() {
    const {
        api,
        user,
        loading,
        selectedBuilding,
        availableBuildings,
        switchBuilding,
        hasPermission,
        hasFeatureAccess,
    } = useAuth() as any;

    const effectiveRole = user?.effective_role || user?.role || "";
    const canAccess = ["super_admin", "strata_admin", "strata_manager"].includes(effectiveRole)
        && hasPermission?.("can_manage_finances");
    const prepEnabled = !!hasFeatureAccess?.("historical_financial_reconstruction");
    const postingEnabled = !!hasFeatureAccess?.("historical_reconstruction_posting");

    const [buildings, setBuildings] = useState<BuildingOption[]>(availableBuildings || []);
    const [selectedId, setSelectedId] = useState<string>("");
    const [fetchingBuildings, setFetchingBuildings] = useState(false);
    const [switching, setSwitching] = useState(false);

    useEffect(() => {
        if (!selectedId && selectedBuilding?.id) {
            setSelectedId(selectedBuilding.id);
        }
    }, [selectedBuilding?.id, selectedId]);

    useEffect(() => {
        if (!api || !user || !canAccess) return;
        setFetchingBuildings(true);
        api.get("/buildings/me")
            .then((res: any) => {
                const items = res.data || [];
                setBuildings(items);
                if (!selectedId && selectedBuilding?.id) setSelectedId(selectedBuilding.id);
                else if (!selectedId && items.length === 1) setSelectedId(items[0].id);
            })
            .catch((err: any) => {
                toast.error(err?.response?.data?.detail || "Failed to load buildings.");
            })
            .finally(() => setFetchingBuildings(false));
    }, [api, user, canAccess]); // eslint-disable-line react-hooks/exhaustive-deps

    const selectedOption = useMemo(
        () => buildings.find((building) => building.id === selectedId || building.building_id === selectedId) || null,
        [buildings, selectedId],
    );
    const activePlanNumber = selectedBuilding?.building_id || "";
    const targetPlanNumber = selectedOption?.building_id || "";
    const contextAligned = !!targetPlanNumber && targetPlanNumber === activePlanNumber;

    async function openSelectedBuilding() {
        if (!selectedOption) {
            toast.error("Select a building first.");
            return;
        }
        if (contextAligned) return;
        setSwitching(true);
        try {
            await switchBuilding(selectedOption.id, {redirectTo: FINANCIAL_ONBOARDING_ROUTE});
        } finally {
            setSwitching(false);
        }
    }

    if (loading) {
        return <div className="p-6 text-sm text-muted-foreground">Loading...</div>;
    }

    if (!canAccess) {
        return (
            <div className="p-6">
                <Card>
                    <CardHeader>
                        <CardTitle>Financial Onboarding</CardTitle>
                    </CardHeader>
                    <CardContent className="text-sm text-destructive">
                        Access restricted to Super Admins, Strata Admins, and Strata Managers with finance management access.
                    </CardContent>
                </Card>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            <div className="p-6 pb-0">
                <Card>
                    <CardHeader className="pb-3">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                                <CardTitle className="flex items-center gap-2 text-xl">
                                    <Building2 className="h-5 w-5 text-primary"/>
                                    Financial Onboarding
                                </CardTitle>
                                <p className="mt-1 text-sm text-muted-foreground">
                                    Complete historical financial onboarding for an existing building through Demo Bank staging.
                                </p>
                            </div>
                            <div className="flex flex-wrap gap-2">
                                <Badge variant={prepEnabled ? "default" : "outline"}>
                                    Reconstruction {prepEnabled ? "enabled" : "disabled"}
                                </Badge>
                                <Badge variant={postingEnabled ? "default" : "outline"}>
                                    Demo Bank sync {postingEnabled ? "enabled" : "disabled"}
                                </Badge>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent className="grid gap-4 lg:grid-cols-[minmax(260px,1fr)_auto] lg:items-end">
                        <div className="space-y-2">
                            <label htmlFor="financial-onboarding-building" className="text-sm font-medium">
                                Building
                            </label>
                            <select
                                id="financial-onboarding-building"
                                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                                value={selectedId}
                                onChange={(event) => setSelectedId(event.target.value)}
                                disabled={fetchingBuildings || switching}
                            >
                                <option value="">Select building</option>
                                {buildings.map((building) => (
                                    <option key={building.id} value={building.id}>
                                        {building.name || building.building_id || building.id}
                                        {building.building_id ? ` (${building.building_id})` : ""}
                                        {building.tenant_name ? ` - ${building.tenant_name}` : ""}
                                    </option>
                                ))}
                            </select>
                            <p className="text-xs text-muted-foreground">
                                Active context: {selectedBuilding?.name || "None"}
                                {activePlanNumber ? ` (${activePlanNumber})` : ""}
                            </p>
                        </div>
                        <Button
                            type="button"
                            onClick={openSelectedBuilding}
                            disabled={!selectedOption || contextAligned || switching}
                            className="gap-2"
                        >
                            {switching ? <Loader2 className="h-4 w-4 animate-spin"/> : contextAligned ? <ShieldCheck className="h-4 w-4"/> : <RefreshCw className="h-4 w-4"/>}
                            {contextAligned ? "Building Active" : "Open Building Workflow"}
                        </Button>
                    </CardContent>
                </Card>
            </div>

            {contextAligned ? (
                <HistoricalReconstructionPage
                    buildingIdOverride={targetPlanNumber}
                    title="Financial Onboarding"
                    description="Stage historical financial reconstruction through Demo Bank, then sync from Demo Bank into the finance pipeline."
                />
            ) : (
                <div className="px-6">
                    <Card>
                        <CardContent className="py-6 text-sm text-muted-foreground">
                            Select a building and open its workflow to create or continue historical financial onboarding.
                        </CardContent>
                    </Card>
                </div>
            )}
        </div>
    );
}
