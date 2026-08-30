import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '../../components/ui/dialog';
import { AlertCircle, Calendar, CheckCircle2, Clock, Loader2, Plus, Users } from 'lucide-react';
import { toast } from 'sonner';
import { volunteerApi } from '../../lib/api/community-os';

const MANAGE_ROLES = ['super_admin', 'ec_member', 'strata_manager'];
/**
 * @generated FunctionHeader
 * Function: statusBadge
 * Path: frontend/src/pages/dashboard/VolunteerPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function statusBadge(status) {
    const map = {
        upcoming: {label: 'Upcoming', className: 'bg-blue-100 text-blue-800'},
        open: {label: 'Open', className: 'bg-green-100 text-green-800'},
        completed: {label: 'Completed', className: 'bg-gray-100 text-gray-700'},
        cancelled: {label: 'Cancelled', className: 'bg-red-100 text-red-800'},
    };
    const cfg = map[ status ] || {label: status || 'Unknown', className: 'bg-gray-100 text-gray-700'};
    return <Badge className={cfg.className}>{cfg.label}</Badge>;
}
/**
 * @generated FunctionHeader
 * Function: VolunteerPage
 * Path: frontend/src/pages/dashboard/VolunteerPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function VolunteerPage() {
    const {user, api} = useAuth();
    const vApi = volunteerApi(api);

    const [events, setEvents] = useState([]);
    const [loading, setLoading] = useState(true);
    const [registering, setRegistering] = useState(null);

    const [showCreate, setShowCreate] = useState(false);
    const [creating, setCreating] = useState(false);
    const [form, setForm] = useState({
        title: '', description: '', event_date: '', volunteer_hours: '2', max_volunteers: '',
    });

    const isManager = MANAGE_ROLES.includes(user?.role || '');
    const canRegister = ['owner', 'tenant', ...MANAGE_ROLES].includes(user?.role || '');

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const res = await vApi.list();
            setEvents(res.data?.events || res.data || []);
        } catch {
            setEvents([]);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        load();
    }, [load]);
    /**
     * @generated FunctionHeader
     * Function: handleRegister
     * Path: frontend/src/pages/dashboard/VolunteerPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRegister = async (event) => {
        setRegistering(event.id);
        try {
            await vApi.register(event.id);
            toast.success(event.is_registered ? 'Unregistered' : 'Registered for event!');
            load();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to register');
        } finally {
            setRegistering(null);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleCreate
     * Path: frontend/src/pages/dashboard/VolunteerPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCreate = async (e) => {
        e.preventDefault();
        setCreating(true);
        try {
            await vApi.create({
                ...form,
                volunteer_hours: parseFloat(form.volunteer_hours),
                max_volunteers: form.max_volunteers ? parseInt(form.max_volunteers) : undefined,
            });
            toast.success('Volunteer event created');
            setShowCreate(false);
            setForm({title: '', description: '', event_date: '', volunteer_hours: '2', max_volunteers: ''});
            load();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to create event');
        } finally {
            setCreating(false);
        }
    };

    const upcoming = events.filter(e => ['upcoming', 'open'].includes(e.status));
    const past = events.filter(e => !['upcoming', 'open'].includes(e.status));

    return (
        <div className="p-6 max-w-4xl mx-auto space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <Users className="h-6 w-6 text-indigo-500"/>
                        Volunteer Events
                    </h1>
                    <p className="text-muted-foreground text-sm">Get involved in your community</p>
                </div>
                {isManager && (
                    <Button onClick={() => setShowCreate(true)}>
                        <Plus className="h-4 w-4 mr-2"/>Create Event
                    </Button>
                )}
            </div>

            {loading ? (
                <div className="flex justify-center py-16">
                    <Loader2 className="h-10 w-10 animate-spin text-muted-foreground"/>
                </div>
            ) : events.length === 0 ? (
                <Card>
                    <CardContent className="py-12 text-center text-muted-foreground">
                        <AlertCircle className="h-8 w-8 mx-auto mb-2 opacity-50"/>
                        <p>No volunteer events scheduled.</p>
                    </CardContent>
                </Card>
            ) : (
                <>
                    {upcoming.length > 0 && (
                        <div className="space-y-3">
                            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">Upcoming</h2>
                            {upcoming.map((ev) => (
                                <EventCard key={ev.id} event={ev} canRegister={canRegister} onRegister={handleRegister}
                                           registering={registering}/>
                            ))}
                        </div>
                    )}
                    {past.length > 0 && (
                        <div className="space-y-3">
                            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wide">Past
                                Events</h2>
                            {past.map((ev) => (
                                <EventCard key={ev.id} event={ev} canRegister={false} onRegister={handleRegister}
                                           registering={registering}/>
                            ))}
                        </div>
                    )}
                </>
            )}

            <Dialog open={showCreate} onOpenChange={setShowCreate}>
                <DialogContent className="max-w-lg">
                    <DialogHeader>
                        <DialogTitle>Create Volunteer Event</DialogTitle>
                    </DialogHeader>
                    <form onSubmit={handleCreate} className="space-y-4">
                        <div className="space-y-1">
                            <Label htmlFor="v-title">Title *</Label>
                            <Input id="v-title" required value={form.title}
                                   onChange={e => setForm(f => ( {...f, title: e.target.value} ))}
                                   placeholder="Event title"/>
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="v-desc">Description</Label>
                            <Textarea id="v-desc" value={form.description}
                                      onChange={e => setForm(f => ( {...f, description: e.target.value} ))} rows={3}/>
                        </div>
                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1">
                                <Label htmlFor="v-date">Event Date *</Label>
                                <Input id="v-date" type="datetime-local" required value={form.event_date}
                                       onChange={e => setForm(f => ( {...f, event_date: e.target.value} ))}/>
                            </div>
                            <div className="space-y-1">
                                <Label htmlFor="v-hours">Volunteer Hours</Label>
                                <Input id="v-hours" type="number" min="0.5" step="0.5" value={form.volunteer_hours}
                                       onChange={e => setForm(f => ( {...f, volunteer_hours: e.target.value} ))}/>
                            </div>
                        </div>
                        <div className="space-y-1">
                            <Label htmlFor="v-max">Max Volunteers (optional)</Label>
                            <Input id="v-max" type="number" min="1" value={form.max_volunteers}
                                   onChange={e => setForm(f => ( {...f, max_volunteers: e.target.value} ))}
                                   placeholder="Leave blank for unlimited"/>
                        </div>
                        <DialogFooter>
                            <Button type="button" variant="outline" onClick={() => setShowCreate(false)}>Cancel</Button>
                            <Button type="submit" disabled={creating}>
                                {creating && <Loader2 className="h-4 w-4 mr-2 animate-spin"/>}
                                Create
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: EventCard
 * Path: frontend/src/pages/dashboard/VolunteerPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function EventCard({event, canRegister, onRegister, registering}) {
    const isRegistering = registering === event.id;
    return (
        <Card>
            <CardContent className="pt-5 space-y-3">
                <div className="flex items-start justify-between gap-3 flex-wrap">
                    <div className="space-y-1">
                        <div className="flex items-center gap-2 flex-wrap">
                            {statusBadge(event.status)}
                            {event.volunteer_hours && (
                                <Badge variant="outline" className="text-xs">
                                    <Clock className="h-3 w-3 mr-1"/>{event.volunteer_hours}h
                                </Badge>
                            )}
                        </div>
                        <h3 className="font-semibold">{event.title}</h3>
                        {event.description && <p className="text-sm text-muted-foreground">{event.description}</p>}
                    </div>
                    <div className="text-right space-y-1">
                        {event.event_date && (
                            <p className="text-xs text-muted-foreground flex items-center gap-1">
                                <Calendar className="h-3 w-3"/>
                                {new Date(event.event_date).toLocaleDateString('en-AU', {
                                    day: 'numeric',
                                    month: 'short',
                                    year: 'numeric'
                                })}
                            </p>
                        )}
                        {typeof event.registered_count === 'number' && (
                            <p className="text-xs text-muted-foreground flex items-center gap-1">
                                <Users className="h-3 w-3"/>
                                {event.registered_count}{event.max_volunteers ? `/${event.max_volunteers}` : ''} registered
                            </p>
                        )}
                    </div>
                </div>

                {event.is_registered && (
                    <p className="text-xs text-green-600 flex items-center gap-1">
                        <CheckCircle2 className="h-3 w-3"/>You are registered
                    </p>
                )}

                {canRegister && ['upcoming', 'open'].includes(event.status) && (
                    <Button
                        size="sm"
                        variant={event.is_registered ? 'outline' : 'default'}
                        onClick={() => onRegister(event)}
                        disabled={isRegistering}
                    >
                        {isRegistering && <Loader2 className="h-3 w-3 mr-1 animate-spin"/>}
                        {event.is_registered ? 'Unregister' : 'Register'}
                    </Button>
                )}
            </CardContent>
        </Card>
    );
}
