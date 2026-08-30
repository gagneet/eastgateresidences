// @featuretrace:fund-health — Owner Dashboard: displays fund health, levy status, maintenance KPIs, and activity feed.
// Layer: frontend
// Data flow: OwnerDashboard.tsx → /finance/unit-dashboard-overview/{unit}, /analytics/my-streak,
//            /analytics/levy-allocation-breakdown, /analytics/sinking-fund-forecast, /analytics/activities.
//            Finance overview routes are PostgreSQL-first with MongoDB fallback on PG read failure;
//            /analytics/my-streak is PostgreSQL-only and returns zero streak data if unavailable.
// Related: backend/routers/finance.py
//           backend/routers/analytics.py
//           frontend/src/components/dashboard/LandTaxCard.jsx  (@featuretrace:land-tax)
// Scope: (building-scoped)

// @featuretrace:dashboard-v2 — Owner Dashboard v2: "Personal Stake" layout — ring gauge, streak, levy donut, capital-for-me, community pulse.
// Layer: frontend
// Data flow: OwnerDashboard → /finance/unit-dashboard-overview/{unit} + /analytics/levy-allocation-breakdown
//            + /analytics/my-streak + /intelligence/capital-shock + /analytics/activities → building-scoped dashboard contracts.
// Related: backend/routers/analytics.py          (/analytics/levy-allocation-breakdown, /analytics/activities)
//           backend/routers/intelligence.py       (/intelligence/capital-shock)
//           frontend/src/components/dashboard/PaymentStreakCard.tsx
//           frontend/src/components/dashboard/LevyAllocationDonut.tsx
//           frontend/src/components/dashboard/CapitalForMeCard.tsx
//           frontend/src/components/dashboard/CommunityPulseFeed.tsx
//           frontend/src/pages/dashboard/ManagerDashboard.jsx  (manager counterpart)
// Toggle: ft_dashboard_v2
// Scope: (building-scoped)

// @ts-nocheck
"use client";
import React, {useEffect, useState, useCallback} from 'react';
import {useRouter} from 'next/navigation';
import {useAuth} from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import {useTaxSummary} from '../../hooks/useTaxSummary';
import {Card, CardContent, CardHeader, CardTitle, CardDescription} from '../../components/ui/card';
import {Button} from '../../components/ui/button';
import {Badge} from '../../components/ui/badge';
import PaymentModal from '../../components/payments/PaymentModal';
import YearSelector from '../../components/widgets/YearSelector';
import LevySummaryCard from '../../components/widgets/FinancialSummaryCard';
import DashboardDetailModal from '../../components/dashboard/DashboardDetailModal';
import {
    CouncilRateActionCard,
    LandTaxActionCard,
    WaterBillActionCard,
    ElectricityActionCard,
    GasActionCard,
    NBNActionCard,
} from '../../components/dashboard/PropertyServicesActionCards';
import {
    Home,
    DollarSign,
    Wrench,
    ShieldCheck,
    Calendar,
    TrendingUp,
    ChevronRight,
    ArrowUpRight,
    FileText,
    MessageSquare,
    ShoppingBag,
    Phone,
    Activity,
    Pencil,
    Tag,
    MapPin,
    Info,
    AlertCircle,
    CheckCircle2
} from 'lucide-react';
import {Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription} from '../../components/ui/dialog';
import {Input} from '../../components/ui/input';
import {toast} from 'sonner';
import {motion, AnimatePresence} from 'framer-motion';
import {formatCurrency} from '../../lib/utils';
import LevyAllocationDonut from '../../components/dashboard/LevyAllocationDonut';
import PaymentStreakCard from '../../components/dashboard/PaymentStreakCard';
import CommunityPulseFeed from '../../components/dashboard/CommunityPulseFeed';
import CapitalForMeCard from '../../components/dashboard/CapitalForMeCard';
import ThisWeekActions, {WeekAction} from '../../components/dashboard/ThisWeekActions';
import TrueCostBreakdown, {CostCategory} from '../../components/dashboard/TrueCostBreakdown';
import MarketIntelExpanded from '../../components/dashboard/MarketIntelExpanded';
import DashboardFooterCue from '../../components/dashboard/DashboardFooterCue';
import {clampedCollectionPercentage} from '../../lib/finance/financeTransforms';
// v2 backports — added to classic view without replacing existing content
import BuildingStrengthCard from '../../components/dashboard/BuildingStrengthCard';
import YourRequestsCard from '../../components/dashboard/YourRequestsCard';
import {isTerminalRequestStatus, REQUEST_QUEUE_HREF} from '../../lib/requests/requestScope';
import {PageHeader} from '../../components/shared/PageHeader';
import { normaliseReserveProjection } from '@/lib/reserve-projection';

/**
 * Enhanced Property Intelligence Dashboard for Owners
 */
const OwnerDashboard = () => {
    const {user, api, token, isManager, isECMember, selectedYear, setSelectedYear, selectedBuilding} = useAuth();
    const router = useRouter();
    const {activeUnit: activeUnitNumber} = useActiveUnit();
    const activeUnitDisplayLabel = activeUnitNumber ? `Unit ${activeUnitNumber}` : 'Unit not selected';

    const {loading: downloadingTax, downloadTaxSummary} = useTaxSummary(api);

    const [loading, setLoading] = useState(true);

    // Data States
    const [financialData, setFinancialData] = useState(null);
    const [sinkingFundForecast, setSinkingFundForecast] = useState(null);
    const [maintenanceStats, setMaintenanceStats] = useState(null);
    const [activities, setActivities] = useState([]);
    const [marketSnapshot, setMarketSnapshot] = useState(null);
    const [rawLevies, setRawLevies] = useState([]);
    const [levyTrendData, setLevyTrendData] = useState([]);
    const [upcomingAgm, setUpcomingAgm] = useState(null);
    const [complianceSummary, setComplianceSummary] = useState(null);

    // Security activity state
    const [securityActivity, setSecurityActivity] = useState(null);
    // 'idle' -> 'confirm' -> 'working' -> 'done' | 'error'. Deliberately a two-step
    // inline confirm: the action cannot be undone and ends sessions on devices the user
    // may not have to hand, so a stray click must not fire it. The confirm step is also
    // where the consequence is spelled out, which keeps the resting state quiet.
    const [signOutState, setSignOutState] = useState<'idle' | 'confirm' | 'working' | 'done' | 'error'>('idle');
    const [buildingIntelligence, setBuildingIntelligence] = useState(null);
    const [capitalShock, setCapitalShock] = useState(null);
    const [levyAllocation, setLevyAllocation] = useState(null);
    const [dashboardExtras, setDashboardExtras] = useState(null);
    const [unitTco, setUnitTco] = useState(null);
    const [nextMeeting, setNextMeeting] = useState(null);
    // Streak + entitlement — served by /analytics/my-streak (PostgreSQL-only; no Mongo fallback)
    const [streakData, setStreakData] = useState<{
        streak: number;
        total_quarters: number;
        on_time_count: number;
        on_time_pct: number;
        recent_quarters: any[];
        entitlement_pct: number;
    } | null>(null);

    // Owner's own open workflow requests — drives YourRequestsCard (v2 backport)
    const [ownerRequests, setOwnerRequests] = useState([]);

    // Important documents alert state
    const [importantDocs, setImportantDocs] = useState([]);
    const [dismissedDocs, setDismissedDocs] = useState<Set<string>>(
        () => new Set(typeof window !== 'undefined' ? JSON.parse(localStorage.getItem('dismissed_important_docs') || '[]') : [])
    );

    // UI States
    const [isPaymentModalOpen, setIsPaymentModalOpen] = useState(false);
    const [isDetailModalOpen, setIsDetailModalOpen] = useState(false);
    const [ownerInsight, setOwnerInsight] = useState<{
        title: string;
        description?: string;
        actionLabel?: string;
        actionHref?: string;
        content: React.ReactNode;
    } | null>(null);
    const [paymentData, setPaymentData] = useState(null);

    // Market valuation state
    const [marketValuePopupOpen, setMarketValuePopupOpen] = useState(false);
    const [estimatedPrice, setEstimatedPrice] = useState(null);
    const [priceLastUpdated, setPriceLastUpdated] = useState(null);
    const [priceNotes, setPriceNotes] = useState('');
    const [priceInput, setPriceInput] = useState('');
    const [notesInput, setNotesInput] = useState('');
    const [savingPrice, setSavingPrice] = useState(false);

    // Dashboard fan-out.
    //
    // PERF (2026-08-24): these 16 calls used to run as 16 sequential `await`s, so the
    // page cost the SUM of every endpoint (~1.4 s of backend time on localhost, and
    // 16x the round-trip on a real network) before showing anything but a spinner.
    // They are independent, so they now all start together and the page paints once
    // the owner's own finance cards land; the rest stream into their own cards.
    // Every derived value in the render is null-guarded, which makes that safe.
    const fetchDashboardData = useCallback(async () => {
        if (!token || !user || !selectedYear) return;
        setLoading(true);

        // Each task owns its own failure: one dead endpoint must never blank the
        // rest of the dashboard, exactly as the previous per-call try/catch did.
        const task = async (label: string, fn: () => Promise<void>, quiet = false) => {
            try {
                await fn();
            } catch (err) {
                if (!quiet) console.error(`Failed to fetch ${label}:`, err);
            }
        };

        // --- Above-the-fold wave: this owner's own money. ---
        const critical = [
            task('financial data', async () => {
                if (!activeUnitNumber) return;
                const financialRes = await api.get(
                    `/finance/unit-dashboard-overview/${activeUnitNumber}?year=${selectedYear}`
                );
                setFinancialData(financialRes.data);
            }),
            // Canonical Owner Hub TCO totals for the true-cost card.
            task('unit TCO', async () => {
                if (!activeUnitNumber) return;
                const tcoRes = await api.get(
                    `/owner-hub/unit-tco?unit_number=${encodeURIComponent(activeUnitNumber)}&year=${selectedYear}`
                );
                setUnitTco(tcoRes.data || null);
            }, true),
        ];

        // --- Secondary wave: building context, charts and feeds. ---
        const secondary = [
            task('sinking fund forecast', async () => {
                const forecastRes = await api.get('/analytics/sinking-fund-forecast?years=10');
                setSinkingFundForecast(forecastRes.data);
            }),
            task('maintenance stats', async () => {
                const maintRes = await api.get('/analytics/maintenance-stats');
                setMaintenanceStats(maintRes.data);
            }),
            task('activities', async () => {
                const activityRes = await api.get('/analytics/activities?limit=15');
                setActivities(activityRes.data || []);
            }),
            // No hardcoded suburb — backend derives it from building_id context.
            task('market snapshot', async () => {
                const marketRes = await api.get('/analytics/market-snapshot');
                setMarketSnapshot(marketRes.data);
            }),
            // Stored raw; the trend is computed separately.
            task('levy data', async () => {
                const trendRes = await api.get('/annual-levies');
                setRawLevies(trendRes.data || []);
            }),
            task('AGM', async () => {
                const agmRes = await api.get('/agm');
                const upcoming = (agmRes.data || [])
                    .filter(a => new Date(a.date) > new Date())
                    .sort((a, b) => new Date(a.date) - new Date(b.date))[0];
                setUpcomingAgm(upcoming);
            }),
            task('compliance summary', async () => {
                const compRes = await api.get('/analytics/compliance-summary');
                setComplianceSummary(compRes.data);
            }),
            // Feature may not be enabled for this building.
            task('building intelligence', async () => {
                const intRes = await api.get('/intelligence/summary');
                setBuildingIntelligence(intRes.data);
            }, true),
            task('capital shock', async () => {
                const shockRes = await api.get('/intelligence/capital-shock');
                setCapitalShock(shockRes.data);
            }, true),
            // PostgreSQL-first via /analytics/levy-allocation-breakdown.
            task('levy allocation', async () => {
                const allocRes = await api.get(`/analytics/levy-allocation-breakdown?year=${selectedYear}`);
                setLevyAllocation(allocRes.data);
            }, true),
            // /analytics/my-streak is PostgreSQL-only; card shows zeros gracefully.
            task('payment streak', async () => {
                const streakRes = await api.get(
                    `/analytics/my-streak${activeUnitNumber ? `?unit_number=${encodeURIComponent(activeUnitNumber)}` : ''}`
                );
                setStreakData(streakRes.data);
            }, true),
            // Bounded; feeds YourRequestsCard (v2 backport).
            task('owner requests', async () => {
                const reqRes = await api.get('/workflow-requests?limit=5');
                setOwnerRequests(Array.isArray(reqRes.data) ? reqRes.data : []);
            }, true),
            // Auxiliary v2 signals — not a financial source of truth.
            task('dashboard extras', async () => {
                const extrasRes = await api.get(
                    `/analytics/dashboard-v2-extras${activeUnitNumber ? `?unit_number=${encodeURIComponent(activeUnitNumber)}` : ''}`
                );
                setDashboardExtras(extrasRes.data || null);
            }, true),
            // Next scheduled meeting for the v2 weekly action list.
            task('next meeting', async () => {
                const meetingsRes = await api.get('/meetings?status=scheduled&limit=1');
                const meetings = Array.isArray(meetingsRes.data)
                    ? meetingsRes.data
                    : (meetingsRes.data?.items || []);
                setNextMeeting(meetings[0] || null);
            }, true),
            // Consolidated secondary fetches (previously in individual post-mount useEffects)
            task('security activity', async () => {
                const secRes = await api.get('/security/my-activity');
                setSecurityActivity(secRes.data);
            }, true),
            task('important docs', async () => {
                const docsRes = await api.get('/documents/important');
                setImportantDocs(docsRes.data || []);
            }, true),
            task('market valuation', async () => {
                if (!activeUnitNumber) return;
                const valRes = await api.get(`/units/${activeUnitNumber}/market-valuation`);
                if (valRes.data?.estimated_market_price) {
                    setEstimatedPrice(valRes.data.estimated_market_price);
                    setPriceLastUpdated(valRes.data.market_price_updated_at ?? null);
                    setPriceNotes(valRes.data.market_price_notes ?? '');
                }
            }, true),
        ];

        // Both waves are already in flight; only the critical one gates first paint.
        await Promise.all(critical);
        setLoading(false);
        await Promise.all(secondary);
    }, [activeUnitNumber, api, selectedYear, token, user, selectedBuilding?.id]);

    useEffect(() => {
        fetchDashboardData();
    }, [fetchDashboardData]);
    /**
     * @generated FunctionHeader
     * Function: dismissDoc
     * Path: frontend/src/pages/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const dismissDoc = (docId: string) => {
        const newSet = new Set([...dismissedDocs, docId]);
        setDismissedDocs(newSet);
        localStorage.setItem('dismissed_important_docs', JSON.stringify([...newSet]));
    };

    const openEstimateDialog = useCallback(() => {
        setPriceInput(estimatedPrice ? String(Math.round(estimatedPrice)) : '');
        setNotesInput(priceNotes);
        setMarketValuePopupOpen(true);
    }, [estimatedPrice, priceNotes]);

    const saveEstimate = useCallback(async () => {
        if (!activeUnitNumber) return;
        const price = Number(priceInput.replace(/[^0-9.]/g, ''));
        if (!price || price <= 0) return;
        setSavingPrice(true);
        try {
            const res = await api.put(`/units/${activeUnitNumber}/market-valuation`, {
                estimated_market_price: price,
                market_price_notes: notesInput,
            });
            setEstimatedPrice(res.data.estimated_market_price);
            setPriceLastUpdated(res.data.market_price_updated_at);
            setPriceNotes(res.data.market_price_notes ?? '');
            setMarketValuePopupOpen(false);
        } catch {
            /* error toast handled by interceptor */
        } finally {
            setSavingPrice(false);
        }
    }, [activeUnitNumber, api, priceInput, notesInput]);
    /**
     * @generated FunctionHeader
     * Function: formatEstimate
     * Path: frontend/src/pages/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    function formatEstimate(price) {
        if (!price) return null;
        if (price >= 1_000_000) return `$${(price / 1_000_000).toFixed(2).replace(/\.?0+$/, '')}M`;
        if (price >= 1_000) return `$${Math.round(price / 1_000)}K`;
        return `$${price.toLocaleString()}`;
    }
    /**
     * @generated FunctionHeader
     * Function: openOwnerInsight
     * Path: frontend/src/pages/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openOwnerInsight = (detail: {
        title: string;
        description?: string;
        actionLabel?: string;
        actionHref?: string;
        content: React.ReactNode;
    }) => setOwnerInsight(detail);
    /**
     * @generated FunctionHeader
     * Function: openOwnerInsightRoute
     * Path: frontend/src/pages/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openOwnerInsightRoute = () => {
        if (ownerInsight?.actionHref) {
            router.push(ownerInsight.actionHref);
            setOwnerInsight(null);
        }
    };

    // Compute levy trend from API data only — no fallback fabrication
    useEffect(() => {
        const uoe = financialData?.unit_entitlement || 0;
        // Only include years that have real data from the database
        const trend = (rawLevies || [])
            .filter(l => l.admin_levy_per_uoe_annual != null && l.sinking_levy_per_uoe_annual != null)
            .map(l => {
                return {
                    year: l.year,
                    admin: Math.round((l.admin_levy_per_uoe_annual * uoe) * 100) / 100,
                    sinking: Math.round((l.sinking_levy_per_uoe_annual * uoe) * 100) / 100,
                    total: Math.round(((l.admin_levy_per_uoe_annual + l.sinking_levy_per_uoe_annual) * uoe) * 100) / 100,
                };
            })
            .sort((a, b) => a.year.localeCompare(b.year));
        setLevyTrendData(trend);
    }, [rawLevies, financialData]);
    /**
     * @generated FunctionHeader
     * Function: handlePayNow
     * Path: frontend/src/pages/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePayNow = () => {
        const nextUnpaid = (financialData?.quarters || []).find(
            (q: any) => q.status !== 'paid' && (Number(q.outstanding ?? q.amount_due ?? 0) > 0)
        );
        // Use consolidated unit-dashboard values; legacy net_balance is no longer part of the current contract.
        const nextPayment = financialData?.next_payment_adjusted
            ?? financialData?.next_payment_amount
            ?? (nextUnpaid ? (nextUnpaid.outstanding ?? nextUnpaid.amount_due ?? 0) : 0)
            ?? financialData?.balance_owing
            ?? financialData?.total_outstanding
            ?? 0;
        if (!financialData || nextPayment <= 0) {
            toast.info('Your account is up to date!');
            return;
        }

        setPaymentData({
            unit_number: financialData.unit_number,
            amount: nextPayment,
            levy_period: 'Current Period',
            admin_fund_amount: financialData.admin_fund?.closing_balance || 0,
            sinking_fund_amount: financialData.sinking_fund?.closing_balance || 0,
            description: `Levy Payment - Unit ${financialData.unit_number}`
        });
        setIsPaymentModalOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handleDownloadTaxSummary
     * Path: frontend/src/pages/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownloadTaxSummary = async () => {
        if (!activeUnitNumber || !selectedYear) return;
        await downloadTaxSummary(activeUnitNumber, selectedYear);
    };

    const quickActions = [
        {
            label: 'Pay Levy',
            icon: DollarSign,
            href: '/financials/levy-payments',
            color: 'bg-primary/5 text-primary'
        },
        {label: 'Maintenance', icon: Wrench, href: '/maintenance', color: 'bg-primary/5 text-primary'},
        {label: 'Documents', icon: FileText, href: '/documents', color: 'bg-primary/5 text-primary'},
        {label: 'Chat', icon: MessageSquare, href: '/community/chat', color: 'bg-primary/5 text-primary'},
        {label: 'Marketplace', icon: ShoppingBag, href: '/community/marketplace', color: 'bg-primary/5 text-primary'},
        {label: 'Emergency', icon: Phone, href: '/emergency-services', color: 'bg-red-50 text-red-600'},
    ];

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[600px]">
                <div className="text-center">
                    <motion.div
                        animate={{rotate: 360}}
                        transition={{repeat: Infinity, duration: 1, ease: "linear"}}
                        className="h-12 w-12 border-4 border-primary border-t-transparent rounded-full mx-auto"
                    />
                    <p className="mt-4 text-muted-foreground font-medium">Preparing your Property Intelligence...</p>
                </div>
            </div>
        );
    }

    const today = new Date();
    // paid_this_year (backend: total_levied - net_balance), NOT total_paid -- confirmed live
    // 2026-08-01 that total_paid on this same API response is not reliably scoped to one year
    // (a real East Gate unit's own reconciliation_note: "back-solved... cumulative payment
    // history through the scrape date, not payments received within this calendar year
    // specifically"). Falls back to total_paid only if an older cached response lacks the
    // new field. See app/(dashboard)/dashboard/OwnerDashboard.tsx for the same fix on the
    // current dashboard -- both read the same GET /finance/unit-dashboard-overview endpoint.
    const totalPaid = Number(financialData?.paid_this_year ?? financialData?.total_paid ?? financialData?.yearly_forecast?.paid_so_far ?? 0);
    const totalAnnualLevy =
        Number(financialData?.admin_fund?.annual ?? 0) + Number(financialData?.sinking_fund?.annual ?? 0);
    const totalLevied = Number(financialData?.total_levied ?? financialData?.yearly_forecast?.total_levies ?? totalAnnualLevy ?? 0);
    const paidPercentage = Math.round(
        totalAnnualLevy > 0
            ? clampedCollectionPercentage(totalPaid, totalAnnualLevy)
            : clampedCollectionPercentage(totalPaid, totalLevied),
    );
    const quarters = Array.isArray(financialData?.quarters) ? financialData.quarters : [];
    const overdueQuarters = quarters.filter((q: any) =>
        q.status !== 'paid' && q.due_date && new Date(q.due_date) < today && Number(q.outstanding ?? q.amount_due ?? 0) > 0
    );
    const nextUnpaidQuarter = quarters.find((q: any) =>
        q.status !== 'paid' && (!q.due_date || new Date(q.due_date) >= today)
    );
    let nextDueDateObj: Date | null = null;
    if (financialData?.next_due_date) {
        nextDueDateObj = new Date(`${financialData.next_due_date}T00:00:00`);
    } else if (nextUnpaidQuarter?.due_date) {
        nextDueDateObj = new Date(nextUnpaidQuarter.due_date);
    }
    const nextDueDate = nextDueDateObj
        ? nextDueDateObj.toLocaleDateString('en-AU', {day: 'numeric', month: 'short', year: 'numeric'})
        : 'Not scheduled';
    const daysUntilNextDue = nextDueDateObj
        ? Math.max(0, Math.round((nextDueDateObj.getTime() - today.getTime()) / 86_400_000))
        : null;
    const nextInstalment = financialData?.next_payment_adjusted
        ?? financialData?.next_payment_amount
        ?? (nextUnpaidQuarter ? (nextUnpaidQuarter.outstanding ?? nextUnpaidQuarter.amount_due ?? 0) : 0)
        ?? 0;

    const daysToAgm = upcomingAgm ? Math.ceil((new Date(upcomingAgm.date) - new Date()) / (1000 * 60 * 60 * 24)) : null;

    // Administration Fund Health — % of admin levy paid by this unit
    // Use annual (full-year) amount as denominator, not levied-to-date, to avoid 100% after Q1 payment
    const adminFundAnnual = financialData?.admin_fund?.annual || financialData?.admin_fund?.levied || 0;
    const adminFundPaid = financialData?.admin_fund?.paid || 0;
    // Without a denominator there is no percentage to state. Returning 0 here reported
    // "0% of levies collected" for a unit whose levy figures had simply not loaded.
    const adminPaidPct = adminFundAnnual > 0
        ? Math.round(clampedCollectionPercentage(adminFundPaid, adminFundAnnual))
        : null;

    // Sinking Fund Health — derived from forecast projection (% of years with positive balance)
    const sfHealthPct = (() => {
        // Count only years whose balance is actually KNOWN. Previously an absent forecast
        // returned 0, which read as "0% of years solvent" — a failing verdict produced by
        // having no forecast at all — and an unknown balance failed the `> 0` test the
        // same way a negative one did.
        const known = normaliseReserveProjection(sinkingFundForecast?.projection)
            .map((p) => p.closing_balance)
            .filter((v): v is number => typeof v === 'number');
        if (!known.length) return null;
        return Math.round((known.filter((v) => v > 0).length / known.length) * 100);
    })();
    // --- null-aware presentation helpers -------------------------------------------
    // Every figure below can legitimately be unknown. `null` must reach the screen as
    // "—" and as a neutral colour; it must never be interpolated (`${null}%` -> "null%")
    // nor arithmetically absorbed (`(null + null) / 2` -> 0, a confident zero).
    const UNKNOWN_TONE = '#94A3B8';   // slate-400
    const pctLabel = (v: number | null) => (v == null ? '—' : `${v}%`);
    const adminHealthColor = adminPaidPct == null
        ? UNKNOWN_TONE
        : adminPaidPct >= 90 ? '#16A34A' : adminPaidPct >= 25 ? '#2563EB' : '#F59E0B';
    // Combined fund health = mean of the KNOWN components only (see buildingStrengthScore,
    // which is this same value — one derivation, not two that can drift apart).
    const fundHealthComponents = [adminPaidPct, sfHealthPct].filter(
        (v): v is number => typeof v === 'number');
    const fundHealthPct: number | null = fundHealthComponents.length
        ? Math.round(fundHealthComponents.reduce((a, b) => a + b, 0) / fundHealthComponents.length)
        : null;

    const shockRows = capitalShock?.capital_shock_index?.rows || [];
    const nextShock = capitalShock?.capital_shock_index?.next_shock;
    const rawAllocationCategories = Array.isArray(levyAllocation?.categories) ? levyAllocation.categories : [];
    const ownerAnnualTotal = totalAnnualLevy > 0 ? totalAnnualLevy : totalLevied;
    const levyAllocationTotal = Number(levyAllocation?.total_annual ?? 0);
    const allocationTotal = ownerAnnualTotal > 0 ? ownerAnnualTotal : levyAllocationTotal;
    const allocationCategories = rawAllocationCategories.map((c: any) => {
        const amount = Number(c?.amount ?? 0);
        const pctFromAmount = allocationTotal > 0 ? (amount / allocationTotal) * 100 : 0;
        const pct = Number(c?.pct ?? pctFromAmount ?? 0);
        const amountFromPct = allocationTotal > 0 ? Number(((allocationTotal * pct) / 100).toFixed(2)) : amount;
        return {...c, pct, amount: amountFromPct};
    });
    const _rawSignals = dashboardExtras?.market_signals ?? {};
    const marketSignals = {
        ..._rawSignals,
        estimate: _rawSignals.estimate ?? estimatedPrice ?? 0,
        suburb_median: _rawSignals.suburb_median ?? marketSnapshot?.median_price ?? 0,
        yoy_pct: _rawSignals.yoy_pct ?? marketSnapshot?.growth_yoy ?? null,
        rental_yield_pct: _rawSignals.rental_yield_pct ?? marketSnapshot?.rental_yield ?? null,
        days_on_market: _rawSignals.days_on_market ?? marketSnapshot?.days_on_market ?? null,
    };
    const tcoCostCategories: CostCategory[] = unitTco ? [
        {name: 'Strata levies', annual: Number(unitTco.strata_levies ?? totalAnnualLevy ?? 0), color: '#4F46E5'},
        {name: 'Council rates', annual: Number(unitTco.council_rates ?? 0), color: '#F59E0B'},
        {name: 'Land tax', annual: Number(unitTco.land_tax ?? 0), color: '#E11D48'},
        {name: 'Water charges', annual: Number(unitTco.water_charges ?? 0), color: '#0EA5E9'},
        {name: 'Mortgage interest', annual: Number(unitTco.mortgage_interest ?? 0), color: '#7C3AED'},
    ].filter((category) => Number(category.annual) > 0) : [
        {name: 'Strata levies', annual: totalAnnualLevy || totalLevied || 0, color: '#4F46E5'},
        ...((Array.isArray(dashboardExtras?.cost_categories) ? dashboardExtras.cost_categories : [])
            .filter((category: CostCategory) => category.name !== 'Strata levies')),
    ];
    const trueCostTotal = tcoCostCategories.reduce((sum, category) => sum + Number(category.annual || 0), 0);
    const footerBenchmark = dashboardExtras?.market_signals?.median_levy_pct_delta;
    const meetingForActions = nextMeeting || upcomingAgm;
    const meetingDateObj = meetingForActions?.date ? new Date(meetingForActions.date) : null;
    const meetingDateLabel = meetingDateObj
        ? meetingDateObj.toLocaleDateString('en-AU', {day: 'numeric', month: 'short'})
        : null;
    const weekActions: WeekAction[] = [];
    if (overdueQuarters.length > 0) {
        weekActions.push({
            id: 'levy-overdue',
            tone: 'rose',
            icon: 'dollar',
            title: `${overdueQuarters.length} levy quarter${overdueQuarters.length === 1 ? '' : 's'} overdue`,
            sub: 'Clear arrears to restore your on-time streak',
            cta: 'Pay now',
            href: '/financials/levy-payments',
        });
    } else if (nextInstalment > 0 || nextDueDateObj) {
        weekActions.push({
            id: 'levy-next',
            tone: 'amber',
            icon: 'dollar',
            title: daysUntilNextDue != null ? `Next levy due in ${daysUntilNextDue} days` : 'Next levy due',
            sub: `${formatCurrency(nextInstalment)} · ${nextDueDate}`,
            cta: 'Review',
            href: '/financials/levy-payments',
        });
    }
    if (meetingForActions?.date) {
        weekActions.push({
            id: 'next-meeting',
            tone: 'indigo',
            icon: 'vote',
            title: `${meetingForActions.title || 'Owners meeting'} on ${meetingDateLabel}`,
            sub: 'Agenda and proxies open before the meeting',
            cta: 'Open',
            href: '/governance/meetings',
        });
    }
    if ((maintenanceStats?.open_requests ?? 0) > 0) {
        weekActions.push({
            id: 'maintenance-open',
            tone: 'slate',
            icon: 'wrench',
            title: `${maintenanceStats.open_requests} maintenance request${maintenanceStats.open_requests === 1 ? '' : 's'} in flight`,
            sub: 'Track progress or add a photo',
            cta: 'View',
            href: '/maintenance',
        });
    }
    if (nextShock?.year) {
        weekActions.push({
            id: 'capital-upcoming',
            tone: 'indigo',
            icon: 'users',
            title: `Capital project flagged for FY ${nextShock.year}`,
            sub: nextShock.description || 'Sinking fund covers your share',
            cta: 'See impact',
            href: '/intelligence/building',
        });
    }

    // BuildingStrengthCard — replaces the two GaugeCards with a unified strength view
    // Same derivation as the Fund Health tile above — deliberately reusing it rather than
    // recomputing, so the card and the tile can never disagree. Unknown components are
    // excluded, not counted as 0; if nothing is known the score is null and renders "—".
    const buildingStrengthScore = fundHealthPct;
    const buildingStrengthItems = [
        {
            k: 'Admin fund',
            ok: adminPaidPct == null ? null : adminPaidPct >= 50,
            detail: adminPaidPct == null
                ? `No ${selectedYear} levy figures available yet`
                : `${adminPaidPct}% of ${selectedYear} levies collected`,
        },
        {
            k: 'Sinking fund',
            ok: sfHealthPct == null ? null : sfHealthPct >= 70,
            detail: sfHealthPct == null
                ? 'No reserve forecast on record'
                : sfHealthPct >= 70 ? 'Trajectory healthy' : 'Funding below target',
        },
        {
            k: 'Compliance',
            // A green tick beside the caption "No compliance data yet" is a contradiction
            // the old `?? 0` produced on every building with no compliance register.
            ok: complianceSummary ? (complianceSummary.overdue ?? 0) === 0 : null,
            detail: complianceSummary
                ? `${complianceSummary.completed}/${complianceSummary.total} tasks checked`
                : 'No compliance data yet',
        },
        {
            k: 'Maintenance SLA',
            ok: maintenanceStats ? (maintenanceStats.sla_breaches ?? 0) === 0 : null,
            detail: maintenanceStats
                ? `${maintenanceStats.open_requests ?? 0} requests open`
                : 'No maintenance data reported',
        },
    ];
    /**
     * @generated FunctionHeader
     * Function: openLevyAllocationDetail
     * Path: frontend/src/pages/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openLevyAllocationDetail = (category?: any) => {
        const categories = allocationCategories;
        const totalAnnual = allocationTotal;
        openOwnerInsight({
            title: category ? `${category.name} levy allocation` : 'Levy allocation',
            description: `FY ${selectedYear} owner levy breakdown`,
            actionLabel: 'Open levies',
            actionHref: '/financials/levy-payments',
            content: (
                <div className="space-y-4">
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                        <div className="rounded-xl bg-card ring-1 ring-border p-3">
                            <div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Annual levy</div>
                            <div className="text-lg font-semibold text-foreground">{formatCurrency(totalAnnual)}</div>
                        </div>
                        <div className="rounded-xl bg-card ring-1 ring-border p-3">
                            <div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Quarterly</div>
                            <div className="text-lg font-semibold text-foreground">{formatCurrency(totalAnnual / 4)}</div>
                        </div>
                        <div className="rounded-xl bg-card ring-1 ring-border p-3">
                            <div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Categories</div>
                            <div className="text-lg font-semibold text-foreground">{categories.length}</div>
                        </div>
                    </div>
                    {category ? (
                        <p className="text-sm font-semibold text-muted-foreground">
                            {category.name} represents <strong>{category.pct}%</strong> of your annual levy{category.amount != null ? `, or ${formatCurrency(category.amount)}.` : '.'}
                        </p>
                    ) : (
                        <div className="space-y-2">
                            {categories.slice(0, 6).map((item: any, index: number) => (
                                <div key={index} className="flex items-center justify-between rounded-xl bg-card ring-1 ring-border p-3 text-sm">
                                    <span className="font-bold text-foreground">{item.name}</span>
                                    <span className="font-semibold text-foreground">{item.pct}% {item.amount != null ? `· ${formatCurrency(item.amount)}` : ''}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            ),
        });
    };
    /**
     * @generated FunctionHeader
     * Function: openCostDetail
     * Path: frontend/src/pages/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openCostDetail = (category: CostCategory) => {
        const pct = trueCostTotal > 0 ? (Number(category.annual || 0) / trueCostTotal) * 100 : 0;
        openOwnerInsight({
            title: category.name,
            description: 'Ownership cost detail',
            actionLabel: 'Open owner hub',
            actionHref: '/owner-hub/tco',
            content: (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div className="rounded-xl bg-card ring-1 ring-border p-3">
                        <div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Annual cost</div>
                        <div className="text-xl font-semibold text-foreground">{formatCurrency(category.annual)}</div>
                    </div>
                    <div className="rounded-xl bg-card ring-1 ring-border p-3">
                        <div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Share of total</div>
                        <div className="text-xl font-semibold text-foreground">{pct.toFixed(1)}%</div>
                    </div>
                </div>
            ),
        });
    };
    /**
     * @generated FunctionHeader
     * Function: openCapitalDetail
     * Path: frontend/src/pages/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openCapitalDetail = (event?: any) => openOwnerInsight({
        title: event ? `Capital works FY ${event.year}` : 'Capital works ahead',
        description: 'Your estimated share of the 10-year plan',
        actionLabel: 'Open capital plan',
        actionHref: '/intelligence/capital-risk',
        content: event ? (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="rounded-xl bg-card ring-1 ring-border p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Project</div><div className="text-sm font-semibold text-foreground">{event.description || 'Capital works'}</div></div>
                <div className="rounded-xl bg-card ring-1 ring-border p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Estimated cost</div><div className="text-lg font-semibold text-foreground">{formatCurrency(event.estimated_cost ?? 0)}</div></div>
                <div className="rounded-xl bg-card ring-1 ring-border p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Your share</div><div className="text-lg font-semibold text-foreground">{event.myShare ? formatCurrency(event.myShare) : '—'}</div></div>
            </div>
        ) : (
            <p className="text-sm font-semibold text-muted-foreground">
                The card estimates your share from the capital works plan and your unit entitlement of {streakData?.entitlement_pct?.toFixed?.(2) ?? '—'}%.
            </p>
        ),
    });
    /**
     * @generated FunctionHeader
     * Function: openMarketDetail
     * Path: frontend/src/pages/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openMarketDetail = (payload?: any) => openOwnerInsight({
        title: payload?.unit ? `Comparable sale ${payload.unit}` : `Market pulse: ${marketSnapshot?.suburb ?? 'Building suburb'}`,
        description: payload?.unit ? 'Recent comparable sale' : 'Local sale and rental indicators',
        actionLabel: 'Open marketplace',
        actionHref: '/community/marketplace',
        content: payload?.unit ? (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="rounded-xl bg-card ring-1 ring-border p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Price</div><div className="text-lg font-semibold text-foreground">{formatCurrency(payload.price ?? 0)}</div></div>
                <div className="rounded-xl bg-card ring-1 ring-border p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Bedrooms</div><div className="text-lg font-semibold text-foreground">{payload.bedrooms || '—'}</div></div>
                <div className="rounded-xl bg-card ring-1 ring-border p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Sold</div><div className="text-lg font-semibold text-foreground">{payload.sold_date ? new Date(payload.sold_date).toLocaleDateString('en-AU') : '—'}</div></div>
            </div>
        ) : (
            <div className="grid grid-cols-2 gap-3">
                <div className="rounded-xl bg-card ring-1 ring-border p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Estimate</div><div className="text-lg font-semibold text-foreground">{formatCurrency(marketSignals?.estimate ?? 0)}</div></div>
                <div className="rounded-xl bg-card ring-1 ring-border p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Median price</div><div className="text-lg font-semibold text-foreground">{formatCurrency(marketSignals?.suburb_median ?? 0)}</div></div>
                <div className="rounded-xl bg-card ring-1 ring-border p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Annual growth</div><div className={`text-lg font-semibold ${(marketSignals?.yoy_pct ?? 0) >= 0 ? 'text-emerald-600' : 'text-rose-600'}`}>{marketSignals?.yoy_pct > 0 ? '+' : ''}{marketSignals?.yoy_pct ?? 0}%</div></div>
                <div className="rounded-xl bg-card ring-1 ring-border p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Days on market</div><div className="text-lg font-semibold text-foreground">{marketSignals?.days_on_market ?? '—'}d</div></div>
            </div>
        ),
    });
    /**
     * @generated FunctionHeader
     * Function: openActivityDetail
     * Path: frontend/src/pages/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openActivityDetail = (activity: any) => openOwnerInsight({
        title: activity.title || activity.message || 'Community update',
        description: activity.type || activity.kind || 'Activity',
        actionLabel: 'Open community',
        actionHref: '/community',
        content: (
            <div className="space-y-3 text-sm font-semibold text-muted-foreground">
                {activity.message && <p>{activity.message}</p>}
                {activity.created_at && <p>Created {new Date(activity.created_at).toLocaleString('en-AU')}</p>}
            </div>
        ),
    });
    /**
     * @generated FunctionHeader
     * Function: openBuildingHealthDetail
     * Path: frontend/src/pages/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openBuildingHealthDetail = () => openOwnerInsight({
        title: 'Building health',
        description: 'Asset intelligence summary',
        actionLabel: 'Open intelligence',
        actionHref: '/intelligence/building',
        content: (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="rounded-xl bg-card ring-1 ring-emerald-200 p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-emerald-600">Asset score</div><div className="text-2xl font-semibold text-foreground">{buildingIntelligence?.asset_health_score ?? '—'} / 100</div></div>
                <div className="rounded-xl bg-card ring-1 ring-amber-200 p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-amber-600">High-risk assets</div><div className="text-2xl font-semibold text-foreground">{buildingIntelligence?.high_risk_assets_count ?? 0}</div></div>
            </div>
        ),
    });
    /**
     * @generated FunctionHeader
     * Function: openComplianceDetail
     * Path: frontend/src/pages/dashboard/OwnerDashboard.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openComplianceDetail = () => openOwnerInsight({
        title: 'Compliance status',
        description: 'Building compliance checks',
        actionLabel: 'Open compliance',
        actionHref: '/compliance',
        content: (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <div className="rounded-xl bg-card ring-1 ring-border p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Completed</div><div className="text-xl font-semibold text-foreground">{complianceSummary?.completed ?? 0}</div></div>
                <div className="rounded-xl bg-card ring-1 ring-border p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">Total</div><div className="text-xl font-semibold text-foreground">{complianceSummary?.total ?? 0}</div></div>
                <div className="rounded-xl bg-card ring-1 ring-rose-200 p-3"><div className="text-[10px] font-semibold uppercase tracking-widest text-rose-600">Overdue</div><div className="text-xl font-semibold text-foreground">{complianceSummary?.overdue ?? 0}</div></div>
            </div>
        ),
    });

    /**
     * End every other session for this account, keeping the current one.
     *
     * The backend records a revocation instant on the user and spares this session's
     * jti; every other token 401s on its next request. Re-fetches afterwards so the
     * "signed in from N other places" list reflects the change rather than showing
     * stale sources next to a success message.
     */
    const handleSignOutEverywhere = async () => {
        setSignOutState('working');
        try {
            const res = await api.post('/security/sign-out-everywhere', {});
            setSignOutState('done');
            // If the backend could not identify this session's token it revoked ours too,
            // and the next request will 401. Send the user to sign in rather than leaving
            // them to discover it on a random click.
            if (res?.data?.current_session_kept === false) {
                setTimeout(() => router.push('/login'), 1500);
                return;
            }
            const secRes = await api.get('/security/my-activity');
            setSecurityActivity(secRes.data);
        } catch (err) {
            console.error('Sign out everywhere failed', err);
            setSignOutState('error');
        }
    };

    return (
        <div className="space-y-8 pb-12" data-testid="owner-dashboard">
            {/* Canonical page chrome.
                This page had NO <h1> at all — not a hand-rolled one at the wrong size,
                none. Its topmost element was the Property Pulse band, which is a <div>,
                so the document outline started at h2 and a screen-reader user landed on
                a page that never said what it was. PageHeader supplies the single h1 and
                puts the route at the same visual altitude as every migrated page. */}
            <PageHeader
                title="My Property"
                description={
                    activeUnitNumber
                        ? `Unit ${activeUnitNumber} · ${selectedBuilding?.name || 'your building'}`
                        : 'Your levies, requests and building activity'
                }
                icon={<Home className="h-5 w-5"/>}
            />

            {/* Important document alerts */}
            {importantDocs.filter(d => !dismissedDocs.has(d.id)).map(doc => (
                <div key={doc.id}
                     className="flex items-start gap-3 p-4 rounded-lg border border-amber-400 bg-amber-50 text-amber-900 shadow-sm">
                    <span className="text-amber-500 mt-0.5 shrink-0">⚠</span>
                    <div className="flex-1 min-w-0">
                        <p className="font-semibold text-sm">{doc.title}</p>
                        {doc.importance_summary && (
                            <p className="text-sm mt-0.5 line-clamp-3">{doc.importance_summary}</p>
                        )}
                        <p className="text-xs text-amber-600 mt-1">{doc.category?.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</p>
                    </div>
                    <button onClick={() => dismissDoc(doc.id)}
                            className="shrink-0 text-amber-400 hover:text-amber-700 transition-colors"
                            aria-label="Dismiss">✕
                    </button>
                </div>
            ))}

            {/* Management role context banner — shown when EC/Chairman visits their property view */}
            {(isManager() || isECMember()) && (
                <motion.div initial={{opacity: 0, y: -10}} animate={{opacity: 1, y: 0}}>
                    <Card className="border-none shadow-sm bg-primary/5">
                        <CardContent className="p-4 flex items-center justify-between">
                            <div className="flex items-center gap-3">
                                <Home size={16} className="text-primary"/>
                                <p className="text-sm font-medium text-foreground">
                                    My Property View — {activeUnitDisplayLabel}
                                </p>
                            </div>
                            <Button variant="ghost" size="sm" onClick={() => router.push('/dashboard')}
                                    className="text-primary font-bold text-xs">
                                ← Back to Management Dashboard
                            </Button>
                        </CardContent>
                    </Card>
                </motion.div>
            )}

            {/* Property Pulse Header (Action-Driven) */}
            <motion.div
                initial={{opacity: 0, y: -20}}
                animate={{opacity: 1, y: 0}}
                className="bg-primary bg-gradient-to-br from-primary via-primary to-secondary/30 rounded-xl p-1 shadow-md overflow-hidden relative"
            >
                {/* Animated background glow */}
                <div
                    className="absolute top-0 left-1/4 w-1/2 h-full bg-primary/20 blur-[100px] animate-pulse pointer-events-none"/>

                <div
                    className="bg-primary-foreground/10 rounded-xl px-8 py-5 flex flex-wrap items-center justify-between gap-8 relative z-10 border border-primary-foreground/10">
                    <div className="flex items-center gap-8">
                        <div className="hidden sm:flex flex-col cursor-pointer group"
                             onClick={() => setIsDetailModalOpen(true)}
                             aria-label={`Fund Health: ${pctLabel(fundHealthPct)} — click for details`}>
                            <span
                                className="text-[10px] font-semibold text-primary-foreground/60 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Fund Health</span>
                            <span className="text-sm font-semibold text-primary-foreground flex items-center gap-2">
                <span className={`w-2 h-2 rounded-full shadow-[0_0_10px_rgba(16,185,129,0.5)] ${
                    fundHealthPct == null ? 'bg-muted-foreground' :
                        fundHealthPct >= 80 ? 'bg-emerald-500' :
                            fundHealthPct >= 50 ? 'bg-amber-400' : 'bg-red-400'
                }`}/>
                                {fundHealthPct == null ? 'Funding unknown' : `${fundHealthPct}% Funded`}
              </span>
                        </div>
                        <div className="flex flex-col border-l border-primary-foreground/20 pl-8 cursor-pointer group"
                             onClick={() => router.push('/financials/levy-payments')}>
                            <span
                                className="text-[10px] font-semibold text-primary-foreground/60 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Levies Paid</span>
                            <span className="text-sm font-semibold text-primary-foreground">{paidPercentage}% of {selectedYear}</span>
                        </div>
                        <div className="flex flex-col border-l border-primary-foreground/20 pl-8 cursor-pointer group"
                             onClick={() => router.push('/maintenance')}>
                            <span
                                className="text-[10px] font-semibold text-primary-foreground/60 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Maintenance</span>
                            <span
                                className="text-sm font-semibold text-primary-foreground">{maintenanceStats?.open_requests || 0} Active</span>
                        </div>
                        {daysToAgm !== null && (
                            <div
                                className="hidden lg:flex flex-col border-l border-primary-foreground/20 pl-8 cursor-pointer group"
                                onClick={() => router.push('/governance/agm')}>
                                <span
                                    className="text-[10px] font-semibold text-primary-foreground/60 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">AGM</span>
                                <span className="text-sm font-semibold text-amber-400">{daysToAgm} Days Away</span>
                            </div>
                        )}
                        {marketSnapshot && (
                            <div
                                className="hidden xl:flex flex-col border-l border-primary-foreground/20 pl-8 cursor-pointer group"
                                onClick={() => router.push('/community/marketplace')}>
                                <span
                                    className="text-[10px] font-semibold text-primary-foreground/60 uppercase tracking-[0.2em] mb-1 group-hover:text-primary transition-colors">Growth</span>
                                <span
                                    className={`text-sm font-semibold ${Number(marketSnapshot.growth_yoy) < 0 ? 'text-rose-400' : 'text-emerald-400'}`}>{Number(marketSnapshot.growth_yoy) > 0 ? '+' : ''}{marketSnapshot.growth_yoy}% YoY</span>
                            </div>
                        )}
                    </div>

                    <div className="flex items-center gap-3">
                        <Button
                            size="lg"
                            variant="outline"
                            className="bg-transparent text-primary-foreground border-primary-foreground/20 hover:bg-primary-foreground/10 font-semibold rounded-xl transition-all duration-300 active:scale-95 px-6 hidden sm:flex items-center gap-2"
                            onClick={handleDownloadTaxSummary}
                            disabled={downloadingTax}
                        >
                            <FileText size={18}/>
                            {downloadingTax ? 'Generating...' : 'Tax Summary'}
                        </Button>
                        <Button
                            size="lg"
                            className="bg-primary-foreground text-primary hover:bg-primary-foreground/90 font-semibold rounded-xl shadow-md transition-all duration-300 active:scale-95 px-8"
                            onClick={handlePayNow}
                        >
                            Quick Pay
                        </Button>
                    </div>
                </div>
            </motion.div>

            {/* ── V2 Dashboard Layout ── */}

            {/* Hero: Your Standing — dark card with ring gauge + streak + fund gauges */}
            <div className="grid grid-cols-12 gap-6">
                {/* Left: dark personal-stake hero */}
                <motion.div
                    initial={{opacity: 0, y: 20}}
                    animate={{opacity: 1, y: 0}}
                    className="col-span-12 lg:col-span-5 self-start rounded-xl bg-primary p-6 text-primary-foreground
                        shadow-[0_20px_50px_rgba(0,0,0,0.18)] overflow-hidden relative flex flex-col justify-start gap-5"
                    data-testid="owner-hero-card"
                >
                    <div className="absolute top-0 left-1/3 w-1/2 h-full bg-primary/15 blur-[80px] pointer-events-none" aria-hidden="true"/>

                    {/* Unit heading */}
                    <div className="relative z-10">
                        <div className="text-[10px] font-semibold text-primary-foreground/60 uppercase tracking-[0.22em] mb-0.5">Your Standing</div>
                        <h2 className="text-xl font-semibold text-primary-foreground leading-tight">
                            {activeUnitDisplayLabel} · {selectedBuilding?.name || 'Your Building'}
                        </h2>
                        <div className="mt-2">
                            <YearSelector/>
                        </div>
                    </div>

                    {/* Ring gauge + streak */}
                    <div className="relative z-10 flex items-center gap-6 mt-5">
                        {/* SVG ring gauge — levy paid % */}
                        <div className="flex-shrink-0 relative w-[96px] h-[96px]" aria-label={`${paidPercentage}% of levies paid`}>
                            <svg viewBox="0 0 100 100" className="w-full h-full -rotate-90" aria-hidden="true">
                                <circle cx="50" cy="50" r="38" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="10"/>
                                <circle
                                    cx="50" cy="50" r="38" fill="none"
                                    stroke={adminHealthColor}
                                    strokeWidth="10"
                                    strokeLinecap="round"
                                    strokeDasharray={`${(paidPercentage / 100) * 238.76} 238.76`}
                                />
                            </svg>
                            <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
                                <span className="text-2xl font-semibold text-primary-foreground" style={{fontFeatureSettings: '"tnum" 1'}}>{paidPercentage}%</span>
                                <span className="text-[8px] font-bold text-primary-foreground/60 uppercase tracking-widest">Paid</span>
                            </div>
                        </div>

                        {/* Payment streak — data from /analytics/my-streak (PG-only) */}
                        <div className="flex-1 min-w-0">
                            <PaymentStreakCard
                                data={{
                                    streak:           streakData?.streak         ?? 0,
                                    on_time_pct:      streakData?.on_time_pct    ?? 0,
                                    recent_quarters:  streakData?.recent_quarters ?? [],
                                    total_quarters:   streakData?.total_quarters ?? 0,
                                    on_time_count:    streakData?.on_time_count  ?? 0,
                                }}
                                totalLots={selectedBuilding?.lot_count}
                                dark={true}
                            />
                        </div>
                    </div>

                    {/* Action buttons */}
                    <div className="relative z-10 mt-5 flex gap-3">
                        <Button
                            onClick={handlePayNow}
                            className="flex-1 bg-primary-foreground text-primary hover:bg-primary-foreground/90 font-semibold rounded-xl text-sm active:scale-95"
                        >
                            Quick Pay
                        </Button>
                        <Button
                            variant="outline"
                            onClick={handleDownloadTaxSummary}
                            disabled={downloadingTax}
                            className="flex-1 bg-transparent text-primary-foreground border-primary-foreground/20 hover:bg-primary-foreground/10 font-semibold rounded-xl text-sm active:scale-95"
                        >
                            {downloadingTax ? 'Generating…' : 'Tax Summary'}
                        </Button>
                    </div>
                </motion.div>

                {/* Right: Building Strength (v2) + levy summary */}
                <motion.div
                    initial={{opacity: 0, y: 20}}
                    animate={{opacity: 1, y: 0}}
                    transition={{delay: 0.1}}
                    className="col-span-12 lg:col-span-7 grid grid-rows-[auto_1fr] gap-4"
                >
                    {/* BuildingStrengthCard replaces the two individual GaugeCards */}
                    <BuildingStrengthCard
                        score={buildingStrengthScore}
                        items={buildingStrengthItems}
                        onBreakdown={() => router.push('/financials/overview')}
                    />

                    {financialData && (
                        <LevySummaryCard financialData={financialData} onPayClick={handlePayNow}/>
                    )}
                </motion.div>
            </div>

            <ThisWeekActions
                items={weekActions}
                estimatedMinutes={weekActions.length > 0 ? weekActions.length * 2 : undefined}
                onNavigate={(action) => action.href && router.push(action.href)}
            />

            {/* Row 3: Levy Allocation Donut + True Cost breakdown */}
            <div className="grid grid-cols-12 gap-6">
                <div className="col-span-12 lg:col-span-5">
                    <LevyAllocationDonut
                        categories={allocationCategories}
                        totalAnnual={allocationTotal}
                        year={String(selectedYear)}
                        onOpenDetails={() => openLevyAllocationDetail()}
                        onCategorySelect={openLevyAllocationDetail}
                    />
                </div>

                <div className="col-span-12 lg:col-span-7">
                    <TrueCostBreakdown
                        categories={tcoCostCategories}
                        yoyDelta={dashboardExtras?.cost_yoy_delta_pct ?? null}
                        onCategorySelect={openCostDetail}
                        className="h-full"
                    />
                </div>
            </div>

            {/* Row 4a: Market Intel + Capital For Me */}
            <div className="grid grid-cols-12 gap-6">
                {/* Market snapshot + unit estimate */}
                <div className="col-span-12 lg:col-span-7 space-y-4">
                    <MarketIntelExpanded
                        signals={marketSignals}
                        onRefine={openEstimateDialog}
                        onSignalSelect={openMarketDetail}
                    />

                    {/* My Unit Estimate teaser */}
                    <motion.div
                        initial={{opacity: 0, y: 20}}
                        animate={{opacity: 1, y: 0}}
                        transition={{delay: 0.15}}
                        className="rounded-xl bg-primary bg-gradient-to-br from-primary via-primary to-secondary/30 text-primary-foreground p-6 shadow-md overflow-hidden cursor-pointer hover:opacity-95 transition-opacity"
                        onClick={openEstimateDialog}
                        role="button"
                        aria-label="Set or update your unit market estimate"
                        tabIndex={0}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault();
                                openEstimateDialog();
                            }
                        }}
                    >
                        <div className="flex items-center justify-between mb-3">
                            <div>
                                <div className="text-[10px] font-semibold text-primary-foreground/70 uppercase tracking-[0.22em] mb-0.5">My Unit</div>
                                <h3 className="text-base font-semibold text-primary-foreground">Personal Market Estimate</h3>
                            </div>
                            <div className="p-3 bg-primary-foreground/20 rounded-xl" aria-hidden="true">
                                <Pencil size={16}/>
                            </div>
                        </div>

                        {estimatedPrice ? (
                            <div>
                                <p className="text-3xl font-semibold" style={{fontFeatureSettings: '"tnum" 1'}}>{formatEstimate(estimatedPrice)}</p>
                                {priceLastUpdated && (
                                    <p className="text-xs text-primary-foreground/70 mt-1">
                                        Updated {new Date(priceLastUpdated).toLocaleDateString('en-AU', {day: 'numeric', month: 'short', year: 'numeric'})}
                                    </p>
                                )}
                                <p className="text-xs text-primary-foreground/50 mt-2 italic">Tap to update</p>
                            </div>
                        ) : (
                            <div className="flex items-center gap-3 mt-2">
                                <div className="p-2 bg-primary-foreground/10 rounded-xl border border-dashed border-primary-foreground/30" aria-hidden="true">
                                    <Tag className="w-4 h-4 text-primary-foreground/70"/>
                                </div>
                                <div>
                                    <p className="text-sm font-bold text-primary-foreground/80">Set your market value estimate</p>
                                    <p className="text-[11px] text-primary-foreground/70">Tap to add · private to you only</p>
                                </div>
                            </div>
                        )}
                    </motion.div>
                </div>

                {/* Capital For Me */}
                <div className="col-span-12 lg:col-span-5">
                    <CapitalForMeCard
                        shockRows={shockRows}
                        entitlementPct={
                            // /analytics/my-streak returns entitlement_pct as 0–100 (already a %).
                            // The raw unit_entitlement UOE integer divided by 10000 is NOT a valid
                            // percentage (total UOE varies per building), and 1.35 was a
                            // hardcoded East Gate–specific fallback — both are removed.
                            streakData?.entitlement_pct ?? 0
                        }
                        onEventSelect={openCapitalDetail}
                        onOpenPlan={() => openCapitalDetail()}
                    />
                </div>
            </div>

            {/* Row 5: Community Pulse + Building Health + Emergency */}
            <div className="grid grid-cols-12 gap-6">
                {/* Community feed */}
                <div className="col-span-12 lg:col-span-7">
                    <CommunityPulseFeed items={activities} onItemSelect={openActivityDetail}/>
                </div>

                {/* Right column: building health + compliance + emergency */}
                <div className="col-span-12 lg:col-span-5 flex flex-col gap-4">
                    {/* Building intelligence score */}
                    {buildingIntelligence && (
                        <motion.div
                            initial={{opacity: 0, y: 20}}
                            animate={{opacity: 1, y: 0}}
                            className="rounded-xl bg-emerald-50 ring-1 ring-emerald-200 p-5 cursor-pointer hover:bg-emerald-100 transition-colors"
                            onClick={openBuildingHealthDetail}
                            role="button"
                            aria-label={`Building health score: ${buildingIntelligence.asset_health_score ?? '—'} out of 100`}
                            tabIndex={0}
                            onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                    e.preventDefault();
                                    openBuildingHealthDetail();
                                }
                            }}
                        >
                            <div className="text-[10px] font-semibold text-emerald-600 uppercase tracking-widest mb-1">Building Health</div>
                            <div className="flex items-baseline gap-2">
                                <span className="text-4xl font-semibold text-emerald-800" style={{fontFeatureSettings: '"tnum" 1'}}>
                                    {buildingIntelligence.asset_health_score ?? '—'}
                                </span>
                                <span className="text-sm font-bold text-emerald-600">/ 100</span>
                            </div>
                            {(buildingIntelligence.high_risk_assets_count ?? 0) > 0 ? (
                                <p className="text-xs text-amber-600 font-semibold mt-1">
                                    {buildingIntelligence.high_risk_assets_count} high-risk asset{buildingIntelligence.high_risk_assets_count !== 1 ? 's' : ''} — view intelligence →
                                </p>
                            ) : (
                                <p className="text-xs text-emerald-600 font-semibold mt-1">All assets within normal range</p>
                            )}
                        </motion.div>
                    )}

                    {/* Compliance status */}
                    <div
                        className="rounded-xl bg-card ring-1 ring-border/70 p-5 shadow-sm cursor-pointer hover:ring-primary/40 transition-colors focus:outline-none focus:ring-2 focus:ring-primary/50"
                        data-testid="owner-compliance-widget"
                        role="button"
                        tabIndex={0}
                        aria-label="Open compliance summary"
                        onClick={openComplianceDetail}
                        onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault();
                                openComplianceDetail();
                            }
                        }}
                    >
                        <div className="text-[10px] font-semibold text-muted-foreground uppercase tracking-widest mb-1">Compliance</div>
                        <div className={`text-xl font-semibold ${complianceSummary?.overdue > 0 ? 'text-rose-600' : 'text-emerald-700'}`}>
                            {complianceSummary?.status === 'healthy' ? 'All Clear' : complianceSummary?.label || '—'}
                        </div>
                        {complianceSummary && (
                            <p className="text-[11px] text-muted-foreground mt-0.5">
                                {complianceSummary.completed}/{complianceSummary.total} items checked
                                {complianceSummary.overdue > 0 && (
                                    <span className="text-rose-500 font-bold"> · {complianceSummary.overdue} overdue</span>
                                )}
                            </p>
                        )}
                    </div>

                    {/* Your Requests — v2 backport; replaces standalone emergency button
                        (emergency is already in the Quick Actions row below).
                        Raw workflow-request fields must be mapped to OwnerRequest shape:
                        request_number → reference, subject → title. */}
                    <YourRequestsCard
                        requests={ownerRequests
                            // GET /workflow-requests returns closed requests too, and
                            // YourRequestsCard renders anything it isn't told about as
                            // "In progress" — so without this filter a closed request
                            // was counted in the card's "N open" headline.
                            .filter((r: any) => !isTerminalRequestStatus(r.status))
                            .map((r: any) => {
                                const id = r.id ?? r._id;
                                return {
                                    id,
                                    reference:   r.request_number ?? r.reference,
                                    status:      r.status,
                                    title:       r.title ?? r.subject ?? r.request_type?.replace(/_/g, ' ') ?? 'Maintenance request',
                                    summary:     r.description ?? r.summary,
                                    needs_reply: r.needs_reply ?? (r.status === 'awaiting_owner'),
                                    // Only build a detail href when there is an id — `/requests/`
                                    // with an empty dynamic segment is not a route.
                                    href:        id ? `/requests/${id}` : REQUEST_QUEUE_HREF,
                                };
                            })}
                        onNewRequest={() => router.push('/requests/new')}
                        onRequest={(r) => router.push(r.href ?? REQUEST_QUEUE_HREF)}
                    />
                </div>
            </div>

            {/* Row 4: Quick Actions */}
            <motion.div
                initial={{opacity: 0, y: 20}}
                animate={{opacity: 1, y: 0}}
                transition={{delay: 0.4}}
            >
                <Card className="border-none shadow-lg bg-card overflow-hidden">
                    <CardHeader className="bg-muted/60/50 border-b">
                        <CardTitle className="text-lg font-bold">Owner Tools & Resources</CardTitle>
                    </CardHeader>
                    <CardContent className="p-6">
                        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                            {quickActions.map((action, index) => (
                                <button
                                    key={index}
                                    onClick={() => router.push(action.href)}
                                    className="flex flex-col items-center justify-center p-6 rounded-xl border border-border bg-card hover:border-primary/20 hover:bg-primary/5 transition-all duration-300 group shadow-sm hover:shadow-xl hover:-translate-y-1"
                                >
                                    <div
                                        className={`p-4 rounded-xl ${action.color} mb-4 group-hover:scale-110 transition-transform duration-300 shadow-sm group-hover:shadow-md`}>
                                        <action.icon size={28} strokeWidth={2.5}/>
                                    </div>
                                    <span
                                        className="font-semibold text-[11px] text-muted-foreground group-hover:text-primary uppercase tracking-widest transition-colors">{action.label}</span>
                                </button>
                            ))}
                        </div>
                    </CardContent>
                </Card>
            </motion.div>

            {/* Row 5: Utilities & Property Services — compact action cards */}
            <motion.div
                initial={{opacity: 0, y: 20}}
                animate={{opacity: 1, y: 0}}
                transition={{delay: 0.5}}
            >
                <Card className="border-none shadow-lg bg-card overflow-hidden">
                    <CardHeader className="bg-muted/60/50 border-b">
                        <CardTitle className="text-lg font-bold">Utilities &amp; Property Services</CardTitle>
                        <CardDescription>Click any card to view details for your unit.</CardDescription>
                    </CardHeader>
                    <CardContent className="p-6 space-y-6">
                        {/* Rates & Property Taxes */}
                        <div>
                            <p className="text-sm font-semibold text-muted-foreground uppercase tracking-widest mb-3">
                                Rates &amp; Property Taxes
                            </p>
                            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
                                <CouncilRateActionCard unitNumber={activeUnitNumber}/>
                                <LandTaxActionCard unitNumber={activeUnitNumber}/>
                                <WaterBillActionCard unitNumber={activeUnitNumber}/>
                            </div>
                        </div>
                        {/* Utilities */}
                        <div>
                            <p className="text-sm font-semibold text-muted-foreground uppercase tracking-widest mb-3">
                                Utilities
                            </p>
                            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
                                <ElectricityActionCard unitNumber={activeUnitNumber}/>
                                <GasActionCard unitNumber={activeUnitNumber}/>
                                <NBNActionCard unitNumber={activeUnitNumber}/>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            </motion.div>

            {/* Security Card — shown when security activity data is available */}
            {securityActivity && (
                <motion.div
                    initial={{opacity: 0, y: 20}}
                    animate={{opacity: 1, y: 0}}
                    transition={{delay: 0.6}}
                >
                    <Card className="border-none shadow-lg bg-card overflow-hidden">
                        <CardHeader className="bg-muted/60/50 border-b">
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <ShieldCheck size={18}
                                                 className={securityActivity.suspicious_events_30d > 0 ? 'text-orange-500' : 'text-emerald-500'}/>
                                    <CardTitle className="text-lg font-bold">Account Security</CardTitle>
                                </div>
                                {/* Where else this account has been used.
                                There is no session store — auth is a stateless JWT — so this
                                cannot say "logged in right now" and must not imply it. It reports
                                sign-ins inside the token lifetime, whose session could still be
                                valid. Copy is worded to that limit deliberately. */}
                            {(() => {
                                const ca = securityActivity.concurrent_access;
                                if (!ca) return null;
                                if (!ca.history_available) {
                                    // Absent history is NOT an all-clear. Zero other sources here
                                    // would render as "only you are using this account" while
                                    // actually meaning nothing was ever recorded.
                                    return (
                                        <div className="mt-4 p-3 bg-muted/60 border border-border rounded-xl">
                                            <p className="text-sm text-muted-foreground">
                                                No sign-in history recorded in the last {ca.window_hours} hours, so
                                                other sign-ins cannot be checked.
                                            </p>
                                        </div>
                                    );
                                }
                                const others = ca.sources?.filter((src: any) => !src.is_current_ip) || [];
                                return (
                                    <div className="mt-4 space-y-3">
                                        {ca.impossible_travel && (
                                            /* Positive evidence rather than a prompt to look: one
                                               person cannot be in two places at once. Ranked above
                                               the source list because it is the stronger signal. */
                                            <div className="p-3 bg-red-50 border border-red-200 rounded-xl">
                                                <div className="flex items-start gap-2">
                                                    <AlertCircle size={16} className="text-red-600 shrink-0 mt-0.5"/>
                                                    <div className="flex-1">
                                                        <p className="text-sm font-bold text-red-800">
                                                            Sign-ins from two places too far apart to be the same trip
                                                        </p>
                                                        <p className="text-xs text-red-700 mt-0.5">
                                                            {ca.impossible_travel.from.city || ca.impossible_travel.from.country || 'Unknown'}
                                                            {' → '}
                                                            {ca.impossible_travel.to.city || ca.impossible_travel.to.country || 'Unknown'}
                                                            {' — '}{ca.impossible_travel.distance_km.toLocaleString()} km
                                                            in {ca.impossible_travel.hours_apart} h.
                                                        </p>
                                                        <button onClick={() => router.push('/profile')}
                                                                className="text-xs font-bold text-red-700 hover:text-red-900 mt-1">
                                                            Reset password →
                                                        </button>
                                                    </div>
                                                </div>
                                            </div>
                                        )}
                                        <div className={`p-3 rounded-xl border ${others.length > 0 ? 'bg-amber-50 border-amber-200' : 'bg-muted/60 border-border'}`}>
                                            <p className={`text-sm font-bold ${others.length > 0 ? 'text-amber-900' : 'text-foreground'}`}>
                                                {others.length === 0
                                                    ? 'No other sign-ins to your account'
                                                    : `Your account was also signed in from ${others.length} other ${others.length === 1 ? 'place' : 'places'}`}
                                            </p>
                                            <p className="text-xs text-muted-foreground mt-0.5">
                                                {others.length === 0
                                                    ? `Only this device and network in the last ${ca.window_hours} hours.`
                                                    : `In the last ${ca.window_hours} hours. A sign-in from that long ago may still have an active session.`}
                                            </p>
                                            {others.length > 0 && (
                                                <ul className="mt-2 space-y-1.5">
                                                    {others.slice(0, 4).map((src: any, i: number) => (
                                                        <li key={i}
                                                            className="flex items-start justify-between gap-3 text-xs">
                                                            <span className="text-amber-900 font-medium truncate">
                                                                {src.device}
                                                                {(src.city || src.country) &&
                                                                    ` — ${[src.city, src.country].filter(Boolean).join(', ')}`}
                                                            </span>
                                                            <span className="text-muted-foreground font-mono whitespace-nowrap"
                                                                  title={src.ip_addresses.join(', ')}>
                                                                {src.ip_addresses[0]}
                                                                {src.ip_addresses.length > 1 && ` +${src.ip_addresses.length - 1}`}
                                                            </span>
                                                        </li>
                                                    ))}
                                                </ul>
                                            )}
                                        </div>
                                    </div>
                                );
                            })()}
                            {securityActivity.suspicious_events_30d > 0 && (
                                    <span
                                        className="px-2.5 py-1 bg-orange-100 text-orange-800 rounded-full text-xs font-bold flex items-center gap-1">
                    <AlertCircle
                        size={11}/> {securityActivity.suspicious_events_30d} suspicious event{securityActivity.suspicious_events_30d !== 1 ? 's' : ''}
                  </span>
                                )}
                            </div>
                        </CardHeader>
                        <CardContent className="p-6">
                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                <div className="p-4 rounded-xl bg-muted/60 border border-border">
                                    <p className="text-xs font-bold text-muted-foreground uppercase tracking-widest mb-1">Last
                                        Login</p>
                                    <p className="text-sm font-bold text-foreground">
                                        {securityActivity.last_login_at
                                            ? new Date(securityActivity.last_login_at).toLocaleDateString('en-AU', {
                                                day: 'numeric',
                                                month: 'short'
                                            })
                                            : '—'}
                                    </p>
                                    <p className="text-xs text-muted-foreground mt-0.5">
                                        {securityActivity.last_login_at
                                            ? new Date(securityActivity.last_login_at).toLocaleTimeString('en-AU', {
                                                hour: '2-digit',
                                                minute: '2-digit'
                                            })
                                            : ''}
                                    </p>
                                </div>
                                <div className="p-4 rounded-xl bg-muted/60 border border-border">
                                    <p className="text-xs font-bold text-muted-foreground uppercase tracking-widest mb-1">Location</p>
                                    <p className="text-sm font-bold text-foreground">{securityActivity.last_login_country || '—'}</p>
                                    <p className="text-xs text-muted-foreground mt-0.5">{securityActivity.last_login_city || ''}</p>
                                    {securityActivity.last_login_ip && (
                                        <p className="text-xs text-muted-foreground mt-1 font-mono truncate"
                                           title={securityActivity.last_login_ip}>{securityActivity.last_login_ip}</p>
                                    )}
                                </div>
                                <div className="p-4 rounded-xl bg-muted/60 border border-border">
                                    <p className="text-xs font-bold text-muted-foreground uppercase tracking-widest mb-1">Device</p>
                                    <p className="text-sm font-bold text-foreground truncate">{securityActivity.last_login_device || '—'}</p>
                                </div>
                                <div
                                    className={`p-4 rounded-xl border ${securityActivity.failed_attempts_30d > 0 ? 'bg-red-50 border-red-100' : 'bg-muted/60 border-border'}`}>
                                    <p className="text-xs font-bold text-muted-foreground uppercase tracking-widest mb-1">Failed
                                        (30d)</p>
                                    <p className={`text-sm font-bold ${securityActivity.failed_attempts_30d > 0 ? 'text-red-700' : 'text-emerald-700'}`}>
                                        {securityActivity.failed_attempts_30d}
                                    </p>
                                    <p className="text-xs text-muted-foreground mt-0.5">{securityActivity.unique_ips_30d} IP{securityActivity.unique_ips_30d !== 1 ? 's' : ''} used</p>
                                </div>
                            </div>
                            {securityActivity.suspicious_events_30d > 0 && (
                                <div
                                    className="mt-4 p-3 bg-orange-50 border border-orange-200 rounded-xl flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                        <AlertCircle size={16} className="text-orange-600 flex-shrink-0"/>
                                        <p className="text-sm text-orange-800">Suspicious activity detected on your
                                            account this month.</p>
                                    </div>
                                    <button
                                        onClick={() => router.push('/profile')}
                                        className="text-xs font-bold text-orange-700 hover:text-orange-900 whitespace-nowrap ml-4"
                                    >
                                        Reset password →
                                    </button>
                                </div>
                            )}

                            {/* Sign out everywhere.
                                Kept deliberately quiet: text-[11px], no bold, no uppercase and
                                muted-foreground/60 — a step below the "Last Login" label
                                (text-xs font-bold uppercase) directly above it. This is a remedy
                                you go looking for after noticing something, not a call to action
                                competing with the account figures. */}
                            <div className="mt-4 pt-3 border-t border-border/60">
                                {signOutState === 'idle' && (
                                    <button
                                        onClick={() => setSignOutState('confirm')}
                                        className="text-[11px] text-muted-foreground/60 hover:text-muted-foreground underline underline-offset-2 decoration-dotted transition-colors"
                                    >
                                        Sign out of all other devices
                                    </button>
                                )}

                                {signOutState === 'confirm' && (
                                    <div className="space-y-2">
                                        <p className="text-[11px] text-muted-foreground/80 leading-relaxed max-w-xl">
                                            Every other sign-in is ended immediately — each one is signed out the
                                            next time it does anything, even if it was still in use. Only this
                                            device stays signed in. Anyone who has your password can sign back in,
                                            so change it too if you think someone else knows it.
                                        </p>
                                        <div className="flex items-center gap-3">
                                            <button
                                                onClick={handleSignOutEverywhere}
                                                className="text-[11px] font-medium text-rose-600/90 hover:text-rose-700 underline underline-offset-2"
                                            >
                                                Yes, sign out everywhere else
                                            </button>
                                            <button
                                                onClick={() => setSignOutState('idle')}
                                                className="text-[11px] text-muted-foreground/60 hover:text-muted-foreground"
                                            >
                                                Cancel
                                            </button>
                                        </div>
                                    </div>
                                )}

                                {signOutState === 'working' && (
                                    <p className="text-[11px] text-muted-foreground/60">Signing out other devices…</p>
                                )}

                                {signOutState === 'done' && (
                                    <p className="text-[11px] text-emerald-600/80">
                                        Other devices signed out. This one is still signed in.
                                    </p>
                                )}

                                {signOutState === 'error' && (
                                    /* Never silently degrade to the idle link: the user would read that
                                       as "nothing happened" when in fact their other sessions are still
                                       live. Name the fallback that does work. */
                                    <p className="text-[11px] text-rose-600/80">
                                        Could not sign out your other devices.{' '}
                                        <button onClick={() => router.push('/profile')}
                                                className="underline underline-offset-2">
                                            Change your password
                                        </button>{' '}
                                        instead.
                                    </p>
                                )}
                            </div>
                        </CardContent>
                    </Card>
                </motion.div>
            )}

            {footerBenchmark != null && (
                <DashboardFooterCue>
                    Your levy is{' '}
                    <span className={`${footerBenchmark < 0 ? 'text-emerald-600' : 'text-rose-600'} font-semibold`}>
                        {Math.abs(footerBenchmark)}% {footerBenchmark < 0 ? 'below' : 'above'}
                    </span>{' '}
                    the {dashboardExtras?.market_signals?.benchmark_label || 'ACT median'} for similar units in this suburb.
                </DashboardFooterCue>
            )}
            {trueCostTotal === 0 && footerBenchmark == null && (
                <DashboardFooterCue dotColor="#94a3b8">
                    Connect council, water and market data feeds to unlock full true-cost and market signals.
                </DashboardFooterCue>
            )}

            {/* Detail Modal — Fund Health Intelligence */}
            <DashboardDetailModal
                isOpen={isDetailModalOpen}
                onClose={() => setIsDetailModalOpen(false)}
                title="Fund Health Intelligence"
                description="How your building's financial health is calculated"
                actionLabel="View My Finances"
                onAction={() => router.push('/financials/my-finances')}
            >
                <div className="space-y-5">
                    {/* Combined score */}
                    <div className="bg-primary rounded-xl p-4 text-primary-foreground">
                        <p className="text-[10px] font-semibold text-primary-foreground/60 uppercase tracking-widest mb-1">Combined
                            Fund Health</p>
                        <p className="text-3xl font-semibold">{pctLabel(fundHealthPct)}</p>
                        <p className="text-xs text-primary-foreground/60 mt-1">Average of the known components — Admin Fund
                            health ({pctLabel(adminPaidPct)}) and Sinking Fund health ({pctLabel(sfHealthPct)})</p>
                    </div>

                    {/* Admin Fund breakdown */}
                    <div className="border rounded-xl p-4 space-y-2">
                        <div className="flex items-center justify-between">
                            <p className="text-sm font-bold text-foreground">Administration Fund</p>
                            <span
                                className={`text-sm font-semibold ${adminPaidPct == null ? 'text-muted-foreground' : adminPaidPct >= 90 ? 'text-emerald-600' : adminPaidPct >= 25 ? 'text-primary' : 'text-amber-500'}`}>{pctLabel(adminPaidPct)}</span>
                        </div>
                        <div className="w-full bg-muted rounded-full h-2">
                            <div className="h-2 rounded-full transition-all"
                                 style={{width: `${adminPaidPct ?? 0}%`, backgroundColor: adminHealthColor}}/>
                        </div>
                        <p className="text-xs text-muted-foreground">
                            <strong>How it's calculated:</strong> This is the percentage of your annual admin levy that
                            has been paid so far.
                            {financialData?.admin_fund?.paid != null && financialData?.admin_fund?.annual != null && (
                                <> You have
                                    paid <strong>${financialData.admin_fund.paid.toLocaleString('en-AU', {minimumFractionDigits: 2})}</strong> of
                                    your <strong>${financialData.admin_fund.annual.toLocaleString('en-AU', {minimumFractionDigits: 2})}</strong> annual
                                    levy.</>
                            )}
                        </p>
                        <div className="flex gap-2 text-[10px] text-muted-foreground">
                            <span className="flex items-center gap-1"><span
                                className="w-2 h-2 rounded-full bg-emerald-500 inline-block"/> ≥90% = Fully paid</span>
                            <span className="flex items-center gap-1"><span
                                className="w-2 h-2 rounded-full bg-primary inline-block"/> ≥25% = On track</span>
                            <span className="flex items-center gap-1"><span
                                className="w-2 h-2 rounded-full bg-amber-400 inline-block"/> &lt;25% = Payment due</span>
                        </div>
                    </div>

                    {/* Sinking Fund breakdown */}
                    <div className="border rounded-xl p-4 space-y-2">
                        <div className="flex items-center justify-between">
                            <p className="text-sm font-bold text-foreground">Sinking Fund</p>
                            <span
                                className={`text-sm font-semibold ${sfHealthPct == null ? 'text-muted-foreground' : sfHealthPct >= 80 ? 'text-primary' : sfHealthPct >= 50 ? 'text-amber-500' : 'text-red-500'}`}>{pctLabel(sfHealthPct)}</span>
                        </div>
                        <div className="w-full bg-muted rounded-full h-2">
                            <div className="h-2 rounded-full transition-all" style={{
                                width: `${sfHealthPct ?? 0}%`,
                                backgroundColor: sfHealthPct == null ? UNKNOWN_TONE : sfHealthPct >= 80 ? '#2563EB' : sfHealthPct >= 50 ? '#F59E0B' : '#DC2626'
                            }}/>
                        </div>
                        <p className="text-xs text-muted-foreground">
                            <strong>How it's calculated:</strong> Percentage of the next 10 forecast years where the
                            sinking fund balance remains positive.
                            {sinkingFundForecast?.projection && (
                                <> Currently {sinkingFundForecast.projection.filter((p: any) => p.closing_balance > 0).length} of {sinkingFundForecast.projection.length} forecast
                                    years have a positive balance.</>
                            )}
                        </p>
                        <div className="flex gap-2 text-[10px] text-muted-foreground">
                            <span className="flex items-center gap-1"><span
                                className="w-2 h-2 rounded-full bg-primary inline-block"/> ≥80% = Well funded</span>
                            <span className="flex items-center gap-1"><span
                                className="w-2 h-2 rounded-full bg-amber-400 inline-block"/> ≥50% = Adequately funded</span>
                            <span className="flex items-center gap-1"><span
                                className="w-2 h-2 rounded-full bg-red-400 inline-block"/> &lt;50% = Funding at risk</span>
                        </div>
                    </div>

                    <p className="text-xs text-muted-foreground italic">
                        Fund health is a snapshot indicator. For full details, view the Finance Report or speak with
                        your strata manager.
                    </p>
                </div>
            </DashboardDetailModal>

            <DashboardDetailModal
                isOpen={Boolean(ownerInsight)}
                onClose={() => setOwnerInsight(null)}
                title={ownerInsight?.title || ''}
                description={ownerInsight?.description}
                actionLabel={ownerInsight?.actionLabel}
                onAction={openOwnerInsightRoute}
            >
                {ownerInsight?.content}
            </DashboardDetailModal>

            {/* Payment Modal */}
            <PaymentModal
                isOpen={isPaymentModalOpen}
                onClose={() => setIsPaymentModalOpen(false)}
                paymentData={paymentData}
                onPaymentSuccess={() => {
                    toast.success('Payment successful!');
                    fetchDashboardData();
                }}
            />

            {/* ── My Unit Market Estimate Dialog ── */}
            <Dialog open={marketValuePopupOpen} onOpenChange={setMarketValuePopupOpen}>
                <DialogContent className="sm:max-w-lg rounded-xl p-0 overflow-hidden">
                    {/* Coloured header */}
                    <div className="bg-primary bg-gradient-to-br from-primary via-primary to-secondary/30 px-7 pt-7 pb-6 text-primary-foreground">
                        <div className="flex items-center gap-3 mb-4">
                            <div className="p-3 bg-primary-foreground/20 rounded-xl">
                                <Home className="w-5 h-5 text-primary-foreground"/>
                            </div>
                            <div>
                                <DialogTitle className="text-lg font-semibold text-primary-foreground">My Unit Estimate</DialogTitle>
                                <DialogDescription className="text-primary-foreground/70 text-xs">
                                    {activeUnitDisplayLabel} — set your personal market value estimate
                                </DialogDescription>
                            </div>
                        </div>

                        {/* Market context chips.
                            /analytics/market-snapshot returns {suburb, median_price, rental_yield,
                            days_on_market, growth_yoy, last_updated} — unit_median/growth_12m/last_sale
                            never existed on that response shape, so these chips never rendered. */}
                        <div className="grid grid-cols-2 gap-2 text-xs">
                            {marketSnapshot?.median_price && (
                                <div className="bg-primary-foreground/15 rounded-xl px-3 py-2 flex items-center gap-2">
                                    <MapPin className="w-3 h-3 text-primary-foreground/70 shrink-0"/>
                                    <div>
                                        <p className="text-[9px] font-bold text-primary-foreground/70 uppercase tracking-widest">Suburb
                                            Median</p>
                                        <p className="font-semibold text-primary-foreground">{formatCurrency(marketSnapshot.median_price)}</p>
                                    </div>
                                </div>
                            )}
                            {marketSnapshot?.growth_yoy != null && (
                                <div className="bg-primary-foreground/15 rounded-xl px-3 py-2 flex items-center gap-2">
                                    <TrendingUp className="w-3 h-3 text-emerald-300 shrink-0"/>
                                    <div>
                                        <p className="text-[9px] font-bold text-primary-foreground/70 uppercase tracking-widest">12-Mo
                                            Growth</p>
                                        {/* market_snapshots is a real collection (scraper-fed) and can hold a
                                            negative growth_yoy for a declining market — only prefix "+" for
                                            genuinely positive values so a decline doesn't render as "+-3.8%". */}
                                        <p className="font-semibold text-emerald-300">{marketSnapshot.growth_yoy > 0 ? "+" : ""}{marketSnapshot.growth_yoy}%</p>
                                    </div>
                                </div>
                            )}
                            {marketSnapshot?.rental_yield && (
                                <div className="bg-primary-foreground/15 rounded-xl px-3 py-2 flex items-center gap-2">
                                    <DollarSign className="w-3 h-3 text-amber-300 shrink-0"/>
                                    <div>
                                        <p className="text-[9px] font-bold text-primary-foreground/70 uppercase tracking-widest">Rental
                                            Yield</p>
                                        <p className="font-semibold text-amber-300">{marketSnapshot.rental_yield}%</p>
                                    </div>
                                </div>
                            )}
                            {marketSnapshot?.days_on_market != null && (
                                <div className="bg-primary-foreground/15 rounded-xl px-3 py-2 flex items-center gap-2">
                                    <CheckCircle2 className="w-3 h-3 text-emerald-300 shrink-0"/>
                                    <div>
                                        <p className="text-[9px] font-bold text-primary-foreground/70 uppercase tracking-widest">Days on
                                            Market</p>
                                        <p className="font-semibold text-primary-foreground text-[11px]">{marketSnapshot.days_on_market}d avg</p>
                                    </div>
                                </div>
                            )}
                        </div>
                    </div>

                    <div className="px-7 py-5 space-y-5">
                        {/* Valuation tips */}
                        <div className="bg-amber-50 border border-amber-200 rounded-xl p-4">
                            <div className="flex items-center gap-2 mb-2">
                                <Info className="w-3.5 h-3.5 text-amber-600 shrink-0"/>
                                <p className="text-[10px] font-semibold text-amber-700 uppercase tracking-widest">Pricing
                                    Tips</p>
                            </div>
                            <ul className="space-y-1 text-[11px] text-amber-800">
                                <li className="flex items-start gap-1.5"><span
                                    className="text-emerald-600 font-bold mt-px">+</span><span>Higher floor, north-facing aspect, or building views</span>
                                </li>
                                <li className="flex items-start gap-1.5"><span
                                    className="text-emerald-600 font-bold mt-px">+</span><span>2+ car spaces, large balcony, recent kitchen/bath reno</span>
                                </li>
                                <li className="flex items-start gap-1.5"><span
                                    className="text-emerald-600 font-bold mt-px">+</span><span>Strong building fund health — buyers pay a premium</span>
                                </li>
                                <li className="flex items-start gap-1.5"><span
                                    className="text-rose-500 font-bold mt-px">−</span><span>Ground floor, west-facing, single or no car space</span>
                                </li>
                                <li className="flex items-start gap-1.5"><span
                                    className="text-rose-500 font-bold mt-px">−</span><span>Original fit-out, busy road exposure, smaller floorplan</span>
                                </li>
                            </ul>
                        </div>

                        {/* Price input */}
                        <div className="space-y-1.5">
                            <label
                                className="text-[10px] font-semibold text-muted-foreground uppercase tracking-widest flex items-center gap-1.5">
                                <DollarSign className="w-3 h-3"/>
                                Your Estimated Market Price (AUD)
                            </label>
                            <div className="relative">
                                <span
                                    className="absolute left-3.5 top-1/2 -translate-y-1/2 text-muted-foreground font-bold text-sm">$</span>
                                <Input
                                    type="text"
                                    inputMode="numeric"
                                    placeholder="e.g. 850000"
                                    value={priceInput}
                                    onChange={(e) => setPriceInput(e.target.value.replace(/[^0-9]/g, ''))}
                                    className="pl-7 rounded-xl font-bold text-foreground border-border focus:border-primary"
                                />
                            </div>
                            {priceInput && Number(priceInput) > 0 && (
                                <p className="text-[11px] text-primary font-bold">
                                    = {Number(priceInput) >= 1_000_000
                                    ? `$${(Number(priceInput) / 1_000_000).toFixed(2).replace(/\.?0+$/, '')}M`
                                    : `$${Math.round(Number(priceInput) / 1_000)}K`}
                                </p>
                            )}
                        </div>

                        {/* Notes */}
                        <div className="space-y-1.5">
                            <label className="text-[10px] font-semibold text-muted-foreground uppercase tracking-widest">
                                Notes (optional)
                            </label>
                            <textarea
                                placeholder="e.g. Based on recent comparable sales, pending renovation..."
                                value={notesInput}
                                onChange={(e) => setNotesInput(e.target.value)}
                                rows={2}
                                maxLength={500}
                                className="w-full rounded-xl border border-border focus:border-primary focus:outline-none px-3.5 py-2.5 text-sm text-foreground resize-none"
                            />
                        </div>

                        {/* Disclaimer */}
                        <div className="flex gap-2 text-[10px] text-muted-foreground leading-relaxed">
                            <AlertCircle className="w-3 h-3 shrink-0 mt-px"/>
                            <span>This is a personal estimate only — not an official valuation. Consult a licensed agent for a formal appraisal.</span>
                        </div>

                        {/* Actions */}
                        <div className="flex gap-3 pt-1">
                            <Button
                                variant="outline"
                                className="flex-1 rounded-xl border-border text-muted-foreground font-bold"
                                onClick={() => setMarketValuePopupOpen(false)}
                            >
                                Cancel
                            </Button>
                            <Button
                                className="flex-1 rounded-xl font-semibold"
                                disabled={!priceInput || Number(priceInput) <= 0 || savingPrice}
                                onClick={saveEstimate}
                            >
                                {savingPrice ? 'Saving…' : 'Save Estimate'}
                            </Button>
                        </div>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default OwnerDashboard;
