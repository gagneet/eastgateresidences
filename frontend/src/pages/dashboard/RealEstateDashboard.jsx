// @ts-nocheck
"use client";

import React, { useCallback, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { motion } from 'framer-motion';
import { toast } from 'sonner';
import {
    AlertTriangle,
    Building2,
    Calendar,
    Loader2,
    Mail,
    MessageSquare,
    Phone,
    RefreshCw,
    Users,
    Wrench
} from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../components/ui/table';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import { Tooltip, TooltipContent, TooltipTrigger } from '../../components/ui/tooltip';
import { Textarea } from '../../components/ui/textarea';
import { Label } from '../../components/ui/label';
import { formatCurrency, formatDate } from '../../lib/utils';

const ALLOWED_ROLES = ['real_estate_agent', 'super_admin'];

const URGENCY_CONFIG = {
    emergency: {label: 'Emergency', color: 'bg-red-100 text-red-800', order: 0},
    urgent: {label: 'Urgent', color: 'bg-orange-100 text-orange-800', order: 1},
    routine: {label: 'Routine', color: 'bg-gray-100 text-gray-800', order: 2},
};

const STATUS_CONFIG = {
    submitted: {label: 'Submitted', color: 'bg-blue-100 text-blue-800'},
    acknowledged: {label: 'Acknowledged', color: 'bg-yellow-100 text-yellow-800'},
    assigned: {label: 'Assigned', color: 'bg-purple-100 text-purple-800'},
    in_progress: {label: 'In Progress', color: 'bg-orange-100 text-orange-800'},
    completed: {label: 'Completed', color: 'bg-green-100 text-green-800'},
};
/**
 * @generated FunctionHeader
 * Function: RealEstateDashboard
 * Path: frontend/src/pages/dashboard/RealEstateDashboard.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
/**
 * @param {{initialTab?: string}} [props]
 */
const RealEstateDashboard = ({initialTab} = {}) => {
    const {user, api} = useAuth();
    const router = useRouter();
    const searchParams = useSearchParams();
    // Nav links deep-link into a specific tab. A nested App Router page (e.g.
    // /real-estate/renewals) passes `initialTab`; the legacy ?tab= query still
    // works and is used when no prop is supplied. Falls back to Properties for
    // an absent or unrecognised value.
    const VALID_TABS = ['properties', 'maintenance', 'renewals'];
    const requestedTab = initialTab ?? searchParams?.get?.('tab');
    const [properties, setProperties] = useState([]);
    const [maintenance, setMaintenance] = useState([]);
    const [, setRadar] = useState(null);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [activeTab, setActiveTab] = useState(
        VALID_TABS.includes(requestedTab) ? requestedTab : 'properties'
    );
    const [noteOpen, setNoteOpen] = useState(false);
    const [selectedMaint, setSelectedMaint] = useState(null);
    const [noteText, setNoteText] = useState('');
    const [submitting, setSubmitting] = useState(false);

    const canAccess = user && ALLOWED_ROLES.includes(user.role);

    const fetchAll = useCallback(async () => {
        setRefreshing(true);
        try {
            const [propsRes, maintRes, radarRes] = await Promise.all([
                api.get('/owner-hub/properties'),
                api.get('/tenant/maintenance'),
                api.get('/owner-hub/weekly-radar'),
            ]);
            setProperties(propsRes.data || []);
            setMaintenance(maintRes.data || []);
            setRadar(radarRes.data || null);
        } catch {
            toast.error('Failed to load dashboard data');
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, [api]);

    useEffect(() => {
        if (!canAccess) {
            router.push('/dashboard');
            return;
        }
        fetchAll();
    }, [canAccess, fetchAll, router]);
    /**
     * @generated FunctionHeader
     * Function: handleAddNote
     * Path: frontend/src/pages/dashboard/RealEstateDashboard.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAddNote = async (e) => {
        e.preventDefault();
        if (!selectedMaint || !noteText.trim()) return;
        setSubmitting(true);
        try {
            await api.post(`/tenant/maintenance/${selectedMaint.id}/messages`, {content: noteText});
            toast.success('Note added');
            setNoteOpen(false);
            setNoteText('');
        } catch {
            toast.error('Failed to add note');
        } finally {
            setSubmitting(false);
        }
    };

    if (!canAccess) return null;

    const totalProperties = properties.length;
    const activeTenancies = properties.filter(p => p.status === 'occupied').length;
    const pendingMaintenance = maintenance.filter(m => !['completed', 'rejected'].includes(m.status)).length;
    const today = new Date();
    const in90Days = new Date(today.getTime() + 90 * 24 * 60 * 60 * 1000);
    const expiringLeases = properties.filter(p => p.lease_end && new Date(p.lease_end) <= in90Days && new Date(p.lease_end) >= today);

    const sortedMaintenance = [...maintenance]
        .filter(m => !['completed', 'rejected'].includes(m.status))
        .sort((a, b) => ( URGENCY_CONFIG[ a.urgency ]?.order ?? 2 ) - ( URGENCY_CONFIG[ b.urgency ]?.order ?? 2 ));

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">Real Estate Portal</h1>
                    <p className="text-sm text-gray-500">Manage your assigned properties and tenants</p>
                </div>
                <Button variant="outline" size="sm" onClick={fetchAll} disabled={refreshing}>
                    <RefreshCw className={`h-4 w-4 mr-2 ${refreshing ? 'animate-spin' : ''}`}/>Refresh
                </Button>
            </div>

            {/* Summary cards */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-xs text-gray-500">Properties Managed</p>
                                <p className="text-2xl font-bold">{loading ? '—' : totalProperties}</p>
                            </div>
                            <Building2 className="h-7 w-7 text-blue-400 opacity-80"/>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-xs text-gray-500">Active Tenancies</p>
                                <p className="text-2xl font-bold">{loading ? '—' : activeTenancies}</p>
                            </div>
                            <Users className="h-7 w-7 text-green-400 opacity-80"/>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-xs text-gray-500">Pending Maintenance</p>
                                <p className={`text-2xl font-bold ${pendingMaintenance > 0 ? 'text-orange-600' : ''}`}>
                                    {loading ? '—' : pendingMaintenance}
                                </p>
                            </div>
                            <Wrench className="h-7 w-7 text-orange-400 opacity-80"/>
                        </div>
                    </CardContent>
                </Card>
                <Card>
                    <CardContent className="pt-6">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="text-xs text-gray-500">Leases Expiring (90d)</p>
                                <p className={`text-2xl font-bold ${expiringLeases.length > 0 ? 'text-amber-600' : ''}`}>
                                    {loading ? '—' : expiringLeases.length}
                                </p>
                            </div>
                            <Calendar className="h-7 w-7 text-amber-400 opacity-80"/>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Main tabs */}
            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList>
                    <TabsTrigger value="properties">Properties</TabsTrigger>
                    <TabsTrigger value="maintenance">
                        Maintenance Queue
                        {pendingMaintenance > 0 && (
                            <span
                                className="ml-1.5 inline-flex h-4 w-4 items-center justify-center rounded-full bg-orange-500 text-[10px] text-white">
                {pendingMaintenance}
              </span>
                        )}
                    </TabsTrigger>
                    <TabsTrigger value="renewals">
                        Lease Renewals
                        {expiringLeases.length > 0 && (
                            <span
                                className="ml-1.5 inline-flex h-4 w-4 items-center justify-center rounded-full bg-amber-500 text-[10px] text-white">
                {expiringLeases.length}
              </span>
                        )}
                    </TabsTrigger>
                </TabsList>

                {/* Properties tab */}
                <TabsContent value="properties" className="mt-4">
                    <Card>
                        <CardContent className="p-0">
                            {loading ? (
                                <div className="flex justify-center py-16"><Loader2
                                    className="h-8 w-8 animate-spin text-gray-300"/></div>
                            ) : (
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Address</TableHead>
                                            <TableHead>Owner</TableHead>
                                            <TableHead>Tenant</TableHead>
                                            <TableHead>Lease End</TableHead>
                                            <TableHead>Rent/wk</TableHead>
                                            <TableHead>Last Inspection</TableHead>
                                            <TableHead>Status</TableHead>
                                            <TableHead className="text-right">Actions</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {properties.length === 0 ? (
                                            <TableRow>
                                                <TableCell colSpan={8} className="text-center text-gray-400 py-8">No
                                                    properties assigned.</TableCell>
                                            </TableRow>
                                        ) : properties.map(prop => {
                                            const leaseEndSoon = prop.lease_end && new Date(prop.lease_end) <= in90Days && new Date(prop.lease_end) >= today;
                                            return (
                                                <TableRow key={prop.id}>
                                                    <TableCell>
                                                        <div>
                                                            <p className="font-medium">{prop.address}</p>
                                                            <p className="text-xs text-gray-500">{prop.suburb}, {prop.state}</p>
                                                        </div>
                                                    </TableCell>
                                                    <TableCell>{prop.owner_name || '—'}</TableCell>
                                                    <TableCell>{prop.tenant_name || '—'}</TableCell>
                                                    <TableCell>
                            <span className={leaseEndSoon ? 'text-amber-600 font-medium' : ''}>
                              {prop.lease_end ? formatDate(prop.lease_end) : '—'}
                            </span>
                                                    </TableCell>
                                                    <TableCell>{prop.weekly_rent ? formatCurrency(prop.weekly_rent) : '—'}</TableCell>
                                                    <TableCell>{prop.last_inspection ? formatDate(prop.last_inspection) : '—'}</TableCell>
                                                    <TableCell>
                            <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium
                              ${prop.status === 'occupied' ? 'bg-green-100 text-green-800' : 'bg-amber-100 text-amber-800'}`}>
                              {prop.status === 'occupied' ? 'Occupied' : 'Vacant'}
                            </span>
                                                    </TableCell>
                                                    <TableCell className="text-right">
                                                        <div className="flex gap-1 justify-end">
                                                            {prop.owner_email && (
                                                                <Tooltip>
                                                                    <TooltipTrigger asChild>
                                                                        <Button variant="ghost" size="icon"
                                                                                aria-label="Email Owner" asChild>
                                                                            <motion.a
                                                                                href={`mailto:${prop.owner_email}`}
                                                                                whileTap={{scale: 0.95}}>
                                                                                <Mail className="h-4 w-4"/>
                                                                            </motion.a>
                                                                        </Button>
                                                                    </TooltipTrigger>
                                                                    <TooltipContent>Email Owner</TooltipContent>
                                                                </Tooltip>
                                                            )}
                                                            {prop.tenant_email && (
                                                                <Tooltip>
                                                                    <TooltipTrigger asChild>
                                                                        <Button variant="ghost" size="icon"
                                                                                aria-label="Email Tenant" asChild>
                                                                            <motion.a
                                                                                href={`mailto:${prop.tenant_email}`}
                                                                                whileTap={{scale: 0.95}}>
                                                                                <Mail
                                                                                    className="h-4 w-4 text-green-600"/>
                                                                            </motion.a>
                                                                        </Button>
                                                                    </TooltipTrigger>
                                                                    <TooltipContent>Email Tenant</TooltipContent>
                                                                </Tooltip>
                                                            )}
                                                        </div>
                                                    </TableCell>
                                                </TableRow>
                                            );
                                        })}
                                    </TableBody>
                                </Table>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* Maintenance Queue tab */}
                <TabsContent value="maintenance" className="mt-4 space-y-3">
                    {loading ? (
                        <div className="flex justify-center py-16"><Loader2
                            className="h-8 w-8 animate-spin text-gray-300"/></div>
                    ) : sortedMaintenance.length === 0 ? (
                        <Card><CardContent className="py-10 text-center text-gray-400">No pending maintenance
                            requests.</CardContent></Card>
                    ) : sortedMaintenance.map(item => {
                        const uc = URGENCY_CONFIG[ item.urgency ] || URGENCY_CONFIG.routine;
                        const sc = STATUS_CONFIG[ item.status ] || STATUS_CONFIG.submitted;
                        return (
                            <Card key={item.id} className={item.urgency === 'emergency' ? 'border-red-300' : ''}>
                                <CardContent className="pt-4 pb-3">
                                    <div className="flex items-start gap-3">
                                        {item.urgency === 'emergency' &&
                                            <AlertTriangle className="h-5 w-5 text-red-500 shrink-0 mt-0.5"/>}
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <p className="font-medium">{item.issue_title}</p>
                                                <span
                                                    className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${uc.color}`}>{uc.label}</span>
                                                <span
                                                    className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${sc.color}`}>{sc.label}</span>
                                            </div>
                                            <p className="text-sm text-gray-500 mt-0.5">{item.property_address || item.unit_number} · {formatDate(item.created_at)}</p>
                                            {item.issue_description && (
                                                <p className="text-sm text-gray-600 mt-1 line-clamp-2">{item.issue_description}</p>
                                            )}
                                        </div>
                                        <Button variant="outline" size="sm" onClick={() => {
                                            setSelectedMaint(item);
                                            setNoteOpen(true);
                                        }}>
                                            <MessageSquare className="h-4 w-4 mr-1"/>Note
                                        </Button>
                                    </div>
                                </CardContent>
                            </Card>
                        );
                    })}
                </TabsContent>

                {/* Lease Renewals tab */}
                <TabsContent value="renewals" className="mt-4">
                    <Card>
                        <CardContent className="p-0">
                            {loading ? (
                                <div className="flex justify-center py-16"><Loader2
                                    className="h-8 w-8 animate-spin text-gray-300"/></div>
                            ) : expiringLeases.length === 0 ? (
                                <div className="py-12 text-center text-gray-400">
                                    <Calendar className="mx-auto h-12 w-12 text-gray-200 mb-3"/>
                                    No leases expiring within 90 days.
                                </div>
                            ) : (
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Property</TableHead>
                                            <TableHead>Tenant</TableHead>
                                            <TableHead>Lease End</TableHead>
                                            <TableHead>Days Left</TableHead>
                                            <TableHead>Rent/wk</TableHead>
                                            <TableHead className="text-right">Contact</TableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {expiringLeases
                                            .sort((a, b) => new Date(a.lease_end) - new Date(b.lease_end))
                                            .map(prop => {
                                                const daysLeft = Math.ceil(( new Date(prop.lease_end) - today ) / ( 1000 * 60 * 60 * 24 ));
                                                return (
                                                    <TableRow key={prop.id}>
                                                        <TableCell>
                                                            <p className="font-medium">{prop.address}</p>
                                                            <p className="text-xs text-gray-500">{prop.suburb}</p>
                                                        </TableCell>
                                                        <TableCell>{prop.tenant_name || '—'}</TableCell>
                                                        <TableCell
                                                            className="font-medium text-amber-600">{formatDate(prop.lease_end)}</TableCell>
                                                        <TableCell>
                              <span className={`font-semibold ${daysLeft <= 30 ? 'text-red-600' : 'text-amber-600'}`}>
                                {daysLeft}d
                              </span>
                                                        </TableCell>
                                                        <TableCell>{prop.weekly_rent ? formatCurrency(prop.weekly_rent) : '—'}</TableCell>
                                                        <TableCell className="text-right">
                                                            <div className="flex gap-1 justify-end">
                                                                {prop.tenant_email && (
                                                                    <Tooltip>
                                                                        <TooltipTrigger asChild>
                                                                            <Button variant="ghost" size="icon"
                                                                                    aria-label="Email Tenant" asChild>
                                                                                <motion.a
                                                                                    href={`mailto:${prop.tenant_email}`}
                                                                                    whileTap={{scale: 0.95}}>
                                                                                    <Mail className="h-4 w-4"/>
                                                                                </motion.a>
                                                                            </Button>
                                                                        </TooltipTrigger>
                                                                        <TooltipContent>Email Tenant</TooltipContent>
                                                                    </Tooltip>
                                                                )}
                                                                {prop.tenant_phone && (
                                                                    <Tooltip>
                                                                        <TooltipTrigger asChild>
                                                                            <Button variant="ghost" size="icon"
                                                                                    aria-label="Call Tenant" asChild>
                                                                                <motion.a
                                                                                    href={`tel:${prop.tenant_phone}`}
                                                                                    whileTap={{scale: 0.95}}>
                                                                                    <Phone className="h-4 w-4"/>
                                                                                </motion.a>
                                                                            </Button>
                                                                        </TooltipTrigger>
                                                                        <TooltipContent>Call Tenant</TooltipContent>
                                                                    </Tooltip>
                                                                )}
                                                            </div>
                                                        </TableCell>
                                                    </TableRow>
                                                );
                                            })}
                                    </TableBody>
                                </Table>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>

            {/* Add Note Dialog */}
            <Dialog open={noteOpen} onOpenChange={setNoteOpen}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>Add Maintenance Note</DialogTitle>
                    </DialogHeader>
                    <form onSubmit={handleAddNote} className="space-y-4">
                        {selectedMaint && (
                            <div className="rounded-lg bg-gray-50 p-3 text-sm text-gray-600">
                                <p className="font-medium">{selectedMaint.issue_title}</p>
                                <p className="text-xs text-gray-400 mt-0.5">{selectedMaint.property_address}</p>
                            </div>
                        )}
                        <div className="space-y-2">
                            <Label>Note *</Label>
                            <Textarea value={noteText} onChange={e => setNoteText(e.target.value)} rows={4} required
                                      placeholder="Add a note or update…"/>
                        </div>
                        <DialogFooter>
                            <Button type="button" variant="outline" onClick={() => setNoteOpen(false)}>Cancel</Button>
                            <Button type="submit" disabled={submitting}>
                                {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}Add Note
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default RealEstateDashboard;
