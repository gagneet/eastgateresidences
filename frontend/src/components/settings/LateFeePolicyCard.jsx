"use client";
// @featuretrace:act_statutory_interest — Settings UI: per-building arrears late-fee policy
//   (amount / period / start year) that drives the computed interest & late fees on unit pages.
// Layer: frontend
// Data flow: this card -> GET/PUT /settings/late-fee-policy -> routers/settings.py
//            -> interest_penalty_service (building-scoped via X-Building-ID).
import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Switch } from '../ui/switch';
import { Loader2, Percent } from 'lucide-react';
import { toast } from 'sonner';

/**
 * Per-building arrears late-fee policy. Building-agnostic: scopes to the building selected in the
 * switcher (X-Building-ID). Drives the "Estimated Interest & Late Fees" shown on unit pages when a
 * building has no actual interest transactions. Any building can set its own amount/period/start.
 */
const LateFeePolicyCard = () => {
    const { api } = useAuth();
    const [form, setForm] = useState({ enabled: false, amount: '', period_days: 14, gst_inclusive: true, start_year: '' });
    const [rateInput, setRateInput] = useState('');       // per-building override (blank = fall back)
    const [effectiveRate, setEffectiveRate] = useState(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const [policyRes, rateRes] = await Promise.allSettled([
                api.get('/settings/late-fee-policy'),
                api.get('/settings/arrears-interest-rate'),
            ]);
            if (policyRes.status === 'fulfilled') {
                const d = policyRes.value.data || {};
                setForm({
                    enabled: !!d.enabled,
                    amount: d.amount != null ? d.amount : '',
                    period_days: d.period_days != null ? d.period_days : 14,
                    gst_inclusive: d.gst_inclusive !== false,
                    start_year: d.start_year != null ? d.start_year : '',
                });
            }
            if (rateRes.status === 'fulfilled') {
                const r = rateRes.value.data || {};
                setEffectiveRate(r);
                setRateInput(r.override_pct != null ? String(r.override_pct) : '');
            }
        } catch {
            // leave defaults
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => { load(); }, [load]);

    const handleSave = async () => {
        setSaving(true);
        try {
            // Interest-rate override: blank clears it (→ special resolution → jurisdiction default;
            // actual recorded interest transactions always take precedence on the unit pages).
            await api.put('/settings/arrears-interest-rate', {
                rate_pct: rateInput === '' ? null : Number(rateInput),
            });
            await api.put('/settings/late-fee-policy', {
                enabled: form.enabled,
                amount: form.amount === '' ? 0 : Number(form.amount),
                period_days: form.period_days === '' ? 14 : Number(form.period_days),
                gst_inclusive: form.gst_inclusive,
                start_year: form.start_year === '' ? null : Number(form.start_year),
            });
            toast.success('Arrears interest & late-fee policy saved');
            load();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to save policy');
        } finally {
            setSaving(false);
        }
    };

    const _sourceLabel = {
        special_resolution: 'special resolution',
        building_config: 'this building override',
        act_default: 'ACT statutory default',
        jurisdiction_default: 'jurisdiction default',
    };

    return (
        <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
            <CardHeader className="bg-muted/30 pb-4">
                <div className="flex items-center gap-2">
                    <Percent className="h-5 w-5 text-primary" />
                    <CardTitle>Arrears Interest &amp; Late Fees</CardTitle>
                </div>
                <CardDescription>
                    How overdue-levy interest and late fees are charged for this building. Actual
                    recorded interest transactions (from the bank) always take precedence; the
                    settings below only drive the estimate on unit pages when a unit has no recorded
                    interest transactions.
                </CardDescription>
            </CardHeader>
            <CardContent className="pt-6 space-y-4">
                {loading ? (
                    <div className="h-32 bg-muted/40 animate-pulse rounded-xl" />
                ) : (
                    <>
                        {/* Statutory / arrears interest rate */}
                        <div className="rounded-xl border p-3 space-y-2">
                            <div className="flex items-center justify-between gap-3 flex-wrap">
                                <Label htmlFor="interest_rate" className="text-sm font-semibold">Arrears interest rate (% p.a.)</Label>
                                <div className="flex items-center gap-2">
                                    <Input
                                        id="interest_rate" type="number" min="0" step="0.1" inputMode="decimal"
                                        placeholder="blank = default"
                                        className="w-28"
                                        value={rateInput}
                                        onChange={e => setRateInput(e.target.value)}
                                    />
                                    <span className="text-sm text-muted-foreground">% p.a.</span>
                                </div>
                            </div>
                            {effectiveRate && (
                                <p className="text-[11px] text-muted-foreground">
                                    Currently effective: <strong>{Number(effectiveRate.rate_pct ?? 0)}% p.a.</strong>
                                    {' '}({_sourceLabel[effectiveRate.source] || effectiveRate.source}
                                    {effectiveRate.jurisdiction ? `, ${effectiveRate.jurisdiction}` : ''})
                                    {effectiveRate.max_rate_pct != null ? ` · ceiling ${effectiveRate.max_rate_pct}%` : ''}.
                                </p>
                            )}
                            <p className="text-[11px] text-muted-foreground">
                                Leave blank to use any active special resolution, then the jurisdiction
                                default. If neither applies, interest is taken from actual recorded
                                transactions (the bank) rather than assumed. Setting a value here caps
                                to the jurisdiction ceiling when applied.
                            </p>
                        </div>

                        <div className="flex items-center justify-between rounded-xl border p-3">
                            <div>
                                <p className="text-sm font-semibold">Charge a late fee on overdue levies</p>
                                <p className="text-[11px] text-muted-foreground">When off, no late fee is estimated for this building.</p>
                            </div>
                            <Switch
                                checked={form.enabled}
                                onCheckedChange={v => setForm(f => ({ ...f, enabled: v }))}
                                aria-label="Enable late fee"
                            />
                        </div>

                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-1.5">
                                <Label htmlFor="lf_amount">Fee amount ($)</Label>
                                <Input
                                    id="lf_amount" type="number" min="0" step="0.01" inputMode="decimal"
                                    placeholder="55.00"
                                    value={form.amount}
                                    onChange={e => setForm(f => ({ ...f, amount: e.target.value }))}
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="lf_period">Per (days overdue)</Label>
                                <Input
                                    id="lf_period" type="number" min="1" step="1" inputMode="numeric"
                                    placeholder="14"
                                    value={form.period_days}
                                    onChange={e => setForm(f => ({ ...f, period_days: e.target.value }))}
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="lf_start">Applies from year (optional)</Label>
                                <Input
                                    id="lf_start" type="number" min="2000" step="1" inputMode="numeric"
                                    placeholder="e.g. 2022"
                                    value={form.start_year}
                                    onChange={e => setForm(f => ({ ...f, start_year: e.target.value }))}
                                />
                            </div>
                            <div className="flex items-end">
                                <div className="flex items-center justify-between rounded-xl border p-3 w-full">
                                    <Label htmlFor="lf_gst" className="text-sm">Fee is GST-inclusive</Label>
                                    <Switch
                                        id="lf_gst"
                                        checked={form.gst_inclusive}
                                        onCheckedChange={v => setForm(f => ({ ...f, gst_inclusive: v }))}
                                        aria-label="GST inclusive"
                                    />
                                </div>
                            </div>
                        </div>

                        <div className="pt-1">
                            <Button onClick={handleSave} disabled={saving} className="rounded-full">
                                {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
                                Save arrears settings
                            </Button>
                        </div>
                    </>
                )}
            </CardContent>
        </Card>
    );
};

export default LateFeePolicyCard;
