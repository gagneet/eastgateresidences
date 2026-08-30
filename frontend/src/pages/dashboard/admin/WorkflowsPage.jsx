import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Badge } from '../../../components/ui/badge';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../../components/ui/table';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../../components/ui/select';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger, } from '../../../components/ui/tooltip';
import {
    Activity,
    AlertTriangle,
    CheckCircle2,
    ChevronDown,
    ChevronRight,
    Circle,
    Clock,
    GitBranch,
    Hand,
    Info,
    Loader2,
    Play,
    RefreshCw,
    Settings2,
    XCircle,
    Zap,
} from 'lucide-react';
import { toast } from 'sonner';

// ─── Status helpers ────────────────────────────────────────────────────────

const HEALTH_CONFIG = {
    healthy: {icon: CheckCircle2, label: 'Healthy', className: 'text-green-600'},
    stale: {icon: AlertTriangle, label: 'Stale', className: 'text-amber-500'},
    failing: {icon: XCircle, label: 'Failing', className: 'text-red-600'},
    never_run: {icon: Clock, label: 'Never run', className: 'text-blue-500'},
    not_implemented: {icon: Circle, label: 'Not implemented', className: 'text-gray-400'},
};
/**
 * @generated FunctionHeader
 * Function: HealthIcon
 * Path: frontend/src/pages/dashboard/admin/WorkflowsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function HealthIcon({health}) {
    const cfg = HEALTH_CONFIG[ health ] || HEALTH_CONFIG.never_run;
    const Icon = cfg.icon;
    return (
        <span className={`flex items-center gap-1 text-sm font-medium ${cfg.className}`}>
      <Icon className="w-4 h-4"/>
            {cfg.label}
    </span>
    );
}

const CATEGORY_COLOURS = {
    financial: 'bg-green-100 text-green-800',
    operations: 'bg-blue-100 text-blue-800',
    governance: 'bg-purple-100 text-purple-800',
    compliance: 'bg-orange-100 text-orange-800',
    analytics: 'bg-cyan-100 text-cyan-800',
    security: 'bg-red-100 text-red-800',
    residency: 'bg-yellow-100 text-yellow-800',
    maintenance: 'bg-amber-100 text-amber-800',
    communications: 'bg-indigo-100 text-indigo-800',
    community: 'bg-pink-100 text-pink-800',
};
/**
 * @generated FunctionHeader
 * Function: CategoryBadge
 * Path: frontend/src/pages/dashboard/admin/WorkflowsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function CategoryBadge({category}) {
    const cls = CATEGORY_COLOURS[ category ] || 'bg-gray-100 text-gray-700';
    return <Badge className={`${cls} capitalize text-xs`}>{category}</Badge>;
}
/** "Automated" or "Manual" trigger type badge */
function TriggerBadge({triggerType}) {
    const isAuto = triggerType === 'cron' || triggerType === 'scheduled';
    return (
        <Tooltip>
            <TooltipTrigger asChild>
        <span
            className={`inline-flex items-center gap-1 text-xs px-1.5 py-0.5 rounded font-medium ${
                isAuto ? 'bg-blue-50 text-blue-700' : 'bg-amber-50 text-amber-700'
            }`}
        >
          {isAuto ? <Zap className="w-3 h-3"/> : <Hand className="w-3 h-3"/>}
            {isAuto ? 'Automated' : 'On-demand'}
        </span>
            </TooltipTrigger>
            <TooltipContent className="text-xs max-w-xs">
                {isAuto
                    ? 'Runs automatically on the configured schedule. This workflow cannot be manually triggered by residents — only strata managers and above can run it manually.'
                    : 'Triggered by an event or manual request rather than a schedule.'}
            </TooltipContent>
        </Tooltip>
    );
}
/** Format SLA hours into human-readable string */
function fmtSLA(hours) {
    if (hours == null) return '—';
    if (hours === 0) return 'Real-time';
    if (hours < 1) return `${Math.round(hours * 60)}m`;
    if (hours < 24) return `${hours}h`;
    if (hours < 168) return `${( hours / 24 ).toFixed(0)}d`;
    if (hours < 8760) return `${( hours / 168 ).toFixed(0)}w`;
    return `${( hours / 8760 ).toFixed(0)}y`;
}
/**
 * @generated FunctionHeader
 * Function: formatRelative
 * Path: frontend/src/pages/dashboard/admin/WorkflowsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function formatRelative(iso) {
    if (!iso) return '—';
    try {
        const diff = ( Date.now() - new Date(iso).getTime() ) / 1000;
        if (diff < 60) return 'just now';
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
        return `${Math.floor(diff / 86400)}d ago`;
    } catch {
        return iso;
    }
}
/**
 * @generated FunctionHeader
 * Function: durationMs
 * Path: frontend/src/pages/dashboard/admin/WorkflowsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function durationMs(ms) {
    if (ms == null) return '—';
    if (ms < 1000) return `${ms}ms`;
    return `${( ms / 1000 ).toFixed(1)}s`;
}
// ─── Summary card ─────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: SummaryCard
 * Path: frontend/src/pages/dashboard/admin/WorkflowsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function SummaryCard({summary}) {
    if (!summary) return null;
    const coveragePct = summary.coverage_pct ?? 0;

    return (
        <Card className="card-dashboard border-l-4 border-l-blue-500">
            <CardContent className="p-4">
                <div className="flex flex-wrap gap-6 items-center">
                    <div className="flex items-center gap-2">
                        <GitBranch className="w-5 h-5 text-blue-500"/>
                        <div>
                            <p className="text-2xl font-bold">{summary.implemented}/{summary.total}</p>
                            <p className="text-xs text-muted-foreground">workflows implemented ({coveragePct}%)</p>
                        </div>
                    </div>
                    <div className="flex flex-wrap gap-4">
                        <Stat icon={<CheckCircle2 className="w-4 h-4 text-green-500"/>} value={summary.healthy}
                              label="healthy"/>
                        <Stat icon={<AlertTriangle className="w-4 h-4 text-amber-500"/>} value={summary.stale}
                              label="stale"/>
                        <Stat icon={<XCircle className="w-4 h-4 text-red-500"/>} value={summary.failing}
                              label="failing"/>
                        <Stat icon={<Clock className="w-4 h-4 text-blue-500"/>} value={summary.never_run}
                              label="never run"/>
                        <Stat icon={<Circle className="w-4 h-4 text-gray-400"/>} value={summary.not_implemented}
                              label="not built"/>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
/**
 * @generated FunctionHeader
 * Function: Stat
 * Path: frontend/src/pages/dashboard/admin/WorkflowsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function Stat({icon, value, label}) {
    return (
        <div className="flex items-center gap-1.5">
            {icon}
            <span className="font-semibold">{value}</span>
            <span className="text-xs text-muted-foreground">{label}</span>
        </div>
    );
}
// ─── SLA edit popover ────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: SLAEditor
 * Path: frontend/src/pages/dashboard/admin/WorkflowsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function SLAEditor({workflowId, currentSla, canEdit, api, onUpdated}) {
    const [editing, setEditing] = useState(false);
    const [value, setValue] = useState('');
    const [saving, setSaving] = useState(false);

    if (!canEdit) {
        return <span className="text-sm tabular-nums">{fmtSLA(currentSla)}</span>;
    }
    /**
     * @generated FunctionHeader
     * Function: save
     * Path: frontend/src/pages/dashboard/admin/WorkflowsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const save = async () => {
        const h = parseFloat(value);
        if (isNaN(h) || h < 0) {
            toast.error('Enter a valid number of hours');
            return;
        }
        setSaving(true);
        try {
            await api.put(`/workflows/${workflowId}/sla`, {sla_hours: h});
            toast.success(`SLA updated to ${fmtSLA(h)}`);
            onUpdated(h);
            setEditing(false);
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to save SLA');
        } finally {
            setSaving(false);
        }
    };

    if (editing) {
        return (
            <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                <input
                    type="number"
                    min="0"
                    step="0.25"
                    placeholder="hours"
                    className="w-20 h-6 text-xs border rounded px-1"
                    value={value}
                    onChange={(e) => setValue(e.target.value)}
                    onKeyDown={(e) => {
                        if (e.key === 'Enter') save();
                        if (e.key === 'Escape') setEditing(false);
                    }}
                    autoFocus
                />
                <Button size="sm" className="h-6 px-2 text-xs" onClick={save} disabled={saving}>
                    {saving ? <Loader2 className="w-3 h-3 animate-spin"/> : 'Save'}
                </Button>
                <Button variant="ghost" size="sm" className="h-6 px-1" onClick={() => setEditing(false)}>✕</Button>
            </div>
        );
    }

    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <button
                    className="text-sm tabular-nums flex items-center gap-1 hover:text-blue-600 transition-colors"
                    onClick={(e) => {
                        e.stopPropagation();
                        setValue(String(currentSla ?? ''));
                        setEditing(true);
                    }}
                >
                    {fmtSLA(currentSla)}
                    <Settings2 className="w-3 h-3 text-muted-foreground"/>
                </button>
            </TooltipTrigger>
            <TooltipContent className="text-xs">Click to override SLA for this building</TooltipContent>
        </Tooltip>
    );
}
// ─── Expandable run history row ───────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: WorkflowRow
 * Path: frontend/src/pages/dashboard/admin/WorkflowsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function WorkflowRow({workflow, onViewRuns, canRunNow, canEditSLA, api, onSLAUpdated}) {
    const [expanded, setExpanded] = useState(false);
    const [running, setRunning] = useState(false);
    const lastRun = workflow.last_run;
    const isLevyRelated = ['levy_run_quarterly', 'arrears_notice_tier1', 'levy_arrears_notifier'].includes(workflow.id);
    /**
     * @generated FunctionHeader
     * Function: runNow
     * Path: frontend/src/pages/dashboard/admin/WorkflowsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const runNow = async (e) => {
        e.stopPropagation();
        setRunning(true);
        try {
            const res = await api.post(`/workflows/${workflow.id}/run`);
            toast.success(`Workflow queued: run ID ${res.data?.run_id?.slice(0, 8)}…`);
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to trigger workflow');
        } finally {
            setRunning(false);
        }
    };

    return (
        <>
            <TableRow
                className={`cursor-pointer hover:bg-muted/50 ${!workflow.implemented ? 'opacity-60' : ''}`}
                onClick={() => setExpanded((v) => !v)}
            >
                <TableCell>
                    <div className="flex items-center gap-1.5">
                        {expanded ? (
                            <ChevronDown className="w-4 h-4 text-muted-foreground flex-shrink-0"/>
                        ) : (
                            <ChevronRight className="w-4 h-4 text-muted-foreground flex-shrink-0"/>
                        )}
                        <div>
                            <span className="font-medium text-sm">{workflow.name}</span>
                            {isLevyRelated && (
                                <Tooltip>
                                    <TooltipTrigger asChild>
                                        <Info className="inline w-3 h-3 ml-1 text-blue-400 cursor-help"/>
                                    </TooltipTrigger>
                                    <TooltipContent className="text-xs max-w-xs">
                                        Schedule is driven by Building Settings → Financials for this building.
                                        Levy due dates and frequencies are configured per-building by the EC
                                        or Strata Manager.
                                    </TooltipContent>
                                </Tooltip>
                            )}
                        </div>
                    </div>
                </TableCell>
                <TableCell>
                    <CategoryBadge category={workflow.category}/>
                </TableCell>
                <TableCell>
                    <TriggerBadge triggerType={workflow.trigger_type}/>
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                    {lastRun ? formatRelative(lastRun.started_at) : '—'}
                </TableCell>
                <TableCell>
                    <HealthIcon health={workflow.health}/>
                </TableCell>
                <TableCell className="text-sm">
                    {lastRun ? (
                        <span className="tabular-nums">{lastRun.items_processed ?? 0}</span>
                    ) : '—'}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                    <SLAEditor
                        workflowId={workflow.id}
                        currentSla={workflow.sla_hours}
                        canEdit={canEditSLA}
                        api={api}
                        onUpdated={(h) => onSLAUpdated(workflow.id, h)}
                    />
                </TableCell>
                <TableCell>
                    <div className="flex items-center gap-1">
                        {workflow.implemented && (
                            <Button
                                variant="ghost"
                                size="sm"
                                className="h-6 px-2 text-xs"
                                onClick={(e) => {
                                    e.stopPropagation();
                                    onViewRuns(workflow.id);
                                }}
                            >
                                Runs
                            </Button>
                        )}
                        {canRunNow && workflow.implemented && (
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Button
                                        variant="ghost"
                                        size="sm"
                                        className="h-6 px-2 text-xs text-blue-600"
                                        onClick={runNow}
                                        disabled={running}
                                    >
                                        {running ? <Loader2 className="w-3 h-3 animate-spin"/> :
                                            <Play className="w-3 h-3"/>}
                                        {running ? '' : 'Run'}
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent className="text-xs">Manually trigger this workflow now</TooltipContent>
                            </Tooltip>
                        )}
                    </div>
                </TableCell>
            </TableRow>
            {expanded && (
                <TableRow className="bg-muted/30">
                    <TableCell colSpan={9} className="py-3 px-6">
                        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
                            <div>
                                <p className="text-xs text-muted-foreground mb-0.5">Description</p>
                                <p>{workflow.description || '—'}</p>
                            </div>
                            <div>
                                <p className="text-xs text-muted-foreground mb-0.5">Schedule / Trigger</p>
                                <p>{workflow.trigger_description || workflow.schedule || '—'}</p>
                            </div>
                            {workflow.required_artefacts && (
                                <div>
                                    <p className="text-xs text-muted-foreground mb-0.5">Required artefacts</p>
                                    <p>{workflow.required_artefacts.join(', ')}</p>
                                </div>
                            )}
                            {workflow.failure_mode && (
                                <div>
                                    <p className="text-xs text-muted-foreground mb-0.5">On failure</p>
                                    <p>{workflow.failure_mode}</p>
                                </div>
                            )}
                            {workflow.jurisdiction_notes && (
                                <div className="col-span-2">
                                    <p className="text-xs text-muted-foreground mb-0.5">Jurisdiction</p>
                                    <p>{workflow.jurisdiction_notes}</p>
                                </div>
                            )}
                            {lastRun && (
                                <div>
                                    <p className="text-xs text-muted-foreground mb-0.5">Last run duration</p>
                                    <p>{durationMs(lastRun.duration_ms)}</p>
                                </div>
                            )}
                            {lastRun?.error_detail && (
                                <div className="col-span-3">
                                    <p className="text-xs text-red-600 mb-0.5">Error</p>
                                    <p className="text-red-700 font-mono text-xs">{lastRun.error_detail}</p>
                                </div>
                            )}
                        </div>
                    </TableCell>
                </TableRow>
            )}
        </>
    );
}
// ─── Run history dialog ───────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: RunHistory
 * Path: frontend/src/pages/dashboard/admin/WorkflowsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function RunHistory({workflowId, onClose, api}) {
    const [runs, setRuns] = useState([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        if (!workflowId) return;
        api.get(`/workflows/${workflowId}/runs?limit=20`)
            .then((res) => setRuns(res.data || []))
            .catch(() => toast.error('Failed to load run history'))
            .finally(() => setLoading(false));
    }, [workflowId, api]);

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
            <Card className="w-full max-w-2xl max-h-[80vh] overflow-auto">
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                    <CardTitle className="text-base">Run history: {workflowId}</CardTitle>
                    <Button variant="ghost" size="sm" onClick={onClose}>Close</Button>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="flex justify-center py-4"><Loader2 className="animate-spin"/></div>
                    ) : runs.length === 0 ? (
                        <p className="text-sm text-muted-foreground py-4 text-center">No run records found.</p>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Started</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Items</TableHead>
                                    <TableHead>Duration</TableHead>
                                    <TableHead>Trigger</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {runs.map((run) => (
                                    <TableRow key={run.id}>
                                        <TableCell className="text-xs">{formatRelative(run.started_at)}</TableCell>
                                        <TableCell>
                                            <HealthIcon
                                                health={run.status === 'success' ? 'healthy' : run.status === 'failure' ? 'failing' : run.status === 'partial' ? 'failing' : 'stale'}/>
                                        </TableCell>
                                        <TableCell className="text-sm">{run.items_processed ?? 0}</TableCell>
                                        <TableCell className="text-sm">{durationMs(run.duration_ms)}</TableCell>
                                        <TableCell
                                            className="text-xs text-muted-foreground">{run.trigger_detail || run.trigger_type || '—'}</TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}

// ─── Main page ─────────────────────────────────────────────────────────────

const CATEGORIES = [
    'all', 'operations', 'financial', 'governance', 'compliance',
    'analytics', 'security', 'maintenance', 'residency', 'communications', 'community',
];
/**
 * @generated FunctionHeader
 * Function: WorkflowsPage
 * Path: frontend/src/pages/dashboard/admin/WorkflowsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function WorkflowsPage() {
    const {api, user} = useAuth();

    const [workflows, setWorkflows] = useState([]);
    const [summary, setSummary] = useState(null);
    const [loading, setLoading] = useState(true);
    const [categoryFilter, setCategoryFilter] = useState('all');
    const [selectedRunWorkflow, setSelectedRunWorkflow] = useState(null);
    const [buildingInfo, setBuildingInfo] = useState(null);

    // Roles that can manually run workflows
    const canRunNow = ['super_admin', 'strata_manager'].includes(
        user?.effective_role || user?.role
    );
    // Roles that can edit SLA overrides
    const canEditSLA = ['super_admin', 'strata_manager'].includes(
        user?.effective_role || user?.role
    );

    const fetchStatus = useCallback(async () => {
        setLoading(true);
        try {
            const params = categoryFilter !== 'all' ? `?category=${categoryFilter}` : '';
            const [statusRes, settingsRes] = await Promise.allSettled([
                api.get(`/workflows/status${params}`),
                api.get('/settings'),
            ]);

            if (statusRes.status === 'fulfilled') {
                setWorkflows(statusRes.value.data?.workflows || []);
                setSummary(statusRes.value.data?.summary || null);
            } else {
                const msg = statusRes.reason?.response?.data?.detail || 'Failed to load workflow status';
                toast.error(msg);
            }

            if (settingsRes.status === 'fulfilled') {
                setBuildingInfo(settingsRes.value.data);
            }
        } finally {
            setLoading(false);
        }
    }, [api, categoryFilter]);

    useEffect(() => {
        fetchStatus();
    }, [fetchStatus]);
    /**
     * @generated FunctionHeader
     * Function: handleSLAUpdated
     * Path: frontend/src/pages/dashboard/admin/WorkflowsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSLAUpdated = (workflowId, newSla) => {
        setWorkflows((prev) =>
            prev.map((wf) => ( wf.id === workflowId ? {...wf, sla_hours: newSla} : wf ))
        );
    };

    return (
        <TooltipProvider>
            <div className="max-w-7xl mx-auto space-y-6">
                {/* Header */}
                <div className="flex items-center justify-between flex-wrap gap-2">
                    <div>
                        <h1 className="text-2xl font-bold flex items-center gap-2">
                            <Activity className="w-6 h-6 text-blue-500"/>
                            Workflow Governance
                        </h1>
                        <p className="text-muted-foreground text-sm mt-0.5">
                            Named, observable automations
                            — {summary ? `${summary.coverage_pct ?? 0}% coverage` : 'loading…'}
                            {buildingInfo?.building_name && (
                                <span className="ml-2 text-xs bg-muted px-2 py-0.5 rounded font-medium">
                  Building: {buildingInfo.building_name}
                </span>
                            )}
                        </p>
                    </div>
                    <Button variant="outline" size="sm" onClick={fetchStatus} disabled={loading}>
                        {loading ? <Loader2 className="w-4 h-4 animate-spin mr-1"/> :
                            <RefreshCw className="w-4 h-4 mr-1"/>}
                        Refresh
                    </Button>
                </div>

                {/* Guidance banner */}
                <div className="flex items-start gap-2 text-xs text-muted-foreground bg-muted/40 rounded px-3 py-2">
                    <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0"/>
                    <div>
                        <strong>Automated</strong> workflows run on a cron
                        schedule; <strong>On-demand</strong> workflows are triggered by events.
                        {canRunNow && ' Click "Run" to manually trigger an implemented workflow for the current building.'}
                        {canEditSLA && ' Click the SLA value to set a per-building override.'}
                        {' '}Levy-related workflows (marked <Info className="inline w-3 h-3 text-blue-400"/>) use due
                        dates from Building Settings → Financials.
                    </div>
                </div>

                {/* Summary */}
                <SummaryCard summary={summary}/>

                {/* Category filter */}
                <div className="flex items-center gap-3">
                    <p className="text-sm text-muted-foreground">Filter by category:</p>
                    <Select value={categoryFilter} onValueChange={setCategoryFilter}>
                        <SelectTrigger className="w-44 h-8">
                            <SelectValue/>
                        </SelectTrigger>
                        <SelectContent>
                            {CATEGORIES.map((cat) => (
                                <SelectItem key={cat} value={cat} className="capitalize">
                                    {cat === 'all' ? 'All categories' : cat}
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>

                {/* Workflows table */}
                <Card className="card-dashboard">
                    <CardContent className="p-0">
                        {loading ? (
                            <div className="flex justify-center py-12">
                                <Loader2 className="w-8 h-8 animate-spin text-muted-foreground"/>
                            </div>
                        ) : workflows.length === 0 ? (
                            <p className="text-sm text-muted-foreground text-center py-8">No workflows found.</p>
                        ) : (
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Workflow</TableHead>
                                        <TableHead>Category</TableHead>
                                        <TableHead>Type</TableHead>
                                        <TableHead>Last Run</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Items</TableHead>
                                        <TableHead>
                                            <Tooltip>
                                                <TooltipTrigger asChild>
                          <span className="flex items-center gap-1 cursor-help">
                            SLA
                            <Info className="w-3 h-3 text-muted-foreground"/>
                          </span>
                                                </TooltipTrigger>
                                                <TooltipContent className="text-xs max-w-xs">
                                                    Service Level Agreement — maximum time this workflow is allowed
                                                    to run without
                                                    completing. {canEditSLA ? 'Click the value to set a per-building override.' : ''}
                                                </TooltipContent>
                                            </Tooltip>
                                        </TableHead>
                                        <TableHead></TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {workflows.map((wf) => (
                                        <WorkflowRow
                                            key={wf.id}
                                            workflow={wf}
                                            onViewRuns={setSelectedRunWorkflow}
                                            canRunNow={canRunNow}
                                            canEditSLA={canEditSLA}
                                            api={api}
                                            onSLAUpdated={handleSLAUpdated}
                                        />
                                    ))}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>

                {/* Run history overlay */}
                {selectedRunWorkflow && (
                    <RunHistory
                        workflowId={selectedRunWorkflow}
                        api={api}
                        onClose={() => setSelectedRunWorkflow(null)}
                    />
                )}
            </div>
        </TooltipProvider>
    );
}
