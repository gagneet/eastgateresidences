// @ts-nocheck
"use client";

import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { toast } from 'sonner';
import { HelpCircle, Home, Loader2, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-react';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import { Label } from '../../../components/ui/label';
import { Textarea } from '../../../components/ui/textarea';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle
} from '../../../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '../../../components/ui/table';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '../../../components/ui/tooltip';
import { formatCurrency, formatDate } from '../../../lib/utils';

const ALLOWED_ROLES = ['owner', 'real_estate_agent', 'super_admin', 'strata_manager', 'ec_member'];
const PROPERTY_TYPES = ['apartment', 'townhouse', 'house', 'villa', 'studio', 'commercial'];
const AU_STATES = ['ACT', 'NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT'];

const EMPTY_FORM = {
    address: '', suburb: '', state: 'ACT', postcode: '',
    property_type: 'apartment', bedrooms: '', bathrooms: '',
    weekly_rent: '', purchase_price: '', eastgate_unit_number: '', notes: ''
};
// Defined outside PropertiesPage so it is never recreated on re-render,
// which would cause inputs to lose focus on every keystroke.
/**
 * @generated FunctionHeader
 * Function: PropertyForm
 * Path: frontend/src/pages/dashboardhub/PropertiesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function PropertyForm({form, handleField, submitting, onSubmit, title}) {
    return (
        <form onSubmit={onSubmit} className="space-y-4">
            <div className="grid grid-cols-1 gap-4">
                <div className="space-y-2">
                    <Label>Street Address *</Label>
                    <Input value={form.address} onChange={e => handleField('address', e.target.value)} required
                           placeholder="123 Example St"/>
                </div>
                <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-2">
                        <Label>Suburb *</Label>
                        <Input value={form.suburb} onChange={e => handleField('suburb', e.target.value)} required/>
                    </div>
                    <div className="space-y-2">
                        <Label>State</Label>
                        <Select value={form.state} onValueChange={v => handleField('state', v)}>
                            <SelectTrigger><SelectValue/></SelectTrigger>
                            <SelectContent>{AU_STATES.map(s => <SelectItem key={s}
                                                                           value={s}>{s}</SelectItem>)}</SelectContent>
                        </Select>
                    </div>
                </div>
                <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-2">
                        <Label>Postcode *</Label>
                        <Input value={form.postcode} onChange={e => handleField('postcode', e.target.value)} required
                               maxLength={4}/>
                    </div>
                    <div className="space-y-2">
                        <Label>Property Type</Label>
                        <Select value={form.property_type} onValueChange={v => handleField('property_type', v)}>
                            <SelectTrigger><SelectValue/></SelectTrigger>
                            <SelectContent>
                                {PROPERTY_TYPES.map(t => <SelectItem key={t}
                                                                     value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</SelectItem>)}
                            </SelectContent>
                        </Select>
                    </div>
                </div>
                <div className="grid grid-cols-3 gap-3">
                    <div className="space-y-2">
                        <Label>Beds</Label>
                        <Input type="number" min="0" value={form.bedrooms}
                               onChange={e => handleField('bedrooms', e.target.value)}/>
                    </div>
                    <div className="space-y-2">
                        <Label>Baths</Label>
                        <Input type="number" min="0" value={form.bathrooms}
                               onChange={e => handleField('bathrooms', e.target.value)}/>
                    </div>
                    <div className="space-y-2">
                        <Label>Weekly Rent ($)</Label>
                        <Input type="number" min="0" value={form.weekly_rent}
                               onChange={e => handleField('weekly_rent', e.target.value)}/>
                    </div>
                </div>
                <div className="space-y-2">
                    <Label>Purchase Price ($)</Label>
                    <Input type="number" min="0" value={form.purchase_price}
                           onChange={e => handleField('purchase_price', e.target.value)}/>
                </div>
                <div className="space-y-2">
                    <div className="flex items-center gap-1.5">
                        <Label>Eastgate Unit Number</Label>
                        <TooltipProvider>
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <HelpCircle className="h-3.5 w-3.5 text-gray-400 cursor-help"/>
                                </TooltipTrigger>
                                <TooltipContent className="max-w-xs">
                                    Link to an Eastgate unit to automatically pull levy data, strata fees, and
                                    compliance info.
                                </TooltipContent>
                            </Tooltip>
                        </TooltipProvider>
                    </div>
                    <Input value={form.eastgate_unit_number}
                           onChange={e => handleField('eastgate_unit_number', e.target.value)}
                           placeholder="e.g. TH017 (optional)"/>
                </div>
                <div className="space-y-2">
                    <Label>Notes</Label>
                    <Textarea value={form.notes} onChange={e => handleField('notes', e.target.value)} rows={3}/>
                </div>
            </div>
            <DialogFooter>
                <Button type="submit" disabled={submitting} className="w-full">
                    {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}
                    {title}
                </Button>
            </DialogFooter>
        </form>
    );
}
/**
 * @generated FunctionHeader
 * Function: StatusBadge
 * Path: frontend/src/pages/dashboardhub/PropertiesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function StatusBadge({status}) {
    return (
        <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium
      ${status === 'occupied' ? 'bg-green-100 text-green-800' : 'bg-amber-100 text-amber-800'}`}>
      {status === 'occupied' ? 'Occupied' : 'Vacant'}
    </span>
    );
}
/**
 * @generated FunctionHeader
 * Function: PropertiesPage
 * Path: frontend/src/pages/dashboardhub/PropertiesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const PropertiesPage = () => {
    const {user, api} = useAuth();
    const router = useRouter();
    const [properties, setProperties] = useState([]);
    const [loading, setLoading] = useState(true);
    const [submitting, setSubmitting] = useState(false);
    const [addOpen, setAddOpen] = useState(false);
    const [editOpen, setEditOpen] = useState(false);
    const [deleteOpen, setDeleteOpen] = useState(false);
    const [selectedProp, setSelectedProp] = useState(null);
    const [form, setForm] = useState(EMPTY_FORM);

    const canAccess = user && ALLOWED_ROLES.includes(user.role);

    const fetchProperties = useCallback(async () => {
        try {
            const res = await api.get('/owner-hub/properties');
            setProperties(res.data || []);
        } catch {
            toast.error('Failed to load properties');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        if (!canAccess) {
            router.push('/dashboard');
            return;
        }
        fetchProperties();
    }, [canAccess, fetchProperties, router]);
    /**
     * @generated FunctionHeader
     * Function: handleField
     * Path: frontend/src/pages/dashboardhub/PropertiesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleField = (field, value) => setForm(prev => ( {...prev, [ field ]: value} ));
    /**
     * @generated FunctionHeader
     * Function: handleAdd
     * Path: frontend/src/pages/dashboardhub/PropertiesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAdd = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await api.post('/owner-hub/properties', form);
            toast.success('Property added');
            setAddOpen(false);
            setForm(EMPTY_FORM);
            fetchProperties();
        } catch {
            toast.error('Failed to add property');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: openEdit
     * Path: frontend/src/pages/dashboardhub/PropertiesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openEdit = (prop) => {
        setSelectedProp(prop);
        setForm({
            address: prop.address || '', suburb: prop.suburb || '',
            state: prop.state || 'ACT', postcode: prop.postcode || '',
            property_type: prop.property_type || 'apartment',
            bedrooms: prop.bedrooms || '', bathrooms: prop.bathrooms || '',
            weekly_rent: prop.weekly_rent || '', purchase_price: prop.purchase_price || '',
            eastgate_unit_number: prop.eastgate_unit_number || '', notes: prop.notes || ''
        });
        setEditOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handleEdit
     * Path: frontend/src/pages/dashboardhub/PropertiesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleEdit = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await api.put(`/owner-hub/properties/${selectedProp.id}`, form);
            toast.success('Property updated');
            setEditOpen(false);
            fetchProperties();
        } catch {
            toast.error('Failed to update property');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/pages/dashboardhub/PropertiesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async () => {
        setSubmitting(true);
        try {
            await api.delete(`/owner-hub/properties/${selectedProp.id}`);
            toast.success('Property deleted');
            setDeleteOpen(false);
            fetchProperties();
        } catch {
            toast.error('Failed to delete property');
        } finally {
            setSubmitting(false);
        }
    };

    if (!canAccess) return null;

    return (
        <div className="space-y-6 p-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900">My Properties</h1>
                    <p className="text-sm text-gray-500">Manage your property portfolio</p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={fetchProperties}><RefreshCw className="h-4 w-4 mr-2"/>Refresh</Button>
                    <Button size="sm" onClick={() => {
                        setForm(EMPTY_FORM);
                        setAddOpen(true);
                    }}>
                        <Plus className="h-4 w-4 mr-2"/>Add Property
                    </Button>
                </div>
            </div>

            <Card>
                <CardContent className="p-0">
                    {loading ? (
                        <div className="flex items-center justify-center h-48"><Loader2
                            className="h-8 w-8 animate-spin text-gray-400"/></div>
                    ) : properties.length === 0 ? (
                        <div className="flex flex-col items-center justify-center h-48 gap-3">
                            <Home className="h-12 w-12 text-gray-300"/>
                            <p className="text-gray-500">No properties yet. Add your first property.</p>
                            <Button size="sm" onClick={() => {
                                setForm(EMPTY_FORM);
                                setAddOpen(true);
                            }}><Plus className="mr-2 h-4 w-4"/>Add Property</Button>
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Address</TableHead>
                                    <TableHead>Type</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead>Weekly Rent</TableHead>
                                    <TableHead>Active Tenant</TableHead>
                                    <TableHead>Lease End</TableHead>
                                    <TableHead>Health</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {properties.map(prop => (
                                    <TableRow key={prop.id}>
                                        <TableCell>
                                            <div>
                                                <p className="font-medium">{prop.address}</p>
                                                <p className="text-xs text-gray-500">{prop.suburb}, {prop.state}</p>
                                            </div>
                                        </TableCell>
                                        <TableCell className="capitalize">{prop.property_type || '—'}</TableCell>
                                        <TableCell><StatusBadge status={prop.status}/></TableCell>
                                        <TableCell>{prop.weekly_rent ? formatCurrency(prop.weekly_rent) : '—'}</TableCell>
                                        <TableCell>{prop.tenant_name || '—'}</TableCell>
                                        <TableCell>{prop.lease_end ? formatDate(prop.lease_end) : '—'}</TableCell>
                                        <TableCell>
                                            {prop.health_score != null ? (
                                                <span
                                                    className={`font-semibold ${prop.health_score >= 75 ? 'text-green-600' : prop.health_score >= 50 ? 'text-yellow-600' : prop.health_score >= 30 ? 'text-orange-600' : 'text-red-600'}`}>
                          {prop.health_score}
                        </span>
                                            ) : '—'}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <div className="flex gap-1 justify-end">
                                                <Button variant="ghost" size="icon" onClick={() => openEdit(prop)}>
                                                    <Pencil className="h-4 w-4"/>
                                                </Button>
                                                <Button variant="ghost" size="icon"
                                                        className="text-red-500 hover:text-red-700"
                                                        onClick={() => {
                                                            setSelectedProp(prop);
                                                            setDeleteOpen(true);
                                                        }}>
                                                    <Trash2 className="h-4 w-4"/>
                                                </Button>
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

            {/* Add Dialog */}
            <Dialog open={addOpen} onOpenChange={setAddOpen}>
                <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>Add New Property</DialogTitle>
                        <DialogDescription>Add a property to your portfolio</DialogDescription>
                    </DialogHeader>
                    <PropertyForm form={form} handleField={handleField} submitting={submitting} onSubmit={handleAdd}
                                  title="Add Property"/>
                </DialogContent>
            </Dialog>

            {/* Edit Dialog */}
            <Dialog open={editOpen} onOpenChange={setEditOpen}>
                <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>Edit Property</DialogTitle>
                    </DialogHeader>
                    <PropertyForm form={form} handleField={handleField} submitting={submitting} onSubmit={handleEdit}
                                  title="Save Changes"/>
                </DialogContent>
            </Dialog>

            {/* Delete Confirm */}
            <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
                <DialogContent className="max-w-sm">
                    <DialogHeader>
                        <DialogTitle>Delete Property</DialogTitle>
                        <DialogDescription>
                            Are you sure you want to delete <strong>{selectedProp?.address}</strong>?
                            This will also remove all associated tenancy and ledger records.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter className="gap-2">
                        <Button variant="outline" onClick={() => setDeleteOpen(false)}>Cancel</Button>
                        <Button variant="destructive" onClick={handleDelete} disabled={submitting}>
                            {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}Delete
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default PropertiesPage;
