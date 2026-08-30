// @featuretrace:levy — Financial projections page: multi-year admin/sinking fund forecasts and what-if modelling.
// Layer: frontend
// Data flow: this page → GET /projections, GET /years → finance.py → projections + annual_levies (building-scoped).
// Related: backend/routers/finance.py
//          tests/frontend/unit/pages/dashboard/BugFixesApr2026.test.tsx
// Toggle: finance
// Collection: projections, annual_levies
import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Label } from '../../components/ui/label';
import { Checkbox } from '../../components/ui/checkbox';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '../../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../components/ui/table';
import {
    AlertTriangle,
    BarChart3,
    Calculator,
    CheckSquare,
    ChevronDown,
    ChevronRight,
    Loader2,
    PiggyBank,
    Plus,
    Save,
    Square,
    Trash2,
    TrendingUp
} from 'lucide-react';
import { formatCurrency, formatDate } from '../../lib/utils';
import {PageHeader} from "../../components/shared/PageHeader";
import { toast } from 'sonner';
import {
    Area,
    AreaChart,
    Bar,
    BarChart,
    CartesianGrid,
    Legend,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis
} from 'recharts';

// ─── Budget Planning Tab ──────────────────────────────────────────────────────

const AVAILABLE_YEARS = ['2021', '2022', '2023', '2024', '2025', '2026'];
// Defined outside BudgetPlanningTab to avoid remounting on every rows state update,
// which would cause the amendedAmount Input to lose focus on each keystroke.
/**
 * @generated FunctionHeader
 * Function: FundSection
 * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function FundSection({
                         title,
                         fundType,
                         collapsed,
                         setCollapsed,
                         rows,
                         showZeroRows,
                         canManage,
                         approveAll,
                         clearAll,
                         toggleApprove,
                         setAmended
                     }) {
    const visibleRows = ( () => {
        const filtered = rows.filter(r => r.fund_type === fundType);
        if (showZeroRows) return filtered;
        return filtered.filter(r => r.prior_year_actual > 0 || r.prior_year_budgeted > 0 || r.proposed_amount > 0);
    } )();

    const total = rows
        .filter(r => r.fund_type === fundType && r.approved)
        .reduce((sum, r) => {
            const amended = parseFloat(r.amendedAmount);
            return sum + ( isNaN(amended) ? r.proposed_amount : amended );
        }, 0);

    const approvedCount = rows.filter(r => r.fund_type === fundType && r.approved).length;
    const totalCount = visibleRows.length;

    return (
        <Card className="card-dashboard">
            <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                    <button
                        className="flex items-center gap-2 font-semibold text-lg hover:text-primary transition-colors"
                        onClick={() => setCollapsed(!collapsed)}
                    >
                        {collapsed ? <ChevronRight className="h-5 w-5"/> : <ChevronDown className="h-5 w-5"/>}
                        {title}
                        <Badge variant="secondary">{approvedCount}/{totalCount} approved</Badge>
                    </button>
                    <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-muted-foreground">
              Subtotal: <span className="text-foreground font-semibold">{formatCurrency(total)}</span>
            </span>
                        {canManage && (
                            <>
                                <Button size="sm" variant="outline" onClick={() => approveAll(fundType)}>
                                    <CheckSquare className="h-3.5 w-3.5 mr-1"/>Approve All
                                </Button>
                                <Button size="sm" variant="ghost" onClick={() => clearAll(fundType)}>
                                    <Square className="h-3.5 w-3.5 mr-1"/>Clear
                                </Button>
                            </>
                        )}
                    </div>
                </div>
            </CardHeader>

            {!collapsed && (
                <CardContent className="pt-0">
                    <div className="overflow-x-auto">
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead className="w-8">
                                        <span className="sr-only">Approved</span>
                                    </TableHead>
                                    <TableHead>Category</TableHead>
                                    <TableHead className="text-right">Prior Actual</TableHead>
                                    <TableHead className="text-right">Proposed (+CPI)</TableHead>
                                    <TableHead className="text-right w-36">Amended</TableHead>
                                    <TableHead className="text-right">Final Amount</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {visibleRows.length === 0 ? (
                                    <TableRow>
                                        <TableCell colSpan={6} className="text-center text-muted-foreground py-6">
                                            No categories found
                                        </TableCell>
                                    </TableRow>
                                ) : (
                                    visibleRows.map((row) => {
                                        const globalIdx = rows.findIndex(r => r.fund_type === row.fund_type && r.name === row.name);
                                        const amended = parseFloat(row.amendedAmount);
                                        const finalAmt = isNaN(amended) ? row.proposed_amount : amended;
                                        const isOverspend = row.prior_year_actual > row.prior_year_budgeted && row.prior_year_budgeted > 0;
                                        const isAmended = row.amendedAmount !== '' && !isNaN(amended);

                                        return (
                                            <TableRow
                                                key={row.name}
                                                className={`${!row.approved ? 'opacity-50' : ''} ${isOverspend ? 'bg-amber-50/50' : ''}`}
                                            >
                                                <TableCell>
                                                    <Checkbox
                                                        checked={row.approved}
                                                        onCheckedChange={() => canManage && toggleApprove(globalIdx)}
                                                        disabled={!canManage}
                                                    />
                                                </TableCell>
                                                <TableCell className="font-medium text-sm">
                                                    {row.name}
                                                    {isOverspend && (
                                                        <span className="ml-1 text-amber-600 text-xs"
                                                              title="Prior year overspend">⚠</span>
                                                    )}
                                                </TableCell>
                                                <TableCell className="text-right text-sm">
                                                    {row.prior_year_actual > 0 ? formatCurrency(row.prior_year_actual) : (
                                                        <span className="text-muted-foreground">—</span>
                                                    )}
                                                </TableCell>
                                                <TableCell className="text-right text-sm text-muted-foreground">
                                                    {formatCurrency(row.proposed_amount)}
                                                </TableCell>
                                                <TableCell className="text-right">
                                                    {canManage ? (
                                                        <Input
                                                            type="number"
                                                            step="0.01"
                                                            min="0"
                                                            value={row.amendedAmount}
                                                            onChange={(e) => setAmended(globalIdx, e.target.value)}
                                                            placeholder={row.proposed_amount.toFixed(2)}
                                                            className={`h-7 text-right text-sm w-32 ${isAmended ? 'border-green-500 text-green-700' : ''}`}
                                                        />
                                                    ) : (
                                                        <span className="text-sm">
                              {isAmended ? formatCurrency(amended) : '—'}
                            </span>
                                                    )}
                                                </TableCell>
                                                <TableCell className="text-right font-medium text-sm">
                                                    {row.approved ? formatCurrency(finalAmt) : (
                                                        <span
                                                            className="text-muted-foreground text-xs">not approved</span>
                                                    )}
                                                </TableCell>
                                            </TableRow>
                                        );
                                    })
                                )}
                            </TableBody>
                        </Table>
                    </div>
                    {/* Sticky subtotal */}
                    <div className="border-t mt-2 pt-3 flex justify-end">
                        <div className="text-sm font-semibold">
                            {title} Subtotal: <span className="text-primary">{formatCurrency(total)}</span>
                        </div>
                    </div>
                </CardContent>
            )}
        </Card>
    );
}
/**
 * @generated FunctionHeader
 * Function: BudgetPlanningTab
 * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function BudgetPlanningTab({api, canManage}) {
    const [baseYear, setBaseYear] = useState('2025');
    const [targetYear, setTargetYear] = useState('2027');
    const [inflationRate, setInflationRate] = useState('3.0');
    const [loading, setLoading] = useState(false);
    const [saving, setSaving] = useState(false);
    const [rows, setRows] = useState([]);
    const [showZeroRows, setShowZeroRows] = useState(false);
    const [adminCollapsed, setAdminCollapsed] = useState(false);
    const [sinkingCollapsed, setSinkingCollapsed] = useState(false);

    const targetYearOptions = ['2026', '2027', '2028', '2029', '2030'];
    /**
     * @generated FunctionHeader
     * Function: loadProposals
     * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const loadProposals = async () => {
        setLoading(true);
        try {
            const res = await api.get('/budget-proposals', {
                params: {
                    base_year: baseYear,
                    target_year: targetYear,
                    inflation_rate: parseFloat(inflationRate) || 3.0
                },
            });
            const items = ( res.data.items || [] ).map(item => ( {
                ...item,
                approved: item.approved ?? false,
                amendedAmount: item.amended_amount != null ? String(item.amended_amount) : '',
            } ));
            setRows(items);
        } catch (err) {
            toast.error(err.response?.data?.detail || 'Failed to load proposals');
        } finally {
            setLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: toggleApprove
     * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleApprove = (idx) => {
        setRows(prev => prev.map((r, i) => i === idx ? {...r, approved: !r.approved} : r));
    };
    /**
     * @generated FunctionHeader
     * Function: setAmended
     * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const setAmended = (idx, val) => {
        setRows(prev => prev.map((r, i) => i === idx ? {...r, amendedAmount: val} : r));
    };
    /**
     * @generated FunctionHeader
     * Function: approveAll
     * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const approveAll = (fundType) => {
        setRows(prev => prev.map(r => r.fund_type === fundType ? {...r, approved: true} : r));
    };
    /**
     * @generated FunctionHeader
     * Function: clearAll
     * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const clearAll = (fundType) => {
        setRows(prev => prev.map(r => r.fund_type === fundType ? {...r, approved: false} : r));
    };
    /**
     * @generated FunctionHeader
     * Function: visibleRows
     * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const visibleRows = (fundType) => {
        const filtered = rows.filter(r => r.fund_type === fundType);
        if (showZeroRows) return filtered;
        return filtered.filter(r => r.prior_year_actual > 0 || r.prior_year_budgeted > 0 || r.proposed_amount > 0);
    };
    /**
     * @generated FunctionHeader
     * Function: subtotal
     * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const subtotal = (fundType) => {
        return rows
            .filter(r => r.fund_type === fundType && r.approved)
            .reduce((sum, r) => {
                const amended = parseFloat(r.amendedAmount);
                return sum + ( isNaN(amended) ? r.proposed_amount : amended );
            }, 0);
    };

    const adminTotal = subtotal('administrative');
    const sinkingTotal = subtotal('sinking');
    const grandTotal = adminTotal + sinkingTotal;
    const anyApproved = rows.some(r => r.approved);
    /**
     * @generated FunctionHeader
     * Function: handleSave
     * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSave = async () => {
        setSaving(true);
        try {
            const items = rows.map(r => ( {
                fund_type: r.fund_type,
                name: r.name,
                prior_year_actual: r.prior_year_actual,
                prior_year_budgeted: r.prior_year_budgeted,
                proposed_amount: r.proposed_amount,
                amended_amount: r.amendedAmount !== '' ? parseFloat(r.amendedAmount) : null,
                approved: r.approved,
            } ));
            const res = await api.post('/budget-proposals', {
                target_year: targetYear,
                base_year: baseYear,
                inflation_rate: parseFloat(inflationRate) || 3.0,
                items,
            });
            toast.success(
                `Saved ${res.data.categories_saved} categories for ${targetYear} — Admin: ${formatCurrency(res.data.admin_total)}, Sinking: ${formatCurrency(res.data.sinking_total)}`
            );
        } catch (err) {
            toast.error(err.response?.data?.detail || 'Failed to save budget');
        } finally {
            setSaving(false);
        }
    };

    return (
        <div className="space-y-6" data-testid="budget-planning-tab">
            {/* Controls */}
            <Card className="card-dashboard">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Calculator className="h-5 w-5"/>
                        Budget Planning Controls
                    </CardTitle>
                    <CardDescription>
                        Select a base year with actuals, a target year to plan for, and apply CPI inflation to generate
                        draft budgets.
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <div className="flex flex-wrap items-end gap-4">
                        <div className="space-y-1">
                            <Label>Base Year (actuals)</Label>
                            <Select value={baseYear} onValueChange={setBaseYear}>
                                <SelectTrigger className="w-32">
                                    <SelectValue/>
                                </SelectTrigger>
                                <SelectContent>
                                    {AVAILABLE_YEARS.map(y => (
                                        <SelectItem key={y} value={y}>{y}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        <div className="space-y-1">
                            <Label>Target Year</Label>
                            <Select value={targetYear} onValueChange={setTargetYear}>
                                <SelectTrigger className="w-32">
                                    <SelectValue/>
                                </SelectTrigger>
                                <SelectContent>
                                    {targetYearOptions.map(y => (
                                        <SelectItem key={y} value={y}>{y}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        <div className="space-y-1">
                            <Label>CPI Rate (%)</Label>
                            <Input
                                type="number"
                                step="0.1"
                                min="0"
                                max="20"
                                value={inflationRate}
                                onChange={(e) => setInflationRate(e.target.value)}
                                className="w-24"
                            />
                        </div>

                        <Button onClick={loadProposals} disabled={loading}>
                            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> :
                                <BarChart3 className="mr-2 h-4 w-4"/>}
                            Load Categories
                        </Button>

                        <div className="flex items-center gap-2 ml-auto">
                            <Checkbox
                                id="show-zero"
                                checked={showZeroRows}
                                onCheckedChange={setShowZeroRows}
                            />
                            <Label htmlFor="show-zero" className="text-sm cursor-pointer">Show zero-amount rows</Label>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* Results */}
            {rows.length > 0 && (
                <>
                    <FundSection
                        title="Administrative Fund"
                        fundType="administrative"
                        collapsed={adminCollapsed}
                        setCollapsed={setAdminCollapsed}
                        rows={rows}
                        showZeroRows={showZeroRows}
                        canManage={canManage}
                        approveAll={approveAll}
                        clearAll={clearAll}
                        toggleApprove={toggleApprove}
                        setAmended={setAmended}
                    />
                    <FundSection
                        title="Sinking Fund"
                        fundType="sinking"
                        collapsed={sinkingCollapsed}
                        setCollapsed={setSinkingCollapsed}
                        rows={rows}
                        showZeroRows={showZeroRows}
                        canManage={canManage}
                        approveAll={approveAll}
                        clearAll={clearAll}
                        toggleApprove={toggleApprove}
                        setAmended={setAmended}
                    />

                    {/* Grand total + Save */}
                    <Card className="card-dashboard border-primary/30 bg-primary/5">
                        <CardContent className="py-4">
                            <div className="flex items-center justify-between flex-wrap gap-4">
                                <div className="flex gap-8 text-sm">
                                    <div>
                                        <span className="text-muted-foreground">Admin Fund</span>
                                        <p className="text-lg font-bold">{formatCurrency(adminTotal)}</p>
                                    </div>
                                    <div>
                                        <span className="text-muted-foreground">Sinking Fund</span>
                                        <p className="text-lg font-bold">{formatCurrency(sinkingTotal)}</p>
                                    </div>
                                    <div>
                                        <span className="text-muted-foreground">Grand Total</span>
                                        <p className="text-xl font-bold text-primary">{formatCurrency(grandTotal)}</p>
                                    </div>
                                    <div>
                                        <span className="text-muted-foreground">Target Year</span>
                                        <p className="text-lg font-bold">{targetYear}</p>
                                    </div>
                                </div>
                                {canManage && (
                                    <Button
                                        onClick={handleSave}
                                        disabled={saving || !anyApproved}
                                        size="lg"
                                    >
                                        {saving ? (
                                            <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                                        ) : (
                                            <Save className="mr-2 h-4 w-4"/>
                                        )}
                                        Save Budget Proposal
                                    </Button>
                                )}
                            </div>
                        </CardContent>
                    </Card>
                </>
            )}

            {rows.length === 0 && !loading && (
                <Card className="card-dashboard">
                    <CardContent className="py-16 text-center">
                        <Calculator className="h-16 w-16 text-muted-foreground/40 mx-auto mb-4"/>
                        <h3 className="text-lg font-medium mb-2">Ready to Plan</h3>
                        <p className="text-muted-foreground">
                            Select a base year and target year, then click "Load Categories" to generate CPI-adjusted
                            proposals.
                        </p>
                    </CardContent>
                </Card>
            )}
        </div>
    );
}
// ─── Main Page ────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: FinancialProjectionsPage
 * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const FinancialProjectionsPage = () => {
    const {api, hasPermission} = useAuth();
    const [projections, setProjections] = useState([]);
    const [selectedProjection, setSelectedProjection] = useState(null);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [majorWorkDialogOpen, setMajorWorkDialogOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);

    const canManage = hasPermission('can_manage_finances');

    const [availableYears, setAvailableYears] = useState(['2026', '2025', '2024']);
    const [formData, setFormData] = useState({
        projection_name: '',
        base_year: '2026',
        projection_years: 5,
        assumptions: {
            inflation_rate: 3.0,
            insurance_increase: 5.0,
            utilities_increase: 4.0,
            wages_increase: 3.5,
            sinking_fund_contribution: 10000,
            major_works: []
        }
    });

    const [majorWork, setMajorWork] = useState({
        year: new Date().getFullYear() + 1,
        description: '',
        amount: ''
    });

    const fetchProjections = useCallback(async () => {
        try {
            const [projectionsRes, yearsRes] = await Promise.allSettled([
                api.get('/projections'),
                api.get('/years'),
            ]);
            if (projectionsRes.status === 'fulfilled') {
                setProjections(projectionsRes.value.data);
                if (projectionsRes.value.data.length > 0 && !selectedProjection) {
                    setSelectedProjection(projectionsRes.value.data[ 0 ]);
                }
            }
            if (yearsRes.status === 'fulfilled') {
                const yrs = yearsRes.value.data?.years || yearsRes.value.data || [];
                if (yrs.length > 0) {
                    setAvailableYears(yrs);
                    setFormData(prev => ( {...prev, base_year: String(yrs[ 0 ])} ));
                }
            }
        } catch (error) {
            console.error('Failed to fetch projections:', error);
        } finally {
            setLoading(false);
        }
    }, [api, selectedProjection]);

    useEffect(() => {
        fetchProjections();
    }, [fetchProjections]);
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            const response = await api.post('/projections', formData);
            toast.success('Projection created successfully');
            setDialogOpen(false);
            setSelectedProjection(response.data);
            setFormData({
                projection_name: '',
                base_year: '2024-2025',
                projection_years: 5,
                assumptions: {
                    inflation_rate: 3.0,
                    insurance_increase: 5.0,
                    utilities_increase: 4.0,
                    wages_increase: 3.5,
                    sinking_fund_contribution: 10000,
                    major_works: []
                }
            });
            fetchProjections();
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to create projection');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: addMajorWork
     * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const addMajorWork = () => {
        if (!majorWork.description || !majorWork.amount) return;

        setFormData(prev => ( {
            ...prev,
            assumptions: {
                ...prev.assumptions,
                major_works: [
                    ...prev.assumptions.major_works,
                    {...majorWork, amount: parseFloat(majorWork.amount)}
                ]
            }
        } ));
        setMajorWork({year: new Date().getFullYear() + 1, description: '', amount: ''});
        setMajorWorkDialogOpen(false);
    };
    /**
     * @generated FunctionHeader
     * Function: removeMajorWork
     * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const removeMajorWork = (index) => {
        setFormData(prev => ( {
            ...prev,
            assumptions: {
                ...prev.assumptions,
                major_works: prev.assumptions.major_works.filter((_, i) => i !== index)
            }
        } ));
    };
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/pages/dashboard/FinancialProjectionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async (id) => {
        if (!confirm('Delete this projection?')) return;
        try {
            await api.delete(`/projections/${id}`);
            toast.success('Projection deleted');
            if (selectedProjection?.id === id) setSelectedProjection(null);
            fetchProjections();
        } catch (error) {
            toast.error('Failed to delete');
        }
    };

    // Prepare chart data
    const chartData = selectedProjection?.projections?.map(p => ( {
        year: p.year.split('-')[ 0 ],
        'Admin Expenses': p.admin_expenses,
        'Sinking Fund': p.sinking_closing,
        'Total Budget': p.total_budget,
        'Levy/Unit': p.levy_per_unit * 100  // Scale for visibility
    } )) || [];

    return (
        <div className="space-y-6" data-testid="projections-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <PageHeader
                    className="flex-1 border-b-0 pb-0"
                    title="Financial Projections"
                    icon={<TrendingUp className="h-5 w-5"/>}
                    description="3-5 year budget forecasts and planning"
                />
                {canManage && (
                    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                        <DialogTrigger asChild>
                            <Button><Plus className="mr-2 h-4 w-4"/>New Projection</Button>
                        </DialogTrigger>
                        <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
                            <DialogHeader>
                                <DialogTitle>Create Financial Projection</DialogTitle>
                                <DialogDescription>Set assumptions for 3-5 year budget forecasts</DialogDescription>
                            </DialogHeader>
                            <form onSubmit={handleSubmit} className="space-y-6">
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <Label>Projection Name</Label>
                                        <Input
                                            value={formData.projection_name}
                                            onChange={(e) => setFormData(prev => ( {
                                                ...prev,
                                                projection_name: e.target.value
                                            } ))}
                                            placeholder="e.g., 5-Year Plan 2024"
                                            required
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Base Year</Label>
                                        <Select value={formData.base_year}
                                                onValueChange={(v) => setFormData(prev => ( {...prev, base_year: v} ))}>
                                            <SelectTrigger><SelectValue/></SelectTrigger>
                                            <SelectContent>
                                                {availableYears.map(y => (
                                                    <SelectItem key={String(y)}
                                                                value={String(y)}>{String(y)}</SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                </div>

                                <div className="space-y-2">
                                    <Label>Projection Period (Years)</Label>
                                    <Select value={formData.projection_years.toString()}
                                            onValueChange={(v) => setFormData(prev => ( {
                                                ...prev,
                                                projection_years: parseInt(v)
                                            } ))}>
                                        <SelectTrigger><SelectValue/></SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="3">3 Years</SelectItem>
                                            <SelectItem value="5">5 Years</SelectItem>
                                            <SelectItem value="10">10 Years</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>

                                <div className="border-t pt-4">
                                    <h4 className="font-semibold mb-4 flex items-center gap-2">
                                        <Calculator className="h-4 w-4"/>
                                        Assumptions (Annual Increase %)
                                    </h4>
                                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                                        <div className="space-y-2">
                                            <Label>Inflation</Label>
                                            <Input
                                                type="number"
                                                step="0.1"
                                                value={formData.assumptions.inflation_rate}
                                                onChange={(e) => setFormData(prev => ( {
                                                    ...prev,
                                                    assumptions: {
                                                        ...prev.assumptions,
                                                        inflation_rate: parseFloat(e.target.value)
                                                    }
                                                } ))}
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Insurance</Label>
                                            <Input
                                                type="number"
                                                step="0.1"
                                                value={formData.assumptions.insurance_increase}
                                                onChange={(e) => setFormData(prev => ( {
                                                    ...prev,
                                                    assumptions: {
                                                        ...prev.assumptions,
                                                        insurance_increase: parseFloat(e.target.value)
                                                    }
                                                } ))}
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Utilities</Label>
                                            <Input
                                                type="number"
                                                step="0.1"
                                                value={formData.assumptions.utilities_increase}
                                                onChange={(e) => setFormData(prev => ( {
                                                    ...prev,
                                                    assumptions: {
                                                        ...prev.assumptions,
                                                        utilities_increase: parseFloat(e.target.value)
                                                    }
                                                } ))}
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Wages</Label>
                                            <Input
                                                type="number"
                                                step="0.1"
                                                value={formData.assumptions.wages_increase}
                                                onChange={(e) => setFormData(prev => ( {
                                                    ...prev,
                                                    assumptions: {
                                                        ...prev.assumptions,
                                                        wages_increase: parseFloat(e.target.value)
                                                    }
                                                } ))}
                                            />
                                        </div>
                                    </div>
                                </div>

                                <div className="space-y-2">
                                    <Label>Annual Sinking Fund Contribution ($)</Label>
                                    <Input
                                        type="number"
                                        value={formData.assumptions.sinking_fund_contribution}
                                        onChange={(e) => setFormData(prev => ( {
                                            ...prev,
                                            assumptions: {
                                                ...prev.assumptions,
                                                sinking_fund_contribution: parseFloat(e.target.value)
                                            }
                                        } ))}
                                    />
                                </div>

                                <div className="border-t pt-4">
                                    <div className="flex items-center justify-between mb-4">
                                        <h4 className="font-semibold flex items-center gap-2">
                                            <AlertTriangle className="h-4 w-4"/>
                                            Planned Major Works
                                        </h4>
                                        <Dialog open={majorWorkDialogOpen} onOpenChange={setMajorWorkDialogOpen}>
                                            <DialogTrigger asChild>
                                                <Button type="button" variant="outline" size="sm">
                                                    <Plus className="mr-2 h-4 w-4"/>Add Major Work
                                                </Button>
                                            </DialogTrigger>
                                            <DialogContent>
                                                <DialogHeader><DialogTitle>Add Major Work</DialogTitle></DialogHeader>
                                                <div className="space-y-4">
                                                    <div className="space-y-2">
                                                        <Label>Year</Label>
                                                        <Input type="number" value={majorWork.year}
                                                               onChange={(e) => setMajorWork(prev => ( {
                                                                   ...prev,
                                                                   year: parseInt(e.target.value)
                                                               } ))}/>
                                                    </div>
                                                    <div className="space-y-2">
                                                        <Label>Description</Label>
                                                        <Input value={majorWork.description}
                                                               onChange={(e) => setMajorWork(prev => ( {
                                                                   ...prev,
                                                                   description: e.target.value
                                                               } ))} placeholder="e.g., Lift upgrade"/>
                                                    </div>
                                                    <div className="space-y-2">
                                                        <Label>Estimated Cost ($)</Label>
                                                        <Input type="number" value={majorWork.amount}
                                                               onChange={(e) => setMajorWork(prev => ( {
                                                                   ...prev,
                                                                   amount: e.target.value
                                                               } ))}/>
                                                    </div>
                                                    <Button type="button" onClick={addMajorWork}
                                                            className="w-full">Add</Button>
                                                </div>
                                            </DialogContent>
                                        </Dialog>
                                    </div>

                                    {formData.assumptions.major_works.length === 0 ? (
                                        <p className="text-sm text-muted-foreground">No major works planned</p>
                                    ) : (
                                        <div className="space-y-2">
                                            {formData.assumptions.major_works.map((work, i) => (
                                                <div key={i}
                                                     className="flex items-center justify-between p-2 bg-muted/50 rounded">
                                                    <div>
                                                        <span className="font-medium">{work.year}</span>
                                                        <span className="mx-2">-</span>
                                                        <span>{work.description}</span>
                                                        <span className="mx-2">-</span>
                                                        <span
                                                            className="font-medium">{formatCurrency(work.amount)}</span>
                                                    </div>
                                                    <Button type="button" variant="ghost" size="sm"
                                                            onClick={() => removeMajorWork(i)}>
                                                        <Trash2 className="h-4 w-4"/>
                                                    </Button>
                                                </div>
                                            ))}
                                        </div>
                                    )}
                                </div>

                                <Button type="submit" className="w-full" disabled={submitting}>
                                    {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> :
                                        <TrendingUp className="mr-2 h-4 w-4"/>}
                                    Generate Projection
                                </Button>
                            </form>
                        </DialogContent>
                    </Dialog>
                )}
            </div>

            <Tabs defaultValue="projections" className="space-y-6">
                <TabsList>
                    <TabsTrigger value="projections">Multi-Year Projections</TabsTrigger>
                    <TabsTrigger value="budget-planning">Budget Planning</TabsTrigger>
                </TabsList>

                <TabsContent value="projections" className="space-y-0">
                    <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
                        {/* Projection List */}
                        <Card className="card-dashboard">
                            <CardHeader>
                                <CardTitle className="text-lg">Saved Projections</CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-2">
                                {loading ? (
                                    <div className="space-y-2">{[1, 2, 3].map(i => <div key={i}
                                                                                        className="skeleton h-16 w-full"/>)}</div>
                                ) : projections.length === 0 ? (
                                    <div className="py-8 text-center">
                                        <TrendingUp className="h-12 w-12 text-muted-foreground/50 mx-auto mb-2"/>
                                        <p className="text-sm text-muted-foreground">No projections yet</p>
                                    </div>
                                ) : (
                                    projections.map(proj => (
                                        <button
                                            key={proj.id}
                                            onClick={() => setSelectedProjection(proj)}
                                            className={`w-full p-3 rounded-lg text-left transition-colors ${
                                                selectedProjection?.id === proj.id ? 'bg-primary/10 border border-primary' : 'bg-muted/50 hover:bg-muted'
                                            }`}
                                        >
                                            <p className="font-medium text-sm">{proj.projection_name}</p>
                                            <p className="text-xs text-muted-foreground">{proj.projection_years} years
                                                from {proj.base_year}</p>
                                            <p className="text-xs text-muted-foreground">{formatDate(proj.created_at)}</p>
                                        </button>
                                    ))
                                )}
                            </CardContent>
                        </Card>

                        {/* Projection Details */}
                        <div className="lg:col-span-3 space-y-6">
                            {selectedProjection ? (
                                <>
                                    {/* Header */}
                                    <Card className="card-dashboard">
                                        <CardHeader>
                                            <div className="flex items-center justify-between">
                                                <div>
                                                    <CardTitle>{selectedProjection.projection_name}</CardTitle>
                                                    <CardDescription>
                                                        {selectedProjection.projection_years} year projection from
                                                        FY {selectedProjection.base_year}
                                                    </CardDescription>
                                                </div>
                                                {canManage && (
                                                    <Button variant="outline" size="sm"
                                                            onClick={() => handleDelete(selectedProjection.id)}>
                                                        <Trash2 className="h-4 w-4"/>
                                                    </Button>
                                                )}
                                            </div>
                                        </CardHeader>
                                        <CardContent>
                                            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
                                                <div className="p-3 bg-muted/50 rounded-lg">
                                                    <p className="text-muted-foreground">Inflation</p>
                                                    <p className="font-semibold">{selectedProjection.assumptions.inflation_rate}%</p>
                                                </div>
                                                <div className="p-3 bg-muted/50 rounded-lg">
                                                    <p className="text-muted-foreground">Insurance Inc.</p>
                                                    <p className="font-semibold">{selectedProjection.assumptions.insurance_increase}%</p>
                                                </div>
                                                <div className="p-3 bg-muted/50 rounded-lg">
                                                    <p className="text-muted-foreground">Utilities Inc.</p>
                                                    <p className="font-semibold">{selectedProjection.assumptions.utilities_increase}%</p>
                                                </div>
                                                <div className="p-3 bg-muted/50 rounded-lg">
                                                    <p className="text-muted-foreground">Sinking Fund</p>
                                                    <p className="font-semibold">{formatCurrency(selectedProjection.assumptions.sinking_fund_contribution)}/yr</p>
                                                </div>
                                            </div>
                                        </CardContent>
                                    </Card>

                                    {/* Charts */}
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                        <Card className="card-dashboard">
                                            <CardHeader>
                                                <CardTitle className="text-lg flex items-center gap-2">
                                                    <BarChart3 className="h-5 w-5"/>
                                                    Budget Projection
                                                </CardTitle>
                                            </CardHeader>
                                            <CardContent>
                                                <div className="h-64">
                                                    <ResponsiveContainer width="100%" height="100%">
                                                        <BarChart data={chartData}>
                                                            <CartesianGrid strokeDasharray="3 3"/>
                                                            <XAxis dataKey="year"/>
                                                            <YAxis
                                                                tickFormatter={(v) => `$${( v / 1000 ).toFixed(0)}k`}/>
                                                            <Tooltip formatter={(v) => formatCurrency(v)}/>
                                                            <Legend/>
                                                            <Bar dataKey="Admin Expenses" fill="#3b82f6"/>
                                                            <Bar dataKey="Total Budget" fill="#22c55e"/>
                                                        </BarChart>
                                                    </ResponsiveContainer>
                                                </div>
                                            </CardContent>
                                        </Card>

                                        <Card className="card-dashboard">
                                            <CardHeader>
                                                <CardTitle className="text-lg flex items-center gap-2">
                                                    <PiggyBank className="h-5 w-5"/>
                                                    Sinking Fund Growth
                                                </CardTitle>
                                            </CardHeader>
                                            <CardContent>
                                                <div className="h-64">
                                                    <ResponsiveContainer width="100%" height="100%">
                                                        <AreaChart data={chartData}>
                                                            <CartesianGrid strokeDasharray="3 3"/>
                                                            <XAxis dataKey="year"/>
                                                            <YAxis
                                                                tickFormatter={(v) => `$${( v / 1000 ).toFixed(0)}k`}/>
                                                            <Tooltip formatter={(v) => formatCurrency(v)}/>
                                                            <Area type="monotone" dataKey="Sinking Fund"
                                                                  stroke="#8b5cf6" fill="#8b5cf680"/>
                                                        </AreaChart>
                                                    </ResponsiveContainer>
                                                </div>
                                            </CardContent>
                                        </Card>
                                    </div>

                                    {/* Data Table */}
                                    <Card className="card-dashboard">
                                        <CardHeader>
                                            <CardTitle className="text-lg">Year-by-Year Breakdown</CardTitle>
                                        </CardHeader>
                                        <CardContent>
                                            <div className="overflow-x-auto">
                                                <Table>
                                                    <TableHeader>
                                                        <TableRow>
                                                            <TableHead>FY</TableHead>
                                                            <TableHead className="text-right">Admin Expenses</TableHead>
                                                            <TableHead className="text-right">Admin Balance</TableHead>
                                                            <TableHead className="text-right">Sinking Income</TableHead>
                                                            <TableHead className="text-right">Sinking
                                                                Balance</TableHead>
                                                            <TableHead className="text-right">Total Budget</TableHead>
                                                            <TableHead className="text-right">Levy/Unit</TableHead>
                                                        </TableRow>
                                                    </TableHeader>
                                                    <TableBody>
                                                        {selectedProjection.projections.map((p, i) => (
                                                            <TableRow key={i}>
                                                                <TableCell className="font-medium">{p.year}</TableCell>
                                                                <TableCell
                                                                    className="text-right">{formatCurrency(p.admin_expenses)}</TableCell>
                                                                <TableCell
                                                                    className="text-right">{formatCurrency(p.admin_closing)}</TableCell>
                                                                <TableCell
                                                                    className="text-right">{formatCurrency(p.sinking_income)}</TableCell>
                                                                <TableCell
                                                                    className="text-right">{formatCurrency(p.sinking_closing)}</TableCell>
                                                                <TableCell
                                                                    className="text-right font-medium">{formatCurrency(p.total_budget)}</TableCell>
                                                                <TableCell
                                                                    className="text-right">${p.levy_per_unit.toFixed(4)}</TableCell>
                                                            </TableRow>
                                                        ))}
                                                    </TableBody>
                                                </Table>
                                            </div>

                                            {/* Major Works */}
                                            {selectedProjection.assumptions.major_works?.length > 0 && (
                                                <div className="mt-6 p-4 bg-orange-50 rounded-lg">
                                                    <h4 className="font-semibold text-orange-800 mb-2 flex items-center gap-2">
                                                        <AlertTriangle className="h-4 w-4"/>
                                                        Planned Major Works
                                                    </h4>
                                                    <div className="space-y-1">
                                                        {selectedProjection.assumptions.major_works.map((work, i) => (
                                                            <div key={i} className="text-sm text-orange-700">
                                                                <span
                                                                    className="font-medium">{work.year}:</span> {work.description} - {formatCurrency(work.amount)}
                                                            </div>
                                                        ))}
                                                    </div>
                                                </div>
                                            )}
                                        </CardContent>
                                    </Card>
                                </>
                            ) : (
                                <Card className="card-dashboard">
                                    <CardContent className="py-16 text-center">
                                        <TrendingUp className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                                        <h3 className="text-lg font-medium mb-2">No Projection Selected</h3>
                                        <p className="text-muted-foreground">Create or select a projection to view
                                            forecasts</p>
                                    </CardContent>
                                </Card>
                            )}
                        </div>
                    </div>
                </TabsContent>

                <TabsContent value="budget-planning">
                    <BudgetPlanningTab api={api} canManage={canManage}/>
                </TabsContent>
            </Tabs>
        </div>
    );
};

export default FinancialProjectionsPage;
