"use client";
// @featuretrace:financial-mock-boundary — Settings UI: per-building mock/live switches for the
//   external financial integrations (DEFT/BPAY, Stripe, provider protocols, ABA; plus bank direct
//   debit & transaction history as its own switch).
// Layer: frontend
// Data flow: this card -> GET/PUT /buildings/{building_id}/integrations/mock-mode
//            -> routers/building_integrations.py -> core.feature_toggle_overrides
//            -> services/financial_mock_mode.py -> DEFT / Stripe / ProviderRegistry / ABA.
// Related: backend/routers/building_integrations.py
//          backend/services/financial_mock_mode.py
import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { Badge } from '../ui/badge';
import { Button } from '../ui/button';
import { Label } from '../ui/label';
import { Switch } from '../ui/switch';
import { Textarea } from '../ui/textarea';
import { AlertTriangle, FlaskConical, Loader2, Lock } from 'lucide-react';
import { toast } from 'sonner';

/** Roles allowed to see and hold these switches. Mirrors the backend capability
 *  `building.integrations.manage` (_BUILDING_MANAGERS) — ec_member is deliberately
 *  excluded: pointing a building at a real bank is a management act, not a
 *  committee one. */
const MANAGER_ROLES = ['super_admin', 'strata_admin', 'strata_manager'];

const FinancialMockModeCard = () => {
    const { api, user, selectedBuilding } = useAuth();
    const buildingId = selectedBuilding?.building_id;

    const [state, setState] = useState(null);
    const [loading, setLoading] = useState(true);
    const [savingKey, setSavingKey] = useState(null);
    const [pendingKey, setPendingKey] = useState(null);   // switch awaiting a go-live reason
    const [reason, setReason] = useState('');

    const role = user?.effective_role || user?.role;
    const canSee = MANAGER_ROLES.includes(role || '');

    const load = useCallback(async () => {
        if (!buildingId) return;
        setLoading(true);
        try {
            const res = await api.get(`/buildings/${buildingId}/integrations/mock-mode`);
            setState(res.data);
        } catch {
            // A 403 here means this manager is not assigned to the selected building —
            // the backend decides that, not the client.
            setState(null);
        } finally {
            setLoading(false);
        }
    }, [api, buildingId]);

    useEffect(() => { if (canSee) load(); }, [canSee, load]);

    const applyChange = async (featureKey, isMocked, why) => {
        setSavingKey(featureKey);
        try {
            const res = await api.put(
                `/buildings/${buildingId}/integrations/mock-mode/${featureKey}`,
                { is_mocked: isMocked, reason: why || null },
            );
            setState(res.data);
            setPendingKey(null);
            setReason('');
            toast.success(isMocked ? 'Switched back to mock providers.' : 'Switched to LIVE providers.');
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Could not change this setting.');
        } finally {
            setSavingKey(null);
        }
    };

    const onToggle = (featureKey, nextMocked) => {
        // Returning to mock is the safe direction and applies immediately. Going live
        // needs a reason, which the backend also enforces — this is the prompt, not the check.
        if (nextMocked) return applyChange(featureKey, true, null);
        setPendingKey(featureKey);
    };

    if (!canSee) return null;

    return (
        <Card data-testid="financial-mock-mode-card">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <FlaskConical className="h-5 w-5"/>
                    Financial Integrations
                </CardTitle>
                <CardDescription>
                    While a switch is on, this building&apos;s integrations run against mock
                    implementations instead of a live financial institution. Demo Bank is not
                    affected — it is a built-in emulator with its own settings.
                </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
                {loading && (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin"/> Loading…
                    </div>
                )}

                {!loading && !state && (
                    <p className="text-sm text-muted-foreground">
                        These settings are not available for the selected building.
                    </p>
                )}

                {!loading && state?.forced_by_environment && (
                    <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950/40">
                        <Lock className="mt-0.5 h-4 w-4 shrink-0"/>
                        <span>
                            Mock mode is enforced for every building by this deployment&apos;s
                            configuration, so these switches cannot be turned off here.
                        </span>
                    </div>
                )}

                {!loading && state?.switches?.map((s) => (
                    <div key={s.feature_key} className="rounded-lg border p-4">
                        <div className="flex items-start justify-between gap-4">
                            <div className="space-y-1">
                                <div className="flex items-center gap-2">
                                    <Label htmlFor={s.feature_key} className="font-medium">{s.label}</Label>
                                    <Badge variant={s.is_mocked ? 'secondary' : 'destructive'}>
                                        {s.is_mocked ? 'Mock' : 'Live'}
                                    </Badge>
                                </div>
                                <p className="text-sm text-muted-foreground">{s.detail}</p>
                            </div>
                            <Switch
                                id={s.feature_key}
                                data-testid={`toggle-${s.feature_key}`}
                                checked={s.is_mocked}
                                disabled={state.forced_by_environment || savingKey === s.feature_key}
                                onCheckedChange={(checked) => onToggle(s.feature_key, checked)}
                            />
                        </div>

                        {pendingKey === s.feature_key && (
                            <div className="mt-4 space-y-3 rounded-md border border-destructive/40 bg-destructive/5 p-3">
                                <div className="flex items-start gap-2 text-sm">
                                    <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive"/>
                                    <span>
                                        Turning this off points {selectedBuilding?.name || 'this building'} at
                                        <strong> live financial providers</strong>. Real money can move.
                                    </span>
                                </div>
                                <div className="space-y-1">
                                    <Label htmlFor={`reason-${s.feature_key}`}>Reason (recorded)</Label>
                                    <Textarea
                                        id={`reason-${s.feature_key}`}
                                        data-testid={`reason-${s.feature_key}`}
                                        value={reason}
                                        maxLength={500}
                                        onChange={(e) => setReason(e.target.value)}
                                        placeholder="e.g. Bank integration signed off by the committee on 2026-09-01"
                                    />
                                </div>
                                <div className="flex gap-2">
                                    <Button
                                        variant="destructive"
                                        size="sm"
                                        data-testid={`confirm-live-${s.feature_key}`}
                                        disabled={!reason.trim() || savingKey === s.feature_key}
                                        onClick={() => applyChange(s.feature_key, false, reason.trim())}
                                    >
                                        {savingKey === s.feature_key && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}
                                        Go live
                                    </Button>
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        onClick={() => { setPendingKey(null); setReason(''); }}
                                    >
                                        Cancel
                                    </Button>
                                </div>
                            </div>
                        )}
                    </div>
                ))}
            </CardContent>
        </Card>
    );
};

export default FinancialMockModeCard;
