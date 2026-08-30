// @featuretrace:levy — Spending Categories page: per-fund levy expense categories with proposed budget (stored/portal/fund-level) vs actual + variance.
// Layer: frontend
import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../components/ui/table';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '../../components/ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger, } from '../../components/ui/tabs';
import {
    Building,
    DollarSign,
    Hammer,
    Info,
    Pencil,
    PiggyBank,
    Plus,
    Search,
    Trash2,
    TrendingDown,
    TrendingUp,
    Wallet,
} from 'lucide-react';
import { formatCurrency } from '../../lib/utils';
import { toast } from 'sonner';
import YearSelector from '../../components/widgets/YearSelector';
/**
 * SpendingCategoriesPage
 *
 * Displays levy expense categories (admin + sinking funds) for a selected year.
 * - All users with can_view_finances can view
 * - Super Admin, Chairman, Strata Manager can add and edit
 * - Super Admin only can delete
 *
 * API endpoints used:
 *   GET  /years
 *   GET  /levy-categories?year={year}
 *   POST /levy-categories
 *   PUT  /levy-categories/{id}
 *   DELETE /levy-categories/{id}
 */
const SpendingCategoriesPage = () => {
    const {user, api, hasPermission, selectedYear} = useAuth();

    // ─── Categories ─────────────────────────────────────────────────────────────
    const [categories, setCategories] = useState([]);
    const [loading, setLoading] = useState(true);

    // ─── Search ─────────────────────────────────────────────────────────────────
    const [searchTerm, setSearchTerm] = useState('');

    // ─── Edit / Add modal ───────────────────────────────────────────────────────
    const EMPTY_FORM = {
        name: '',
        fund_type: 'administrative',
        budgeted_amount: '',
        actual_amount: '0',
        description: '',
    };
    const [editModal, setEditModal] = useState({open: false, category: null});
    const [formData, setFormData] = useState(EMPTY_FORM);
    const [saving, setSaving] = useState(false);

    // ─── Delete confirmation ─────────────────────────────────────────────────────
    const [deleteConfirm, setDeleteConfirm] = useState({open: false, category: null});
    const [deleting, setDeleting] = useState(false);

    // ─── Permissions ─────────────────────────────────────────────────────────────
    const canManage = hasPermission('can_manage_finances');
    const canDelete = user?.role === 'super_admin';

    // ─── Portal actuals (Strata Web scraper snapshot — read-only cross-check) ────────
    const [portalActuals, setPortalActuals] = useState([]);

    // ─── Fund-level proposed budget (GAP-FIN-041) ────────────────────────────────
    // For imported buildings the proposed budget is stored only at FUND level in annual_levies
    // (no per-category breakdown), so the per-category budgeted_amount column is a genuine $0.
    // This summary surfaces the honest fund-level proposed budget + variance from the backend.
    const [budgetSummary, setBudgetSummary] = useState(null);

    // ─── Fetch categories + portal actuals + budget summary in parallel ──────────
    const fetchCategories = useCallback(async () => {
        if (!selectedYear) return;
        try {
            setLoading(true);
            const [catRes, portalRes, summaryRes] = await Promise.allSettled([
                api.get(`/levy-categories?year=${selectedYear}`),
                api.get(`/finance/portal-actuals?year=${selectedYear}`),
                api.get(`/levy-categories/budget-summary?year=${selectedYear}`),
            ]);
            if (catRes.status === 'fulfilled') {
                setCategories(catRes.value.data || []);
            } else {
                // Do NOT silently blank the page on a real backend error. The previous behaviour
                // coerced a rejected request (e.g. a 500) into [], rendering a misleading
                // "no categories found" empty state with no error — which is exactly how the
                // datetime→str validation 500 on /levy-categories went unnoticed. Surface it.
                console.error('Failed to load /levy-categories', catRes.reason);
                setCategories([]);
                toast.error('Could not load spending categories — please retry.');
            }
            // Portal actuals are a supplemental cross-check only; their absence is not an error.
            setPortalActuals(
                portalRes.status === 'fulfilled'
                    ? ( portalRes.value.data?.categories || [] )
                    : []
            );
            // Budget summary is supplemental; absence just means the SummaryStrip falls back to
            // the per-category budget totals.
            setBudgetSummary(
                summaryRes.status === 'fulfilled' ? ( summaryRes.value.data || null ) : null
            );
        } catch (err) {
            toast.error('Failed to load spending categories');
        } finally {
            setLoading(false);
        }
    }, [api, selectedYear]);

    useEffect(() => {
        fetchCategories();
    }, [fetchCategories]);

    // ─── Filter helpers ─────────────────────────────────────────────────────────
    const filterByFund = useCallback((fundType) => {
        return categories.filter(c =>
            c.fund_type === fundType &&
            ( !searchTerm || [c.name, c.description || ''].join(' ').toLowerCase().includes(searchTerm.toLowerCase()) )
        );
    }, [categories, searchTerm]);

    const adminCats = filterByFund('administrative');
    const sinkingCats = filterByFund('sinking');
    /**
     * @generated FunctionHeader
     * Function: getTotal
     * Path: frontend/src/pages/dashboard/SpendingCategoriesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getTotal = (cats, field) => cats.reduce((sum, c) => sum + ( c[ field ] || 0 ), 0);

    // ─── Per-category proposed budget resolution (GAP-FIN-041 follow-up) ──────────
    // The stored per-category `budgeted_amount` is a genuine $0 for imported buildings (the AGM
    // PDF import lands actuals only). The Strata Web portal scrape, however, DOES carry a real
    // per-category proposed figure in `planned` (surfaced via /finance/portal-actuals). So the
    // honest per-category proposed budget is: the stored itemised budget when it exists, else the
    // portal's `planned` for the same category. Neither is fabricated — both are real source data.
    // `none` = genuinely no proposed budget on record (shown as "—", never a misleading $0).
    const resolveCategoryBudget = (cat, portalMap) => {
        const stored = Number(cat.budgeted_amount) || 0;
        if (stored > 0) return {amount: stored, source: 'stored'};
        const portalRow = portalMap ? portalMap.get(cat.name.toLowerCase()) : null;
        const planned = portalRow && portalRow.planned != null ? Number(portalRow.planned) : 0;
        if (planned > 0) return {amount: planned, source: 'portal'};
        return {amount: 0, source: 'none'};
    };

    // ─── Open modals ─────────────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: openEdit
     * Path: frontend/src/pages/dashboard/SpendingCategoriesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openEdit = (category) => {
        setFormData({
            name: category.name,
            fund_type: category.fund_type,
            budgeted_amount: String(category.budgeted_amount ?? ''),
            actual_amount: String(category.actual_amount ?? '0'),
            description: category.description || '',
        });
        setEditModal({open: true, category});
    };
    /**
     * @generated FunctionHeader
     * Function: openAdd
     * Path: frontend/src/pages/dashboard/SpendingCategoriesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openAdd = (fundType = 'administrative') => {
        setFormData({...EMPTY_FORM, fund_type: fundType});
        setEditModal({open: true, category: null});
    };
    /**
     * @generated FunctionHeader
     * Function: closeEdit
     * Path: frontend/src/pages/dashboard/SpendingCategoriesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const closeEdit = () => setEditModal({open: false, category: null});
    // ─── Save (create or update) ─────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: handleSave
     * Path: frontend/src/pages/dashboard/SpendingCategoriesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSave = async () => {
        if (!formData.name.trim()) {
            toast.error('Category name is required');
            return;
        }
        if (!formData.budgeted_amount || isNaN(Number(formData.budgeted_amount))) {
            toast.error('A valid budgeted amount is required');
            return;
        }
        setSaving(true);
        try {
            const payload = {
                name: formData.name.trim(),
                fund_type: formData.fund_type,
                budgeted_amount: parseFloat(formData.budgeted_amount),
                actual_amount: parseFloat(formData.actual_amount || '0'),
                description: formData.description.trim() || null,
                year: selectedYear,
                status: 'proposed',
            };
            if (editModal.category) {
                await api.put(`/levy-categories/${editModal.category.id}`, payload);
                toast.success('Category updated successfully');
            } else {
                await api.post('/levy-categories', payload);
                toast.success('Category created successfully');
            }
            closeEdit();
            fetchCategories();
        } catch (err) {
            toast.error(err.response?.data?.detail || 'Failed to save category');
        } finally {
            setSaving(false);
        }
    };
    // ─── Delete ─────────────────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/pages/dashboard/SpendingCategoriesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async () => {
        if (!deleteConfirm.category) return;
        setDeleting(true);
        try {
            await api.delete(`/levy-categories/${deleteConfirm.category.id}`);
            toast.success('Category deleted');
            setDeleteConfirm({open: false, category: null});
            fetchCategories();
        } catch (err) {
            toast.error('Failed to delete category');
        } finally {
            setDeleting(false);
        }
    };
    // ─── Summary strip ──────────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: SummaryStrip
     * Path: frontend/src/pages/dashboard/SpendingCategoriesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const SummaryStrip = ({cats, fundSummary, portalMap}) => {
        const totalActual = getTotal(cats, 'actual_amount');

        // Resolve each category's proposed budget from the best real source (stored itemised
        // budget, else portal `planned`) so the strip total matches what the rows below display.
        const perCat = cats.map(c => resolveCategoryBudget(c, portalMap));
        const perCatTotal = perCat.reduce((s, b) => s + b.amount, 0);
        const anyStored = perCat.some(b => b.source === 'stored');
        const anyPortal = perCat.some(b => b.source === 'portal');
        const anyPerCat = anyStored || anyPortal;

        // Budget-total precedence (all honest — never a fabricated split, never a misleading $0):
        //   1. the fund-level proposed from annual_levies (budget-summary) — the AUTHORITATIVE
        //      total, the SAME figure /financials shows. Per-category budgets can be an
        //      INCOMPLETE breakdown for some years (e.g. East Gate 2023 admin itemises $91,355 of a
        //      $221,316 adopted budget), so a partial itemised sum must never override this total.
        //   2. else the per-category budgets (stored and/or portal-proposed) when any exist,
        //   3. else genuinely missing → "Not on record".
        const fundProposed = fundSummary?.proposed_budget;
        const itemisedTotal = fundSummary?.itemised_total;
        const openingBalance = fundSummary?.opening_balance;
        const closingBalance = fundSummary?.closing_balance;
        let totalBudget, budgetMissing, label, subLabel;
        if (fundProposed != null && fundProposed > 0) {
            totalBudget = fundProposed;
            budgetMissing = false;
            label = 'Total Budgeted';
            subLabel = (anyPerCat && itemisedTotal != null && Math.abs(itemisedTotal - fundProposed) > 1)
                ? `${formatCurrency(itemisedTotal)} itemised · remainder not broken out by category`
                : null;
        } else if (anyPerCat) {
            totalBudget = perCatTotal;
            budgetMissing = false;
            label = (!anyStored && anyPortal) ? 'Proposed Budget (per category)' : 'Total Budgeted';
            subLabel = (!anyStored && anyPortal) ? 'Sourced from the Strata Web portal' : null;
        } else if (fundProposed != null) {
            totalBudget = fundProposed;   // genuine $0 fund budget on record
            budgetMissing = false;
            label = 'Total Budgeted';
            subLabel = null;
        } else {
            totalBudget = 0;
            budgetMissing = true;
            label = 'Proposed Budget';
            subLabel = null;
        }
        const showSourceIcon = !budgetMissing && subLabel != null;

        const variance = totalActual - totalBudget;
        const over = variance > 0;
        const hasFundPosition = openingBalance != null || closingBalance != null;
        return (
          <>
            {/* Fund position — opening reserve carried in and what remains at year end. Most
                meaningful for the Sinking fund. Rendered only when annual_levies supplies it. */}
            {hasFundPosition && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-4">
                    <div className="p-4 rounded-lg bg-amber-50 border border-amber-200">
                        <p className="text-xs text-muted-foreground mb-1 flex items-center gap-1">
                            <Wallet className="h-3.5 w-3.5"/> Opening Balance — {selectedYear}
                        </p>
                        {openingBalance != null ? (
                            <p className="text-xl font-bold text-amber-700">{formatCurrency(openingBalance)}</p>
                        ) : (
                            <p className="text-xl font-bold text-muted-foreground">Not on record</p>
                        )}
                        <p className="text-[11px] text-muted-foreground mt-0.5">Reserve carried in from the prior year</p>
                    </div>
                    <div className="p-4 rounded-lg bg-violet-50 border border-violet-200">
                        <p className="text-xs text-muted-foreground mb-1 flex items-center gap-1">
                            <PiggyBank className="h-3.5 w-3.5"/> Closing Balance — {selectedYear}
                        </p>
                        {closingBalance != null ? (
                            <p className="text-xl font-bold text-violet-700">{formatCurrency(closingBalance)}</p>
                        ) : (
                            <p className="text-xl font-bold text-muted-foreground">Not on record</p>
                        )}
                        <p className="text-[11px] text-muted-foreground mt-0.5">Funds remaining at year end</p>
                    </div>
                </div>
            )}
            <div className="grid grid-cols-3 gap-4 mb-4">
                <div className="p-4 rounded-lg bg-blue-50 border border-blue-200">
                    <p className="text-xs text-muted-foreground mb-1 flex items-center gap-1">
                        {label}
                        {showSourceIcon && (
                            <span title={subLabel}>
                                <Info className="h-3 w-3 text-blue-500"/>
                            </span>
                        )}
                    </p>
                    {budgetMissing ? (
                        <p className="text-xl font-bold text-muted-foreground">Not on record</p>
                    ) : (
                        <p className="text-xl font-bold text-blue-700">{formatCurrency(totalBudget)}</p>
                    )}
                    {!budgetMissing && subLabel && (
                        <p className="text-[11px] text-muted-foreground mt-0.5">{subLabel}</p>
                    )}
                </div>
                <div className="p-4 rounded-lg bg-emerald-50 border border-emerald-200">
                    <p className="text-xs text-muted-foreground mb-1">Total Actual</p>
                    <p className="text-xl font-bold text-emerald-700">{formatCurrency(totalActual)}</p>
                </div>
                <div
                    className={`p-4 rounded-lg border ${budgetMissing ? 'bg-muted/30 border-muted' : over ? 'bg-red-50 border-red-200' : 'bg-green-50 border-green-200'}`}>
                    <p className="text-xs text-muted-foreground mb-1">Variance</p>
                    {budgetMissing ? (
                        <p className="text-sm text-muted-foreground">No budget to compare</p>
                    ) : (
                        <div
                            className={`flex items-center gap-1 text-xl font-bold ${over ? 'text-red-700' : 'text-green-700'}`}>
                            {over ? <TrendingUp className="h-4 w-4"/> : <TrendingDown className="h-4 w-4"/>}
                            {formatCurrency(Math.abs(variance))}
                            <span className="text-sm font-normal ml-1">{over ? 'over' : 'under'}</span>
                        </div>
                    )}
                </div>
            </div>
          </>
        );
    };
    // ─── Category table ─────────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: CategoryTable
     * Path: frontend/src/pages/dashboard/SpendingCategoriesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const CategoryTable = ({cats, fundType, portalMap, fundSummary}) => {
        const showActions = canManage || canDelete;
        const hasPortal = portalMap && portalMap.size > 0;
        const totalCols = ( showActions ? 6 : 5 ) + ( hasPortal ? 1 : 0 );
        return (
            <div className="space-y-4">
                <SummaryStrip cats={cats} fundSummary={fundSummary} portalMap={portalMap}/>

                <div className="overflow-x-auto">
                    <Table>
                        <TableHeader>
                            <TableRow className="bg-muted/30">
                                <TableHead className="w-[32%]">Category Name</TableHead>
                                <TableHead className="text-right">Budgeted</TableHead>
                                <TableHead className="text-right">Actual</TableHead>
                                {hasPortal && (
                                    <TableHead className="text-right text-violet-700"
                                               title="Strata Web portal actual — read-only cross-check">
                                        Portal Actual
                                    </TableHead>
                                )}
                                <TableHead className="text-right">Variance</TableHead>
                                <TableHead className="text-center w-[90px]">Status</TableHead>
                                {showActions && <TableHead className="text-right w-[80px]">Actions</TableHead>}
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading ? (
                                <TableRow>
                                    <TableCell colSpan={totalCols}
                                               className="py-12 text-center text-muted-foreground">
                                        <div className="flex justify-center">
                                            <div
                                                className="animate-spin h-6 w-6 border-b-2 border-primary rounded-full"></div>
                                        </div>
                                    </TableCell>
                                </TableRow>
                            ) : cats.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={totalCols}
                                               className="py-12 text-center text-muted-foreground">
                                        {searchTerm
                                            ? `No categories match "${searchTerm}"`
                                            : `No ${fundType === 'administrative' ? 'administrative' : 'sinking'} categories found for ${selectedYear}`}
                                    </TableCell>
                                </TableRow>
                            ) : (
                                cats.map(cat => {
                                    const actual = cat.actual_amount || 0;
                                    const portalRow = portalMap ? portalMap.get(cat.name.toLowerCase()) : null;
                                    const portalActual = portalRow && portalRow.actual != null ? portalRow.actual : null;
                                    // Proposed budget from the best real source (stored, else portal `planned`).
                                    const resolvedBudget = resolveCategoryBudget(cat, portalMap);
                                    const budget = resolvedBudget.amount;
                                    const budgetFromPortal = resolvedBudget.source === 'portal';
                                    const hasBudget = budget > 0;
                                    const variance = actual - budget;
                                    const variancePct = budget > 0 ? ( ( Math.abs(variance) / budget ) * 100 ).toFixed(1) : null;
                                    const isOver = variance > 0;

                                    return (
                                        <TableRow key={cat.id} className="hover:bg-muted/30 transition-colors">
                                            <TableCell>
                                                <div>
                                                    <p className="font-medium text-sm">{cat.name}</p>
                                                    {cat.description && (
                                                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{cat.description}</p>
                                                    )}
                                                </div>
                                            </TableCell>
                                            <TableCell className="text-right font-medium tabular-nums">
                                                {hasBudget ? (
                                                    <span className="inline-flex items-center justify-end gap-1">
                                                        {formatCurrency(budget)}
                                                        {budgetFromPortal && (
                                                            <span title="Proposed budget from the Strata Web portal — no itemised budget captured for this category">
                                                                <Info className="h-3 w-3 text-violet-500"/>
                                                            </span>
                                                        )}
                                                    </span>
                                                ) : (
                                                    <span className="text-muted-foreground"
                                                          title="No proposed budget on record for this category">—</span>
                                                )}
                                            </TableCell>
                                            <TableCell className="text-right tabular-nums">
                                                {actual > 0 ? formatCurrency(actual) :
                                                    <span className="text-muted-foreground">—</span>}
                                            </TableCell>
                                            {hasPortal && (
                                                <TableCell className="text-right tabular-nums text-violet-700">
                                                    {portalActual != null
                                                        ? formatCurrency(portalActual)
                                                        : <span className="text-muted-foreground">—</span>}
                                                </TableCell>
                                            )}
                                            <TableCell className="text-right tabular-nums">
                                                {actual > 0 ? (
                                                    <span
                                                        className={`flex items-center justify-end gap-1 font-medium text-sm ${isOver ? 'text-red-600' : 'text-green-600'}`}>
                            {isOver ? <TrendingUp className="h-3 w-3"/> : <TrendingDown className="h-3 w-3"/>}
                                                        {formatCurrency(Math.abs(variance))}
                                                        {variancePct && <span
                                                            className="text-xs opacity-70">({variancePct}%)</span>}
                          </span>
                                                ) : (
                                                    <span className="text-muted-foreground">—</span>
                                                )}
                                            </TableCell>
                                            <TableCell className="text-center">
                                                <Badge
                                                    variant={cat.status === 'actual' ? 'default' : cat.status === 'partial_actual' ? 'outline' : 'secondary'}
                                                    className="text-xs capitalize"
                                                >
                                                    {cat.status === 'actual' ? 'Actual' : cat.status === 'partial_actual' ? 'YTD' : 'Proposed'}
                                                </Badge>
                                            </TableCell>
                                            {showActions && (
                                                <TableCell className="text-right">
                                                    <div className="flex items-center justify-end gap-1">
                                                        {canManage && (
                                                            <Button
                                                                variant="ghost"
                                                                size="sm"
                                                                className="h-7 w-7 p-0 hover:bg-blue-50 hover:text-blue-700"
                                                                onClick={() => openEdit(cat)}
                                                                title="Edit"
                                                            >
                                                                <Pencil className="h-3.5 w-3.5"/>
                                                            </Button>
                                                        )}
                                                        {canDelete && (
                                                            <Button
                                                                variant="ghost"
                                                                size="sm"
                                                                className="h-7 w-7 p-0 hover:bg-red-50 hover:text-red-700"
                                                                onClick={() => setDeleteConfirm({
                                                                    open: true,
                                                                    category: cat
                                                                })}
                                                                title="Delete"
                                                            >
                                                                <Trash2 className="h-3.5 w-3.5"/>
                                                            </Button>
                                                        )}
                                                    </div>
                                                </TableCell>
                                            )}
                                        </TableRow>
                                    );
                                })
                            )}
                        </TableBody>
                    </Table>
                </div>

                {/* Add button at bottom of each fund's table */}
                {canManage && (
                    <Button
                        variant="outline"
                        className="w-full border-dashed text-muted-foreground hover:text-foreground hover:border-primary"
                        onClick={() => openAdd(fundType)}
                    >
                        <Plus className="h-4 w-4 mr-2"/>
                        Add {fundType === 'administrative' ? 'Administrative' : 'Sinking Fund'} Category
                    </Button>
                )}
            </div>
        );
    };

    // ─── Render ──────────────────────────────────────────────────────────────────
    return (
        <div className="space-y-6" data-testid="spending-categories-page">

            {/* Page header */}
            <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <DollarSign className="h-6 w-6"/>
                        Spending Categories
                    </h1>
                    <p className="text-muted-foreground mt-1">
                        Budget and actual expense breakdown by category for each fund
                    </p>
                </div>

                {/* Year + Search controls */}
                <div className="flex flex-wrap items-center gap-3">
                    <YearSelector/>

                    <div className="relative">
                        <Search
                            className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground pointer-events-none"/>
                        <Input
                            placeholder="Search categories…"
                            value={searchTerm}
                            onChange={e => setSearchTerm(e.target.value)}
                            className="pl-9 h-9 w-[200px]"
                        />
                    </div>

                    {canManage && (
                        <Button size="sm" onClick={() => openAdd()}>
                            <Plus className="h-4 w-4 mr-1.5"/>
                            Add Category
                        </Button>
                    )}
                </div>
            </div>

            {/* Info banner for read-only users */}
            {!canManage && (
                <div
                    className="flex items-start gap-3 p-3 rounded-lg bg-blue-50 border border-blue-200 text-sm text-blue-800">
                    <Info className="h-4 w-4 mt-0.5 shrink-0"/>
                    <span>You are viewing spending categories in read-only mode. Contact your strata manager to make changes.</span>
                </div>
            )}

            {/* Fund Tabs */}
            <Tabs defaultValue="administrative">
                <TabsList className="grid w-full grid-cols-2 lg:w-[400px]">
                    <TabsTrigger value="administrative" className="gap-2">
                        <Building className="h-4 w-4"/>
                        Administrative Fund
                    </TabsTrigger>
                    <TabsTrigger value="sinking" className="gap-2">
                        <Hammer className="h-4 w-4"/>
                        Sinking Fund
                    </TabsTrigger>
                </TabsList>

                {/* Administrative Fund */}
                <TabsContent value="administrative" className="mt-4">
                    <Card className="card-dashboard">
                        <CardHeader className="pb-4">
                            <div className="flex items-center justify-between">
                                <div>
                                    <CardTitle className="flex items-center gap-2">
                                        <Building className="h-5 w-5 text-blue-600"/>
                                        Administrative Fund — {selectedYear}
                                    </CardTitle>
                                    <CardDescription className="mt-1">
                                        Day-to-day operational and management expenses · {adminCats.length} categories
                                    </CardDescription>
                                </div>
                                {canManage && (
                                    <Button size="sm" variant="outline" onClick={() => openAdd('administrative')}>
                                        <Plus className="h-4 w-4 mr-1"/>
                                        Add
                                    </Button>
                                )}
                            </div>
                        </CardHeader>
                        <CardContent>
                            <CategoryTable
                                cats={adminCats}
                                fundType="administrative"
                                fundSummary={budgetSummary?.administrative}
                                portalMap={new Map(
                                    portalActuals
                                        .filter(p => p.fund === 'admin')
                                        .map(p => [p.category.toLowerCase(), p])
                                )}
                            />
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* Sinking Fund */}
                <TabsContent value="sinking" className="mt-4">
                    <Card className="card-dashboard">
                        <CardHeader className="pb-4">
                            <div className="flex items-center justify-between">
                                <div>
                                    <CardTitle className="flex items-center gap-2">
                                        <Hammer className="h-5 w-5 text-purple-600"/>
                                        Sinking Fund — {selectedYear}
                                    </CardTitle>
                                    <CardDescription className="mt-1">
                                        Capital works, maintenance reserves and long-term expenditure
                                        · {sinkingCats.length} categories
                                    </CardDescription>
                                </div>
                                {canManage && (
                                    <Button size="sm" variant="outline" onClick={() => openAdd('sinking')}>
                                        <Plus className="h-4 w-4 mr-1"/>
                                        Add
                                    </Button>
                                )}
                            </div>
                        </CardHeader>
                        <CardContent>
                            <CategoryTable
                                cats={sinkingCats}
                                fundType="sinking"
                                fundSummary={budgetSummary?.sinking}
                                portalMap={new Map(
                                    portalActuals
                                        .filter(p => p.fund === 'capital_works')
                                        .map(p => [p.category.toLowerCase(), p])
                                )}
                            />
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>

            {/* ── Edit / Add Dialog ───────────────────────────────────────────────── */}
            <Dialog open={editModal.open} onOpenChange={(open) => {
                if (!open) closeEdit();
            }}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>{editModal.category ? 'Edit Category' : 'Add New Category'}</DialogTitle>
                        <DialogDescription>
                            {editModal.category
                                ? `Editing "${editModal.category.name}" for ${selectedYear}`
                                : `Creating a new category for ${selectedYear}`}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-2">
                        {/* Name */}
                        <div className="space-y-1.5">
                            <Label htmlFor="cat-name">Category Name <span className="text-red-500">*</span></Label>
                            <Input
                                id="cat-name"
                                value={formData.name}
                                onChange={e => setFormData(f => ( {...f, name: e.target.value} ))}
                                placeholder="e.g., Insurance, Cleaning, Lift Maintenance"
                            />
                        </div>

                        {/* Fund Type */}
                        <div className="space-y-1.5">
                            <Label>Fund Type <span className="text-red-500">*</span></Label>
                            <Select value={formData.fund_type}
                                    onValueChange={v => setFormData(f => ( {...f, fund_type: v} ))}>
                                <SelectTrigger>
                                    <SelectValue/>
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="administrative">Administrative Fund</SelectItem>
                                    <SelectItem value="sinking">Sinking Fund</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>

                        {/* Amounts — side by side */}
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1.5">
                                <Label htmlFor="cat-budget">Budgeted ($) <span className="text-red-500">*</span></Label>
                                <Input
                                    id="cat-budget"
                                    type="number"
                                    min="0"
                                    step="0.01"
                                    value={formData.budgeted_amount}
                                    onChange={e => setFormData(f => ( {...f, budgeted_amount: e.target.value} ))}
                                    placeholder="0.00"
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="cat-actual">Actual ($)</Label>
                                <Input
                                    id="cat-actual"
                                    type="number"
                                    min="0"
                                    step="0.01"
                                    value={formData.actual_amount}
                                    onChange={e => setFormData(f => ( {...f, actual_amount: e.target.value} ))}
                                    placeholder="0.00"
                                />
                            </div>
                        </div>

                        {/* Description */}
                        <div className="space-y-1.5">
                            <Label htmlFor="cat-desc">Description <span
                                className="text-muted-foreground text-xs">(optional)</span></Label>
                            <Input
                                id="cat-desc"
                                value={formData.description}
                                onChange={e => setFormData(f => ( {...f, description: e.target.value} ))}
                                placeholder="Brief description of this expense line"
                            />
                        </div>
                    </div>

                    <DialogFooter className="gap-2">
                        <Button variant="outline" onClick={closeEdit} disabled={saving}>Cancel</Button>
                        <Button onClick={handleSave} disabled={saving}>
                            {saving ? 'Saving…' : editModal.category ? 'Update Category' : 'Create Category'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* ── Delete Confirmation Dialog ──────────────────────────────────────── */}
            <Dialog
                open={deleteConfirm.open}
                onOpenChange={(open) => {
                    if (!open) setDeleteConfirm({open: false, category: null});
                }}
            >
                <DialogContent className="sm:max-w-sm">
                    <DialogHeader>
                        <DialogTitle className="text-red-600 flex items-center gap-2">
                            <Trash2 className="h-5 w-5"/>
                            Delete Category
                        </DialogTitle>
                        <DialogDescription>
                            This action cannot be undone.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="py-2">
                        <p className="text-sm">
                            Are you sure you want to permanently delete{' '}
                            <span className="font-semibold">"{deleteConfirm.category?.name}"</span>{' '}
                            from {deleteConfirm.category?.fund_type === 'administrative' ? 'Administrative' : 'Sinking'} Fund
                            ({selectedYear})?
                        </p>
                    </div>

                    <DialogFooter className="gap-2">
                        <Button
                            variant="outline"
                            onClick={() => setDeleteConfirm({open: false, category: null})}
                            disabled={deleting}
                        >
                            Cancel
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={handleDelete}
                            disabled={deleting}
                        >
                            {deleting ? 'Deleting…' : 'Delete Category'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default SpendingCategoriesPage;
