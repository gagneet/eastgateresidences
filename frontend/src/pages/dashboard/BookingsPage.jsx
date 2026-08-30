// @featuretrace:bookings — move-in/out and amenity booking requests for residents; approval queue for managers.
// Layer: frontend
// Data flow: BookingsPage → GET/POST /move-bookings, /amenity-bookings, GET /amenities,
//            PUT /move-bookings/{id}/status → move_bookings + amenity_bookings (building-scoped).
// Related: backend/server.py (move-bookings / amenity-bookings inline routes)
//           frontend/src/hooks/useActiveUnit.ts (the unit a booking is made for and scoped to)
//
import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit, useActiveUnitSync} from '../../hooks/useActiveUnit';
import { Card, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '../../components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger, } from '../../components/ui/tabs';
import {
    Building2,
    Calendar,
    Car,
    Dumbbell,
    Loader2,
    Plus,
    Settings,
    Trash2,
    Truck,
    Users,
    UtensilsCrossed
} from 'lucide-react';
import { formatDate } from '../../lib/utils';
import { toast } from 'sonner';

const ICON_MAP = {
    UtensilsCrossed, Users, Car, Dumbbell, Building2,
};

const DEFAULT_AMENITY_CONFIG = {
    bbq_area: {label: 'BBQ Area', icon: UtensilsCrossed, color: 'text-orange-500'},
    meeting_room: {label: 'Meeting Room', icon: Users, color: 'text-blue-500'},
    visitor_parking: {label: 'Visitor Parking', icon: Car, color: 'text-green-500'},
    gym: {label: 'Gym', icon: Dumbbell, color: 'text-purple-500'},
};

const timeSlots = [
    '08:00', '09:00', '10:00', '11:00', '12:00', '13:00',
    '14:00', '15:00', '16:00', '17:00', '18:00', '19:00', '20:00'
];
/**
 * @generated FunctionHeader
 * Function: BookingsPage
 * Path: frontend/src/pages/dashboard/BookingsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
// Nested App Router pages preselect a view without a query string:
//   /community/bookings/moves    → initialTab="move"
//   /community/bookings/visitors → initialTab="amenity", initialAmenityFilter="visitor_parking"
//   /community/bookings/my-stay  → scope="mine" (limits to the current user's bookings)
/**
 * @param {{initialTab?: 'move' | 'amenity', initialAmenityFilter?: string | null, scope?: 'mine'}} [props]
 */
const BookingsPage = ({initialTab, initialAmenityFilter, scope} = {}) => {
    const {api, hasPermission, user} = useAuth();
    const {activeUnit} = useActiveUnit();
    const [moveBookings, setMoveBookings] = useState([]);
    const [amenityBookings, setAmenityBookings] = useState([]);
    const [amenityConfig, setAmenityConfig] = useState(DEFAULT_AMENITY_CONFIG);
    const [amenitiesList, setAmenitiesList] = useState([]);
    const [manageAmenitiesOpen, setManageAmenitiesOpen] = useState(false);
    const [newAmenityForm, setNewAmenityForm] = useState({key: '', label: '', description: '', icon: 'Building2'});
    const [loading, setLoading] = useState(true);
    const [moveDialogOpen, setMoveDialogOpen] = useState(false);
    const [amenityDialogOpen, setAmenityDialogOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [activeTab, setActiveTab] = useState(initialTab || 'move');
    const [amenityFilter, setAmenityFilter] = useState(initialAmenityFilter || null);

    const [moveData, setMoveData] = useState({
        unit_number: activeUnit || '',
        move_type: 'move_in',
        scheduled_date: '',
        time_slot: 'morning',
        requires_lift: true,
        moving_company: '',
        bulk_waste: false,
        notes: ''
    });

    const [amenityData, setAmenityData] = useState({
        amenity_type: 'bbq_area',
        date: '',
        start_time: '10:00',
        end_time: '12:00',
        notes: ''
    });

    // The useState initialiser above runs once, and on that first render the session
    // may not have hydrated yet — so the unit field would sit empty on a required
    // form. This seeds it as soon as the active unit resolves, and re-points it on a
    // switch, without clobbering a value the user has typed for a different unit
    // (nothing is re-applied unless the active unit itself changed).
    useActiveUnitSync((unit) => setMoveData(prev => ( {...prev, unit_number: unit} )));

    const canManage = hasPermission('can_manage_users');
    // scope="mine" (the "My stay" nested view) limits both lists to the current
    // user's own bookings, matched by booker id or their unit number.
    // Scoped to the ACTIVE unit, consistently with every other owner-facing page:
    // a multi-unit owner's other unit's bookings appear when they switch to it.
    const isMine = (b) => b.booked_by === user?.id
        || (!!activeUnit && b.unit_number === activeUnit);
    const scopedMoveBookings = scope === 'mine' ? moveBookings.filter(isMine) : moveBookings;
    const filteredAmenityBookings = (amenityFilter
        ? amenityBookings.filter(b => b.amenity_type === amenityFilter)
        : amenityBookings
    ).filter(b => scope !== 'mine' || isMine(b));

    const fetchData = useCallback(async () => {
        try {
            const [moveRes, amenityRes, amenitiesRes] = await Promise.all([
                api.get('/move-bookings'),
                api.get('/amenity-bookings'),
                api.get('/amenities'),
            ]);
            setMoveBookings(moveRes.data);
            setAmenityBookings(amenityRes.data);
            const list = amenitiesRes.data?.amenities || [];
            setAmenitiesList(list);
            if (list.length > 0) {
                const config = {};
                list.forEach(a => {
                    config[ a.key ] = {
                        label: a.label,
                        icon: ICON_MAP[ a.icon ] || Building2,
                        color: 'text-blue-500',
                    };
                });
                setAmenityConfig(config);
            }
        } catch (error) {
            console.error('Failed to fetch bookings:', error);
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);
    /**
     * @generated FunctionHeader
     * Function: handleMoveSubmit
     * Path: frontend/src/pages/dashboard/BookingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleMoveSubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);

        try {
            await api.post('/move-bookings', moveData);
            toast.success('Move booking submitted');
            setMoveDialogOpen(false);
            setMoveData({
                unit_number: activeUnit || '',
                move_type: 'move_in',
                scheduled_date: '',
                time_slot: 'morning',
                requires_lift: true,
                moving_company: '',
                bulk_waste: false,
                notes: ''
            });
            fetchData();
        } catch (error) {
            toast.error('Failed to submit booking');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleAmenitySubmit
     * Path: frontend/src/pages/dashboard/BookingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAmenitySubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);

        try {
            await api.post('/amenity-bookings', amenityData);
            toast.success('Amenity booked successfully');
            setAmenityDialogOpen(false);
            setAmenityData({
                amenity_type: 'bbq_area',
                date: '',
                start_time: '10:00',
                end_time: '12:00',
                notes: ''
            });
            await fetchData();
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to book amenity');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleMoveStatusUpdate
     * Path: frontend/src/pages/dashboard/BookingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleMoveStatusUpdate = async (bookingId, status) => {
        try {
            await api.put(`/move-bookings/${bookingId}/status?status=${status}`);
            toast.success('Booking updated');
            fetchData();
        } catch (error) {
            toast.error('Failed to update booking');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleCancelAmenity
     * Path: frontend/src/pages/dashboard/BookingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCancelAmenity = async (bookingId) => {
        try {
            await api.delete(`/amenity-bookings/${bookingId}`);
            toast.success('Booking cancelled');
            fetchData();
        } catch (error) {
            toast.error('Failed to cancel booking');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleAddAmenity
     * Path: frontend/src/pages/dashboard/BookingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAddAmenity = async (e) => {
        e.preventDefault();
        try {
            await api.post('/amenities', newAmenityForm);
            toast.success('Amenity added');
            setNewAmenityForm({key: '', label: '', description: '', icon: 'Building2'});
            fetchData();
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to add amenity');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleRemoveAmenity
     * Path: frontend/src/pages/dashboard/BookingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRemoveAmenity = async (key) => {
        if (!window.confirm(`Remove "${amenityConfig[ key ]?.label || key}" from this building?`)) return;
        try {
            await api.delete(`/amenities/${key}`);
            toast.success('Amenity removed');
            fetchData();
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to remove amenity');
        }
    };

    return (
        <div className="space-y-6" data-testid="bookings-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Bookings</h1>
                    <p className="text-muted-foreground">Move in/out scheduling and amenity reservations</p>
                </div>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList className="grid w-full grid-cols-2 lg:w-auto lg:inline-flex">
                    <TabsTrigger value="move"><Truck className="mr-2 h-4 w-4"/>Move In/Out</TabsTrigger>
                    <TabsTrigger value="amenity"><Calendar className="mr-2 h-4 w-4"/>Amenities</TabsTrigger>
                </TabsList>

                {/* Move In/Out Tab */}
                <TabsContent value="move" className="space-y-6">
                    <div className="flex justify-end">
                        <Dialog open={moveDialogOpen} onOpenChange={setMoveDialogOpen}>
                            <DialogTrigger asChild>
                                <Button><Plus className="mr-2 h-4 w-4"/>Book Move</Button>
                            </DialogTrigger>
                            <DialogContent>
                                <DialogHeader>
                                    <DialogTitle>Book Move In/Out</DialogTitle>
                                    <DialogDescription>Schedule your move and lift booking</DialogDescription>
                                </DialogHeader>
                                <form onSubmit={handleMoveSubmit} className="space-y-4">
                                    <div className="grid grid-cols-2 gap-4">
                                        <div className="space-y-2">
                                            <Label>Unit Number</Label>
                                            <Input
                                                value={moveData.unit_number}
                                                onChange={(e) => setMoveData(prev => ( {
                                                    ...prev,
                                                    unit_number: e.target.value
                                                } ))}
                                                required
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Move Type</Label>
                                            <Select value={moveData.move_type}
                                                    onValueChange={(v) => setMoveData(prev => ( {
                                                        ...prev,
                                                        move_type: v
                                                    } ))}>
                                                <SelectTrigger><SelectValue/></SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="move_in">Move In</SelectItem>
                                                    <SelectItem value="move_out">Move Out</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>
                                    <div className="grid grid-cols-2 gap-4">
                                        <div className="space-y-2">
                                            <Label>Date</Label>
                                            <Input
                                                type="date"
                                                value={moveData.scheduled_date}
                                                onChange={(e) => setMoveData(prev => ( {
                                                    ...prev,
                                                    scheduled_date: e.target.value
                                                } ))}
                                                required
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Time Slot</Label>
                                            <Select value={moveData.time_slot}
                                                    onValueChange={(v) => setMoveData(prev => ( {
                                                        ...prev,
                                                        time_slot: v
                                                    } ))}>
                                                <SelectTrigger><SelectValue/></SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="morning">Morning (8am - 12pm)</SelectItem>
                                                    <SelectItem value="afternoon">Afternoon (1pm - 5pm)</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Moving Company (optional)</Label>
                                        <Input
                                            value={moveData.moving_company}
                                            onChange={(e) => setMoveData(prev => ( {
                                                ...prev,
                                                moving_company: e.target.value
                                            } ))}
                                            placeholder="e.g., Allied Moving"
                                        />
                                    </div>
                                    <div className="flex gap-4">
                                        <label className="flex items-center gap-2">
                                            <input
                                                type="checkbox"
                                                checked={moveData.requires_lift}
                                                onChange={(e) => setMoveData(prev => ( {
                                                    ...prev,
                                                    requires_lift: e.target.checked
                                                } ))}
                                                className="rounded"
                                            />
                                            <span className="text-sm">Requires lift booking</span>
                                        </label>
                                        <label className="flex items-center gap-2">
                                            <input
                                                type="checkbox"
                                                checked={moveData.bulk_waste}
                                                onChange={(e) => setMoveData(prev => ( {
                                                    ...prev,
                                                    bulk_waste: e.target.checked
                                                } ))}
                                                className="rounded"
                                            />
                                            <span className="text-sm">Bulk waste collection</span>
                                        </label>
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Notes</Label>
                                        <Textarea
                                            value={moveData.notes}
                                            onChange={(e) => setMoveData(prev => ( {...prev, notes: e.target.value} ))}
                                            placeholder="Any special requirements..."
                                        />
                                    </div>
                                    <Button type="submit" className="w-full" disabled={submitting}>
                                        {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : null}
                                        Submit Booking
                                    </Button>
                                </form>
                            </DialogContent>
                        </Dialog>
                    </div>

                    {loading ? (
                        <div className="space-y-4">{[1, 2, 3].map((i) => <div key={i}
                                                                              className="skeleton h-24 w-full"/>)}</div>
                    ) : scopedMoveBookings.length === 0 ? (
                        <Card className="card-dashboard">
                            <CardContent className="py-16 text-center">
                                <Truck className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                                <h3 className="text-lg font-medium mb-2">No Move Bookings</h3>
                                <p className="text-muted-foreground">Book your move in or move out</p>
                            </CardContent>
                        </Card>
                    ) : (
                        <div className="space-y-4">
                            {scopedMoveBookings.map((booking) => (
                                <Card key={booking.id} className="card-dashboard">
                                    <CardContent className="p-4">
                                        <div className="flex items-center justify-between">
                                            <div className="flex items-center gap-4">
                                                <div
                                                    className={`p-3 rounded-full ${booking.move_type === 'move_in' ? 'bg-green-100' : 'bg-orange-100'}`}>
                                                    <Truck
                                                        className={`h-6 w-6 ${booking.move_type === 'move_in' ? 'text-green-600' : 'text-orange-600'}`}/>
                                                </div>
                                                <div>
                                                    <div className="flex items-center gap-2">
                                                        <h3 className="font-semibold">Unit {booking.unit_number}</h3>
                                                        <Badge
                                                            variant={booking.move_type === 'move_in' ? 'default' : 'secondary'}>
                                                            {booking.move_type === 'move_in' ? 'Move In' : 'Move Out'}
                                                        </Badge>
                                                        <Badge
                                                            variant={booking.status === 'approved' ? 'default' : booking.status === 'pending' ? 'secondary' : 'outline'}>
                                                            {booking.status}
                                                        </Badge>
                                                    </div>
                                                    <div
                                                        className="flex items-center gap-4 mt-1 text-sm text-muted-foreground">
                            <span className="flex items-center gap-1">
                              <Calendar className="h-4 w-4"/>
                                {formatDate(booking.scheduled_date)}
                            </span>
                                                        <span className="capitalize">{booking.time_slot}</span>
                                                        {booking.requires_lift &&
                                                            <Badge variant="outline" className="text-xs">Lift
                                                                Required</Badge>}
                                                        {booking.bulk_waste &&
                                                            <Badge variant="outline" className="text-xs">Bulk
                                                                Waste</Badge>}
                                                    </div>
                                                </div>
                                            </div>
                                            {canManage && booking.status === 'pending' && (
                                                <div className="flex gap-2">
                                                    <Button size="sm"
                                                            onClick={() => handleMoveStatusUpdate(booking.id, 'approved')}>Approve</Button>
                                                    <Button size="sm" variant="outline"
                                                            onClick={() => handleMoveStatusUpdate(booking.id, 'cancelled')}>Decline</Button>
                                                </div>
                                            )}
                                        </div>
                                    </CardContent>
                                </Card>
                            ))}
                        </div>
                    )}
                </TabsContent>

                {/* Amenity Bookings Tab */}
                <TabsContent value="amenity" className="space-y-6">
                    <div className="flex justify-end gap-2">
                        {canManage && (
                            <Dialog open={manageAmenitiesOpen} onOpenChange={setManageAmenitiesOpen}>
                                <DialogTrigger asChild>
                                    <Button variant="outline"><Settings className="mr-2 h-4 w-4"/>Manage
                                        Amenities</Button>
                                </DialogTrigger>
                                <DialogContent className="max-w-lg">
                                    <DialogHeader>
                                        <DialogTitle>Manage Building Amenities</DialogTitle>
                                        <DialogDescription>Add or remove amenities available for
                                            booking</DialogDescription>
                                    </DialogHeader>
                                    <div className="space-y-4">
                                        <div className="space-y-2">
                                            <h4 className="text-sm font-semibold">Current Amenities</h4>
                                            {amenitiesList.length === 0 ? (
                                                <p className="text-xs text-muted-foreground">Using default amenities.
                                                    Add a custom one to override.</p>
                                            ) : (
                                                <div className="space-y-2">
                                                    {amenitiesList.map(a => (
                                                        <div key={a.key}
                                                             className="flex items-center justify-between p-2 border rounded">
                                                            <div>
                                                                <span className="font-medium text-sm">{a.label}</span>
                                                                {a.description &&
                                                                    <p className="text-xs text-muted-foreground">{a.description}</p>}
                                                            </div>
                                                            <Button size="sm" variant="destructive"
                                                                    onClick={() => handleRemoveAmenity(a.key)}>
                                                                <Trash2 className="h-3 w-3"/>
                                                            </Button>
                                                        </div>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                        <div className="border-t pt-4">
                                            <h4 className="text-sm font-semibold mb-3">Add New Amenity</h4>
                                            <form onSubmit={handleAddAmenity} className="space-y-3">
                                                <div className="grid grid-cols-2 gap-3">
                                                    <div className="space-y-1">
                                                        <Label className="text-xs">Key (unique ID)</Label>
                                                        <Input placeholder="e.g. rooftop_terrace"
                                                               value={newAmenityForm.key}
                                                               onChange={e => setNewAmenityForm(p => ( {
                                                                   ...p,
                                                                   key: e.target.value.toLowerCase().replace(/\s+/g, '_')
                                                               } ))} required/>
                                                    </div>
                                                    <div className="space-y-1">
                                                        <Label className="text-xs">Display Name</Label>
                                                        <Input placeholder="e.g. Rooftop Terrace"
                                                               value={newAmenityForm.label}
                                                               onChange={e => setNewAmenityForm(p => ( {
                                                                   ...p,
                                                                   label: e.target.value
                                                               } ))} required/>
                                                    </div>
                                                </div>
                                                <div className="space-y-1">
                                                    <Label className="text-xs">Description (optional)</Label>
                                                    <Input placeholder="Brief description"
                                                           value={newAmenityForm.description}
                                                           onChange={e => setNewAmenityForm(p => ( {
                                                               ...p,
                                                               description: e.target.value
                                                           } ))}/>
                                                </div>
                                                <Button type="submit" size="sm" className="w-full">
                                                    <Plus className="mr-2 h-3 w-3"/>Add Amenity
                                                </Button>
                                            </form>
                                        </div>
                                    </div>
                                </DialogContent>
                            </Dialog>
                        )}
                        <Dialog open={amenityDialogOpen} onOpenChange={setAmenityDialogOpen}>
                            <DialogTrigger asChild>
                                <Button><Plus className="mr-2 h-4 w-4"/>Book Amenity</Button>
                            </DialogTrigger>
                            <DialogContent>
                                <DialogHeader>
                                    <DialogTitle>Book Amenity</DialogTitle>
                                    <DialogDescription>Reserve a building amenity</DialogDescription>
                                </DialogHeader>
                                <form onSubmit={handleAmenitySubmit} className="space-y-4">
                                    <div className="space-y-2">
                                        <Label>Amenity</Label>
                                        <Select value={amenityData.amenity_type}
                                                onValueChange={(v) => setAmenityData(prev => ( {
                                                    ...prev,
                                                    amenity_type: v
                                                } ))}>
                                            <SelectTrigger><SelectValue/></SelectTrigger>
                                            <SelectContent>
                                                {Object.entries(amenityConfig).map(([key, config]) => (
                                                    <SelectItem key={key} value={key}>
                                                        <div className="flex items-center gap-2">
                                                            <config.icon className={`h-4 w-4 ${config.color}`}/>
                                                            {config.label}
                                                        </div>
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Date</Label>
                                        <Input
                                            type="date"
                                            value={amenityData.date}
                                            onChange={(e) => setAmenityData(prev => ( {
                                                ...prev,
                                                date: e.target.value
                                            } ))}
                                            required
                                        />
                                    </div>
                                    <div className="grid grid-cols-2 gap-4">
                                        <div className="space-y-2">
                                            <Label>Start Time</Label>
                                            <Select value={amenityData.start_time}
                                                    onValueChange={(v) => setAmenityData(prev => ( {
                                                        ...prev,
                                                        start_time: v
                                                    } ))}>
                                                <SelectTrigger><SelectValue/></SelectTrigger>
                                                <SelectContent>
                                                    {timeSlots.map(t => <SelectItem key={t} value={t}>{t}</SelectItem>)}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>End Time</Label>
                                            <Select value={amenityData.end_time}
                                                    onValueChange={(v) => setAmenityData(prev => ( {
                                                        ...prev,
                                                        end_time: v
                                                    } ))}>
                                                <SelectTrigger><SelectValue/></SelectTrigger>
                                                <SelectContent>
                                                    {timeSlots.map(t => <SelectItem key={t} value={t}>{t}</SelectItem>)}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Notes (optional)</Label>
                                        <Textarea
                                            value={amenityData.notes}
                                            onChange={(e) => setAmenityData(prev => ( {
                                                ...prev,
                                                notes: e.target.value
                                            } ))}
                                            placeholder="e.g., Birthday party for 20 people"
                                        />
                                    </div>
                                    <Button type="submit" className="w-full" disabled={submitting}>
                                        {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : null}
                                        Book Amenity
                                    </Button>
                                </form>
                            </DialogContent>
                        </Dialog>
                    </div>

                    {/* Amenity Cards — click to filter bookings below */}
                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        {Object.entries(amenityConfig).map(([key, config]) => {
                            const Icon = config.icon;
                            const bookingsForAmenity = amenityBookings.filter(b => b.amenity_type === key && b.status === 'confirmed');
                            const isSelected = amenityFilter === key;
                            return (
                                <Card
                                    key={key}
                                    className={`card-dashboard cursor-pointer transition-all hover:shadow-md ${isSelected ? 'ring-2 ring-primary' : ''}`}
                                    onClick={() => setAmenityFilter(isSelected ? null : key)}
                                >
                                    <CardContent className="p-4 text-center">
                                        <Icon className={`h-8 w-8 mx-auto mb-2 ${config.color}`}/>
                                        <p className="font-medium">{config.label}</p>
                                        <p className="text-xs text-muted-foreground">{bookingsForAmenity.length} upcoming</p>
                                        {isSelected &&
                                            <p className="text-xs text-primary font-medium mt-1">Filtered</p>}
                                    </CardContent>
                                </Card>
                            );
                        })}
                    </div>
                    {amenityFilter && (
                        <div className="flex items-center gap-2">
                            <span className="text-sm text-muted-foreground">
                                Showing: <strong>{amenityConfig[ amenityFilter ]?.label}</strong>
                            </span>
                            <Button variant="ghost" size="sm" onClick={() => setAmenityFilter(null)}
                                    className="h-6 px-2 text-xs">
                                Clear filter
                            </Button>
                        </div>
                    )}

                    {loading ? (
                        <div className="space-y-4">{[1, 2, 3].map((i) => <div key={i}
                                                                              className="skeleton h-20 w-full"/>)}</div>
                    ) : filteredAmenityBookings.length === 0 ? (
                        <Card className="card-dashboard">
                            <CardContent className="py-16 text-center">
                                <Calendar className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                                <h3 className="text-lg font-medium mb-2">No Bookings</h3>
                                <p className="text-muted-foreground">
                                    {amenityFilter
                                        ? `No bookings for ${amenityConfig[ amenityFilter ]?.label}`
                                        : 'Book an amenity for your next event'}
                                </p>
                            </CardContent>
                        </Card>
                    ) : (
                        <div className="space-y-4">
                            {filteredAmenityBookings.map((booking) => {
                                const config = amenityConfig[ booking.amenity_type ];
                                const Icon = config?.icon || Calendar;
                                return (
                                    <Card key={booking.id} className="card-dashboard">
                                        <CardContent className="p-4">
                                            <div className="flex items-center justify-between">
                                                <div className="flex items-center gap-4">
                                                    <div className="p-3 rounded-full bg-muted">
                                                        <Icon
                                                            className={`h-6 w-6 ${config?.color || 'text-gray-500'}`}/>
                                                    </div>
                                                    <div>
                                                        <div className="flex items-center gap-2">
                                                            <h3 className="font-semibold">{config?.label}</h3>
                                                            <Badge
                                                                variant={booking.status === 'confirmed' ? 'default' : 'secondary'}>
                                                                {booking.status}
                                                            </Badge>
                                                        </div>
                                                        <div
                                                            className="flex items-center gap-4 mt-1 text-sm text-muted-foreground">
                                                            <span>{formatDate(booking.date)}</span>
                                                            <span>{booking.start_time} - {booking.end_time}</span>
                                                            <span>Unit {booking.unit_number}</span>
                                                        </div>
                                                    </div>
                                                </div>
                                                {( booking.booked_by === user?.id || canManage ) && booking.status === 'confirmed' && (
                                                    <Button size="sm" variant="outline"
                                                            onClick={() => handleCancelAmenity(booking.id)}>
                                                        <Trash2 className="h-4 w-4"/>
                                                    </Button>
                                                )}
                                            </div>
                                        </CardContent>
                                    </Card>
                                );
                            })}
                        </div>
                    )}
                </TabsContent>
            </Tabs>
        </div>
    );
};

export default BookingsPage;
