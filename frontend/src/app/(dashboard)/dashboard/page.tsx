"use client";

// @featuretrace:levy — Dashboard shell: centralizes the owner's finance data-fetch consumed by
//                      OwnerDashboard.tsx (presentational only).
// Layer: frontend
// Data flow: this page -> GET /finance/unit-dashboard-overview/{unit}?year=, GET /finance/building-overview
//            -> finance.py -> unit_levy_ledger + annual_levies (building-scoped).
// Related: frontend/src/app/(dashboard)/dashboard/OwnerDashboard.tsx (presentational consumer)
//           backend/routers/finance.py
// Collection: unit_levy_ledger, annual_levies
import React, {useCallback, useEffect, useRef, useState} from "react";
import {useAuth} from "@/contexts/AuthContext";
import {useActiveUnit} from "@/hooks/useActiveUnit";
import {AnimatePresence, motion} from "framer-motion";
import {Skeleton} from "@/components/ui/skeleton";
import {toast} from "sonner";
import {OwnerDashboard} from "./OwnerDashboard";
import {TenantDashboard} from "./TenantDashboard";
import {ManagementDashboard} from "./ManagementDashboard";
import YearSelector from "@/components/widgets/YearSelector";
import MorningCard from "@/components/dashboard/MorningCard";
import {fundDisplayBalance} from "@/lib/moneyNormalization";
import {History, Search, X} from "lucide-react";
import {useRouter} from "next/navigation";
import Link from "next/link";
/**
 * @generated FunctionHeader
 * Function: DashboardPage
 * Path: frontend/src/app/(dashboard)/dashboard/page.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
/**
 * Render a user's last-login addresses as "public (local)".
 *
 * Falls back to whichever single address exists, then to the pre-0094
 * `last_login_ip`. Returns "" when nothing is known so the caller can omit the
 * separator entirely rather than printing a dangling middot.
 */
function formatLoginIp(user: any): string {
    const publicIp = user?.last_login_public_ip;
    const localIp = user?.last_login_local_ip;
    if (publicIp && localIp && publicIp !== localIp) return `${publicIp} (${localIp})`;
    return publicIp || localIp || user?.last_login_ip || "";
}

export default function DashboardPage() {
    const {
        user,
        isAdmin,
        isManager,
        isECMember,
        api,
        selectedYear,
        selectedBuilding,
    } = useAuth();
    const router = useRouter();
    // `loading` gates the first-paint skeleton and is released as soon as the hero
    // wave lands. `refreshing` stays true until the WHOLE fan-out has landed and is
    // what dims the page during a re-fetch — keeping them separate matters on a year
    // switch: the hero cards update first, and the secondary charts below still hold
    // the PREVIOUS year's figures until their wave resolves. Those must stay dimmed
    // and non-interactive until they are actually for the selected year.
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(true);
    const [data, setData] = useState<any>(null);
    const [searchQuery, setSearchQuery] = useState("");
    const [searchFocused, setSearchFocused] = useState(false);
    const searchRef = useRef<HTMLInputElement>(null);
    // Multi-unit/co-owner finance must follow the currently selected unit context.
    // Do not pin owner finance to user.unit_number because co-owner and imported
    // accounts can carry display values such as "87" while the canonical ledger
    // row is stored as "TH087" and the UnitSwitcher may already hold the fixed unit.
    const {activeUnit: activeUnitNumber} = useActiveUnit();
    /**
     * @generated FunctionHeader
     * Function: handleSearchSubmit
     * Path: frontend/src/app/(dashboard)/dashboard/page.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSearchSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        const q = searchQuery.trim();
        if (!q) return;
        // Route to arrears page for unit/owner search; workflow for request IDs
        if (/^\d+$/.test(q)) {
            router.push(`/intelligence/debt-recovery?search=${encodeURIComponent(q)}`);
        } else {
            router.push(`/requests/new?search=${encodeURIComponent(q)}`);
        }
        setSearchQuery("");
    };

    // Dashboard fan-out.
    //
    // PERF (2026-08-24): all 19 calls already ran in parallel, but a single
    // `setData(...)` at the end meant first paint waited on the SLOWEST of them —
    // the page sat on a skeleton for the full tail even though the hero cards'
    // data had arrived early. The fan-out is now split into two waves that start
    // together: the hero wave releases the skeleton, and the rest merge into
    // `data` as they land. Every child reads `data` through optional chaining,
    // so a partially-populated object renders correctly.
    const fetchDashboardData = useCallback(async () => {
        if (!selectedYear) return;
        setLoading(true);
        setRefreshing(true);

        const finYearParam = `?financial_year=${selectedYear}`;
        const unitParam = activeUnitNumber ? `?unit_number=${encodeURIComponent(activeUnitNumber)}` : "";

        // Only management roles can call admin/stats (owner/tenant get 403). 'chairman' is
        // not a top-level role (see rules/post-compact-critical.md) — a chairman is a user
        // with role='ec_member', already covered below.
        const managementRoles = ['super_admin', 'strata_manager', 'strata_admin', 'ec_member'];
        const canAccessAdminStats = managementRoles.includes(user?.role || '');

        // One failed panel must never blank the rest of the dashboard, so every call
        // resolves to a fallback instead of rejecting — the same effect the previous
        // per-call `.catch(() => ({data: ...}))` / Promise.allSettled pair had.
        // `warn` re-instates the console warning the old owner-overview branch had;
        // silent failure is fine for a decorative panel, not for the owner's own money.
        const get = (url: string, fallback: any = null, warn?: string) =>
            api.get(url).then((r: any) => r.data).catch((err: any) => {
                if (warn) console.warn(warn, err);
                return fallback;
            });

        // ---- Hero wave: the header cards and notice bar. ----
        // building-overview is fetched for ALL users so the notice bar always shows
        // building-wide Fund Health and Levies Paid, not per-unit. It already carries
        // the aggregate arrears/count used by the header cards, so first render never
        // blocks on the full /arrears/detail board.
        const heroPromise = Promise.all([
            get(`/finance/building-overview?year=${selectedYear}`),
            get("/finance/portal-bank-balances"),
            get("/workflow-requests/stats/triage", {}),
            activeUnitNumber
                ? get(`/finance/unit-dashboard-overview/${activeUnitNumber}?year=${selectedYear}`,
                      null, "Failed to fetch owner dashboard overview")
                : Promise.resolve(null),
            // Canonical total ownership cost source. The UI must not derive
            // rates/water/council amounts from dashboard extras.
            activeUnitNumber
                ? get(`/owner-hub/unit-tco?unit_number=${encodeURIComponent(activeUnitNumber)}&year=${selectedYear}`)
                : Promise.resolve(null),
        ]);

        // ---- Secondary wave: charts, feeds and intelligence panels. ----
        const secondaryPromise = Promise.all([
            get("/analytics/compliance-summary", {}),
            get(`/analytics/levy-benchmarks${finYearParam}`, []),
            get(`/analytics/activities?limit=10&offset=0`, []),
            get(`/analytics/levy-allocation-breakdown?year=${selectedYear}`),
            get("/analytics/sinking-fund-forecast?years=10"),
            canAccessAdminStats ? get("/analytics/maintenance/spend-trend", []) : Promise.resolve([]),
            canAccessAdminStats ? get("/intelligence/levy-fairness") : Promise.resolve(null),
            get("/intelligence/capital-shock"),
            get(`/analytics/dashboard-v2-extras${unitParam}`),
            get("/meetings?status=scheduled&limit=1", []),
            get("/analytics/market-snapshot"),
            // Dashboard v2: payment streak + entitlement_pct (PG-first, Mongo fallback)
            activeUnitNumber
                ? get(`/analytics/my-streak?unit_number=${encodeURIComponent(activeUnitNumber)}`)
                : Promise.resolve(null),
            // Dashboard v2: owner's requests (single fetch bounded to max page size 100).
            // Serves both the top 5 preview and open request count to avoid duplicate fetches.
            activeUnitNumber ? get('/workflow-requests?limit=100', []) : Promise.resolve([]),
        ]);

        try {
            const [overview, portalBank, triageStats, ownerOverviewData, unitTcoData] = await heroPromise;

            const ov = overview ?? {};
            setData((prev: any) => ({
                ...prev,
                stats: {
                    total_arrears: ov.total_outstanding ?? 0,
                    units_in_arrears: ov.units_in_arrears ?? 0,
                    // Cash Position uses the same portal bank-balance snapshot shown on
                    // /financials/overview; building-overview remains the fallback when the
                    // scraper snapshot is unavailable.
                    admin_fund_live_balance: portalBank?.totals?.admin_balance ?? fundDisplayBalance(ov.admin_fund),
                    sinking_fund_live_balance: portalBank?.totals?.sinking_balance ?? fundDisplayBalance(ov.sinking_fund),
                    // The DUE-DATE collection rate (metric 1), not levies_paid_pct.
                    // levies_paid_pct is full-year coverage — (levied - outstanding) /
                    // levied — and the dashboard captions this value "collection", which
                    // CLAUDE.md forbids coverage from carrying. They agree at East Gate
                    // to within 0.03pp, which is why it went unnoticed; they diverge as
                    // soon as a building pays meaningfully ahead.
                    //
                    // Falls back to coverage rather than to 0: a slightly wrong rate
                    // beats a fabricated "0% collected" on a building that is paying.
                    collection_rate: ov.due_date_collection_rate_pct ?? ov.levies_paid_pct ?? null,
                    pending_users: triageStats?.pending_registrations ?? 0,
                    total_lots: triageStats?.total_lots,
                },
                maintenance: {
                    open_requests: triageStats?.pending_total ?? triageStats?.open_total ?? 0,
                    sla_breaches: triageStats?.sla_breaches ?? 0,
                },
                selectedYear,
                building_id: selectedBuilding?.id,
                active_unit_number: activeUnitNumber,
                building_overview: overview,
                portal_bank_balances: portalBank,
                owner_overview: ownerOverviewData,
                unit_tco: unitTcoData,
            }));
            // Hero data is in — drop the skeleton. `refreshing` stays true so the
            // secondary charts remain dimmed until they are for the selected year.
            setLoading(false);

            const [
                compliance, benchmarks, activitiesData, levyAllocation, sinkingFundForecast,
                maintenanceSpendTrend, levyFairness, capitalShock, dashboardV2Extras,
                nextMeetingData, marketSnapshot, streakData, allRequests,
            ] = await secondaryPromise;

            const activityFeed = Array.isArray(activitiesData) ? activitiesData : (activitiesData?.items || []);
            const meetings = Array.isArray(nextMeetingData) ? nextMeetingData : (nextMeetingData?.items || []);
            const future = meetings.find((m: any) => {
                const d = m.meeting_date || m.date;
                return d && new Date(d) > new Date();
            });

            const requestsList = Array.isArray(allRequests) ? allRequests : [];

            setData((prev: any) => ({
                ...prev,
                compliance,
                benchmarks,
                activities: activityFeed,
                nextMeeting: future ? {
                    title: future.title || future.meeting_type || 'Owners meeting',
                    date: (future.meeting_date || future.date || '').slice(0, 10),
                    meeting_type: future.meeting_type,
                } : null,
                // Dashboard v2 data
                levy_allocation: levyAllocation,
                streak_data: streakData,
                sinking_fund_forecast: sinkingFundForecast,
                maintenance_spend_trend: maintenanceSpendTrend,
                levy_fairness: levyFairness,
                capital_shock: capitalShock,
                dashboard_v2_extras: dashboardV2Extras,
                market_snapshot: marketSnapshot,
                // Real open requests for the current owner — endpoint already scopes to user.
                owner_workflow_requests: requestsList.slice(0, 5),
                owner_workflow_open_count: requestsList.filter((r: any) => !['completed', 'closed', 'cancelled'].includes(r?.status)).length,
            }));
        } catch (error) {
            console.error("Failed to fetch dashboard data:", error);
            toast.error("Some dashboard data could not be loaded");
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [activeUnitNumber, api, user?.role, selectedYear, selectedBuilding?.id]);

    useEffect(() => {
        if (user) {
            fetchDashboardData();
        }
    }, [user, fetchDashboardData]);

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

    const isTenant = user?.role === 'tenant';
    const showManagement = isAdmin() || isManager() || isECMember();

    return (
        <div className="pb-20">
            <MorningCard/>
            <header className="flex flex-col md:flex-row justify-between items-end gap-6 mb-10">
                <div>
                    <motion.h1
                        initial={{opacity: 0, x: -20}}
                        animate={{opacity: 1, x: 0}}
                        className="text-4xl font-black text-slate-900 tracking-tight"
                    >
                        Building Intelligence <span className="text-indigo-600">Hub</span>
                    </motion.h1>
                    <motion.div
                        initial={{opacity: 0, x: -20}}
                        animate={{opacity: 1, x: 0}}
                        transition={{delay: 0.1}}
                    >
                        <p className="text-slate-500 font-medium mt-2">
                            Welcome
                            back, {user?.full_name?.split(' ')[0]}. {showManagement ? "Management Mode Active." : "Here is your property pulse."}
                        </p>
                        {(user as any)?.last_login_at && (
                            <p className="text-xs text-slate-400 mt-0.5">
                                Last login:{" "}
                                {new Date((user as any).last_login_at).toLocaleString("en-AU", {
                                    day: "2-digit",
                                    month: "short",
                                    year: "numeric",
                                    hour: "2-digit",
                                    minute: "2-digit",
                                })}
                                {/* "public (local)" — one conflated address could not
                                    distinguish a missing proxy header from a genuinely
                                    local caller, and the dashboard showed the internal
                                    one. See backend/utils/client_ip.py (migration 0094). */}
                                {formatLoginIp(user as any) && ` · ${formatLoginIp(user as any)}`}
                            </p>
                        )}
                    </motion.div>
                </div>

                <motion.div
                    initial={{opacity: 0, scale: 0.9}}
                    animate={{opacity: 1, scale: 1}}
                    transition={{delay: 0.2}}
                    className="flex items-center gap-3 bg-white/40 backdrop-blur-md border border-white/20 p-2 rounded-[1.5rem] shadow-sm"
                >
                    {showManagement && (
                        <form
                            onSubmit={handleSearchSubmit}
                            role="search"
                            aria-label="Search units, owners or requests"
                            className={`flex items-center gap-2 rounded-xl transition-all duration-200 px-3 py-2 ${
                                searchFocused
                                    ? "bg-white ring-2 ring-indigo-400 shadow-sm w-52"
                                    : "bg-white/60 ring-1 ring-slate-200/80 w-40"
                            }`}
                        >
                            <Search size={14} className="text-slate-400 shrink-0" aria-hidden="true"/>
                            <input
                                ref={searchRef}
                                type="search"
                                value={searchQuery}
                                onChange={e => setSearchQuery(e.target.value)}
                                onFocus={() => setSearchFocused(true)}
                                onBlur={() => setSearchFocused(false)}
                                placeholder="Unit, owner, request…"
                                aria-label="Search dashboard"
                                className="flex-1 bg-transparent text-xs font-semibold text-slate-700 placeholder:text-slate-400 outline-none min-w-0"
                            />
                            {searchQuery && (
                                <button
                                    type="button"
                                    onClick={() => { setSearchQuery(""); searchRef.current?.focus(); }}
                                    aria-label="Clear search"
                                    className="text-slate-400 hover:text-slate-700 shrink-0"
                                >
                                    <X size={12}/>
                                </button>
                            )}
                        </form>
                    )}
                    {showManagement && (
                        <Link
                            href="/management/classic"
                            className="flex items-center gap-1.5 px-3 py-2 bg-white/80 border border-slate-200 text-slate-500 hover:text-slate-900 hover:border-slate-300 rounded-xl text-[10px] font-black uppercase tracking-widest transition-colors"
                            title="Switch to classic management layout"
                            aria-label="Switch to classic management layout"
                        >
                            <History size={11} aria-hidden="true"/>
                            Classic View
                        </Link>
                    )}
                    {!showManagement && !isTenant && (
                        <Link
                            href="/owner-hub/classic"
                            className="flex items-center gap-1.5 px-3 py-2 bg-white/80 border border-slate-200 text-slate-500 hover:text-slate-900 hover:border-slate-300 rounded-xl text-[10px] font-black uppercase tracking-widest transition-colors"
                            title="Switch to classic owner layout"
                            aria-label="Switch to classic owner layout"
                        >
                            <History size={11} aria-hidden="true"/>
                            Classic View
                        </Link>
                    )}
                    <div
                        className="hidden sm:flex px-4 py-2 bg-indigo-600 text-white rounded-xl text-[10px] font-black uppercase tracking-widest shadow-lg shadow-indigo-200">
                        Live View
                    </div>
                    {refreshing && data && (
                        <span className="hidden sm:flex items-center gap-1.5 text-[10px] font-black text-slate-400 uppercase tracking-widest" aria-live="polite" aria-label="Refreshing dashboard data">
                            <span className="w-1.5 h-1.5 rounded-full bg-indigo-400 animate-pulse" aria-hidden="true"/>
                            Updating…
                        </span>
                    )}
                    <YearSelector/>
                </motion.div>
            </header>

            <AnimatePresence mode="wait">
                <motion.div
                    key={user?.role}
                    initial={{opacity: 0, y: 10}}
                    animate={{opacity: 1, y: 0}}
                    exit={{opacity: 0, y: -10}}
                    transition={{duration: 0.4}}
                    className={`transition-opacity duration-300 ${refreshing && data ? 'opacity-60 pointer-events-none' : 'opacity-100'}`}
                >
                    {showManagement ? (
                        <ManagementDashboard data={data} selectedYear={selectedYear || undefined}/>
                    ) : isTenant ? (
                        <TenantDashboard data={data} selectedYear={selectedYear || undefined}/>
                    ) : (
                        <OwnerDashboard data={data} selectedYear={selectedYear || undefined}/>
                    )}
                </motion.div>
            </AnimatePresence>
        </div>
    );
}
