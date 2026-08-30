// @featuretrace:levy-fairness — configure which lots are compared with which (building-scoped).
// Layer: frontend
// Data flow: /benefit-groups CRUD → core.benefit_groups + core.lot_benefit_groups.
// Related: backend/routers/benefit_groups.py
//          backend/services/levy_fairness_service.py
/**
 * Benefit Groups — the cohorts the fairness analysis compares.
 *
 * This page exists because the analysis previously INFERRED its cohorts from a unit_type
 * string or a UA/TH unit-number prefix. That collapses to a single group for a scheme
 * that is all one building form, and mis-groups anything split on another axis — a
 * commercial ground floor, two towers over one basement — with no way to correct it.
 *
 * Groups default to "Group A" / "Group B" on purpose. A neutral name keeps the analysis
 * about who benefits from what, rather than implying the building form is itself the
 * justification for a different contribution.
 */
'use client';

import React, {useCallback, useEffect, useState} from 'react';
import {useAuth} from '@/contexts/AuthContext';
import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {Button} from '@/components/ui/button';
import {Input} from '@/components/ui/input';
import {Badge} from '@/components/ui/badge';
import {toast} from 'sonner';
import {Loader2, Plus, Trash2, Users} from 'lucide-react';

export default function BenefitGroupsSettingsPage() {
    const {api, isManager, loading: authLoading} = useAuth();
    const [groups, setGroups] = useState([]);
    const [unassigned, setUnassigned] = useState([]);
    const [selected, setSelected] = useState(new Set());
    const [newName, setNewName] = useState('');
    const [state, setState] = useState('loading');
    const [saving, setSaving] = useState(false);

    const load = useCallback(async () => {
        setState('loading');
        try {
            // Not Promise.all with a catch-to-empty: an unreachable endpoint must show as
            // an error, not as "this building has no groups configured", which is a real
            // and very different state.
            const [g, u] = await Promise.all([
                api.get('/benefit-groups'),
                api.get('/benefit-groups/unassigned'),
            ]);
            setGroups(g.data || []);
            setUnassigned(u.data || []);
            setState('ready');
        } catch (err) {
            setState('failed');
            toast.error(err?.response?.data?.detail || 'Could not load benefit groups');
        }
    }, [api]);

    useEffect(() => {
        if (!authLoading) load();
    }, [authLoading, load]);

    const createGroup = async () => {
        // Default to the next neutral letter rather than making the operator invent a
        // name before they can start.
        const name = newName.trim() || `Group ${String.fromCharCode(65 + groups.length)}`;
        setSaving(true);
        try {
            await api.post('/benefit-groups', {name, display_order: groups.length});
            setNewName('');
            toast.success(`${name} created`);
            await load();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Could not create the group');
        } finally {
            setSaving(false);
        }
    };

    const assign = async (groupId) => {
        if (!selected.size) return;
        setSaving(true);
        try {
            await api.put(`/benefit-groups/${groupId}/lots`, {lot_ids: [...selected]});
            toast.success(`${selected.size} lot(s) assigned`);
            setSelected(new Set());
            await load();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Could not assign the lots');
        } finally {
            setSaving(false);
        }
    };

    const removeGroup = async (groupId, name) => {
        setSaving(true);
        try {
            await api.delete(`/benefit-groups/${groupId}`);
            // Say what happened to the lots. "Deleted" alone leaves the operator unsure
            // whether their assignments went somewhere else.
            toast.success(`${name} deleted — its lots are now unassigned`);
            await load();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Could not delete the group');
        } finally {
            setSaving(false);
        }
    };

    const toggle = (lotId) => setSelected(prev => {
        const next = new Set(prev);
        next.has(lotId) ? next.delete(lotId) : next.add(lotId);
        return next;
    });

    if (authLoading || state === 'loading') {
        return <div className="p-8 flex items-center gap-2 text-muted-foreground">
            <Loader2 className="w-4 h-4 animate-spin"/> Loading benefit groups…
        </div>;
    }
    if (!isManager()) {
        return <div className="p-8 text-muted-foreground">
            Defining benefit groups is a governance decision and is restricted to the
            committee and managers.
        </div>;
    }
    if (state === 'failed') {
        return <div className="p-8">
            <p className="font-medium">Benefit groups could not be loaded.</p>
            <p className="text-sm text-muted-foreground mt-1">
                This is not the same as having none configured — the setting is unknown
                rather than empty.
            </p>
            <Button className="mt-4" onClick={load}>Retry</Button>
        </div>;
    }

    return (
        <div className="p-6 space-y-6 max-w-5xl">
            <div>
                <h1 className="text-2xl font-semibold">Benefit Groups</h1>
                <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
                    The cohorts the levy fairness analysis compares. Assign each lot to the
                    group whose shared services it actually uses. Names are yours — the
                    defaults are deliberately neutral, because the comparison is about
                    benefit, not building type.
                </p>
            </div>

            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-base flex items-center gap-2">
                        <Users className="w-4 h-4"/> Groups
                    </CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                    {groups.length === 0 && (
                        <p className="text-sm text-muted-foreground">
                            No groups configured. Until at least two exist, the fairness
                            analysis falls back to inferring cohorts from unit type, which
                            is unreliable for this scheme.
                        </p>
                    )}
                    {groups.map(g => (
                        <div key={g.benefit_group_id}
                             className="flex items-center gap-3 border rounded-md p-3">
                            <div className="flex-1">
                                <div className="font-medium">{g.name}</div>
                                <div className="text-xs text-muted-foreground">
                                    {g.lot_count} lot{g.lot_count === 1 ? '' : 's'}
                                </div>
                            </div>
                            {selected.size > 0 && (
                                <Button size="sm" disabled={saving}
                                        onClick={() => assign(g.benefit_group_id)}>
                                    Assign {selected.size} selected
                                </Button>
                            )}
                            <Button size="sm" variant="ghost" disabled={saving}
                                    onClick={() => removeGroup(g.benefit_group_id, g.name)}>
                                <Trash2 className="w-4 h-4"/>
                            </Button>
                        </div>
                    ))}
                    <div className="flex gap-2 pt-2">
                        <Input value={newName} onChange={e => setNewName(e.target.value)}
                               placeholder={`Group ${String.fromCharCode(65 + groups.length)}`}
                               className="max-w-xs"/>
                        <Button onClick={createGroup} disabled={saving}>
                            <Plus className="w-4 h-4 mr-1"/> Add group
                        </Button>
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-base">
                        Unassigned lots <Badge variant="secondary">{unassigned.length}</Badge>
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    {unassigned.length === 0 ? (
                        <p className="text-sm text-muted-foreground">Every lot is assigned.</p>
                    ) : (
                        <>
                            <p className="text-xs text-muted-foreground mb-3">
                                An unassigned lot is excluded from the comparison rather than
                                placed in a default group — a default would silently change
                                who subsidises whom.
                            </p>
                            <div className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 gap-2">
                                {unassigned.map(l => (
                                    <button key={l.lot_id} onClick={() => toggle(l.lot_id)}
                                            className={`text-xs border rounded px-2 py-1 transition
                                                ${selected.has(l.lot_id)
                                                ? 'bg-primary text-primary-foreground border-primary'
                                                : 'hover:bg-muted'}`}>
                                        {l.unit_number}
                                    </button>
                                ))}
                            </div>
                        </>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
