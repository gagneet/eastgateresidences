// @featuretrace:levy — Manager/admin finance page: levy ledger table, arrears, fund overview, payments.
// Layer: frontend
// Data flow: FinancePage → GET /unit-levy-ledger, /finance/summary, /levy-payments → unit_levy_ledger + levy_payments (building-scoped).
// Related: backend/routers/finance.py
//           frontend/src/pages/dashboard/UnitFinanceDetailPage.jsx (drill-down per unit)
//           frontend/src/pages/dashboard/ArrearsRecoveryPage.jsx
"use client";

import React, {useCallback, useEffect, useMemo, useState} from 'react';
import {useRouter, useSearchParams} from 'next/navigation';
import {useAuth} from '../../contexts/AuthContext';
import {Card, CardContent, CardDescription, CardHeader, CardTitle} from '../../components/ui/card';
import {Button} from '../../components/ui/button';
import {Badge} from '../../components/ui/badge';
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue,} from '../../components/ui/select';
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow,} from '../../components/ui/table';
import {Tabs, TabsContent, TabsList, TabsTrigger,} from '../../components/ui/tabs';
import {Input} from '../../components/ui/input';
import {
    AlertTriangle,
    ArrowUpDown,
    BarChart3,
    Calculator,
    ChevronDown,
    ChevronRight,
    ChevronUp,
    DollarSign,
    Download,
    Loader2,
    PieChart,
    Search,
    TrendingDown,
    TrendingUp,
    Upload
} from 'lucide-react';
import {formatCurrency} from '../../lib/utils';
import {toast} from 'sonner';
import YearSelector from '../../components/widgets/YearSelector';
import {getAnnualProposedFundIncome} from '../../lib/finance/financeTransforms';
import {
    Bar, BarChart as RechartBar, CartesianGrid, Cell, Label, Legend,
    Pie, PieChart as RechartsPie, ResponsiveContainer, Tooltip as RechartsTooltip,
    XAxis, YAxis,
} from 'recharts';

const EXPENSE_COLORS: string[] = ['#ef4444', '#f97316', '#eab308', '#10b981', '#06b6d4', '#3b82f6', '#8b5cf6', '#ec4899'];
const INCOME_COLORS: string[] = ['#3b82f6', '#14b8a6', '#64748b', '#10b981', '#06b6d4'];

const BreakdownLegend = ({data, colors}: { data: any[]; colors: string[] }) => (
    <div className="max-h-[300px] overflow-y-auto pr-1 space-y-2">
        {data.map((item: any, index: number) => (
            <div key={`${item.name}-${index}`} className="flex items-start justify-between gap-3 text-xs">
                <div className="flex items-start gap-2 min-w-0">
                    <span
                        className="mt-1 h-2.5 w-2.5 rounded-full shrink-0"
                        style={{backgroundColor: colors[index % colors.length]}}
                    />
                    <span className="text-slate-600 leading-snug break-words">{item.name}</span>
                </div>
                <span className="font-semibold tabular-nums text-slate-700 shrink-0">
                    {formatCurrency(item.value || 0)}
                </span>
            </div>
        ))}
    </div>
);
/**
 * @generated FunctionHeader
 * Function: FinancePage
 * Path: frontend/src/pages/dashboard/FinancePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const FinancePage: React.FC = () => {
    const router = useRouter();
    const searchParams = useSearchParams();
    const {api, selectedYear, isManager, isAdmin} = useAuth();
    const [entries, setEntries] = useState<any[]>([]);
    const [summary, setSummary] = useState<any>({});
    const [chartData, setChartData] = useState<any>({
        expense_by_category: [],
        income_by_category: [],
        monthly_trend: []
    });
    const [budgetVsActual, setBudgetVsActual] = useState<any>({administrative: [], sinking: []});
    const [levyData, setLevyData] = useState<any>(null);
    const [transactions, setTransactions] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [portalBankData, setPortalBankData] = useState<any>(null);
    const [kpiContract, setKpiContract] = useState<any>(null);
    const [chartDetailsYear, setChartDetailsYear] = useState<string | null>(null);
    const [levyStatusYear, setLevyStatusYear] = useState<string | null>(null);
    const [allUnitsLoaded, setAllUnitsLoaded] = useState(false);

    const [activeTab, setActiveTab] = useState(searchParams?.get('tab') || 'overview');
    const [allUnits, setAllUnits] = useState<any[]>([]);
    const [levyStatusFilter, setLevyStatusFilter] = useState(searchParams?.get('status') || 'all');
    // Canonical grace-aware set of unit_numbers in arrears (true_arrears > 0), from /arrears/detail.
    // null = not loaded / fetch failed -> getLevyStatus uses its legacy classification.
    // Map of unit_number -> canonical grace-aware true_arrears ($), from /arrears/detail. Used for
    // BOTH the arrears classification AND the displayed arrears amount, so the badge and the amount
    // always come from the same (canonical) source. null = not loaded / fetch failed -> getLevyStatus
    // falls back to its legacy classification. Was a Set — a unit can be in arrears purely from
    // prior-year carry-forward (true_arrears > 0) while its current-year outstanding_due_to_date is
    // $0, so the amount MUST come from true_arrears, not the ledger's due-to-date figure.
    const [arrearsByUnit, setArrearsByUnit] = useState<Map<string, number> | null>(null);
    const [quarterlyBudget, setQuarterlyBudget] = useState<any>(null);

    // Levy status search & sort
    const [levySearch, setLevySearch] = useState('');
    const [levySort, setLevySort] = useState<{ col: string; dir: 'asc' | 'desc' }>({col: 'unit_number', dir: 'asc'});
    const gstLabel = quarterlyBudget?.gst_label || levyData?.gst_label || 'GST (10%)';
    const payableTotalPerUoeAnnual =
        levyData?.total_per_uoe_payable_annual ??
        ((levyData?.admin_per_uoe_annual ?? 0) + (levyData?.sinking_per_uoe_annual ?? 0)) * (1 + (levyData?.gst_rate ?? 0.10));
    const payableTotalPerUoeQuarterly =
        levyData?.total_per_uoe_payable_quarterly ?? (payableTotalPerUoeAnnual / 4);
    const canonicalUnitCount =
        kpiContract?.unit_counts?.canonical_unit_count ??
        summary.ledger_quality?.canonical_unit_count ??
        ((summary.unit_ledger_summary?.units_paid_up || 0) +
            (summary.unit_ledger_summary?.units_owing || 0) +
            (summary.unit_ledger_summary?.units_credit || 0));
    const unitsInArrears = Number(summary.unit_ledger_summary?.units_owing || 0);
    const unitsPaidUpDisplay = canonicalUnitCount
        ? Math.max(0, canonicalUnitCount - unitsInArrears)
        : Number(summary.unit_ledger_summary?.units_paid_up || 0);

    // Transactions search, sort & grouping
    const [txSearch, setTxSearch] = useState('');
    const [txSort, setTxSort] = useState<{ col: string; dir: 'asc' | 'desc' }>({col: 'date', dir: 'desc'});
    const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());

    const fetchData = useCallback(async () => {
        if (!selectedYear) return;
        try {
            setLoading(true);
            const safeGet = async (url: string, fallback: any) => {
                try {
                    const response = await api.get(url);
                    return response?.data ?? fallback;
                } catch (error) {
                    console.error(`Failed to fetch ${url}:`, error);
                    return fallback;
                }
            };
            const [summaryRes, chartsRes, bankRes, kpiRes] = await Promise.all([
                safeGet(`/finance/summary?year=${selectedYear}`, {}),
                safeGet(`/finance/charts?year=${selectedYear}`, {
                    expense_by_category: [],
                    income_by_category: [],
                    monthly_trend: [],
                }),
                safeGet(`/finance/portal-bank-balances`, null),
                safeGet(`/finance/kpi-contract?year=${selectedYear}`, null),
            ]);
            setSummary(summaryRes || {});
            setPortalBankData(bankRes || null);
            setKpiContract(kpiRes || null);

            const expenseByCat = chartsRes?.expense_by_category;
            const flatExpenses = Array.isArray(expenseByCat)
                ? expenseByCat
                : [
                    ...(expenseByCat?.administrative || []),
                    ...(expenseByCat?.sinking || []),
                ];
            setChartData({
                expense_by_category: flatExpenses,
                income_by_category: chartsRes?.income_by_category || [],
                gst_summary: chartsRes?.gst_summary || null,
                // Backend now always returns monthly_trend (quarterly periods with month/income/levies/expenses keys)
                monthly_trend: chartsRes?.monthly_trend || chartsRes?.quarterly_trend || [],
            });
        } catch (error) {
            console.error('Failed to fetch finance data:', error);
        } finally {
            setLoading(false);
        }
    }, [api, selectedYear]);

    const fetchChartDetails = useCallback(async () => {
        if (!selectedYear || chartDetailsYear === selectedYear) return;
        try {
            const [bvaRes, qBudgetRes] = await Promise.all([
                api.get(`/finance/budget-vs-actual?year=${selectedYear}`).catch(() => ({data: {administrative: [], sinking: []}})),
                api.get(`/finance/quarterly-budget?year=${selectedYear}`).catch(() => ({data: null})),
            ]);
            setBudgetVsActual(bvaRes.data || {administrative: [], sinking: []});
            setQuarterlyBudget(qBudgetRes.data || null);
        } finally {
            setChartDetailsYear(selectedYear);
        }
    }, [api, chartDetailsYear, selectedYear]);

    const fetchLevyStatusData = useCallback(async () => {
        if (!selectedYear || levyStatusYear === selectedYear) return;
        try {
            // Keep the Levy Status table off the Financial Overview critical path.
            // These two detail endpoints are only needed once the manager opens that tab.
            const ARREARS_FETCH_FAILED = Symbol('arrears_fetch_failed');
            const [ledgerRes, arrearsDetailRes] = await Promise.all([
                api.get(`/unit-levy-ledger?year=${selectedYear}&limit=200`).then(res => res.data || []).catch(() => []),
                // Canonical grace-aware arrears (unit_arrears_and_credit) — the SAME source the
                // Arrears Recovery board uses, so the Levy Status tab's "arrears" count agrees
                // with /intelligence/debt-recovery by construction instead of re-deriving on a different
                // (grace-unaware) basis.
                api.get(`/arrears/detail?year=${selectedYear}`).then(res => res.data).catch(() => ARREARS_FETCH_FAILED),
            ]);
            setEntries(ledgerRes || []);
            // Build the canonical map unit_number -> true_arrears (grace-aware, > 0). Null on
            // fetch failure -> getLevyStatus keeps its legacy fallback.
            if (arrearsDetailRes === ARREARS_FETCH_FAILED || !Array.isArray(arrearsDetailRes)) {
                setArrearsByUnit(null);
            } else {
                const m = new Map<string, number>();
                for (const r of arrearsDetailRes as any[]) {
                    const ta = Number(r.total_arrears || 0);
                    if (ta > 0.005) m.set(String(r.unit_number), ta);
                }
                setArrearsByUnit(m);
            }
        } finally {
            setLevyStatusYear(selectedYear);
        }
    }, [api, levyStatusYear, selectedYear]);

    const fetchLevyData = useCallback(async () => {
        if (!selectedYear) return;
        setLevyData(null); // reset to show loading state while year changes
        try {
            const response = await api.get(`/levy-calculator?year=${selectedYear}`);
            if (response.data && !response.data.error) {
                setLevyData(response.data);
            } else {
                setLevyData({error: response.data?.error || 'No levy data available', levies: []});
            }
        } catch (error) {
            console.error('Failed to fetch levy data:', error);
            setLevyData({error: 'Failed to load levy data', levies: []});
        }
    }, [api, selectedYear]);

    const fetchAllUnits = useCallback(async () => {
        if (allUnitsLoaded) return;
        try {
            const response = await api.get('/owners-units');
            setAllUnits(response.data || []);
        } catch (error) {
            console.error('Failed to fetch units:', error);
        } finally {
            setAllUnitsLoaded(true);
        }
    }, [allUnitsLoaded, api]);

    const fetchTransactions = useCallback(async () => {
        if (!selectedYear) return;
        try {
            const [expRes, incRes] = await Promise.all([
                api.get(`/expense-transactions?year=${selectedYear}&limit=200`),
                api.get(`/income-transactions?year=${selectedYear}&limit=200`),
            ]);
            const expenses = (expRes.data?.transactions || expRes.data || []).map((t: any) => ({
                ...t,
                tx_type: 'expense'
            }));
            const income = (incRes.data?.transactions || incRes.data || []).map((t: any) => ({
                ...t,
                tx_type: 'income'
            }));
            setTransactions([...income, ...expenses].sort((a, b) => (b.date || b.created_at || '').localeCompare(a.date || a.created_at || '')));
        } catch (err) {
            console.error('Failed to fetch transactions:', err);
        }
    }, [api, selectedYear]);

    useEffect(() => {
        fetchData();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedYear]);

    useEffect(() => {
        setLevyData(null);
        setTransactions([]);
        setEntries([]);
        setArrearsByUnit(null);
        setLevyStatusYear(null);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [selectedYear]);

    useEffect(() => {
        if (activeTab === 'charts') {
            fetchChartDetails();
        }
        if (activeTab === 'levies') {
            fetchLevyData();
        }
        if (activeTab === 'levy-status' && (isManager() || isAdmin())) {
            fetchAllUnits();
            fetchLevyStatusData();
        }
        if (activeTab === 'transactions') {
            fetchTransactions();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeTab, selectedYear]);

    useEffect(() => {
        const tab = searchParams?.get('tab');
        if (tab) {
            setActiveTab(tab);
        }
        const status = searchParams?.get('status');
        if (status) {
            setLevyStatusFilter(status);
        }
    }, [searchParams]);
    /**
     * @generated FunctionHeader
     * Function: handleExport
     * Path: frontend/src/pages/dashboard/FinancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleExport = async () => {
        if (typeof window === 'undefined' || !selectedYear) return;
        try {
            const response = await api.get(`/finance/export?format=csv&year=${selectedYear}`, {responseType: 'blob'});
            const blob = new Blob([response.data], {type: 'text/csv'});
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `finance_export_${selectedYear}.csv`;
            a.click();
            window.URL.revokeObjectURL(url);
            toast.success('Export downloaded');
        } catch (error) {
            toast.error('Failed to export');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleImportDeft
     * Path: frontend/src/pages/dashboard/FinancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleImportDeft = async (e: React.ChangeEvent<HTMLInputElement>) => {
        const file = e.target.files?.[0];
        if (!file || !selectedYear) return;

        const formData = new FormData();
        formData.append('file', file);
        formData.append('year', selectedYear);

        setLoading(true);
        try {
            const res = await api.post('/reconciliation/import-deft', formData, {
                headers: {'Content-Type': 'multipart/form-data'}
            });
            toast.success(`Import complete: ${res.data.imported_count} records processed.`);
            if (res.data.error_count > 0) {
                toast.error(`${res.data.error_count} errors during import.`);
            }
            fetchData();
            fetchLevyData();
        } catch (err) {
            toast.error("Import failed. Ensure CSV format is correct.");
        } finally {
            setLoading(false);
            e.target.value = '';
        }
    };
    /**
     * @generated FunctionHeader
     * Function: getLevyStatus
     * Path: frontend/src/pages/dashboard/FinancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getLevyStatus = (unit: any) => {
        // Arrears is a per-unit, grace-aware, CANONICAL judgement. When the canonical set is
        // loaded, a unit is in arrears iff /arrears/detail (unit_arrears_and_credit) reports
        // true_arrears > 0 for it — identical to the Arrears Recovery board, so the two pages'
        // counts agree. Only fall back to the legacy grace-UNAWARE outstanding_due_to_date basis
        // (which over-counts in-grace units) when that canonical data failed to load.
        const outstandingDue = unit.outstanding_due_to_date ?? Math.max(unit.net_balance ?? 0, 0);
        if (arrearsByUnit !== null) {
            if ((arrearsByUnit.get(String(unit.unit_number)) || 0) > 0.005) return 'arrears';
            // Behind but still WITHIN the grace window — a distinct "reminder" bucket, kept
            // visible (never folded into on_track) so the Strata Manager/EC can chase these
            // owners with a reminder BEFORE the debt becomes formal arrears past grace.
            if (outstandingDue > 0.01) return 'in_grace';
        } else {
            // Legacy fallback (canonical grace-aware set unavailable): can't split grace, so
            // anything due-but-unpaid shows as arrears — the previous behaviour.
            if (outstandingDue > 0.01) return 'arrears';
            if ((unit.opening_arrears ?? 0) > 0.01 && (unit.net_balance ?? 0) > 0.01) return 'arrears';
        }
        const netBalance = unit.net_balance ?? 0;
        const paidDue = unit.paid_due_to_date ?? unit.paid_this_year ?? unit.total_paid ?? 0;
        const leviedDue = unit.levied_due_to_date ?? unit.total_levied ?? 0;
        if (netBalance < -0.01 || paidDue > leviedDue + 0.01) return 'credit';
        const oa = unit.opening_arrears ?? 0;
        const debt = oa - paidDue;
        if (debt > 0.01) return 'balance_due';
        return 'on_track';
    };

    // Merge year-filtered levy ledger entries with owner metadata from allUnits.
    // entries has levy financials for the selected year (total_levied, total_paid, net_balance).
    // allUnits is used only as a unit-list fallback when no ledger data exists for the year;
    // in that case we zero out all financial fields so stale latest-year values are not shown.
    const enrichedEntries = useMemo(() => {
        if (!entries.length && !allUnits.length) return [];

        if (entries.length) {
            // Happy path: ledger data exists for the selected year.
            // owner_name/owner_name_b are now included by the backend; allUnits provides
            // a secondary fallback in case the API returns entries without owner names.
            return entries.map((entry: any) => {
                const unit = allUnits.find((u: any) => u.unit_number === entry.unit_number);
                return {
                    ...entry,
                    owner_name: entry.owner_name || unit?.owner_name || unit?.owner || '',
                    owner_name_b: entry.owner_name_b || unit?.owner_name_b || '',
                };
            });
        }

        // Fallback: no ledger data for the selected year — show the unit list from allUnits
        // but zero out all financial columns so we never display another year's figures.
        return allUnits.map((unit: any) => ({
            ...unit,
            total_levied: 0,
            total_paid: 0,
            net_balance: 0,
            levied_due_to_date: 0,
            paid_due_to_date: 0,
            outstanding_due_to_date: 0,
            paid_this_year: 0,
            annual_total_levied: 0,
            annual_paid_this_year: 0,
            opening_arrears: 0,
            admin_levied: 0,
            admin_paid: 0,
            sinking_levied: 0,
            sinking_paid: 0,
            // Keep owner info from allUnits (current canonical owner)
            owner_name: unit.owner_name || unit.owner || '',
            owner_name_b: unit.owner_name_b || '',
        }));
    }, [entries, allUnits]);

    // Levy Status: filter by status + search, then sort. `paid_up` is a display
    // group, not a stored row status: the overview card counts every unit that
    // is not in arrears, including credit units, so the drill-down must include
    // both on-track and credit rows.
    const filteredSortedUnits = (() => {
        let units = enrichedEntries;
        if (levyStatusFilter === 'paid_up') {
            units = units.filter(u => {
                const status = getLevyStatus(u);
                return status === 'on_track' || status === 'credit';
            });
        } else if (levyStatusFilter !== 'all') {
            units = units.filter(u => getLevyStatus(u) === levyStatusFilter);
        }
        if (levySearch.trim()) {
            const q = levySearch.toLowerCase();
            units = units.filter(u =>
                (u.unit_number || '').toLowerCase().includes(q) ||
                (u.owner_name || '').toLowerCase().includes(q) ||
                (u.owner_name_b || '').toLowerCase().includes(q)
            );
        }
        const {col, dir} = levySort;
        units = [...units].sort((a, b) => {
            let av: any = a[col] ?? '';
            let bv: any = b[col] ?? '';
            if (typeof av === 'string') av = av.toLowerCase();
            if (typeof bv === 'string') bv = bv.toLowerCase();
            if (av < bv) return dir === 'asc' ? -1 : 1;
            if (av > bv) return dir === 'asc' ? 1 : -1;
            return 0;
        });
        return units;
    })();
    /**
     * @generated FunctionHeader
     * Function: toggleLevySort
     * Path: frontend/src/pages/dashboard/FinancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleLevySort = (col: string) => {
        setLevySort(prev => prev.col === col ? {col, dir: prev.dir === 'asc' ? 'desc' : 'asc'} : {col, dir: 'asc'});
    };

    // Transactions: search, sort, then group by category
    const filteredSortedTx = (() => {
        let txs = transactions;
        if (txSearch.trim()) {
            const q = txSearch.toLowerCase();
            txs = txs.filter(t =>
                (t.description || '').toLowerCase().includes(q) ||
                (t.category_name || t.category || '').toLowerCase().includes(q) ||
                (t.supplier_name || '').toLowerCase().includes(q) ||
                (t.notes || '').toLowerCase().includes(q)
            );
        }
        const {col, dir} = txSort;
        txs = [...txs].sort((a, b) => {
            let av: any = col === 'amount' ? (a.amount || 0) : (a[col] ?? '');
            let bv: any = col === 'amount' ? (b.amount || 0) : (b[col] ?? '');
            if (typeof av === 'string') av = av.toLowerCase();
            if (typeof bv === 'string') bv = bv.toLowerCase();
            if (av < bv) return dir === 'asc' ? -1 : 1;
            if (av > bv) return dir === 'asc' ? 1 : -1;
            return 0;
        });
        return txs;
    })();
    /**
     * @generated FunctionHeader
     * Function: toggleTxSort
     * Path: frontend/src/pages/dashboard/FinancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleTxSort = (col: string) => {
        setTxSort(prev => prev.col === col ? {col, dir: prev.dir === 'asc' ? 'desc' : 'asc'} : {col, dir: 'asc'});
    };
    /**
     * @generated FunctionHeader
     * Function: SortIcon
     * Path: frontend/src/pages/dashboard/FinancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const SortIcon = ({col, sortState}: { col: string; sortState: { col: string; dir: 'asc' | 'desc' } }) => {
        if (sortState.col !== col) return <ArrowUpDown className="inline ml-1 h-3 w-3 text-slate-400"/>;
        return sortState.dir === 'asc'
            ? <ChevronUp className="inline ml-1 h-3 w-3 text-indigo-600"/>
            : <ChevronDown className="inline ml-1 h-3 w-3 text-indigo-600"/>;
    };

    // Group transactions by category
    const txGroups = filteredSortedTx.reduce((acc: Record<string, any[]>, tx: any) => {
        const cat = tx.category_name || tx.category || 'Uncategorised';
        if (!acc[cat]) acc[cat] = [];
        acc[cat].push(tx);
        return acc;
    }, {});
    /**
     * @generated FunctionHeader
     * Function: toggleGroup
     * Path: frontend/src/pages/dashboard/FinancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleGroup = (cat: string) => {
        setCollapsedGroups(prev => {
            const next = new Set(prev);
            if (next.has(cat)) next.delete(cat); else next.add(cat);
            return next;
        });
    };

    return (
        <div className="space-y-6" data-testid="finance-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Finance</h1>
                    <p className="text-muted-foreground mt-1">Strata levies, expenses and budget tracking</p>
                </div>
                <div className="flex gap-3 items-center">
                    <YearSelector/>
                    <div className="relative">
                        <input
                            type="file"
                            id="deft-import"
                            className="hidden"
                            accept=".csv"
                            onChange={handleImportDeft}
                        />
                        <Button variant="outline"
                                onClick={() => (document.getElementById('deft-import') as HTMLInputElement).click()}
                                disabled={loading}>
                            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> :
                                <Upload className="mr-2 h-4 w-4"/>}
                            Import DEFT
                        </Button>
                    </div>
                    <Button variant="outline" onClick={handleExport} data-testid="export-btn">
                        <Download className="mr-2 h-4 w-4"/>
                        Export CSV
                    </Button>
                </div>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList
                    className={`grid w-full lg:w-auto lg:grid-cols-none lg:inline-flex ${isManager() || isAdmin() ? 'grid-cols-5' : 'grid-cols-4'}`}>
                    <TabsTrigger value="overview"><BarChart3 className="mr-2 h-4 w-4"/>Overview</TabsTrigger>
                    <TabsTrigger value="charts"><PieChart className="mr-2 h-4 w-4"/>Charts</TabsTrigger>
                    {(isManager() || isAdmin()) && (
                        <TabsTrigger value="levy-status"><TrendingUp className="mr-2 h-4 w-4"/>Levy Status</TabsTrigger>
                    )}
                    <TabsTrigger value="levies"><Calculator className="mr-2 h-4 w-4"/>Levy Calculator</TabsTrigger>
                    <TabsTrigger value="transactions"><DollarSign className="mr-2 h-4 w-4"/>Transactions</TabsTrigger>
                </TabsList>

                <TabsContent value="overview" className="space-y-6">
                    {summary.ledger_quality && summary.ledger_quality.is_unit_count_consistent === false && (
                        <div
                            data-testid="ledger-quality-warning"
                            className="flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50 p-4 text-amber-800"
                        >
                            <AlertTriangle className="h-5 w-5 shrink-0 mt-0.5"/>
                            <div className="text-sm">
                                <p className="font-semibold">Ledger data quality issue detected</p>
                                <p className="mt-1">
                                    {summary.ledger_quality.duplicate_ledger_units?.length > 0 && (
                                        <>{summary.ledger_quality.duplicate_ledger_units.length} duplicate ledger unit(s). </>
                                    )}
                                    {summary.ledger_quality.missing_ledger_units?.length > 0 && (
                                        <>{summary.ledger_quality.missing_ledger_units.length} unit(s) missing a ledger row for {selectedYear}. </>
                                    )}
                                    {summary.ledger_quality.extra_ledger_units?.length > 0 && (
                                        <>{summary.ledger_quality.extra_ledger_units.length} ledger row(s) reference units not in the unit roster. </>
                                    )}
                                    Figures below use the canonical unit count ({summary.ledger_quality.canonical_unit_count}), not the raw ledger row count.
                                </p>
                            </div>
                        </div>
                    )}
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
                        <Card
                            className="card-dashboard border-l-4 border-l-blue-500 cursor-pointer hover:shadow-md transition-shadow"
                            onClick={() => {
                                setActiveTab('charts');
                            }}
                            title="View income breakdown in Charts"
                        >
                            <CardContent className="p-6">
                                <div className="flex items-center justify-between mb-2">
                                    <span className="text-sm text-muted-foreground">Admin Fund Income</span>
                                    <TrendingUp className="h-5 w-5 text-blue-500"/>
                                </div>
                                <p className="text-2xl font-bold text-blue-600">{formatCurrency(getAnnualProposedFundIncome(summary.admin_fund))}</p>
                                <p className="text-xs text-muted-foreground mt-1">
                                    Levy: {formatCurrency(summary.admin_fund?.annual_levy_proposed ?? summary.admin_fund?.proposed_income ?? 0)}
                                    {(summary.admin_fund?.ytd_total_income ?? 0) > 0 ? ` · YTD actual: ${formatCurrency(summary.admin_fund.ytd_total_income)}` : ''}
                                </p>
                                <p className="text-xs text-indigo-500 mt-2 flex items-center gap-1">View
                                    breakdown <ChevronRight className="h-3 w-3"/></p>
                            </CardContent>
                        </Card>
                        <Card
                            className="card-dashboard border-l-4 border-l-purple-500 cursor-pointer hover:shadow-md transition-shadow"
                            onClick={() => {
                                setActiveTab('charts');
                            }}
                            title="View expense breakdown in Charts"
                        >
                            <CardContent className="p-6">
                                <div className="flex items-center justify-between mb-2">
                                    <span className="text-sm text-muted-foreground">Admin Fund Expenses</span>
                                    <TrendingDown className="h-5 w-5 text-purple-500"/>
                                </div>
                                <p className="text-2xl font-bold text-purple-600">{formatCurrency(summary.admin_fund?.total_expenses || 0)}</p>
                                <p className="text-xs text-muted-foreground mt-1">Budgeted: {formatCurrency(summary.admin_fund?.budgeted_expenses || 0)}</p>
                                <p className="text-xs text-indigo-500 mt-2 flex items-center gap-1">View
                                    breakdown <ChevronRight className="h-3 w-3"/></p>
                            </CardContent>
                        </Card>
                        <Card
                            className="card-dashboard border-l-4 border-l-green-500 cursor-pointer hover:shadow-md transition-shadow"
                            onClick={() => {
                                setActiveTab('charts');
                            }}
                            title="View sinking fund breakdown in Charts"
                        >
                            <CardContent className="p-6">
                                <div className="flex items-center justify-between mb-2">
                                    <span className="text-sm text-muted-foreground">Sinking Fund Income</span>
                                    <TrendingUp className="h-5 w-5 text-green-500"/>
                                </div>
                                <p className="text-2xl font-bold text-green-600">{formatCurrency(getAnnualProposedFundIncome(summary.sinking_fund))}</p>
                                <p className="text-xs text-muted-foreground mt-1">
                                    Levy: {formatCurrency(summary.sinking_fund?.annual_levy_proposed ?? summary.sinking_fund?.proposed_income ?? 0)}
                                    {(summary.sinking_fund?.ytd_total_income ?? 0) > 0 ? ` · YTD actual: ${formatCurrency(summary.sinking_fund.ytd_total_income)}` : ''}
                                </p>
                                <p className="text-xs text-indigo-500 mt-2 flex items-center gap-1">View
                                    breakdown <ChevronRight className="h-3 w-3"/></p>
                            </CardContent>
                        </Card>
                        <Card
                            className="card-dashboard border-l-4 border-l-teal-500 cursor-pointer hover:shadow-md transition-shadow"
                            onClick={() => {
                                setActiveTab('charts');
                            }}
                            title="View sinking expense breakdown in Charts"
                        >
                            <CardContent className="p-6">
                                <div className="flex items-center justify-between mb-2">
                                    <span className="text-sm text-muted-foreground">Sinking Fund Expenses</span>
                                    <TrendingDown className="h-5 w-5 text-teal-500"/>
                                </div>
                                <p className="text-2xl font-bold text-teal-600">{formatCurrency(summary.sinking_fund?.total_expenses || 0)}</p>
                                <p className="text-xs text-muted-foreground mt-1">Budgeted: {formatCurrency(summary.sinking_fund?.budgeted_expenses || 0)}</p>
                                <p className="text-xs text-indigo-500 mt-2 flex items-center gap-1">View
                                    breakdown <ChevronRight className="h-3 w-3"/></p>
                            </CardContent>
                        </Card>
                        <Card
                            className="card-dashboard border-l-4 border-l-orange-500 cursor-pointer hover:shadow-md transition-shadow"
                            onClick={() => {
                                setActiveTab('levy-status');
                                setLevyStatusFilter('arrears');
                            }}
                            title="View units in arrears in Levy Status"
                        >
                            <CardContent className="p-6">
                                <div className="flex items-center justify-between mb-2">
                                    <span className="text-sm text-muted-foreground">Current Arrears</span>
                                    {(summary.unit_ledger_summary?.total_outstanding || 0) > 0 ?
                                        <TrendingDown className="h-5 w-5 text-red-500"/> :
                                        <TrendingUp className="h-5 w-5 text-green-500"/>}
                                </div>
                                <p className={`text-2xl font-bold ${(summary.unit_ledger_summary?.total_outstanding || 0) > 0 ? 'text-red-600' : 'text-green-600'}`}>
                                    {formatCurrency(summary.unit_ledger_summary?.total_outstanding || 0)}
                                </p>
                                <p className="text-xs text-muted-foreground mt-1">{summary.unit_ledger_summary?.units_owing || 0} units
                                    in arrears</p>
                                <p className="text-xs text-indigo-500 mt-2 flex items-center gap-1">View
                                    arrears <ChevronRight className="h-3 w-3"/></p>
                            </CardContent>
                        </Card>
                    </div>

                    {summary.unit_ledger_summary && (
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                            <Card
                                className="card-dashboard cursor-pointer hover:shadow-md transition-shadow"
                                onClick={() => setActiveTab('levies')}
                                title="View levy calculator"
                            >
                                <CardContent className="p-6">
                                    <div className="flex items-center justify-between mb-2">
                                        <span
                                            className="text-sm text-muted-foreground">Total Levied ({selectedYear})</span>
                                        <DollarSign className="h-5 w-5 text-primary"/>
                                    </div>
                                    <p className="text-2xl font-bold">
                                        {formatCurrency(summary.unit_ledger_summary.annual_levy_total_inc_gst ?? summary.unit_ledger_summary.annual_levy_total ?? summary.unit_ledger_summary.total_levied)}
                                    </p>
                                    <p className="text-xs text-muted-foreground mt-1">Annual Admin + Sinking + GST levy total</p>
                                    <p className="text-xs text-indigo-500 mt-2 flex items-center gap-1">View levy
                                        calc <ChevronRight className="h-3 w-3"/></p>
                                </CardContent>
                            </Card>
                            <Card
                                className="card-dashboard cursor-pointer hover:shadow-md transition-shadow"
                                onClick={() => router.push("/financials/collection-rate")}
                                title="View collection rate analysis"
                            >
                                <CardContent className="p-6">
                                    <div className="flex items-center justify-between mb-2">
                                        <span className="text-sm text-muted-foreground">Ledger Collected YTD</span>
                                        <TrendingUp className="h-5 w-5 text-green-500"/>
                                    </div>
                                    <p className="text-2xl font-bold text-green-600">
                                        {formatCurrency(summary.unit_ledger_summary?.total_paid ?? 0)}
                                    </p>
                                    {Number(summary.unit_ledger_summary?.raw_total_paid ?? 0) > Number(summary.unit_ledger_summary?.total_paid ?? 0) + 0.01 && (
                                        <p className="text-xs text-slate-500 mt-1">
                                            Raw ledger paid total: {formatCurrency(summary.unit_ledger_summary.raw_total_paid)}
                                        </p>
                                    )}
                                    {(summary.collected_summary?.portal_confirmed_total ?? 0) > 0 && (
                                        <p className="text-xs text-slate-500 mt-1">
                                            incl. {formatCurrency(summary.collected_summary.portal_confirmed_total)} via
                                            portal
                                        </p>
                                    )}
                                    {(summary.collected_summary?.pending_total ?? 0) > 0 && (
                                        <p className="text-xs text-amber-600 mt-1">
                                            + {formatCurrency(summary.collected_summary.pending_total)} pending
                                            verification
                                        </p>
                                    )}
                                    <p className="text-xs text-indigo-500 mt-2 flex items-center gap-1">View
                                        analysis <ChevronRight className="h-3 w-3"/></p>
                                </CardContent>
                            </Card>
                            <Card
                                className="card-dashboard cursor-pointer hover:shadow-md transition-shadow"
                                onClick={() => {
                                    setActiveTab('levy-status');
                                    setLevyStatusFilter('paid_up');
                                }}
                                title="View units paid up in Levy Status"
                            >
                                <CardContent className="p-6">
                                    <div className="flex items-center justify-between mb-2">
                                        <span className="text-sm text-muted-foreground">Units Paid Up</span>
                                        <Calculator className="h-5 w-5 text-blue-500"/>
                                    </div>
                                    <p className="text-2xl font-bold text-blue-600">{unitsPaidUpDisplay}</p>
                                    <p className="text-xs text-muted-foreground mt-1">of {canonicalUnitCount} units</p>
                                    <p className="text-xs text-indigo-500 mt-2 flex items-center gap-1">View
                                        status <ChevronRight className="h-3 w-3"/></p>
                                </CardContent>
                            </Card>
                            <Card
                                className="card-dashboard cursor-pointer hover:shadow-md transition-shadow"
                                onClick={() => {
                                    setActiveTab('levy-status');
                                    setLevyStatusFilter('credit');
                                }}
                                title="View units with a credit balance in Levy Status"
                            >
                                <CardContent className="p-6">
                                    <div className="flex items-center justify-between mb-2">
                                        <span className="text-sm text-muted-foreground">Total Credit (Advance Paid)</span>
                                        <TrendingUp className="h-5 w-5 text-emerald-500"/>
                                    </div>
                                    <p className="text-2xl font-bold text-emerald-600">
                                        {formatCurrency(summary.unit_ledger_summary?.total_credit_amount || 0)}
                                    </p>
                                    <p className="text-xs text-muted-foreground mt-1">
                                        {summary.unit_ledger_summary?.units_credit || 0} units paid in advance — never
                                        offsets other units' arrears
                                    </p>
                                    <p className="text-xs text-indigo-500 mt-2 flex items-center gap-1">View
                                        status <ChevronRight className="h-3 w-3"/></p>
                                </CardContent>
                            </Card>
                        </div>
                    )}

                    {/* Portal Bank Balances — Strata Web scraper snapshot */}
                    {portalBankData?.accounts?.length > 0 && (
                        <Card className="card-dashboard border-l-4 border-l-violet-400">
                            <CardContent className="p-5">
                                <div className="flex flex-wrap items-start justify-between gap-3 mb-4">
                                    <div>
                                        <p className="text-[10px] font-black uppercase tracking-widest text-slate-400">
                                            Bank Account Balances — Portal Snapshot
                                        </p>
                                        {portalBankData.synced_at && (
                                            <p className="text-xs text-slate-400 mt-0.5">
                                                Synced {new Date(portalBankData.synced_at).toLocaleDateString('en-AU', {
                                                day: 'numeric',
                                                month: 'short',
                                                year: 'numeric'
                                            })}
                                            </p>
                                        )}
                                    </div>
                                    <span
                                        className="text-[10px] bg-violet-100 text-violet-700 font-bold px-2 py-1 rounded-full">
                                        Read-only · Strata Web Portal
                                    </span>
                                </div>
                                <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
                                    <div className="p-3 rounded-xl bg-blue-50 border border-blue-100">
                                        <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-1">Admin
                                            Fund</p>
                                        <p className="text-xl font-black text-blue-700">{formatCurrency(portalBankData.totals?.admin_balance ?? 0)}</p>
                                    </div>
                                    <div className="p-3 rounded-xl bg-purple-50 border border-purple-100">
                                        <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-1">Sinking
                                            Fund</p>
                                        <p className="text-xl font-black text-purple-700">{formatCurrency(portalBankData.totals?.sinking_balance ?? 0)}</p>
                                    </div>
                                    <div className="p-3 rounded-xl bg-slate-50 border border-slate-200">
                                        <p className="text-[10px] text-slate-400 uppercase tracking-wide mb-1">Total
                                            Balance</p>
                                        <p className="text-xl font-black text-slate-800">{formatCurrency(portalBankData.totals?.total_balance ?? 0)}</p>
                                    </div>
                                </div>
                                <div className="space-y-1">
                                    {portalBankData.accounts.map((acct: any) => (
                                        <div key={acct.bsb}
                                             className="flex items-center justify-between text-xs text-slate-500 py-1 border-t">
                                            <span className="font-medium">{acct.account_name?.slice(0, 50)}</span>
                                            <span
                                                className="font-bold tabular-nums">{formatCurrency(acct.total_balance ?? 0)}</span>
                                        </div>
                                    ))}
                                </div>
                            </CardContent>
                        </Card>
                    )}

                    <Card className="card-dashboard">
                        <CardHeader>
                            <CardTitle>Quarterly Trend</CardTitle>
                            <CardDescription>Budgeted levies vs actual collected payments vs expenses
                                — {selectedYear}</CardDescription>
                        </CardHeader>
                        <CardContent>
                            <ResponsiveContainer width="100%" height={320} className="mt-2">
                                <RechartBar data={chartData.monthly_trend}>
                                    <CartesianGrid strokeDasharray="3 3" />
                                    <XAxis dataKey="month" />
                                    <YAxis width={55} tickFormatter={(v) => `$${(Number(v) / 1000).toFixed(0)}k`} />
                                    <RechartsTooltip formatter={(v: any) => `$${(Number(v) / 1000).toFixed(0)}k`} />
                                    <Legend />
                                    <Bar dataKey="income" fill="#10b981" name="Income" />
                                    <Bar dataKey="levies" fill="#3b82f6" name="Levies" />
                                    <Bar dataKey="expenses" fill="#ef4444" name="Expenses" />
                                </RechartBar>
                            </ResponsiveContainer>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="charts" className="space-y-6">
                    {/* Paid Additional (Collected in Advance) — metric 3: owner payments received for
                        levy periods NOT yet due. Surfaced as its own figure (never folded into the
                        due-date collection rate). Source: /finance/kpi-contract collection_mix. */}
                    {Number(kpiContract?.collection_mix?.collected_in_advance || 0) > 0.005 && (
                        <Card className="card-dashboard">
                            <CardContent className="p-4 flex items-center justify-between gap-4">
                                <div>
                                    <p className="text-xs font-black uppercase tracking-widest text-slate-400 mb-1">
                                        Paid Additional — Collected in Advance
                                    </p>
                                    <p className="text-sm text-muted-foreground">
                                        Owner payments received for levy periods not yet due — held as credit and
                                        excluded from the due-date collection rate.
                                    </p>
                                </div>
                                <p className="text-2xl font-black text-emerald-600 whitespace-nowrap">
                                    {formatCurrency(Number(kpiContract.collection_mix.collected_in_advance))}
                                </p>
                            </CardContent>
                        </Card>
                    )}
                    {/* Quarterly Budget vs Collected */}
                    {quarterlyBudget?.quarters?.length > 0 && (
                        <Card className="card-dashboard">
                            <CardHeader>
                                <CardTitle>Quarterly Budget vs Collected</CardTitle>
                                <CardDescription>
                                    Per-quarter levy budget (ex-GST + configured GST = inc-GST) vs actual
                                    levied &amp; collected — {selectedYear}.
                                    {quarterlyBudget.gst_registered && (
                                        <span className="ml-1 text-slate-400">GST registered (ACT &gt;$75k/yr).</span>
                                    )}
                                </CardDescription>
                            </CardHeader>
                            <CardContent>
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
                                    {quarterlyBudget.quarters.map((q: any) => (
                                        <div key={q.label} className={`rounded-xl border p-4 ${
                                            q.status === 'overdue' ? 'bg-rose-50 border-rose-200' :
                                                q.status === 'past' ? 'bg-slate-50' :
                                                    q.status === 'due_today' ? 'bg-amber-50 border-amber-200' :
                                                        'bg-blue-50/40'
                                        }`}>
                                            <div className="flex items-center justify-between mb-1">
                                                <p className="font-black text-sm text-slate-700">{q.label}</p>
                                                <Badge variant="outline" className={`text-[10px] ${
                                                    q.status === 'overdue' ? 'border-rose-300 text-rose-700' :
                                                        q.status === 'past' ? 'border-slate-200 text-slate-500' :
                                                            q.status === 'due_today' ? 'border-amber-300 text-amber-700' :
                                                                'border-blue-200 text-blue-600'
                                                }`}>
                                                    {q.status === 'overdue' ? 'Overdue' : q.status === 'past' ? 'Past' : q.status === 'due_today' ? 'Due Today' : 'Upcoming'}
                                                </Badge>
                                            </div>
                                            {q.due_date && (
                                                <p className="text-xs text-slate-400 mb-2">
                                                    Due: {new Date(q.due_date + 'T00:00:00').toLocaleDateString('en-AU', {
                                                    day: 'numeric',
                                                    month: 'short'
                                                })}
                                                </p>
                                            )}
                                            <div className="space-y-1.5">
                                                {/* Budget: ex-GST + GST = inc-GST */}
                                                <div className="flex justify-between text-xs">
                                                    <span className="text-slate-400">Budget (ex-GST)</span>
                                                    <span
                                                        className="font-semibold">{formatCurrency(q.budgeted_income_ex_gst)}</span>
                                                </div>
                                                <div className="flex justify-between text-xs">
                                                    <span className="text-slate-400">+ {gstLabel}</span>
                                                    <span
                                                        className="font-medium text-slate-500">{formatCurrency(q.budgeted_gst)}</span>
                                                </div>
                                                <div
                                                    className="flex justify-between text-xs border-b border-dashed pb-1 mb-1">
                                                    <span className="text-slate-600 font-medium">= Inc-GST</span>
                                                    <span
                                                        className="font-bold">{formatCurrency(q.budgeted_income_inc_gst)}</span>
                                                </div>
                                                {/* Real ledger data — only shown when the quarter has been raised */}
                                                {q.has_ledger_data ? (
                                                    <>
                                                        <div className="flex justify-between text-xs">
                                                            <span className="text-slate-400">Owner Paid / Due</span>
                                                            <span
                                                                className="font-semibold">{formatCurrency(q.levied)}</span>
                                                        </div>
                                                        <div className="flex justify-between text-xs">
                                                            <span className="text-slate-400">Collected</span>
                                                            <span
                                                                className="font-semibold text-emerald-700">{formatCurrency(q.collected)}</span>
                                                        </div>
                                                        {q.outstanding > 0 && (
                                                            <div className="flex justify-between text-xs">
                                                                <span className="text-slate-400">Outstanding</span>
                                                                <span
                                                                    className="font-semibold text-rose-600">{formatCurrency(q.outstanding)}</span>
                                                            </div>
                                                        )}
                                                    </>
                                                ) : (
                                                    <p className="text-[10px] text-slate-400 italic pt-1">
                                                        {q.status === 'upcoming' ? 'Not yet due' : 'Not yet raised in ledger'}
                                                    </p>
                                                )}
                                                {q.portal_pending > 0 && (
                                                    <div className="flex justify-between text-xs">
                                                        <span className="text-slate-400">Portal Pending</span>
                                                        <span
                                                            className="font-semibold text-amber-600">{formatCurrency(q.portal_pending)}</span>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    ))}
                                </div>
                                {/* Annual totals summary row */}
                                {quarterlyBudget.annual_totals && (
                                    <div className="rounded-xl border bg-slate-50 p-4">
                                        <p className="text-xs font-bold text-slate-500 mb-3 uppercase tracking-widest">Annual
                                            Totals — {selectedYear}</p>
                                        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                                            <div>
                                                <p className="text-xs text-slate-400">Admin Fund (ex-GST)</p>
                                                <p className="font-bold">{formatCurrency(quarterlyBudget.annual_totals.admin_ex_gst)}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs text-slate-400">Sinking Fund (ex-GST)</p>
                                                <p className="font-bold">{formatCurrency(quarterlyBudget.annual_totals.sinking_ex_gst)}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs text-slate-400">{gstLabel}</p>
                                                <p className="font-bold text-slate-600">{formatCurrency(quarterlyBudget.annual_totals.gst)}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs text-slate-400">Total Inc-GST</p>
                                                <p className="font-bold">{formatCurrency(quarterlyBudget.annual_totals.total_inc_gst)}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs text-slate-400">YTD Levied</p>
                                                <p className="font-bold">{formatCurrency(quarterlyBudget.annual_totals.total_levied_ytd)}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs text-slate-400">YTD Collected</p>
                                                <p className="font-bold text-emerald-700">{formatCurrency(quarterlyBudget.annual_totals.total_collected_ytd)}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs text-slate-400">YTD Outstanding</p>
                                                <p className="font-bold text-rose-600">{formatCurrency(quarterlyBudget.annual_totals.total_outstanding_ytd)}</p>
                                            </div>
                                            <div>
                                                <p className="text-xs text-slate-400">Expenses Budgeted</p>
                                                <p className="font-bold text-slate-700">{formatCurrency(quarterlyBudget.annual_totals.total_expenses_budgeted)}</p>
                                            </div>
                                        </div>
                                    </div>
                                )}
                            </CardContent>
                        </Card>
                    )}

                    {budgetVsActual.administrative?.length > 0 && (
                        <Card className="card-dashboard">
                            <CardHeader>
                                <CardTitle>Admin Fund: Budget vs. Actual</CardTitle>
                                <CardDescription>Administrative Fund expenses comparison
                                    for {selectedYear}</CardDescription>
                            </CardHeader>
                            <CardContent>
                                <ResponsiveContainer width="100%" height={520} className="mt-2">
                                    <RechartBar data={budgetVsActual.administrative}>
                                        <CartesianGrid strokeDasharray="3 3" />
                                        <XAxis dataKey="category" angle={-40} textAnchor="end" interval={0} height={120} tick={{fontSize: 11}} />
                                        <YAxis width={65} tickFormatter={(v) => formatCurrency(Number(v))} />
                                        <RechartsTooltip formatter={(v: any) => formatCurrency(Number(v))} />
                                        <Legend />
                                        <Bar dataKey="budget" fill="#3b82f6" name="Budget" />
                                        {/* Per-bar colour: a negative actual is money coming BACK to
                                            the fund (e.g. a GST Refund), not spend — show it in
                                            income-green so it reads distinctly from red expenses.
                                            Bar fill stays red so the legend swatch = normal expense. */}
                                        <Bar dataKey="actual" fill="#ef4444" name="Actual">
                                            {(budgetVsActual.administrative as any[]).map((entry: any, i: number) => (
                                                <Cell key={`admin-actual-${i}`} fill={Number(entry.actual) < 0 ? '#10b981' : '#ef4444'} />
                                            ))}
                                        </Bar>
                                    </RechartBar>
                                </ResponsiveContainer>
                            </CardContent>
                        </Card>
                    )}

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        <Card className="card-dashboard">
                            <CardHeader>
                                <CardTitle>Expense Breakdown</CardTitle>
                                <CardDescription>Where the money goes</CardDescription>
                            </CardHeader>
                            <CardContent>
                                <div className="grid grid-cols-1 md:grid-cols-[minmax(240px,1fr)_minmax(220px,0.9fr)] gap-4 items-center">
                                    <ResponsiveContainer width="100%" height={320} className="mt-2">
                                        <RechartsPie>
                                            <Pie data={chartData.expense_by_category} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={68} outerRadius={118}>
                                                <Label value={formatCurrency((chartData.expense_by_category as any[]).reduce((s: number, d: any) => s + (d.value || 0), 0))} position="center" style={{fontSize: 13, fontWeight: 700, fill: '#1e293b'}} />
                                                {(chartData.expense_by_category as any[]).map((_: any, i: number) => (
                                                    <Cell key={i} fill={EXPENSE_COLORS[i % EXPENSE_COLORS.length]} />
                                                ))}
                                            </Pie>
                                            <RechartsTooltip formatter={(v: any) => formatCurrency(Number(v))} />
                                        </RechartsPie>
                                    </ResponsiveContainer>
                                    <BreakdownLegend data={chartData.expense_by_category as any[]} colors={EXPENSE_COLORS}/>
                                </div>
                            </CardContent>
                        </Card>

                        <Card className="card-dashboard">
                            <CardHeader>
                                <CardTitle>Income Sources</CardTitle>
                                <CardDescription>Where the money comes from</CardDescription>
                            </CardHeader>
                            <CardContent>
                                <div className="grid grid-cols-1 md:grid-cols-[minmax(240px,1fr)_minmax(220px,0.9fr)] gap-4 items-center">
                                    <ResponsiveContainer width="100%" height={320} className="mt-2">
                                        <RechartsPie>
                                            <Pie data={chartData.income_by_category} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={68} outerRadius={118}>
                                                <Label value={formatCurrency((chartData.income_by_category as any[]).reduce((s: number, d: any) => s + (d.value || 0), 0))} position="center" style={{fontSize: 13, fontWeight: 700, fill: '#1e293b'}} />
                                                {(chartData.income_by_category as any[]).map((_: any, i: number) => (
                                                    <Cell key={i} fill={INCOME_COLORS[i % INCOME_COLORS.length]} />
                                                ))}
                                            </Pie>
                                            <RechartsTooltip formatter={(v: any) => formatCurrency(Number(v))} />
                                        </RechartsPie>
                                    </ResponsiveContainer>
                                    <BreakdownLegend data={chartData.income_by_category as any[]} colors={INCOME_COLORS}/>
                                </div>
                                {chartData.gst_summary?.gst_registered && (
                                    <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
                                        <div className="flex items-center justify-between gap-3">
                                            <span>{chartData.gst_summary.gst_label} on levy income</span>
                                            <span className="font-semibold tabular-nums">
                                                {formatCurrency(chartData.gst_summary.gst_component || 0)}
                                            </span>
                                        </div>
                                        <p className="mt-1 text-slate-500">
                                            GST is tracked as tax collected on levies, not as an income source.
                                        </p>
                                    </div>
                                )}
                            </CardContent>
                        </Card>
                    </div>
                </TabsContent>

                <TabsContent value="levy-status" className="space-y-6">
                    {(isManager() || isAdmin()) && (
                        <Card className="card-dashboard">
                            <CardHeader>
                                <div className="flex flex-col gap-3">
                                    <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-3">
                                        <div>
                                            <CardTitle>Building-wide Levy Status — FY {selectedYear}</CardTitle>
                                            <CardDescription>
                                                Payment tracking and arrears overview for {selectedYear}
                                                {entries.length === 0 && allUnits.length > 0 && (
                                                    <span className="ml-2 text-amber-600 font-medium">
                                                    (no ledger data for {selectedYear} — financial columns show $0)
                                                </span>
                                                )}
                                            </CardDescription>
                                        </div>
                                        <Select value={levyStatusFilter} onValueChange={setLevyStatusFilter}>
                                            <SelectTrigger className="w-[160px] shrink-0">
                                                <SelectValue placeholder="All statuses"/>
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="all">All Units</SelectItem>
                                                <SelectItem value="arrears">Arrears (past grace)</SelectItem>
                                                <SelectItem value="in_grace">In Grace (reminder)</SelectItem>
                                                <SelectItem value="balance_due">Balance Due</SelectItem>
                                                <SelectItem value="credit">Credit</SelectItem>
                                                <SelectItem value="paid_up">Paid Up</SelectItem>
                                                <SelectItem value="on_track">On Track</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    {/* Live count of units matching the active status filter (+search). */}
                                    <div className="text-sm text-muted-foreground" data-testid="levy-status-filter-count">
                                        {(() => {
                                            // Total $ for the units matching the active filter: outstanding for
                                            // behind units, credit for credit units. Same per-unit basis as the rows.
                                            let outstanding = 0, credit = 0;
                                            for (const u of filteredSortedUnits) {
                                                const st = getLevyStatus(u);
                                                const dueBalance = u.outstanding_due_to_date ?? Math.max(u.net_balance ?? 0, 0);
                                                if (st === 'arrears') {
                                                    // canonical true_arrears (matches the row + the Arrears board), not
                                                    // the ledger's due-to-date figure (which is $0 for prior-year arrears)
                                                    outstanding += arrearsByUnit?.get(String(u.unit_number)) ?? Math.max(dueBalance, 0);
                                                } else if (st === 'in_grace' || st === 'balance_due') {
                                                    outstanding += Math.max(dueBalance, 0);
                                                } else if (st === 'credit') {
                                                    const nb = u.net_balance ?? 0;
                                                    const paidDue = u.paid_due_to_date ?? u.paid_this_year ?? u.total_paid ?? 0;
                                                    const leviedDue = u.levied_due_to_date ?? u.total_levied ?? 0;
                                                    credit += Math.max(Math.abs(nb), paidDue - leviedDue);
                                                }
                                            }
                                            return (
                                                <>
                                                    <span className="font-semibold text-slate-700">{filteredSortedUnits.length}</span>{' '}
                                                    {filteredSortedUnits.length === 1 ? 'unit' : 'units'}
                                                    {levyStatusFilter !== 'all' && (
                                                        <> in <span className="capitalize">{levyStatusFilter.replace(/_/g, ' ')}</span></>
                                                    )}
                                                    {levySearch.trim() && ' matching your search'}
                                                    {outstanding > 0.005 && (
                                                        <> · <span className="font-semibold text-slate-700">{formatCurrency(outstanding)}</span> outstanding</>
                                                    )}
                                                    {credit > 0.005 && (
                                                        <> · <span className="font-semibold text-slate-700">{formatCurrency(credit)}</span> in credit</>
                                                    )}
                                                </>
                                            );
                                        })()}
                                    </div>
                                    <div className="relative">
                                        <Search
                                            className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                                        <Input
                                            placeholder="Search by unit, owner name…"
                                            value={levySearch}
                                            onChange={e => setLevySearch(e.target.value)}
                                            className="pl-9"
                                        />
                                    </div>
                                </div>
                            </CardHeader>
                            <CardContent>
                                <div className="overflow-x-auto">
                                    <Table>
                                        <TableHeader>
                                            <TableRow>
                                                <TableHead className="cursor-pointer select-none"
                                                           onClick={() => toggleLevySort('unit_number')}>
                                                    Unit <SortIcon col="unit_number" sortState={levySort}/>
                                                </TableHead>
                                                <TableHead className="cursor-pointer select-none"
                                                           onClick={() => toggleLevySort('owner_name')}>
                                                    Owner <SortIcon col="owner_name" sortState={levySort}/>
                                                </TableHead>
                                                <TableHead className="text-right cursor-pointer select-none"
                                                           onClick={() => toggleLevySort('levied_due_to_date')}>
                                                    Levied to Date ({selectedYear}) <SortIcon col="levied_due_to_date"
                                                                                            sortState={levySort}/>
                                                </TableHead>
                                                <TableHead className="text-right cursor-pointer select-none"
                                                           onClick={() => toggleLevySort('paid_due_to_date')}>
                                                    Paid to Date ({selectedYear}) <SortIcon col="paid_due_to_date"
                                                                                          sortState={levySort}/>
                                                </TableHead>
                                                <TableHead className="text-right cursor-pointer select-none"
                                                           onClick={() => toggleLevySort('outstanding_due_to_date')}>
                                                    Due Balance <SortIcon col="outstanding_due_to_date" sortState={levySort}/>
                                                </TableHead>
                                                <TableHead className="text-center">Status</TableHead>
                                                <TableHead className="text-right">Actions</TableHead>
                                            </TableRow>
                                        </TableHeader>
                                        <TableBody>
                                            {filteredSortedUnits.length === 0 ? (
                                                <TableRow>
                                                    <TableCell colSpan={7}
                                                               className="text-center py-8 text-muted-foreground">
                                                        No units match the current filter.
                                                    </TableCell>
                                                </TableRow>
                                            ) : filteredSortedUnits.map((unit) => {
                                                const dueBalance = unit.outstanding_due_to_date ?? Math.max(unit.net_balance ?? 0, 0);
                                                const paidDue = unit.paid_due_to_date ?? unit.paid_this_year ?? unit.total_paid ?? 0;
                                                const leviedDue = unit.levied_due_to_date ?? unit.total_levied ?? 0;
                                                const netBalance = unit.net_balance ?? 0;
                                                // Single classification source (getLevyStatus) for the badge, the amount
                                                // colour, the filter and the count — so they can never disagree. 'arrears'
                                                // = past grace (formal notice); 'in_grace' = due but within grace (reminder);
                                                // both owe money now.
                                                const status = getLevyStatus(unit);
                                                const isArrears = status === 'arrears';
                                                const isInGrace = status === 'in_grace';
                                                const isBehind = isArrears || isInGrace;
                                                const hasCredit = status === 'credit';
                                                // Arrears AMOUNT comes from the same canonical source as the arrears
                                                // classification (true_arrears via /arrears/detail), NOT the ledger's
                                                // outstanding_due_to_date — which is $0 for a unit whose arrears is
                                                // prior-year carry-forward, and was showing $0.00 next to an Arrears
                                                // badge. In-grace units still show their current due-to-date amount.
                                                const arrearsAmt = arrearsByUnit?.get(String(unit.unit_number)) ?? dueBalance;
                                                const behindAmt = isArrears ? arrearsAmt : dueBalance;
                                                return (
                                                    <TableRow
                                                        key={unit.id || `${unit.unit_number}-${unit.year || selectedYear}`}>
                                                        <TableCell
                                                            className="font-medium">{unit.unit_number}</TableCell>
                                                        <TableCell>
                                                            {unit.owner_name}{unit.owner_name_b ? ` & ${unit.owner_name_b}` : ''}
                                                        </TableCell>
                                                        <TableCell
                                                            className="text-right">{formatCurrency(leviedDue)}</TableCell>
                                                        <TableCell
                                                            className="text-right">{formatCurrency(paidDue)}</TableCell>
                                                        <TableCell
                                                            className={`text-right font-semibold ${isArrears ? 'text-red-600' : isInGrace ? 'text-amber-600' : hasCredit ? 'text-green-600' : ''}`}>
                                                            {isBehind ? formatCurrency(behindAmt) : hasCredit ? `${formatCurrency(Math.max(Math.abs(netBalance), paidDue - leviedDue))} CR` : '$0.00'}
                                                        </TableCell>
                                                        <TableCell className="text-center">
                                                            {isArrears ? (
                                                                <Badge variant="destructive">Arrears</Badge>
                                                            ) : isInGrace ? (
                                                                <Badge variant="outline"
                                                                       className="bg-amber-50 text-amber-700 border-amber-200">In
                                                                    Grace</Badge>
                                                            ) : hasCredit ? (
                                                                <Badge variant="outline"
                                                                       className="bg-blue-50 text-blue-700 border-blue-200">Credit</Badge>
                                                            ) : (
                                                                <Badge variant="outline"
                                                                       className="bg-green-50 text-green-700 border-green-200">On
                                                                    Track</Badge>
                                                            )}
                                                        </TableCell>
                                                        <TableCell className="text-right">
                                                            <Button variant="ghost" size="sm"
                                                                    onClick={() => router.push(`/financials/unit/${unit.unit_number}`)}>Details</Button>
                                                        </TableCell>
                                                    </TableRow>
                                                );
                                            })}
                                        </TableBody>
                                    </Table>
                                </div>
                                {filteredSortedUnits.length > 0 && (
                                    <p className="text-xs text-muted-foreground mt-3">{filteredSortedUnits.length} unit{filteredSortedUnits.length !== 1 ? 's' : ''} shown</p>
                                )}
                            </CardContent>
                        </Card>
                    )}
                </TabsContent>

                <TabsContent value="levies" className="space-y-6">
                    {!levyData ? (
                        <div className="flex justify-center items-center h-32">
                            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground"/>
                        </div>
                    ) : levyData?.error ? (
                        <Card className="card-dashboard">
                            <CardContent className="p-6 text-center text-muted-foreground">
                                No levy data available for {selectedYear}.
                            </CardContent>
                        </Card>
                    ) : levyData ? (
                        <>
                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                <Card className="card-dashboard">
                                    <CardContent className="p-6">
                                        <p className="text-sm text-muted-foreground mb-1">Total Annual Budget —
                                            FY {selectedYear}</p>
                                        <p className="text-2xl font-bold">{formatCurrency(levyData.total_budget)}</p>
                                    </CardContent>
                                </Card>
                                <Card className="card-dashboard">
                                    <CardContent className="p-6">
                                        <p className="text-sm text-muted-foreground mb-1">Admin Fund —
                                            FY {selectedYear}</p>
                                        <p className="text-2xl font-bold">{formatCurrency(levyData.admin_fund_total)}</p>
                                    </CardContent>
                                </Card>
                                <Card className="card-dashboard">
                                    <CardContent className="p-6">
                                        <p className="text-sm text-muted-foreground mb-1">Sinking Fund —
                                            FY {selectedYear}</p>
                                        <p className="text-2xl font-bold">{formatCurrency(levyData.sinking_fund_total)}</p>
                                    </CardContent>
                                </Card>
                            </div>

                            {/* Class A/B split summary — shown only when split is active */}
                            {levyData.split_summary?.split_active && (
                                <Card className="card-dashboard border-violet-200">
                                    <CardHeader className="pb-3">
                                        <CardTitle
                                            className="text-sm font-semibold text-violet-800 flex items-center gap-2">
                                            <span>⚖</span> Class A/B Scheme Split — Levy Distribution
                                        </CardTitle>
                                    </CardHeader>
                                    <CardContent>
                                        <div className="overflow-x-auto">
                                            <Table>
                                                <TableHeader>
                                                    <TableRow>
                                                        <TableHead>Class</TableHead>
                                                        <TableHead className="text-right">Units</TableHead>
                                                        <TableHead className="text-right">Total UOE</TableHead>
                                                        <TableHead className="text-right">Admin Annual</TableHead>
                                                        <TableHead className="text-right">Sinking Annual</TableHead>
                                                        <TableHead className="text-right">Total Annual</TableHead>
                                                    </TableRow>
                                                </TableHeader>
                                                <TableBody>
                                                    {Object.entries(levyData.split_summary.classes as Record<string, any>).map(([cls, row]: [string, any]) => (
                                                        <TableRow key={cls}>
                                                            <TableCell>
                                <span
                                    className={`inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium border ${
                                        cls === 'A' ? 'bg-amber-50 text-amber-800 border-amber-200' :
                                            cls === 'B' ? 'bg-teal-50 text-teal-800 border-teal-200' :
                                                'bg-slate-50 text-slate-600 border-slate-200'
                                    }`}>
                                  {cls === 'unassigned' ? 'Unassigned' : `Class ${cls}`}
                                </span>
                                                            </TableCell>
                                                            <TableCell
                                                                className="text-right">{row.unit_count}</TableCell>
                                                            <TableCell
                                                                className="text-right">{row.total_uoe}</TableCell>
                                                            <TableCell
                                                                className="text-right">{formatCurrency(row.admin_annual)}</TableCell>
                                                            <TableCell
                                                                className="text-right">{formatCurrency(row.sinking_annual)}</TableCell>
                                                            <TableCell
                                                                className="text-right font-semibold">{formatCurrency(row.total_annual)}</TableCell>
                                                        </TableRow>
                                                    ))}
                                                </TableBody>
                                            </Table>
                                        </div>
                                    </CardContent>
                                </Card>
                            )}

                            {/* Payment schedule */}
                            {levyData.payment_schedule && levyData.payment_schedule.length > 0 && (
                                <Card className="card-dashboard">
                                    <CardHeader className="pb-3">
                                        <CardTitle className="text-sm font-semibold">Payment Schedule
                                            — {levyData.year}</CardTitle>
                                    </CardHeader>
                                    <CardContent>
                                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                                            {levyData.payment_schedule.map((p: any) => (
                                                <div key={p.quarter} className="border rounded p-3 text-center">
                                                    <p className="text-xs text-muted-foreground">{p.quarter}</p>
                                                    <p className="font-semibold text-sm">{p.due_date || '—'}</p>
                                                </div>
                                            ))}
                                        </div>
                                    </CardContent>
                                </Card>
                            )}

                            {/* Rate per UOE — financial/treasurer view shows ex-GST rates */}
                            {(levyData.admin_per_uoe_annual > 0 || levyData.sinking_per_uoe_annual > 0) && (
                                <Card className="card-dashboard">
                                    <CardHeader className="pb-3">
                                        <CardTitle className="text-sm font-semibold">Rate per Unit of Entitlement
                                            (UOE)</CardTitle>
                                        <p className="text-xs text-muted-foreground">ex-GST — owners
                                            pay {levyData.gst_label || 'plus GST'} on top</p>
                                    </CardHeader>
                                    <CardContent>
                                        <div className="grid grid-cols-3 gap-4 text-sm">
                                            <div>
                                                <p className="text-muted-foreground text-xs mb-1">Admin Fund</p>
                                                <p className="font-bold">{formatCurrency(levyData.admin_per_uoe_annual)}<span
                                                    className="font-normal text-xs text-muted-foreground"> /UOE/yr</span>
                                                </p>
                                                <p className="text-xs text-muted-foreground">{formatCurrency(levyData.admin_per_uoe_annual / 4)} /qtr</p>
                                            </div>
                                            <div>
                                                <p className="text-muted-foreground text-xs mb-1">Sinking Fund</p>
                                                <p className="font-bold">{formatCurrency(levyData.sinking_per_uoe_annual)}<span
                                                    className="font-normal text-xs text-muted-foreground"> /UOE/yr</span>
                                                </p>
                                                <p className="text-xs text-muted-foreground">{formatCurrency(levyData.sinking_per_uoe_annual / 4)} /qtr</p>
                                            </div>
                                            <div>
                                                <p className="text-muted-foreground text-xs mb-1">Total (incl. GST)</p>
                                                <p className="font-bold text-emerald-700">{formatCurrency(payableTotalPerUoeAnnual)}<span
                                                    className="font-normal text-xs text-muted-foreground"> /UOE/yr</span>
                                                </p>
                                                <p className="text-xs text-muted-foreground">{formatCurrency(payableTotalPerUoeQuarterly)} /qtr</p>
                                            </div>
                                        </div>
                                    </CardContent>
                                </Card>
                            )}

                            {/* Per-unit levy breakdown */}
                            {levyData.levies && levyData.levies.length > 0 && (
                                <Card className="card-dashboard">
                                    <CardHeader className="pb-3">
                                        <CardTitle className="text-sm font-semibold">Per-Unit Levy Breakdown</CardTitle>
                                    </CardHeader>
                                    <CardContent>
                                        <div className="overflow-x-auto">
                                            <Table>
                                                <TableHeader>
                                                    <TableRow>
                                                        <TableHead>Unit</TableHead>
                                                        <TableHead>Type</TableHead>
                                                        <TableHead className="text-right">UOE</TableHead>
                                                        <TableHead className="text-right">Admin Annual</TableHead>
                                                        <TableHead className="text-right">Sinking Annual</TableHead>
                                                        <TableHead className="text-right">Total Annual</TableHead>
                                                        <TableHead className="text-right">Total Quarterly</TableHead>
                                                    </TableRow>
                                                </TableHeader>
                                                <TableBody>
                                                    {levyData.levies.map((row: any) => (
                                                        <TableRow key={row.unit_number}>
                                                            <TableCell
                                                                className="font-medium">{row.unit_number}</TableCell>
                                                            <TableCell
                                                                className="capitalize text-xs text-muted-foreground">{row.unit_type || '—'}</TableCell>
                                                            <TableCell
                                                                className="text-right text-xs">{row.uoe}</TableCell>
                                                            <TableCell
                                                                className="text-right">{formatCurrency(row.admin_annual)}</TableCell>
                                                            <TableCell
                                                                className="text-right">{formatCurrency(row.sinking_annual)}</TableCell>
                                                            <TableCell
                                                                className="text-right font-semibold">{formatCurrency(row.total_annual)}</TableCell>
                                                            <TableCell
                                                                className="text-right">{formatCurrency(row.total_quarterly)}</TableCell>
                                                        </TableRow>
                                                    ))}
                                                </TableBody>
                                            </Table>
                                        </div>
                                    </CardContent>
                                </Card>
                            )}
                        </>
                    ) : null}
                </TabsContent>

                <TabsContent value="transactions">
                    <Card className="card-dashboard">
                        <CardHeader>
                            <div className="flex flex-col gap-3">
                                <div className="flex items-center justify-between">
                                    <div>
                                        <CardTitle>Financial Transactions — {selectedYear}</CardTitle>
                                        <CardDescription className="mt-1">Grouped by category. All recorded income and
                                            expense transactions.</CardDescription>
                                    </div>
                                    <Button variant="outline" size="sm" onClick={fetchTransactions}>Refresh</Button>
                                </div>
                                <div className="relative">
                                    <Search
                                        className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                                    <Input
                                        placeholder="Search description, category, supplier…"
                                        value={txSearch}
                                        onChange={e => setTxSearch(e.target.value)}
                                        className="pl-9"
                                    />
                                </div>
                            </div>
                        </CardHeader>
                        <CardContent>
                            {filteredSortedTx.length === 0 ? (
                                <div className="py-12 text-center text-muted-foreground">
                                    <p className="font-medium">{transactions.length === 0 ? `No transactions recorded for FY ${selectedYear}` : 'No transactions match your search.'}</p>
                                    {transactions.length === 0 && (
                                        <p className="text-sm mt-1">
                                            {selectedYear && parseInt(selectedYear) < 2025
                                                ? `Transaction records are available from FY 2025 onwards. Historical years (2021–2024) may not have digital records entered yet.`
                                                : 'Income and expense transactions will appear here once entered.'}
                                        </p>
                                    )}
                                </div>
                            ) : (
                                <div className="space-y-2">
                                    {/* Column header with sort */}
                                    <div
                                        className="hidden md:grid grid-cols-[1fr_80px_2fr_100px_80px] gap-2 px-3 py-1.5 text-xs font-bold text-muted-foreground uppercase tracking-wide border-b">
                                        <button className="text-left flex items-center gap-1"
                                                onClick={() => toggleTxSort('date')}>
                                            Date <SortIcon col="date" sortState={txSort}/>
                                        </button>
                                        <button className="text-left flex items-center gap-1"
                                                onClick={() => toggleTxSort('tx_type')}>
                                            Type <SortIcon col="tx_type" sortState={txSort}/>
                                        </button>
                                        <button className="text-left flex items-center gap-1"
                                                onClick={() => toggleTxSort('description')}>
                                            Description <SortIcon col="description" sortState={txSort}/>
                                        </button>
                                        <button className="text-left flex items-center gap-1"
                                                onClick={() => toggleTxSort('fund_type')}>
                                            Fund <SortIcon col="fund_type" sortState={txSort}/>
                                        </button>
                                        <button className="text-right flex items-center justify-end gap-1 w-full"
                                                onClick={() => toggleTxSort('amount')}>
                                            Amount <SortIcon col="amount" sortState={txSort}/>
                                        </button>
                                    </div>

                                    {/* Grouped rows */}
                                    {Object.entries(txGroups).map(([cat, txList]) => {
                                        const isCollapsed = collapsedGroups.has(cat);
                                        const groupTotal = (txList as any[]).reduce((s, t) => s + (t.tx_type === 'income' ? (t.amount || 0) : -(t.amount || 0)), 0);
                                        return (
                                            <div key={cat} className="rounded-lg border overflow-hidden">
                                                <button
                                                    className="w-full flex items-center justify-between px-4 py-2.5 bg-slate-50 hover:bg-slate-100 transition-colors"
                                                    onClick={() => toggleGroup(cat)}
                                                >
                                                    <div className="flex items-center gap-2">
                                                        {isCollapsed ?
                                                            <ChevronRight className="h-4 w-4 text-slate-400"/> :
                                                            <ChevronDown className="h-4 w-4 text-slate-400"/>}
                                                        <span className="font-semibold text-sm">{cat}</span>
                                                        <Badge variant="outline"
                                                               className="text-xs">{(txList as any[]).length}</Badge>
                                                    </div>
                                                    <span
                                                        className={`text-sm font-bold tabular-nums ${groupTotal >= 0 ? 'text-emerald-700' : 'text-rose-600'}`}>
                                                        {groupTotal >= 0 ? '+' : ''}{formatCurrency(Math.abs(groupTotal))}
                                                    </span>
                                                </button>
                                                {!isCollapsed && (
                                                    <Table>
                                                        <TableBody>
                                                            {(txList as any[]).map((tx: any, i: number) => (
                                                                <TableRow key={tx.id || i} className="text-sm">
                                                                    <TableCell
                                                                        className="text-muted-foreground whitespace-nowrap w-28">
                                                                        {tx.date ? new Date(tx.date).toLocaleDateString('en-AU', {
                                                                            day: '2-digit',
                                                                            month: 'short',
                                                                            year: 'numeric'
                                                                        }) : '—'}
                                                                    </TableCell>
                                                                    <TableCell className="w-24">
                                                                        <span
                                                                            className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ${tx.tx_type === 'income' ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'}`}>
                                                                            {tx.tx_type === 'income' ? 'Income' : 'Expense'}
                                                                        </span>
                                                                    </TableCell>
                                                                    <TableCell
                                                                        className="font-medium">{tx.description || tx.supplier_name || tx.notes || '—'}</TableCell>
                                                                    <TableCell
                                                                        className="text-muted-foreground capitalize text-xs">{tx.fund_type || tx.fund || '—'}</TableCell>
                                                                    <TableCell
                                                                        className={`text-right font-bold tabular-nums ${tx.tx_type === 'income' ? 'text-emerald-600' : 'text-rose-600'}`}>
                                                                        {tx.tx_type === 'income' ? '+' : '-'}{formatCurrency(Math.abs(tx.amount || 0))}
                                                                    </TableCell>
                                                                </TableRow>
                                                            ))}
                                                        </TableBody>
                                                    </Table>
                                                )}
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                            {filteredSortedTx.length > 0 && (
                                <p className="text-xs text-muted-foreground mt-3">
                                    {filteredSortedTx.length} transaction{filteredSortedTx.length !== 1 ? 's' : ''} across {Object.keys(txGroups).length} categor{Object.keys(txGroups).length !== 1 ? 'ies' : 'y'}
                                </p>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
};

export default FinancePage;
