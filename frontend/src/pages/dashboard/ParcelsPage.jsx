// @featuretrace:community-hub — parcel/delivery register: residents log an expected parcel, staff mark it collected.
// Layer: frontend
// Data flow: ParcelsPage → GET/POST /parcels, PUT /parcels/{id}/collected → parcels (building-scoped).
// Related: backend/server.py (inline /parcels routes), backend/routers/parcels.py (event hook)
//           frontend/src/hooks/useActiveUnit.ts (the unit a parcel is logged against)
//
import React, { useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Label } from '../../components/ui/label';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '../../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import { CheckCircle2, Clock, ExternalLink, Loader2, Package, Plus, Search } from 'lucide-react';
import { formatDateTime } from '../../lib/utils';
import { toast } from 'sonner';

const carriers = [
    {
        value: 'auspost',
        label: 'Australia Post',

        trackUrl: (n) => `https://auspost.com.au/mypost/track/#/details/${encodeURIComponent(n)}`
    },
    {
        value: 'startrack',
        label: 'StarTrack',

        trackUrl: (n) => `https://startrack.com.au/track-and-trace/?id=${encodeURIComponent(n)}`
    },
    {
        value: 'dhl',
        label: 'DHL',

        trackUrl: (n) => `https://www.dhl.com/au-en/home/tracking.html?tracking-id=${encodeURIComponent(n)}`
    },
    {
        value: 'fedex',
        label: 'FedEx',

        trackUrl: (n) => `https://www.fedex.com/fedextrack/?trknbr=${encodeURIComponent(n)}`
    },
    {
        value: 'tnt',
        label: 'TNT',

        trackUrl: (n) => `https://www.tnt.com/express/en_au/site/tracking.html?searchType=con&cons=${encodeURIComponent(n)}`
    },
    {value: 'amazon', label: 'Amazon',
 trackUrl: (n) => `https://track.amazon.com/tracking/${encodeURIComponent(n)}`},
    {value: 'toll', label: 'Toll',
 trackUrl: (n) => `https://www.tollgroup.com/tracking?ref=${encodeURIComponent(n)}`},
    {value: 'other', label: 'Other', trackUrl: null},
];
/**
 * @generated FunctionHeader
 * Function: ParcelsPage
 * Path: frontend/src/pages/dashboard/ParcelsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ParcelsPage = () => {
    const {api, hasPermission, user} = useAuth();
    const {activeUnit} = useActiveUnit();
    const [parcels, setParcels] = useState([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [searchTerm, setSearchTerm] = useState('');
    const [formData, setFormData] = useState({
        unit_number: '',
        carrier: 'auspost',
        tracking_number: '',
        description: '',
        storage_location: ''
    });

    const canManage = hasPermission('can_manage_users') || user?.role === 'service_provider';
    const isResident = ['owner', 'ec_member', 'strata_admin', 'tenant'].includes(user?.role);

    // Pre-fill unit for residents — backend always enforces their DB unit anyway
    useEffect(() => {
        if (!canManage && activeUnit) {
            setFormData(prev => ( {...prev, unit_number: activeUnit} ));
        }
    }, [activeUnit, canManage]);
    const canLog = canManage || isResident;

    const fetchParcels = React.useCallback(async () => {
        try {
            const response = await api.get('/parcels');
            setParcels(response.data);
        } catch (error) {
            console.error('Failed to fetch parcels:', error);
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchParcels();
    }, [fetchParcels]);
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/ParcelsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);

        try {
            await api.post('/parcels', formData);
            toast.success('Parcel logged successfully');
            setDialogOpen(false);
            setFormData({
                unit_number: !canManage && activeUnit ? activeUnit : '',
                carrier: 'auspost',
                tracking_number: '',
                description: '',
                storage_location: ''
            });
            fetchParcels();
        } catch (error) {
            toast.error('Failed to log parcel');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleCollected
     * Path: frontend/src/pages/dashboard/ParcelsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCollected = async (parcelId) => {
        try {
            await api.put(`/parcels/${parcelId}/collected`);
            toast.success('Parcel marked as collected');
            fetchParcels();
        } catch (error) {
            toast.error('Failed to update parcel');
        }
    };

    const filteredParcels = parcels.filter(p =>
        p.unit_number?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        p.tracking_number?.toLowerCase().includes(searchTerm.toLowerCase()) ||
        p.description?.toLowerCase().includes(searchTerm.toLowerCase())
    );

    const pendingParcels = filteredParcels.filter(p => p.status !== 'collected');
    const collectedParcels = filteredParcels.filter(p => p.status === 'collected');

    return (
        <div className="space-y-6" data-testid="parcels-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Parcel Notifications</h1>
                    <p className="text-muted-foreground">Track deliveries for residents</p>
                </div>
                {canLog && (
                    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                        <DialogTrigger asChild>
                            <Button data-testid="log-parcel-btn">
                                <Plus className="mr-2 h-4 w-4"/>
                                {isResident && !canManage ? 'Register Expected Parcel' : 'Log Parcel'}
                            </Button>
                        </DialogTrigger>
                        <DialogContent>
                            <DialogHeader>
                                <DialogTitle>{isResident && !canManage ? 'Register Expected Delivery' : 'Log New Parcel'}</DialogTitle>
                                <DialogDescription>
                                    {isResident && !canManage
                                        ? 'Pre-register an expected delivery so reception staff can match it when it arrives.'
                                        : 'Record a delivery that has arrived for a resident.'}
                                </DialogDescription>
                            </DialogHeader>
                            <form onSubmit={handleSubmit} className="space-y-4">
                                <div className="grid grid-cols-2 gap-4">
                                    <div className="space-y-2">
                                        <Label htmlFor="unit">Unit Number</Label>
                                        <Input
                                            id="unit"
                                            value={formData.unit_number}
                                            onChange={(e) => !canManage ? undefined : setFormData(prev => ( {
                                                ...prev,
                                                unit_number: e.target.value
                                            } ))}
                                            placeholder="e.g., 12A"
                                            required
                                            readOnly={!canManage}
                                            className={!canManage ? 'bg-muted cursor-default' : ''}
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="carrier">Carrier</Label>
                                        <Select value={formData.carrier}
                                                onValueChange={(v) => setFormData(prev => ( {...prev, carrier: v} ))}>
                                            <SelectTrigger><SelectValue/></SelectTrigger>
                                            <SelectContent>
                                                {carriers.map(c => <SelectItem key={c.value}
                                                                               value={c.value}>{c.label}</SelectItem>)}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="tracking">Tracking Number (optional)</Label>
                                    <Input
                                        id="tracking"
                                        value={formData.tracking_number}
                                        onChange={(e) => setFormData(prev => ( {
                                            ...prev,
                                            tracking_number: e.target.value
                                        } ))}
                                        placeholder="e.g., ABC123456789"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="description">Description (optional)</Label>
                                    <Input
                                        id="description"
                                        value={formData.description}
                                        onChange={(e) => setFormData(prev => ( {
                                            ...prev,
                                            description: e.target.value
                                        } ))}
                                        placeholder="e.g., Large box, Amazon logo"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="location">Storage Location</Label>
                                    <Input
                                        id="location"
                                        value={formData.storage_location}
                                        onChange={(e) => setFormData(prev => ( {
                                            ...prev,
                                            storage_location: e.target.value
                                        } ))}
                                        placeholder="e.g., Concierge desk, Parcel locker B3"
                                    />
                                </div>
                                <Button type="submit" className="w-full" disabled={submitting}>
                                    {submitting ? <><Loader2
                                        className="mr-2 h-4 w-4 animate-spin"/>Logging...</> : 'Log Parcel'}
                                </Button>
                            </form>
                        </DialogContent>
                    </Dialog>
                )}
            </div>

            {/* How Parcel Tracking Works */}
            <div className="rounded-xl border border-sky-100 bg-sky-50 p-4 text-sm text-sky-800 space-y-2">
                <p className="font-semibold flex items-center gap-2"><Package className="h-4 w-4 text-sky-500"/>How
                    Parcel Tracking Works</p>
                <div className="grid sm:grid-cols-2 gap-3 text-xs text-sky-700">
                    <div>
                        <p className="font-medium mb-1">📬 Receiving a parcel</p>
                        <p>When a parcel arrives at reception, building staff log it here. You will receive a bell
                            notification and email to collect it from the concierge or parcel locker area.</p>
                    </div>
                    <div>
                        <p className="font-medium mb-1">📦 Pre-register an expected delivery</p>
                        <p>If you are expecting a parcel (AusPost, Amazon, DHL, StarTrack, etc.), use <strong>Register
                            Expected Parcel</strong> to alert reception in advance. Include the tracking number if
                            known.</p>
                    </div>
                    <div>
                        <p className="font-medium mb-1">🔔 Notifications</p>
                        <p>You will be notified via bell notification and email when your parcel is logged as received.
                            Status updates to "Collected" once you pick it up.</p>
                    </div>
                    <div>
                        <p className="font-medium mb-1">🚚 Supported carriers</p>
                        <p>AusPost, StarTrack, DHL, FedEx, Amazon Logistics, CouriersPlease, TNT, and others. Use
                            "Other" for any carrier not listed.</p>
                    </div>
                </div>
                <p className="text-[11px] text-sky-500 italic border-t border-sky-100 pt-2 mt-1">
                    Coming soon: Automatic AusPost MyPost Business integration for real-time tracking updates.
                </p>
            </div>

            {/* Search and Stats */}
            <div className="flex flex-col sm:flex-row gap-4">
                <div className="relative flex-1 max-w-md">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                    <Input
                        placeholder="Search by unit, tracking number..."
                        value={searchTerm}
                        onChange={(e) => setSearchTerm(e.target.value)}
                        className="pl-10"
                    />
                </div>
                <div className="flex gap-4">
                    <Card className="card-dashboard">
                        <CardContent className="px-6 py-3 flex items-center gap-3">
                            <Clock className="h-5 w-5 text-orange-500"/>
                            <div>
                                <p className="text-xl font-bold">{pendingParcels.length}</p>
                                <p className="text-xs text-muted-foreground">Awaiting Collection</p>
                            </div>
                        </CardContent>
                    </Card>
                    <Card className="card-dashboard">
                        <CardContent className="px-6 py-3 flex items-center gap-3">
                            <CheckCircle2 className="h-5 w-5 text-green-500"/>
                            <div>
                                <p className="text-xl font-bold">{collectedParcels.length}</p>
                                <p className="text-xs text-muted-foreground">Collected</p>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            </div>

            {/* Parcels List */}
            {loading ? (
                <div className="space-y-4">
                    {[1, 2, 3, 4].map((i) => <div key={i} className="skeleton h-20 w-full"/>)}
                </div>
            ) : filteredParcels.length === 0 ? (
                <Card className="card-dashboard">
                    <CardContent className="py-16 text-center">
                        <Package className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                        <h3 className="text-lg font-medium mb-2">No Parcels</h3>
                        <p className="text-muted-foreground">
                            {searchTerm ? 'No parcels match your search' : 'No parcels have been logged'}
                        </p>
                    </CardContent>
                </Card>
            ) : (
                <div className="space-y-6">
                    {pendingParcels.length > 0 && (
                        <div>
                            <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
                                <Clock className="h-5 w-5 text-orange-500"/>
                                Awaiting Collection ({pendingParcels.length})
                            </h3>
                            <div className="space-y-3">
                                {pendingParcels.map((parcel) => {
                                    const carrier = carriers.find(c => c.value === parcel.carrier);
                                    return (
                                        <Card key={parcel.id} className="card-dashboard">
                                            <CardContent className="p-4">
                                                <div className="flex items-center justify-between">
                                                    <div className="flex items-center gap-4">
                                                        <div className="p-3 rounded-full bg-orange-100">
                                                            <Package className="h-6 w-6 text-orange-600"/>
                                                        </div>
                                                        <div>
                                                            <div className="flex items-center gap-2">
                                                                <h4 className="font-semibold">Unit {parcel.unit_number}</h4>
                                                                <Badge
                                                                    variant="secondary">{carrier?.label || parcel.carrier}</Badge>
                                                                <Badge variant="outline"
                                                                       className={parcel.status === 'expected' ? 'text-blue-600 border-blue-300' : 'text-orange-600 border-orange-300'}>
                                                                    {parcel.status === 'expected' ? 'Expected' : 'Pending'}
                                                                </Badge>
                                                            </div>
                                                            <div className="text-sm text-muted-foreground mt-1">
                                                                {parcel.tracking_number && <span
                                                                    className="mr-4">#{parcel.tracking_number}</span>}
                                                                {parcel.tracking_number && carrier?.trackUrl && (
                                                                    <a
                                                                        href={carrier.trackUrl(parcel.tracking_number)}
                                                                        target="_blank"
                                                                        rel="noopener noreferrer"
                                                                        className="inline-flex items-center gap-1 text-blue-600 hover:underline mr-4 text-xs"
                                                                    >
                                                                        <ExternalLink className="h-3 w-3"/>Track parcel
                                                                    </a>
                                                                )}
                                                                {parcel.description &&
                                                                    <span className="mr-4">{parcel.description}</span>}
                                                                {parcel.storage_location &&
                                                                    <span>📍 {parcel.storage_location}</span>}
                                                            </div>
                                                            <p className="text-xs text-muted-foreground mt-1">
                                                                Received {formatDateTime(parcel.received_date)}
                                                            </p>
                                                        </div>
                                                    </div>
                                                    <Button size="sm" onClick={() => handleCollected(parcel.id)}>
                                                        <CheckCircle2 className="mr-2 h-4 w-4"/>
                                                        Collected
                                                    </Button>
                                                </div>
                                            </CardContent>
                                        </Card>
                                    );
                                })}
                            </div>
                        </div>
                    )}

                    {collectedParcels.length > 0 && (
                        <div>
                            <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
                                <CheckCircle2 className="h-5 w-5 text-green-500"/>
                                Recently Collected ({collectedParcels.length})
                            </h3>
                            <div className="space-y-3">
                                {collectedParcels.slice(0, 10).map((parcel) => {
                                    const carrier = carriers.find(c => c.value === parcel.carrier);
                                    return (
                                        <Card key={parcel.id} className="card-dashboard opacity-75">
                                            <CardContent className="p-4">
                                                <div className="flex items-center gap-4">
                                                    <div className="p-3 rounded-full bg-green-100">
                                                        <Package className="h-6 w-6 text-green-600"/>
                                                    </div>
                                                    <div>
                                                        <div className="flex items-center gap-2">
                                                            <h4 className="font-semibold">Unit {parcel.unit_number}</h4>
                                                            <Badge
                                                                variant="secondary">{carrier?.label || parcel.carrier}</Badge>
                                                            <Badge
                                                                className="bg-green-100 text-green-800">Collected</Badge>
                                                        </div>
                                                        <p className="text-xs text-muted-foreground mt-1">
                                                            Collected {formatDateTime(parcel.collected_date)}
                                                        </p>
                                                    </div>
                                                </div>
                                            </CardContent>
                                        </Card>
                                    );
                                })}
                            </div>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default ParcelsPage;
