import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import { Label } from '../../../components/ui/label';
import { Textarea } from '../../../components/ui/textarea';
import { Card, CardContent } from '../../../components/ui/card';
import { Badge } from '../../../components/ui/badge';
import { Separator } from '../../../components/ui/separator';
import { Alert, AlertDescription } from '../../../components/ui/alert';
import { CheckCircle, Clock, Key, Loader2, XCircle } from 'lucide-react';
import { toast } from 'sonner';
import { formatDate } from '../../../lib/utils';
import {StatusFilterSelect, useUrlStatusFilter} from '@/components/requests/statusFilter';
import {trackRequestCatalogueEvent} from '@/config/requestCatalogue';

const ACCESS_TYPES = [
    {value: 'fob', label: 'Entry gate fob', icon: '🔑'},
    {value: 'garage_remote', label: 'Garage remote fob', icon: '🚗'},
    {value: 'access_card', label: 'Access card', icon: '💳'},
    {value: 'visitor_card', label: 'Visitor card', icon: '👤'},
    {value: 'other', label: 'Other access device', icon: '📦'},
];

const money = (cents = 0) => `$${((parseInt(cents || 0, 10) || 0) / 100).toFixed(2)}`;

const statusConfig = {
    pending: {label: 'Pending', color: 'bg-yellow-100 text-yellow-800', icon: Clock},
    approved: {label: 'Approved', color: 'bg-green-100 text-green-800', icon: CheckCircle},
    rejected: {label: 'Rejected', color: 'bg-red-100 text-red-800', icon: XCircle},
    issued: {label: 'Issued', color: 'bg-blue-100 text-blue-800', icon: CheckCircle},
};
/**
 * @generated FunctionHeader
 * Function: AccessControlTab
 * Path: frontend/src/pages/dashboard/requests/AccessControlTab.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const AccessControlTab = () => {
    const {api, user} = useAuth();
    const [loading, setLoading] = useState(false);
    const [requests, setRequests] = useState([]);
    const [deviceTypes, setDeviceTypes] = useState([]);
    const [loadingDeviceTypes, setLoadingDeviceTypes] = useState(true);
    const [showForm, setShowForm] = useState(false);
    const {options, counts, filteredRecords, statusFilter, setStatusFilter} = useUrlStatusFilter({
        records: requests,
        statusConfig,
        tab: 'access-control',
    });

    const [formData, setFormData] = useState({
        access_type: '',
        quantity: 1,
        reason: '',
    });

    const selectedDeviceType = deviceTypes.find(type => type.type_key === formData.access_type);
    const maxQuantity = selectedDeviceType?.max_quantity || 1;
    const estimatedTotalCents = selectedDeviceType
        ? (selectedDeviceType.fee_cents + selectedDeviceType.deposit_cents) * (parseInt(formData.quantity || 1, 10) || 1)
        : 0;

    const fetchRequests = useCallback(async () => {
        try {
            const response = await api.get('/requests/access-control');
            setRequests(Array.isArray(response.data) ? response.data : []);
        } catch (error) {
            console.error('Failed to fetch access control requests:', error);
        }
    }, [api]);

    const fetchDeviceTypes = useCallback(async () => {
        setLoadingDeviceTypes(true);
        try {
            const response = await api.get('/access/device-types');
            const rows = Array.isArray(response.data) ? response.data : [];
            setDeviceTypes(rows);
            setFormData(prev => ({
                ...prev,
                access_type: prev.access_type || rows[0]?.type_key || '',
                quantity: Math.min(parseInt(prev.quantity || 1, 10) || 1, rows[0]?.max_quantity || 1),
            }));
        } catch (error) {
            console.error('Failed to fetch access device settings:', error);
            toast.error(error?.response?.data?.detail?.message || 'Failed to load access device options');
        } finally {
            setLoadingDeviceTypes(false);
        }
    }, [api]);

    useEffect(() => {
        fetchRequests();
        fetchDeviceTypes();
    }, [fetchRequests, fetchDeviceTypes]);
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/requests/AccessControlTab.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!formData.access_type || !formData.reason) {
            toast.error('Please fill in all required fields');
            return;
        }
        if (!selectedDeviceType) {
            toast.error('Select an available access device type');
            return;
        }
        if ((parseInt(formData.quantity || 1, 10) || 1) > maxQuantity) {
            toast.error(`Maximum quantity for ${selectedDeviceType.name} is ${maxQuantity}`);
            return;
        }
        setLoading(true);
        try {
            await api.post('/requests/access-control', {
                access_type: formData.access_type,
                quantity: parseInt(formData.quantity || 1, 10) || 1,
                reason: formData.reason,
            });
            trackRequestCatalogueEvent('request_form_submitted', {
                definition_key: 'access-control',
                definition_version: 1,
                role: user?.effective_role || user?.role,
                status: 'submitted',
            });
            toast.success('Access control request submitted successfully');
            setFormData({access_type: deviceTypes[0]?.type_key || '', quantity: 1, reason: ''});
            setShowForm(false);
            fetchRequests();
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to submit request');
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="space-y-6">
            <Alert>
                <Key className="h-4 w-4"/>
                <AlertDescription>
                    <strong>Access Control:</strong> Request configured building access devices. Fees and refundable
                    deposits are estimated from current building settings.
                </AlertDescription>
            </Alert>

            <div className="flex justify-between items-center">
                <div>
                    <h3 className="text-lg font-semibold">Access Control Requests</h3>
                    <p className="text-sm text-muted-foreground">Request or replace building access devices</p>
                </div>
                <Button onClick={() => setShowForm(!showForm)}>
                    <Key className="mr-2 h-4 w-4"/>
                    {showForm ? 'Cancel' : 'Request Device'}
                </Button>
            </div>

            {showForm && (
                <Card className="border-primary/20">
                    <CardContent className="pt-6">
                        <form onSubmit={handleSubmit} className="space-y-5">
                            <div className="space-y-2">
                                <Label>Device Type *</Label>
                                {loadingDeviceTypes ? (
                                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                        <Loader2 className="h-4 w-4 animate-spin"/>
                                        Loading device options...
                                    </div>
                                ) : deviceTypes.length === 0 ? (
                                    <Alert>
                                        <Key className="h-4 w-4"/>
                                        <AlertDescription>
                                            No access devices are currently available for requests.
                                        </AlertDescription>
                                    </Alert>
                                ) : (
                                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                        {deviceTypes.map(type => (
                                            <button
                                                key={type.device_type_id}
                                                type="button"
                                                onClick={() => setFormData(prev => ( {
                                                    ...prev,
                                                    access_type: type.type_key,
                                                    quantity: Math.min(parseInt(prev.quantity || 1, 10) || 1, type.max_quantity || 1)
                                                } ))}
                                                className={`p-3 rounded-lg border-2 text-left transition-all ${formData.access_type === type.type_key ? 'border-primary bg-primary/5' : 'border-border hover:border-primary/50'}`}
                                            >
                                                <div className="flex items-start gap-2">
                                                    <Key className="mt-0.5 h-4 w-4 text-primary"/>
                                                    <div className="min-w-0">
                                                        <div className="text-sm font-medium">{type.name}</div>
                                                        {type.description && (
                                                            <div className="mt-1 text-xs text-muted-foreground line-clamp-2">{type.description}</div>
                                                        )}
                                                        <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
                                                            <span>Fee {money(type.fee_cents)}</span>
                                                            <span>Deposit {money(type.deposit_cents)}</span>
                                                            <span>Max {type.max_quantity}</span>
                                                        </div>
                                                    </div>
                                                </div>
                                            </button>
                                        ))}
                                    </div>
                                )}
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="quantity">Quantity *</Label>
                                <Input
                                    id="quantity"
                                    type="number"
                                    min="1"
                                    max={maxQuantity}
                                    value={formData.quantity}
                                    onChange={e => setFormData(prev => ( {...prev, quantity: e.target.value} ))}
                                    className="max-w-[120px]"
                                    disabled={!selectedDeviceType}
                                />
                                <p className="text-xs text-muted-foreground">
                                    Maximum quantity for this device is {maxQuantity}.
                                </p>
                            </div>

                            {selectedDeviceType && (
                                <div className="rounded-md border bg-muted/30 p-4 text-sm">
                                    <div className="grid gap-2 sm:grid-cols-3">
                                        <div>
                                            <span className="text-muted-foreground">Fee</span>
                                            <p className="font-medium">{money(selectedDeviceType.fee_cents)} each</p>
                                        </div>
                                        <div>
                                            <span className="text-muted-foreground">Refundable deposit</span>
                                            <p className="font-medium">{money(selectedDeviceType.deposit_cents)} each</p>
                                        </div>
                                        <div>
                                            <span className="text-muted-foreground">Estimated total</span>
                                            <p className="font-semibold">{money(estimatedTotalCents)}</p>
                                        </div>
                                    </div>
                                </div>
                            )}

                            <div className="space-y-2">
                                <Label htmlFor="reason">Reason for Request *</Label>
                                <Textarea
                                    id="reason"
                                    value={formData.reason}
                                    onChange={e => setFormData(prev => ( {...prev, reason: e.target.value} ))}
                                    placeholder="E.g. Lost fob, new tenant moving in, garage remote stopped working..."
                                    rows={3}
                                    required
                                />
                            </div>

                            <div className="flex gap-2 pt-2">
                                <Button type="submit" disabled={loading || !selectedDeviceType}>
                                    {loading ? <><Loader2
                                        className="mr-2 h-4 w-4 animate-spin"/> Submitting...</> : 'Submit Request'}
                                </Button>
                                <Button type="button" variant="outline"
                                        onClick={() => setShowForm(false)}>Cancel</Button>
                            </div>
                        </form>
                    </CardContent>
                </Card>
            )}

            <Separator/>

            <div className="space-y-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <h4 className="font-semibold">Your Requests</h4>
                    <StatusFilterSelect options={options} counts={counts} value={statusFilter} onChange={setStatusFilter}/>
                </div>
                {filteredRecords.length === 0 ? (
                    <Card>
                        <CardContent className="p-8 text-center text-muted-foreground">
                            <Key className="h-12 w-12 mx-auto mb-2 opacity-50"/>
                            <p>{statusFilter === 'all' ? 'No access control requests submitted yet' : 'No access control requests found for this status'}</p>
                        </CardContent>
                    </Card>
                ) : (
                    filteredRecords.map(req => {
                        const StatusIcon = statusConfig[ req.status ]?.icon || Clock;
                        const type = ACCESS_TYPES.find(t => t.value === req.access_type);
                        return (
                            <Card key={req.id} className="border-l-4 border-l-indigo-400">
                                <CardContent className="p-4">
                                    <div className="flex items-start justify-between mb-2">
                                        <div>
                                            <h5 className="font-semibold">{type?.label || req.access_type} × {req.quantity}</h5>
                                            <p className="text-xs text-muted-foreground">{formatDate(req.created_at)}</p>
                                        </div>
                                        <Badge
                                            className={statusConfig[ req.status ]?.color || 'bg-gray-100 text-gray-800'}>
                                            <StatusIcon className="h-3 w-3 mr-1"/>
                                            {statusConfig[ req.status ]?.label || req.status}
                                        </Badge>
                                    </div>
                                    <div className="grid grid-cols-2 gap-2 text-sm">
                                        <div><span className="text-muted-foreground">Cost: </span><span
                                            className="font-medium">${req.cost?.toFixed(2) || '0.00'}</span></div>
                                    </div>
                                    <p className="text-sm text-muted-foreground mt-1 line-clamp-2">{req.reason}</p>
                                    {req.issued_at && (
                                        <p className="text-xs text-green-600 mt-2">Issued
                                            on {formatDate(req.issued_at)}</p>
                                    )}
                                </CardContent>
                            </Card>
                        );
                    })
                )}
            </div>
        </div>
    );
};

export default AccessControlTab;
