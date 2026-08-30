"use client";
// @featuretrace:strata-web-portal-finance-ingest — Settings UI: per-building Strata Web portal
//   connection details (URL, scheme, username, password) + enabled toggle + sync trigger.
// Layer: frontend
// Data flow: this card -> GET/PUT /settings/strata-web-portal, POST /settings/strata-web-portal/sync
//            -> routers/strata_web_portal.py (building-scoped via X-Building-ID).
import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Switch } from '../ui/switch';
import { Globe, KeyRound, Loader2, RefreshCw, ShieldCheck } from 'lucide-react';
import { toast } from 'sonner';

/**
 * Per-building Strata Web portal connection settings.
 * Scopes automatically to the building currently selected in the building switcher
 * (the api instance injects X-Building-ID). Password is write-only: the backend
 * stores it encrypted and never returns it — we only ever learn `password_set`.
 */
const StrataWebPortalCard = () => {
    const { api } = useAuth();
    const [config, setConfig] = useState(null);
    const [form, setForm] = useState({ portal_url: '', scheme_number: '', username: '', password: '', enabled: false });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [syncing, setSyncing] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await api.get('/settings/strata-web-portal');
            const d = res.data || {};
            setConfig(d);
            setForm({
                portal_url: d.portal_url || '',
                scheme_number: d.scheme_number || '',
                username: d.username || '',
                password: '',  // never populated from the server
                enabled: !!d.enabled,
            });
        } catch {
            setConfig(null);
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => { load(); }, [load]);

    const handleSave = async () => {
        setSaving(true);
        try {
            // Only send password if the admin actually typed one (keeps the stored one otherwise).
            const payload = {
                portal_url: form.portal_url,
                scheme_number: form.scheme_number,
                username: form.username,
                enabled: form.enabled,
            };
            if (form.password) payload.password = form.password;
            const res = await api.put('/settings/strata-web-portal', payload);
            setConfig(res.data);
            setForm(f => ({ ...f, password: '' }));  // clear the password field after save
            toast.success('Portal connection saved');
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to save portal connection');
        } finally {
            setSaving(false);
        }
    };

    const handleSync = async () => {
        setSyncing(true);
        try {
            const res = await api.post('/settings/strata-web-portal/sync');
            const d = res.data || {};
            toast.success(d.scraped ? 'Portal scraped and snapshot staged' : 'Snapshot staged (live scrape unavailable)');
            load();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Sync failed');
        } finally {
            setSyncing(false);
        }
    };

    return (
        <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
            <CardHeader className="bg-muted/30 pb-4">
                <div className="flex items-center gap-2">
                    <Globe className="h-5 w-5 text-primary" />
                    <CardTitle>Strata Web Portal Connection</CardTitle>
                </div>
                <CardDescription>
                    Connection details this building uses to reach the Strata Web portal
                    (e.g. Civium). The scraper uses the selected building's own configuration.
                    The password is encrypted at rest and never shown again after saving.
                </CardDescription>
            </CardHeader>
            <CardContent className="pt-6 space-y-4">
                {loading ? (
                    <div className="h-40 bg-muted/40 animate-pulse rounded-xl" />
                ) : (
                    <>
                        <div className="grid gap-4 md:grid-cols-2">
                            <div className="space-y-1.5">
                                <Label htmlFor="portal_url">Portal URL</Label>
                                <Input
                                    id="portal_url"
                                    placeholder="https://my.civium.com.au"
                                    value={form.portal_url}
                                    onChange={e => setForm(f => ({ ...f, portal_url: e.target.value }))}
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="scheme_number">Scheme / Plan number</Label>
                                <Input
                                    id="scheme_number"
                                    placeholder="Portal scheme identifier for this building"
                                    value={form.scheme_number}
                                    onChange={e => setForm(f => ({ ...f, scheme_number: e.target.value }))}
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="portal_username">Username</Label>
                                <Input
                                    id="portal_username"
                                    autoComplete="off"
                                    value={form.username}
                                    onChange={e => setForm(f => ({ ...f, username: e.target.value }))}
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label htmlFor="portal_password" className="flex items-center gap-1">
                                    <KeyRound className="h-3.5 w-3.5" /> Password
                                </Label>
                                <Input
                                    id="portal_password"
                                    type="password"
                                    autoComplete="new-password"
                                    placeholder={config?.password_set ? '•••••••• (unchanged)' : 'Enter portal password'}
                                    value={form.password}
                                    onChange={e => setForm(f => ({ ...f, password: e.target.value }))}
                                />
                                <p className="text-[11px] text-muted-foreground">
                                    {config?.password_set
                                        ? 'A password is stored (encrypted). Leave blank to keep it.'
                                        : 'No password stored yet.'}
                                </p>
                            </div>
                        </div>

                        <div className="flex items-center justify-between rounded-xl border p-3">
                            <div className="flex items-center gap-2">
                                <ShieldCheck className="h-4 w-4 text-primary" />
                                <div>
                                    <p className="text-sm font-semibold">Enable scraper for this building</p>
                                    <p className="text-[11px] text-muted-foreground">
                                        When off, the scraper will not use these details.
                                    </p>
                                </div>
                            </div>
                            <Switch
                                checked={form.enabled}
                                onCheckedChange={v => setForm(f => ({ ...f, enabled: v }))}
                                aria-label="Enable scraper for this building"
                            />
                        </div>

                        {config?.last_synced_at && (
                            <p className="text-[11px] text-muted-foreground">
                                Last sync: {new Date(config.last_synced_at).toLocaleString('en-AU')}
                                {config.last_sync_status ? ` — ${config.last_sync_status}` : ''}
                            </p>
                        )}

                        <div className="flex flex-wrap items-center gap-2 pt-1">
                            <Button onClick={handleSave} disabled={saving} className="rounded-full">
                                {saving ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : null}
                                Save connection
                            </Button>
                            <Button
                                variant="outline"
                                onClick={handleSync}
                                disabled={syncing || !config?.enabled || !config?.password_set}
                                className="rounded-full"
                                title={!config?.enabled || !config?.password_set
                                    ? 'Save an enabled connection with a password first'
                                    : 'Scrape the portal (if available) and stage a snapshot'}
                            >
                                {syncing ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-1 h-4 w-4" />}
                                Sync now
                            </Button>
                        </div>
                    </>
                )}
            </CardContent>
        </Card>
    );
};

export default StrataWebPortalCard;
