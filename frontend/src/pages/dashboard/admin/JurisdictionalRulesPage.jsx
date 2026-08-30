// @featuretrace:jurisdictional-rules — Admin UI for viewing statutory rules per building jurisdiction.
// Layer: frontend
// Data flow: this page → GET /api/jurisdictional-rules/?building_id= → JurisdictionalRuleEngine.
// Related: backend/domain/jurisdictional_rules.py
//           backend/routers/jurisdictional_rules_router.py
//           backend/domain/rules/{act,nsw,vic,qld}.json
// Scope: (building-scoped)
import React, { useEffect, useState, useCallback } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { useRouter } from 'next/navigation';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../../components/ui/card';
import { Badge } from '../../../components/ui/badge';
import { Alert, AlertDescription } from '../../../components/ui/alert';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../../components/ui/tabs';
import {
    AlertCircle,
    BookOpen,
    Building2,
    CheckCircle2,
    Scale,
    XCircle,
    RefreshCw,
    Info,
} from 'lucide-react';
import { Button } from '../../../components/ui/button';

const JURISDICTION_LABELS = {
    ACT: 'Australian Capital Territory',
    NSW: 'New South Wales',
    VIC: 'Victoria',
    QLD: 'Queensland',
};

const JURISDICTION_LEGISLATION = {
    ACT: 'Unit Titles (Management) Act 2011 + Agents Act 2003',
    NSW: 'Strata Schemes Management Act 2015 + PSA Regulation 2022',
    VIC: 'Owners Corporations Act 2006 + Estate Agents Act 1980',
    QLD: 'BCCM Act 1997 + Standard / Accommodation Module',
};
/**
 * @generated FunctionHeader
 * Function: BoolBadge
 * Path: frontend/src/pages/dashboard/admin/JurisdictionalRulesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function BoolBadge({value}) {
    return value ? (
        <Badge className="bg-green-100 text-green-800 border-green-200 gap-1">
            <CheckCircle2 className="h-3 w-3"/> Yes
        </Badge>
    ) : (
        <Badge className="bg-red-100 text-red-800 border-red-200 gap-1">
            <XCircle className="h-3 w-3"/> No
        </Badge>
    );
}
/**
 * @generated FunctionHeader
 * Function: RuleRow
 * Path: frontend/src/pages/dashboard/admin/JurisdictionalRulesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function RuleRow({label, value, legislation, notes}) {
    const [showNotes, setShowNotes] = useState(false);
    /**
     * @generated FunctionHeader
     * Function: displayValue
     * Path: frontend/src/pages/dashboard/admin/JurisdictionalRulesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const displayValue = () => {
        if (value === null || value === undefined) return <span className="text-gray-400 italic">Not applicable</span>;
        if (typeof value === 'boolean') return <BoolBadge value={value}/>;
        if (Array.isArray(value)) return (
            <div className="flex flex-wrap gap-1">
                {value.map(v => <Badge key={v} variant="outline">{v}</Badge>)}
            </div>
        );
        return <span className="font-mono text-sm">{String(value)}</span>;
    };

    return (
        <>
            <tr className="border-b border-gray-100 hover:bg-gray-50 transition-colors">
                <td className="py-2.5 px-4 text-sm font-medium text-gray-700 w-1/3">{label}</td>
                <td className="py-2.5 px-4 w-1/4">{displayValue()}</td>
                <td className="py-2.5 px-4 text-xs text-gray-500 w-1/4">
                    {legislation && <span className="font-mono">{legislation}</span>}
                </td>
                <td className="py-2.5 px-4 w-1/6 text-right">
                    {notes && (
                        <button
                            onClick={() => setShowNotes(v => !v)}
                            className="text-blue-500 hover:text-blue-700 inline-flex items-center gap-1 text-xs"
                            data-testid={`notes-toggle-${label}`}
                        >
                            <Info className="h-3 w-3"/>
                            {showNotes ? 'Hide' : 'Info'}
                        </button>
                    )}
                </td>
            </tr>
            {showNotes && (
                <tr>
                    <td colSpan={4} className="px-4 pb-2 bg-blue-50/50">
                        <p className="text-xs text-gray-600 bg-blue-50 rounded p-2 border border-blue-100">{notes}</p>
                    </td>
                </tr>
            )}
        </>
    );
}
/**
 * @generated FunctionHeader
 * Function: TypedRulesTable
 * Path: frontend/src/pages/dashboard/admin/JurisdictionalRulesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function TypedRulesTable({typed, fullRules = {}}) {
    const rows = [
        {label: 'Max interest rate (% p.a.)', key: 'max_interest_rate_pa'},
        {label: 'Interest grace period (days)', key: 'interest_grace_period_days'},
        {label: 'Can transfer between funds', key: 'can_transfer_between_funds'},
        {label: 'Inter-fund transfer resolution', key: 'inter_fund_transfer_resolution_required'},
        {label: 'Over-budget threshold (%)', key: 'overbudget_threshold_pct'},
        {label: 'Trust withdrawal authority roles', key: 'trust_withdrawal_authority_roles'},
        {label: 'Trial balance deadline (days)', key: 'trial_balance_deadline_days'},
        {label: 'Audit deadline', key: 'audit_deadline'},
        {label: 'Pooled trust permitted', key: 'pooled_trust_permitted'},
        {label: 'Interest belongs to', key: 'interest_belongs_to'},
        {label: 'Committee spending cap formula', key: 'committee_spending_cap_formula'},
        {label: 'Min withholding % (no ABN)', key: 'min_withholding_pct_no_abn'},
    ];

    return (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="w-full text-sm" data-testid="typed-rules-table">
                <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                    <th className="text-left py-2.5 px-4 text-xs font-semibold text-gray-600 uppercase tracking-wide">Rule</th>
                    <th className="text-left py-2.5 px-4 text-xs font-semibold text-gray-600 uppercase tracking-wide">Value</th>
                    <th className="text-left py-2.5 px-4 text-xs font-semibold text-gray-600 uppercase tracking-wide">Legislation</th>
                    <th className="text-left py-2.5 px-4 text-xs font-semibold text-gray-600 uppercase tracking-wide"></th>
                </tr>
                </thead>
                <tbody>
                {rows.map(({label, key}) => (
                    <RuleRow
                        key={key}
                        label={label}
                        value={typed[ key ]}
                        legislation={fullRules[ key ]?.legislation}
                        notes={fullRules[ key ]?.notes}
                    />
                ))}
                </tbody>
            </table>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: FullRulesTable
 * Path: frontend/src/pages/dashboard/admin/JurisdictionalRulesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function FullRulesTable({rules}) {
    const entries = Object.entries(rules).filter(([k]) => !k.startsWith('_'));
    return (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="w-full text-sm" data-testid="full-rules-table">
                <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                    <th className="text-left py-2.5 px-4 text-xs font-semibold text-gray-600 uppercase tracking-wide w-1/4">Key</th>
                    <th className="text-left py-2.5 px-4 text-xs font-semibold text-gray-600 uppercase tracking-wide w-1/6">Value</th>
                    <th className="text-left py-2.5 px-4 text-xs font-semibold text-gray-600 uppercase tracking-wide w-1/6">Unit</th>
                    <th className="text-left py-2.5 px-4 text-xs font-semibold text-gray-600 uppercase tracking-wide w-1/4">Legislation</th>
                    <th className="text-left py-2.5 px-4 text-xs font-semibold text-gray-600 uppercase tracking-wide">Notes</th>
                </tr>
                </thead>
                <tbody>
                {entries.map(([key, rule]) => {
                    const r = rule || {};
                    const val = r.value;
                    return (
                        <tr key={key} className="border-b border-gray-100 hover:bg-gray-50">
                            <td className="py-2 px-4 font-mono text-xs text-gray-600">{key}</td>
                            <td className="py-2 px-4">
                                {val === null || val === undefined ? (
                                    <span className="text-gray-400 italic text-xs">—</span>
                                ) : typeof val === 'boolean' ? (
                                    <BoolBadge value={val}/>
                                ) : Array.isArray(val) ? (
                                    <div className="flex flex-wrap gap-1">
                                        {val.map(v => <Badge key={v} variant="outline" className="text-xs">{v}</Badge>)}
                                    </div>
                                ) : (
                                    <span className="text-sm">{String(val)}</span>
                                )}
                            </td>
                            <td className="py-2 px-4 text-xs text-gray-500">{r.unit || '—'}</td>
                            <td className="py-2 px-4 text-xs text-gray-500 font-mono">{r.legislation || '—'}</td>
                            <td className="py-2 px-4 text-xs text-gray-500">{r.notes || '—'}</td>
                        </tr>
                    );
                })}
                </tbody>
            </table>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: JurisdictionalRulesPage
 * Path: frontend/src/pages/dashboard/admin/JurisdictionalRulesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function JurisdictionalRulesPage() {
    const {isAdmin, isManager, loading, api, user} = useAuth();
    const router = useRouter();

    const [data, setData] = useState(null);
    const [allJurisdictions, setAllJurisdictions] = useState(null);
    const [fetchError, setFetchError] = useState(null);
    const [fetching, setFetching] = useState(false);
    const [activeTab, setActiveTab] = useState('building');

    const buildingId = user?.building_id;
    const canAccess = isAdmin() || isManager();

    useEffect(() => {
        if (loading) return;
        if (!canAccess) {
            router.replace('/dashboard');
        }
    }, [loading, canAccess, router]);

    const fetchBuildingRules = useCallback(async () => {
        if (!buildingId) return;
        setFetching(true);
        setFetchError(null);
        try {
            const res = await api.get(`/jurisdictional-rules/?building_id=${buildingId}`);
            setData(res.data);
        } catch (err) {
            setFetchError(err?.response?.data?.detail || 'Failed to load jurisdictional rules.');
        } finally {
            setFetching(false);
        }
    }, [api, buildingId]);

    const fetchAllJurisdictions = useCallback(async () => {
        setFetching(true);
        setFetchError(null);
        try {
            const res = await api.get('/jurisdictional-rules/all');
            setAllJurisdictions(res.data.jurisdictions);
        } catch (err) {
            setFetchError(err?.response?.data?.detail || 'Failed to load all jurisdictions.');
        } finally {
            setFetching(false);
        }
    }, [api]);

    useEffect(() => {
        if (!loading && canAccess && buildingId) {
            fetchBuildingRules();
        }
    }, [loading, canAccess, buildingId, fetchBuildingRules]);

    if (loading || !canAccess) return null;

    return (
        <div className="p-6 max-w-6xl mx-auto space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
                        <Scale className="h-6 w-6 text-[#2F4F4F]"/>
                        Jurisdictional Rules
                    </h1>
                    <p className="text-gray-500 text-sm mt-1">
                        Statutory constraints from Australian strata legislation — read-only, data-driven.
                    </p>
                </div>
                <Button
                    variant="outline"
                    size="sm"
                    onClick={activeTab === 'building' ? fetchBuildingRules : fetchAllJurisdictions}
                    disabled={fetching}
                    data-testid="refresh-btn"
                    className="gap-2"
                >
                    <RefreshCw className={`h-4 w-4 ${fetching ? 'animate-spin' : ''}`}/>
                    Refresh
                </Button>
            </div>

            {fetchError && (
                <Alert variant="destructive" data-testid="error-alert">
                    <AlertCircle className="h-4 w-4"/>
                    <AlertDescription>{fetchError}</AlertDescription>
                </Alert>
            )}

            <Tabs value={activeTab} onValueChange={(v) => {
                setActiveTab(v);
                if (v === 'all' && !allJurisdictions) fetchAllJurisdictions();
            }}>
                <TabsList>
                    <TabsTrigger value="building" data-testid="tab-building">
                        <Building2 className="h-4 w-4 mr-1"/> Your Building
                    </TabsTrigger>
                    <TabsTrigger value="all" data-testid="tab-all">
                        <BookOpen className="h-4 w-4 mr-1"/> All Jurisdictions
                    </TabsTrigger>
                </TabsList>

                <TabsContent value="building" className="space-y-4 mt-4">
                    {data ? (
                        <>
                            <Card>
                                <CardHeader className="pb-3">
                                    <div className="flex items-center gap-3">
                                        <CardTitle className="text-lg">
                                            {JURISDICTION_LABELS[ data.jurisdiction ] || data.jurisdiction}
                                        </CardTitle>
                                        <Badge className="bg-[#2F4F4F]/10 text-[#2F4F4F] border-[#2F4F4F]/20">
                                            {data.jurisdiction}
                                        </Badge>
                                    </div>
                                    <CardDescription>
                                        {JURISDICTION_LEGISLATION[ data.jurisdiction ]}
                                        {data.meta?.last_reviewed && (
                                            <span
                                                className="ml-2 text-gray-400">· Last reviewed {data.meta.last_reviewed}</span>
                                        )}
                                    </CardDescription>
                                </CardHeader>
                                <CardContent className="space-y-4">
                                    <div>
                                        <h3 className="text-sm font-semibold text-gray-700 mb-2">Key Statutory
                                            Constraints</h3>
                                        <TypedRulesTable typed={data.typed_rules} fullRules={data.full_rules}/>
                                    </div>
                                </CardContent>
                            </Card>

                            <Card>
                                <CardHeader className="pb-3">
                                    <CardTitle className="text-sm font-semibold text-gray-600">Complete Rules
                                        Reference</CardTitle>
                                    <CardDescription>All statutory rules with citations — including per-building
                                        overrides.</CardDescription>
                                </CardHeader>
                                <CardContent>
                                    <FullRulesTable rules={data.full_rules}/>
                                </CardContent>
                            </Card>
                        </>
                    ) : fetching ? (
                        <div className="text-center py-12 text-gray-400">Loading rules…</div>
                    ) : (
                        <div className="text-center py-12 text-gray-400">No data loaded.</div>
                    )}
                </TabsContent>

                <TabsContent value="all" className="space-y-4 mt-4">
                    {allJurisdictions ? (
                        Object.entries(allJurisdictions).map(([jur, jdata]) => (
                            <Card key={jur} data-testid={`jurisdiction-card-${jur}`}>
                                <CardHeader className="pb-3">
                                    <div className="flex items-center gap-3">
                                        <CardTitle className="text-base">
                                            {JURISDICTION_LABELS[ jur ] || jur}
                                        </CardTitle>
                                        <Badge variant="outline">{jur}</Badge>
                                    </div>
                                    <CardDescription>{JURISDICTION_LEGISLATION[ jur ]}</CardDescription>
                                </CardHeader>
                                <CardContent>
                                    <TypedRulesTable typed={jdata.typed_rules} fullRules={jdata.full_rules}/>
                                </CardContent>
                            </Card>
                        ))
                    ) : fetching ? (
                        <div className="text-center py-12 text-gray-400">Loading all jurisdictions…</div>
                    ) : null}
                </TabsContent>
            </Tabs>
        </div>
    );
}
