// @featuretrace:savings_ledger — Savings Ledger page
// Layer: frontend
// Data flow: SavingsLedgerPage → /api/savings/* → savings_events (building-scoped)
// Related: backend/routers/savings.py, backend/services/savings_engine.py

import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import { AlertCircle, Calendar, CheckCircle2, Loader2, Plus, TrendingUp } from 'lucide-react';
import { toast } from 'sonner';
import { savingsApi } from '../../lib/api/community-os';
import YearSelector from '../../components/widgets/YearSelector';

import {formatMoneyFromCents} from '@/lib/currency';
const ADMIN_ROLES = ['super_admin', 'strata_manager'];
/**
 * Convert AuthContext year ("2026") → fiscal year string ("FY2026-27").
 * The savings_events collection stores financial_year in this format.
 */
function toFiscalYear(year) {
    if (!year) return null;
    const nextYr = ( ( parseInt(year) + 1 ) % 100 ).toString().padStart(2, '0');
    return `FY${year}-${nextYr}`;
}
/**
 * @generated FunctionHeader
 * Function: categoryBadgeClass
 * Path: frontend/src/pages/dashboard/SavingsLedgerPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function categoryBadgeClass(category) {
    const map = {
        energy: 'bg-yellow-100 text-yellow-800',
        water: 'bg-blue-100 text-blue-800',
        insurance: 'bg-purple-100 text-purple-800',
        maintenance: 'bg-orange-100 text-orange-800',
        admin: 'bg-gray-100 text-gray-700',
        legal: 'bg-red-100 text-red-800',
    };
    return map[ category?.toLowerCase() ] || 'bg-indigo-100 text-indigo-800';
}
/**
 * @generated FunctionHeader
 * Function: SavingsPage
 * Path: frontend/src/pages/dashboard/SavingsLedgerPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function SavingsPage() {
    const {user, api, selectedYear} = useAuth();
    const sApi = savingsApi(api);

    const [events, setEvents] = useState([]);
    const [summary, setSummary] = useState(null);
    const [loading, setLoading] = useState(true);

    const [showCreate, setShowCreate] = useState(false);
    const [creating, setCreating] = useState(false);
    const [form, setForm] = useState({
        category: 'energy',
        description: '',
        amount_saved: '',        // dollar amount the community saved
        original_cost: '',       // what it would have cost without saving
        saving_method: '',
        resident_summary: '',
        event_date: '',
    });

    const isAdmin = ADMIN_ROLES.includes(user?.role || '');

    // Convert context year to fiscal year format expected by savings API
    const fiscalYear = toFiscalYear(selectedYear);

    const load = useCallback(async () => {
        if (!fiscalYear) return;
        setLoading(true);
        try {
            const [listRes, sumRes] = await Promise.all([
                // Backend param is `financial_year` (not fiscal_year)
                sApi.list({financial_year: fiscalYear}),
                sApi.summary({financial_year: fiscalYear}),
            ]);
            setEvents(listRes.data?.savings || listRes.data || []);
            setSummary(sumRes.data || null);
        } catch {
            setEvents([]);
            setSummary(null);
        } finally {
            setLoading(false);
        }
    }, [fiscalYear]);

    useEffect(() => {
        load();
    }, [load]);
    /**
     * @generated FunctionHeader
     * Function: handleCreate
     * Path: frontend/src/pages/dashboard/SavingsLedgerPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCreate = async (e) => {
        e.preventDefault();
        setCreating(true);
        try {
            // SavingsEventCreate model requires:
            //   original_cost_cents, final_cost_cents (difference = saved_cents)
            //   saving_method, resident_summary, financial_year
            const originalCents = Math.round(parseFloat(form.original_cost || form.amount_saved || 0) * 100);
            const savedCents = Math.round(parseFloat(form.amount_saved || 0) * 100);
            const finalCents = Math.max(0, originalCents - savedCents);

            await sApi.create({
                category: form.category,
                description: form.description,
                original_cost_cents: originalCents,
                final_cost_cents: finalCents,
                saving_method: form.saving_method || form.resident_summary,
                resident_summary: form.resident_summary,
                financial_year: fiscalYear,
                evidence_documents: [],
            });
            toast.success('Saving recorded');
            setShowCreate(false);
            setForm({
                category: 'energy',
                description: '',
                amount_saved: '',
                original_cost: '',
                saving_method: '',
                resident_summary: '',
                event_date: '',
            });
            load();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to record saving');
        } finally {
            setCreating(false);
        }
    };

    // API returns: ytd_saved_cents, all_time_saved_cents
    // by_category values are objects: { total_saved_cents, event_count }
    const ytdCents = summary?.ytd_saved_cents ?? 0;
    const allTimeCents = summary?.all_time_saved_cents ?? ytdCents;
    const byCategory = summary?.by_category || {};

    // Extract cents from each category object; sort descending for bar chart
    const categoryEntries = Object.entries(byCategory)
        .map(([cat, data]) => [cat, typeof data === 'object' ? ( data.total_saved_cents ?? 0 ) : Number(data)])
        .sort(([, a], [, b]) => b - a);
    const maxCents = Math.max(1, ...categoryEntries.map(([, c]) => c));

    return (
        <div className="p-6 max-w-4xl mx-auto space-y-6">
            <div className="flex items-center justify-between flex-wrap gap-3">
                <div>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <TrendingUp className="h-6 w-6 text-green-500"/>
                        Savings Ledger
                    </h1>
                    <p className="text-muted-foreground text-sm">Track community savings and efficiencies</p>
                </div>
                <div className="flex gap-2 items-center">
                    <YearSelector/>
                    {isAdmin && (
                        <Button onClick={() => setShowCreate(true)}>
                            <Plus className="h-4 w-4 mr-2"/>Record Saving
                        </Button>
                    )}
                </div>
            </div>

            {/* Header stats */}
            <div className="grid grid-cols-2 gap-4">
                <Card>
                    <CardContent className="pt-5">
                        <p className="text-xs text-muted-foreground uppercase tracking-wide">YTD Savings</p>
                        <p className="text-3xl font-bold text-green-600 mt-1">{formatMoneyFromCents(ytdCents)}</p>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-5">
                        <p className="text-xs text-muted-foreground uppercase tracking-wide">All Time</p>
                        <p className="text-3xl font-bold text-green-700 mt-1">{formatMoneyFromCents(allTimeCents)}</p>
                    </CardContent>
                </Card>
            </div>

            {/* Category breakdown */}
            {categoryEntries.length > 0 && (
                <Card>
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm font-medium">By Category</CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-2">
                        {categoryEntries.map(([cat, cents]) => (
                            <div key={cat} className="space-y-0.5">
                                <div className="flex justify-between text-sm">
                                    <span className="capitalize">{cat.replace(/_/g, ' ')}</span>
                                    <span className="font-medium text-green-700">{formatMoneyFromCents(cents)}</span>
                                </div>
                                <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                                    <div
                                        className="h-full rounded-full bg-green-500"
                                        style={{width: `${( cents / maxCents ) * 100}%`}}
                                    />
                                </div>
                            </div>
                        ))}
                    </CardContent>
                </Card>
            )}

            {/* Events list */}
            <Card>
                <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-medium">Savings Events</CardTitle>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="flex justify-center py-10">
                            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground"/>
                        </div>
                    ) : events.length === 0 ? (
                        <div className="py-10 text-center text-muted-foreground">
                            <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-50"/>
                            <p>No savings recorded for {fiscalYear || 'this period'}.</p>
                        </div>
                    ) : (
                        <div className="divide-y">
                            {events.map((ev) => (
                                <div key={ev.id || ev._id} className="py-3 flex items-start justify-between gap-3">
                                    <div className="space-y-0.5 flex-1 min-w-0">
                                        <div className="flex items-center gap-2 flex-wrap">
                                            <Badge className={categoryBadgeClass(ev.category)}>
                                                {ev.category?.replace(/_/g, ' ') || 'Other'}
                                            </Badge>
                                            {ev.verified && (
                                                <span className="flex items-center gap-0.5 text-xs text-green-600">
                                                    <CheckCircle2 className="h-3 w-3"/>Verified
                                                </span>
                                            )}
                                        </div>
                                        <p className="text-sm font-medium truncate">
                                            {ev.resident_summary || ev.description}
                                        </p>
                                        {ev.event_date && (
                                            <p className="text-xs text-muted-foreground flex items-center gap-1">
                                                <Calendar className="h-3 w-3"/>
                                                {new Date(ev.event_date).toLocaleDateString('en-AU')}
                                            </p>
                                        )}
                                    </div>
                                    {/* API returns saved_cents */}
                                    <p className="text-sm font-bold text-green-600 whitespace-nowrap">
                                        {formatMoneyFromCents(ev.saved_cents ?? ev.amount_cents ?? 0)}
                                    </p>
                                </div>
                            ))}
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Create Dialog */}
            <Dialog open={showCreate} onOpenChange={setShowCreate}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle>Record Saving</DialogTitle>
                    </DialogHeader>
                    <form onSubmit={handleCreate} className="space-y-4">
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1">
                                <Label>Category</Label>
                                <Select
                                    value={form.category}
                                    onValueChange={v => setForm(f => ( {...f, category: v} ))}
                                >
                                    <SelectTrigger><SelectValue/></SelectTrigger>
                                    <SelectContent>
                                        {['energy', 'water', 'insurance', 'maintenance', 'admin', 'legal', 'other'].map(c => (
                                            <SelectItem key={c} value={c} className="capitalize">{c}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                            <div className="space-y-1">
                                <Label htmlFor="s-saved">Amount Saved ($) *</Label>
                                <Input
                                    id="s-saved"
                                    type="number"
                                    min="0"
                                    step="0.01"
                                    required
                                    value={form.amount_saved}
                                    onChange={e => setForm(f => ( {...f, amount_saved: e.target.value} ))}
                                    placeholder="e.g. 1500.00"
                                />
                            </div>
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="s-original">Original Cost ($)</Label>
                            <Input
                                id="s-original"
                                type="number"
                                min="0"
                                step="0.01"
                                value={form.original_cost}
                                onChange={e => setForm(f => ( {...f, original_cost: e.target.value} ))}
                                placeholder="What it would have cost (optional)"
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="s-summary">Resident Summary *</Label>
                            <Input
                                id="s-summary"
                                required
                                value={form.resident_summary}
                                onChange={e => setForm(f => ( {...f, resident_summary: e.target.value} ))}
                                placeholder="Plain-English summary for residents"
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="s-method">Saving Method *</Label>
                            <Input
                                id="s-method"
                                required
                                value={form.saving_method}
                                onChange={e => setForm(f => ( {...f, saving_method: e.target.value} ))}
                                placeholder="e.g. Bulk purchase, vendor negotiation"
                            />
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="s-desc">Internal Notes</Label>
                            <Textarea
                                id="s-desc"
                                value={form.description}
                                onChange={e => setForm(f => ( {...f, description: e.target.value} ))}
                                rows={2}
                            />
                        </div>
                        <DialogFooter>
                            <Button type="button" variant="outline" onClick={() => setShowCreate(false)}>Cancel</Button>
                            <Button type="submit" disabled={creating}>
                                {creating && <Loader2 className="h-4 w-4 mr-2 animate-spin"/>}
                                Record
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>
        </div>
    );
}
