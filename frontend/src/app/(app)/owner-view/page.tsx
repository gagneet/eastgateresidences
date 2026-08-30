// @ts-nocheck
// @featuretrace:dashboard-v2 — Regular dashboard route for management users who also own a unit.
// Layer: frontend
// Data flow: /owner-view → AuthContext.user/owned_units → dashboard OwnerDashboard or DashboardPage (building-scoped)
// Related: frontend/src/app/(dashboard)/dashboard/page.tsx
//          frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx
//          backend/routers/auth.py
"use client";
import React, {useCallback, useEffect, useState} from "react";
import {useAuth} from "@/contexts/AuthContext";
import {useActiveUnit} from "@/hooks/useActiveUnit";
import {motion} from "framer-motion";
import {Skeleton} from "@/components/ui/skeleton";
import {toast} from "sonner";
import {useRouter} from "next/navigation";
import {ArrowLeft, History, LayoutDashboard} from "lucide-react";
import {OwnerDashboard} from "@/app/(dashboard)/dashboard/OwnerDashboard";
import DashboardPage from "@/app/(dashboard)/dashboard/page";
import YearSelector from "@/components/widgets/YearSelector";
import {fundDisplayBalance} from "@/lib/moneyNormalization";

export const dynamic = "force-dynamic";
/**
 * Regular Dashboard — personal owner view for Chairman/EC Member roles.
 * Shows the Owner dashboard for management users who also own a unit.
 * Falls back to the main DashboardPage for non-owner management users.
 */
export default function RegularDashboardPage() {
    const {user, isManager, isECMember, isAdmin, api, selectedYear, selectedBuilding} = useAuth();
    const router = useRouter();
    const [loading, setLoading] = useState(true);
    const [data, setData] = useState<any>(null);

    const isManagementRole = isAdmin() || isManager() || isECMember();
    // Multi-unit/co-owner finance must follow the currently selected unit context
    // (UnitSwitcher) before falling back to the profile unit.
    const {activeUnit: activeUnitNumber} = useActiveUnit();
    const hasUnit = !!activeUnitNumber;

    const fetchData = useCallback(async () => {
        if (!user || !selectedYear) {
            setLoading(false);
            return;
        }
        setLoading(true);
        try {
            const finYearParam = `?financial_year=${selectedYear}`;
            const unitParam = activeUnitNumber ? `?unit_number=${encodeURIComponent(activeUnitNumber)}` : "";

            // EC/chairman owners get sinking-fund + capital-shock so BuildingStrengthCard works
            const [triageStatsRes, complianceRes, benchmarksRes, activitiesRes, buildingOverviewRes,
                levyAllocationRes, sinkingFundForecastRes, capitalShockRes, dashboardV2ExtrasRes,
                nextMeetingRes, marketSnapshotRes] = await Promise.all([
                api.get("/workflow-requests/stats/triage").catch(() => ({data: {}})),
                api.get("/analytics/compliance-summary").catch(() => ({data: {}})),
                api.get(`/analytics/levy-benchmarks${finYearParam}`).catch(() => ({data: []})),
                api.get(`/analytics/activities?limit=10&offset=0`).catch(() => ({data: []})),
                // Building-wide fund overview for notice bar — all authenticated users
                api.get(`/finance/building-overview?year=${selectedYear}`).catch(() => ({data: null})),
                // Dashboard v2: levy allocation percentages (owner card derives owner-dollar amounts)
                api.get(`/analytics/levy-allocation-breakdown?year=${selectedYear}`).catch(() => ({data: null})),
                // Dashboard v2: sinking fund forecast for BuildingStrengthCard
                api.get("/analytics/sinking-fund-forecast?years=10").catch(() => ({data: null})),
                // Dashboard v2: capital shock for ThisWeekActions
                api.get("/intelligence/capital-shock").catch(() => ({data: null})),
                // Dashboard v2 extras: emergency contacts, market signals, cost categories,
                // badges, insurance. Seeded by backend/seeds/demo_customer.py for demo buildings.
                api.get(`/analytics/dashboard-v2-extras${unitParam}`).catch(() => ({data: null})),
                // Next scheduled meeting — drives ThisWeekActions meeting chip
                api.get("/meetings?status=scheduled&limit=1").catch(() => ({data: []})),
                // Market snapshot — suburb-level data; supplements market_signals when unit
                // estimate is not seeded. Backend derives suburb from building_id context.
                api.get("/analytics/market-snapshot").catch(() => ({data: null})),
            ]);

            let ownerOverviewData = null;
            let unitTcoData = null;
            let streakData = null;
            let ownerWorkflowRequests: any[] = [];
            if (activeUnitNumber) {
                const [ownerOverviewResult, unitTcoResult, streakResult, workflowResult] = await Promise.allSettled([
                    api.get(`/finance/unit-dashboard-overview/${activeUnitNumber}?year=${selectedYear}`),
                    // Canonical total ownership cost source. Keep this aligned
                    // with /dashboard so EC/manager owners see the same values.
                    api.get(`/owner-hub/unit-tco?unit_number=${encodeURIComponent(activeUnitNumber)}&year=${selectedYear}`),
                    api.get(`/analytics/my-streak?unit_number=${encodeURIComponent(activeUnitNumber)}`),
                    // Live open requests — endpoint scopes to current user for non-managers
                    api.get('/workflow-requests?limit=5'),
                ]);
                if (ownerOverviewResult.status === "fulfilled") ownerOverviewData = ownerOverviewResult.value.data;
                else console.warn("Failed to fetch owner dashboard overview", ownerOverviewResult.reason);
                if (unitTcoResult.status === "fulfilled") unitTcoData = (unitTcoResult as any).value.data;
                if (streakResult.status === "fulfilled") streakData = (streakResult as any).value.data;
                // streak failure is silent — card shows zeros gracefully
                if (workflowResult.status === "fulfilled") {
                    const wr = (workflowResult as any).value.data;
                    ownerWorkflowRequests = Array.isArray(wr) ? wr : [];
                }
            }

            const activityFeed = Array.isArray(activitiesRes.data) ? activitiesRes.data : (activitiesRes.data?.items || []);

            const overview = buildingOverviewRes.data ?? {};
            const stats = {
                total_arrears: overview.total_outstanding ?? 0,
                units_in_arrears: overview.units_in_arrears ?? 0,
                // Match /dashboard: display the consolidated fund balance helper
                // instead of treating YTD levy collection as a live balance.
                admin_fund_live_balance: fundDisplayBalance(overview.admin_fund),
                sinking_fund_live_balance: fundDisplayBalance(overview.sinking_fund),
                collection_rate: overview.levies_paid_pct ?? 0,
                pending_users: 0,
                total_lots: triageStatsRes.data?.total_lots,
            };

            const meetingsList = Array.isArray(nextMeetingRes.data)
                ? nextMeetingRes.data
                : (nextMeetingRes.data?.meetings ?? []);
            const nextMeeting = meetingsList.length > 0 ? meetingsList[0] : null;

            setData({
                stats,
                maintenance: {
                    open_requests: triageStatsRes.data?.pending_total ?? triageStatsRes.data?.open_total ?? 0,
                    sla_breaches: triageStatsRes.data?.sla_breaches ?? 0,
                },
                compliance: complianceRes.data,
                benchmarks: benchmarksRes.data,
                activities: activityFeed,
                owner_overview: ownerOverviewData,
                selectedYear,
                nextMeeting,
                building_id: selectedBuilding?.id,
                building_overview: buildingOverviewRes.data,
                unit_tco: unitTcoData,
                // Dashboard v2 data
                levy_allocation: levyAllocationRes.data,
                streak_data: streakData,
                sinking_fund_forecast: sinkingFundForecastRes.data,
                capital_shock: capitalShockRes.data,
                dashboard_v2_extras: dashboardV2ExtrasRes.data,
                market_snapshot: marketSnapshotRes.data,
                owner_workflow_requests: ownerWorkflowRequests,
            });
        } catch (error) {
            console.error("Failed to fetch dashboard data:", error);
            toast.error("Some dashboard data could not be loaded");
        } finally {
            setLoading(false);
        }
    }, [api, user, selectedYear, selectedBuilding?.id, activeUnitNumber]);

    useEffect(() => {
        if (user) fetchData();
    }, [user, fetchData]);

    // Show skeleton while loading or user not yet hydrated
    if (loading && !data) {
        return (
            <div className="space-y-8 animate-in fade-in duration-500">
                <div className="h-12 w-64 bg-slate-200 rounded-lg animate-pulse"/>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
                    {[...Array(4)].map((_, i) => (
                        <Skeleton key={i} className="h-48 rounded-3xl"/>
                    ))}
                </div>
                <div className="h-96 w-full bg-slate-100 rounded-[2rem] animate-pulse"/>
            </div>
        );
    }

    // For management roles with a unit: show their personal Owner dashboard
    if (isManagementRole && hasUnit) {
        return (
            <div className="pb-20">
                <header className="flex flex-col md:flex-row justify-between items-end gap-6 mb-10">
                    <div>
                        <button
                            onClick={() => router.push("/dashboard")}
                            className="flex items-center gap-2 text-slate-500 hover:text-slate-900 text-sm font-bold mb-3 transition-colors group"
                        >
                            <ArrowLeft className="w-4 h-4 group-hover:-translate-x-1 transition-transform"/>
                            Back to Management View
                        </button>
                        <motion.h1
                            initial={{opacity: 0, x: -20}}
                            animate={{opacity: 1, x: 0}}
                            className="text-4xl font-black text-slate-900 tracking-tight"
                        >
                            Building Intelligence <span className="text-indigo-600">Hub</span>
                        </motion.h1>
                        <motion.p
                            initial={{opacity: 0, x: -20}}
                            animate={{opacity: 1, x: 0}}
                            transition={{delay: 0.1}}
                            className="text-slate-500 font-medium mt-2"
                        >
                            Welcome back, {user?.full_name?.split(" ")[0]}. Here is your property pulse.
                        </motion.p>
                    </div>
                    <motion.div
                        initial={{opacity: 0, scale: 0.9}}
                        animate={{opacity: 1, scale: 1}}
                        transition={{delay: 0.2}}
                        className="flex flex-wrap items-center gap-3 bg-white/40 backdrop-blur-md border border-white/20 p-2 rounded-[1.5rem] shadow-sm"
                    >
                        <button
                            onClick={() => router.push("/owner-hub/classic")}
                            className="flex items-center gap-1.5 px-3 py-2 bg-white/80 border border-slate-200 text-slate-500 hover:text-slate-900 hover:border-slate-300 rounded-xl text-[10px] font-black uppercase tracking-widest transition-colors"
                            title="Switch to classic owner layout"
                            aria-label="Switch to classic owner layout"
                        >
                            <History className="w-3 h-3" aria-hidden="true"/>
                            Classic View
                        </button>
                        <button
                            onClick={() => router.push("/dashboard")}
                            className="hidden sm:flex items-center gap-2 px-4 py-2 bg-slate-800 text-white rounded-xl text-[10px] font-black uppercase tracking-widest shadow-lg hover:bg-slate-700 transition-colors"
                        >
                            <LayoutDashboard className="w-3.5 h-3.5"/>
                            Mgmt View
                        </button>
                        <YearSelector/>
                    </motion.div>
                </header>
                <OwnerDashboard data={data} selectedYear={selectedYear || undefined}/>
            </div>
        );
    }

    // Fallback: show main dashboard page (for management roles without a unit)
    return <DashboardPage/>;
}
