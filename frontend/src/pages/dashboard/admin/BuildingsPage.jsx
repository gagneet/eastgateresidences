import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from '../../../components/ui/alert-dialog';
import { Textarea } from '../../../components/ui/textarea';
import { ArrowRight, Archive, Building2, ExternalLink, Loader2, MapPin, Plus, RefreshCcw, Search } from 'lucide-react';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: BuildingsPage
 * Path: frontend/src/pages/dashboard/admin/BuildingsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const BuildingsPage = () => {
    const {api, isAdmin, switchBuilding, selectedBuilding, loading: authLoading} = useAuth();
    const router = useRouter();
    const [buildings, setBuildings] = useState([]);
    const [archivedBuildings, setArchivedBuildings] = useState([]);
    const [loading, setLoading] = useState(true);
    const [loadingArchived, setLoadingArchived] = useState(false);
    const [showArchived, setShowArchived] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');

    // Archive dialog state
    const [archiveTarget, setArchiveTarget] = useState(null); // { id, name }
    const [archiveReason, setArchiveReason] = useState('');
    const [archiving, setArchiving] = useState(false);

    // Revive dialog state
    const [reviveTarget, setReviveTarget] = useState(null); // { id, name }
    const [reviving, setReviving] = useState(false);
    /**
     * @generated FunctionHeader
     * Function: getBuildingSwitchId
     * Path: frontend/src/pages/dashboard/admin/BuildingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getBuildingSwitchId = (building) => building?.id || building?.building_id;
    /**
     * @generated FunctionHeader
     * Function: isSelectedBuilding
     * Path: frontend/src/pages/dashboard/admin/BuildingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const isSelectedBuilding = (building) => {
        const switchId = getBuildingSwitchId(building);
        return selectedBuilding?.id === switchId || selectedBuilding?.building_id === building?.building_id;
    };
    /**
     * @generated FunctionHeader
     * Function: mergeBuildings
     * Path: frontend/src/pages/dashboard/admin/BuildingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const mergeBuildings = (schemeBuildings = [], legacyBuildings = []) => {
        const legacyByPlan = new Map();
        const legacyById = new Map();

        legacyBuildings.forEach((building) => {
            const buildingPlan = building.building_id || building.plan_number;
            if (buildingPlan) legacyByPlan.set(String(buildingPlan), building);
            if (building.id) legacyById.set(String(building.id), building);
        });

        const seenPlans = new Set();
        const mergedBuildings = schemeBuildings.map((scheme) => {
            const buildingPlan = scheme.building_id || scheme.plan_number || scheme.id;
            const legacy = legacyByPlan.get(String(buildingPlan)) || legacyById.get(String(scheme.id));
            if (buildingPlan) seenPlans.add(String(buildingPlan));

            return {
                ...legacy,
                ...scheme,
                id: scheme.id || legacy?.id || buildingPlan,
                building_id: buildingPlan || legacy?.building_id || legacy?.plan_number,
                name: scheme.name || legacy?.name || buildingPlan,
                address: legacy?.address || '',
                suburb: legacy?.suburb || '',
                state: legacy?.state || '',
                canArchive: Boolean(legacy && ( legacy.id || legacy.building_id || legacy.plan_number )),
            };
        });

        legacyBuildings.forEach((building) => {
            const buildingPlan = building.building_id || building.plan_number || building.id;
            if (!buildingPlan || seenPlans.has(String(buildingPlan))) return;
            mergedBuildings.push({
                ...building,
                id: building.id || buildingPlan,
                building_id: buildingPlan,
                canArchive: true,
            });
        });

        return mergedBuildings;
    };

    const fetchBuildings = useCallback(async () => {
        try {
            setLoading(true);
            const [schemesRes, legacyRes] = await Promise.allSettled([
                api.get('/buildings/me'),
                api.get('/buildings/all')
            ]);

            const schemeBuildings = schemesRes.status === 'fulfilled' ? ( schemesRes.value.data || [] ) : [];
            const legacyBuildings = legacyRes.status === 'fulfilled' ? ( legacyRes.value.data || [] ) : [];

            if (schemesRes.status === 'rejected' && legacyRes.status === 'rejected') {
                throw schemesRes.reason || legacyRes.reason;
            }

            setBuildings(mergeBuildings(schemeBuildings, legacyBuildings));
        } catch (err) {
            console.error('Failed to fetch buildings:', err);
            toast.error('Failed to load buildings');
        } finally {
            setLoading(false);
        }
    }, [api]);

    const fetchArchivedBuildings = useCallback(async () => {
        try {
            setLoadingArchived(true);
            const res = await api.get('/portfolio/buildings/archived');
            setArchivedBuildings(res.data?.buildings || []);
        } catch (err) {
            console.error('Failed to fetch archived buildings:', err);
            toast.error('Failed to load archived buildings');
        } finally {
            setLoadingArchived(false);
        }
    }, [api]);

    useEffect(() => {
        fetchBuildings();
    }, [fetchBuildings]);

    useEffect(() => {
        if (showArchived && archivedBuildings.length === 0) {
            fetchArchivedBuildings();
        }
    }, [archivedBuildings.length, fetchArchivedBuildings, showArchived]);
    /**
     * @generated FunctionHeader
     * Function: handleEnterBuilding
     * Path: frontend/src/pages/dashboard/admin/BuildingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleEnterBuilding = async (buildingId) => {
        if (!buildingId) {
            toast.error('This building is missing a switch identifier');
            return;
        }
        if (buildingId === selectedBuilding?.id || buildingId === selectedBuilding?.building_id) {
            toast.info('You are already viewing this building');
            return;
        }
        await switchBuilding(buildingId);
    };
    /**
     * @generated FunctionHeader
     * Function: handleArchiveConfirm
     * Path: frontend/src/pages/dashboard/admin/BuildingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleArchiveConfirm = async () => {
        if (!archiveTarget) return;
        setArchiving(true);
        try {
            await api.delete(`/portfolio/buildings/${archiveTarget.id}`, {
                data: {reason: archiveReason || null},
            });
            toast.success(`"${archiveTarget.name}" has been archived`);
            setBuildings(prev => prev.filter(b => b.id !== archiveTarget.id));
            // Refresh archived list if visible
            if (showArchived) fetchArchivedBuildings();
        } catch (err) {
            const msg = err?.response?.data?.detail || 'Failed to archive building';
            toast.error(msg);
        } finally {
            setArchiving(false);
            setArchiveTarget(null);
            setArchiveReason('');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleReviveConfirm
     * Path: frontend/src/pages/dashboard/admin/BuildingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleReviveConfirm = async () => {
        if (!reviveTarget) return;
        setReviving(true);
        try {
            await api.post(`/portfolio/buildings/${reviveTarget.id}/revive`);
            toast.success(`"${reviveTarget.name}" has been revived`);
            setArchivedBuildings(prev => prev.filter(b => b.id !== reviveTarget.id));
            // Re-fetch active buildings to include the revived one
            fetchBuildings();
        } catch (err) {
            const msg = err?.response?.data?.detail || 'Failed to revive building';
            toast.error(msg);
        } finally {
            setReviving(false);
            setReviveTarget(null);
        }
    };

    const filteredBuildings = buildings.filter(b =>
        b.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        b.address?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        b.building_id?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        b.tenant_name?.toLowerCase().includes(searchQuery.toLowerCase())
    );

    const filteredArchived = archivedBuildings.filter(b =>
        b.name?.toLowerCase().includes(searchQuery.toLowerCase()) ||
        b.address?.toLowerCase().includes(searchQuery.toLowerCase())
    );

    // Auth must resolve before isAdmin() is meaningful — otherwise a real super
    // admin sees "Unauthorized" on first paint.
    if (authLoading) {
        return <div className="p-8 text-center text-muted-foreground">Loading…</div>;
    }

    if (!isAdmin()) {
        return <div className="p-8 text-center">Unauthorized. Super Admin only.</div>;
    }

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">System Buildings</h1>
                    <p className="text-muted-foreground mt-1">Manage and access all property complexes in the
                        system.</p>
                </div>
                <div className="flex items-center gap-2">
                    <Button
                        variant="outline"
                        className="gap-2"
                        onClick={() => setShowArchived(v => !v)}
                        data-testid="toggle-archived"
                    >
                        <Archive className="h-4 w-4"/>
                        {showArchived ? 'Hide Archived' : 'Show Archived'}
                    </Button>
                    <Button className="gap-2" onClick={() => router.push('/admin/buildings/onboard')}>
                        <Plus className="h-4 w-4"/>
                        Add New Building
                    </Button>
                </div>
            </div>

            {/* Search */}
            <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                <Input
                    placeholder="Search by name, address, or plan number..."
                    className="pl-10"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                />
            </div>

            {/* Active buildings */}
            {loading ? (
                <div className="flex items-center justify-center py-20">
                    <Loader2 className="h-8 w-8 animate-spin text-primary"/>
                </div>
            ) : (
                <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                    {filteredBuildings.map((building) => (
                        <Card
                            key={getBuildingSwitchId(building)}
                            className={`group hover:border-primary/50 transition-all ${isSelectedBuilding(building) ? 'ring-2 ring-primary ring-offset-2' : ''}`}
                        >
                            <CardHeader className="pb-3">
                                <div className="flex items-start justify-between">
                                    <div className="flex items-center gap-3">
                                        <div className="p-2 rounded-lg bg-primary/10 text-primary">
                                            <Building2 className="h-5 w-5"/>
                                        </div>
                                        <div>
                                            <CardTitle className="text-lg">{building.name}</CardTitle>
                                            <CardDescription className="flex items-center gap-1 mt-0.5">
                                                <MapPin className="h-3 w-3"/>
                                               {building.address || building.tenant_name || 'Address unavailable'}
                                            </CardDescription>
                                        </div>
                                    </div>
                                   <div className="flex items-center gap-2">
                                       {building.is_demo && (
                                           <span
                                               className="text-[10px] font-bold uppercase bg-amber-100 text-amber-800 px-2 py-0.5 rounded-full">
                                               Demo
                                           </span>
                                       )}
                                       {isSelectedBuilding(building) && (
                                           <span
                                               className="text-[10px] font-bold uppercase bg-primary text-primary-foreground px-2 py-0.5 rounded-full">
                                               Active
                                           </span>
                                       )}
                                   </div>
                                </div>
                            </CardHeader>
                            <CardContent>
                                <div className="grid grid-cols-2 gap-4 mb-4">
                                    <div className="space-y-1">
                                        <p className="text-xs text-muted-foreground font-medium uppercase">Plan ID</p>
                                        <p className="text-sm font-semibold">{building.building_id}</p>
                                    </div>
                                    <div className="space-y-1">
                                        <p className="text-xs text-muted-foreground font-medium uppercase">Location</p>
                                       <p className="text-sm font-semibold">
                                           {[building.suburb, building.state].filter(Boolean).join(', ') || building.jurisdiction || '—'}
                                       </p>
                                   </div>
                                </div>
                                <div className="flex items-center gap-2 pt-2 border-t border-border">
                                    <Button
                                       variant={isSelectedBuilding(building) ? 'secondary' : 'default'}
                                       className="flex-1 gap-2"
                                       onClick={() => handleEnterBuilding(getBuildingSwitchId(building))}
                                       disabled={isSelectedBuilding(building)}
                                    >
                                       {isSelectedBuilding(building) ? 'Already Active' : 'Enter Dashboard'}
                                       <ArrowRight className="h-4 w-4"/>
                                    </Button>
                                    <Button variant="outline" size="icon" title="View Public Page">
                                        <ExternalLink className="h-4 w-4"/>
                                    </Button>
                                    <Button
                                        variant="outline"
                                        size="icon"
                                       title={building.canArchive ? 'Archive Building' : 'Archive unavailable for this record'}
                                        className="text-destructive hover:bg-destructive/10"
                                        onClick={() => setArchiveTarget({id: building.id, name: building.name})}
                                       data-testid={`archive-btn-${building.building_id}`}
                                       disabled={!building.canArchive}
                                    >
                                        <Archive className="h-4 w-4"/>
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            )}

            {!loading && filteredBuildings.length === 0 && (
                <div className="text-center py-20 bg-muted/20 rounded-xl border border-dashed">
                    <Building2 className="h-12 w-12 text-muted-foreground/30 mx-auto mb-4"/>
                    <h3 className="font-semibold text-lg">No buildings found</h3>
                    <p className="text-muted-foreground">Adjust your search or add a new building.</p>
                </div>
            )}

            {/* Archived buildings section */}
            {showArchived && (
                <div className="space-y-4 pt-4 border-t border-dashed">
                    <div className="flex items-center gap-3">
                        <Archive className="h-5 w-5 text-muted-foreground"/>
                        <h2 className="text-xl font-semibold text-muted-foreground">Archived Buildings</h2>
                        <span className="text-xs bg-muted px-2 py-0.5 rounded-full">
                            {archivedBuildings.length} record{archivedBuildings.length !== 1 ? 's' : ''}
                        </span>
                    </div>
                    <p className="text-sm text-muted-foreground">
                        Archived buildings are hidden from all normal views. Data is retained for audit and compliance
                        purposes.
                        Super Admins can revive a building to restore it to active status.
                    </p>

                    {loadingArchived ? (
                        <div className="flex items-center justify-center py-10">
                            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground"/>
                        </div>
                    ) : (
                        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
                            {filteredArchived.map((building) => (
                                <Card key={building.id} className="border-dashed opacity-75">
                                    <CardHeader className="pb-3">
                                        <div className="flex items-center gap-3">
                                            <div className="p-2 rounded-lg bg-muted text-muted-foreground">
                                                <Archive className="h-5 w-5"/>
                                            </div>
                                            <div>
                                                <CardTitle
                                                    className="text-lg text-muted-foreground">{building.name}</CardTitle>
                                                <CardDescription className="flex items-center gap-1 mt-0.5">
                                                    <MapPin className="h-3 w-3"/>
                                                    {building.address}
                                                </CardDescription>
                                            </div>
                                        </div>
                                    </CardHeader>
                                    <CardContent>
                                        <div className="grid grid-cols-2 gap-4 mb-3">
                                            <div className="space-y-1">
                                                <p className="text-xs text-muted-foreground font-medium uppercase">Archived</p>
                                                <p className="text-sm font-semibold">
                                                    {building.archived_at
                                                        ? new Date(building.archived_at).toLocaleDateString('en-AU')
                                                        : '—'}
                                                </p>
                                            </div>
                                            <div className="space-y-1">
                                                <p className="text-xs text-muted-foreground font-medium uppercase">Reason</p>
                                                <p className="text-sm font-semibold truncate"
                                                   title={building.archived_reason}>
                                                    {building.archived_reason || '—'}
                                                </p>
                                            </div>
                                        </div>
                                        <div className="pt-2 border-t border-dashed">
                                            <Button
                                                variant="outline"
                                                className="w-full gap-2"
                                                onClick={() => setReviveTarget({id: building.id, name: building.name})}
                                                data-testid={`revive-btn-${building.id}`}
                                            >
                                                <RefreshCcw className="h-4 w-4"/>
                                                Revive Building
                                            </Button>
                                        </div>
                                    </CardContent>
                                </Card>
                            ))}
                            {filteredArchived.length === 0 && !loadingArchived && (
                                <div className="col-span-3 text-center py-10 text-muted-foreground">
                                    No archived buildings found.
                                </div>
                            )}
                        </div>
                    )}
                </div>
            )}

            {/* Archive confirmation dialog */}
            <AlertDialog open={!!archiveTarget} onOpenChange={(open) => !open && setArchiveTarget(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Archive &ldquo;{archiveTarget?.name}&rdquo;?</AlertDialogTitle>
                        <AlertDialogDescription>
                            This building will be hidden from all views. All data is retained for audit and compliance
                            purposes (7-year retention). A Super Admin can revive it at any time.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <div className="my-2">
                        <Textarea
                            placeholder="Reason for archiving (optional but recommended for audit trail)..."
                            value={archiveReason}
                            onChange={(e) => setArchiveReason(e.target.value)}
                            className="text-sm"
                            data-testid="archive-reason"
                        />
                    </div>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={archiving}>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={handleArchiveConfirm}
                            disabled={archiving}
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                            data-testid="confirm-archive"
                        >
                            {archiving ? <Loader2 className="h-4 w-4 animate-spin mr-2"/> : null}
                            Archive Building
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>

            {/* Revive confirmation dialog */}
            <AlertDialog open={!!reviveTarget} onOpenChange={(open) => !open && setReviveTarget(null)}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Revive &ldquo;{reviveTarget?.name}&rdquo;?</AlertDialogTitle>
                        <AlertDialogDescription>
                            This building will be restored to active status and will reappear across all
                            relevant views. All associated memberships and EC records will also be restored.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel disabled={reviving}>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                            onClick={handleReviveConfirm}
                            disabled={reviving}
                            data-testid="confirm-revive"
                        >
                            {reviving ? <Loader2 className="h-4 w-4 animate-spin mr-2"/> : null}
                            Revive Building
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
};

export default BuildingsPage;
