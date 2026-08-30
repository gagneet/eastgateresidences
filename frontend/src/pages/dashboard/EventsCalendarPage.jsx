// @featuretrace:meetings — Calendar surface for committee meetings and related building events.
// Layer: frontend
// Data flow: EventsCalendarPage.jsx → /events, /meetings → db.events + db.meetings (building-scoped).
// Related: backend/server.py community event and meeting routes
//          frontend/src/pages/dashboard/MeetingNotesPage.jsx
import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
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
import {
    Bell,
    Building2,
    CalendarDays,
    ChevronLeft,
    ChevronRight,
    DollarSign,
    FileText,
    Loader2,
    Plus,
    Users,
    Wrench
} from 'lucide-react';
import { formatDateTime } from '../../lib/utils';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: parseMultilineList
 * Path: frontend/src/pages/dashboard/EventsCalendarPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const parseMultilineList = (value) => value
    .split('\n')
    .map(item => item.trim())
    .filter(Boolean);

const eventTypeConfig = {
    agm: {label: 'AGM', color: 'bg-purple-100 text-purple-800', dot: 'bg-purple-500', icon: Users},
    levy_due: {label: 'Levy Due', color: 'bg-red-100 text-red-800', dot: 'bg-red-500', icon: DollarSign},
    land_tax: {label: 'Land Tax', color: 'bg-rose-100 text-rose-800', dot: 'bg-rose-500', icon: FileText},
    community: {label: 'Community', color: 'bg-green-100 text-green-800', dot: 'bg-green-500', icon: Users},
    maintenance: {label: 'Maintenance', color: 'bg-orange-100 text-orange-800', dot: 'bg-orange-500', icon: Wrench},
    meeting: {label: 'Meeting', color: 'bg-blue-100 text-blue-800', dot: 'bg-blue-500', icon: Building2},
    announcement: {label: 'Notice', color: 'bg-yellow-100 text-yellow-800', dot: 'bg-yellow-500', icon: Bell},
    ppm: {label: 'PPM', color: 'bg-teal-100 text-teal-800', dot: 'bg-teal-500', icon: Wrench},
    compliance: {label: 'Compliance', color: 'bg-indigo-100 text-indigo-800', dot: 'bg-indigo-500', icon: FileText},
    booking: {label: 'Booking', color: 'bg-teal-100 text-teal-800', dot: 'bg-teal-500', icon: CalendarDays},
    request: {label: 'Request', color: 'bg-sky-100 text-sky-800', dot: 'bg-sky-500', icon: Bell},
    other: {label: 'Other', color: 'bg-gray-100 text-gray-800', dot: 'bg-gray-400', icon: CalendarDays}
};
/**
 * @generated FunctionHeader
 * Function: EventsCalendarPage
 * Path: frontend/src/pages/dashboard/EventsCalendarPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const EventsCalendarPage = () => {
    const {api, hasPermission, user} = useAuth();
    const {activeUnit} = useActiveUnit();
    const [events, setEvents] = useState([]);
    const [ppmEvents, setPpmEvents] = useState([]);
    const [complianceEvents, setComplianceEvents] = useState([]);
    const [bookingEvents, setBookingEvents] = useState([]);
    const [maintenanceCalEvents, setMaintenanceCalEvents] = useState([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [currentDate, setCurrentDate] = useState(new Date());

    // Day popup state
    const [selectedDay, setSelectedDay] = useState(null);
    const [dayPopupOpen, setDayPopupOpen] = useState(false);

    const [formData, setFormData] = useState({
        title: '',
        description: '',
        event_type: 'community',
        start_date: '',
        end_date: '',
        location: '',
        is_recurring: false,
        recurrence_rule: '',
        is_public: true,
        meeting_agenda: '',
        meeting_attendees: '',
    });

    const canManage = hasPermission('can_manage_meetings');
    const syncRan = React.useRef(false);

    const getEventDestination = useCallback((event) => {
        const rawId = typeof event?.id === 'string' ? event.id : '';
        const meetingId = rawId.startsWith('meeting_') ? rawId.replace(/^meeting_/, '') : rawId;

        switch (event?.event_type) {
            case 'meeting':
                return {
                    href: canManage
                        ? `/governance/meetings/notes?meetingId=${encodeURIComponent(meetingId)}`
                        : `/governance/meetings?meetingId=${encodeURIComponent(meetingId)}`,
                    label: canManage ? 'Open Meeting Notes' : 'Open Meetings',
                };
            case 'agm':
                return {href: '/governance/agm', label: 'Open AGM'};
            case 'announcement':
                return {href: '/community/notices', label: 'Open Notices'};
            case 'maintenance':
            case 'ppm':
                return {href: '/maintenance', label: 'Open Maintenance'};
            case 'compliance':
                return {href: '/compliance', label: 'Open Compliance'};
            case 'booking':
                return {href: '/community/bookings', label: 'Open Bookings'};
            case 'request':
                return {href: '/requests', label: 'Open Requests'};
            case 'levy_due':
            case 'land_tax':
                return {href: '/financials/overview', label: 'Open Finance'};
            case 'community':
                if (event?.source_url) {
                    return {external: event.source_url, label: 'Open Source'};
                }
                return {href: '/community', label: 'Open Community'};
            default:
                if (event?.source_url) {
                    return {external: event.source_url, label: 'Open Source'};
                }
                return null;
        }
    }, [canManage]);

    const openEventDestination = useCallback((event) => {
        const destination = getEventDestination(event);
        if (!destination) return;

        setDayPopupOpen(false);
        if (destination.external) {
            window.open(destination.external, '_blank', 'noopener,noreferrer');
            return;
        }
        window.location.assign(destination.href);
    }, [getEventDestination]);

    const fetchEvents = useCallback(async () => {
        try {
            const month = `${currentDate.getFullYear()}-${String(currentDate.getMonth() + 1).padStart(2, '0')}`;
            const response = await api.get(`/events?month=${month}`);
            setEvents(response.data);
        } catch (error) {
            console.error('Failed to fetch events:', error);
        } finally {
            setLoading(false);
        }
    }, [api, currentDate]);

    // Fetch events whenever the visible month changes
    useEffect(() => {
        fetchEvents();
    }, [fetchEvents]);

    // One-time sync on mount: levy dates + land tax + PPM items
    useEffect(() => {
        if (syncRan.current) return;
        syncRan.current = true;

        if (canManage) {
            api.post(`/events/generate-levy-dates?year=${new Date().getFullYear()}`)
                .catch(() => { /* silent — already generated */
                });
        }
        if (user?.role === 'owner' && activeUnit) {
            api.post(`/events/sync-land-tax?unit_number=${encodeURIComponent(activeUnit)}`)
                .catch(() => { /* silent — unit may not be tenanted */
                });
        }

        // Fetch PPM upcoming items and convert to synthetic calendar events
        api.get('/ppm/upcoming?days=730')
            .then(res => {
                const items = Array.isArray(res.data) ? res.data : [];
                const synthetic = items
                    .filter(item => item.next_due_date)
                    .map(item => ( {
                        id: `ppm-${item.id || item.schedule_id || item.identifier}`,
                        title: item.description || 'PPM Task',
                        description: `Section: ${item.section || '—'} | Frequency: ${item.frequency || '—'}`,
                        event_type: 'ppm',
                        start_date: item.next_due_date.split('T')[ 0 ] + 'T09:00:00',
                        end_date: item.next_due_date.split('T')[ 0 ] + 'T10:00:00',
                        location: item.section || '',
                        is_public: false,
                        _ppm_frequency: item.frequency,
                        _ppm_status: item.status,
                    } ));
                setPpmEvents(synthetic);

                // Create bell notifications for PPM items due within 30 days (once per day)
                const today = new Date().toISOString().split('T')[ 0 ];
                const notifKey = `ppm_notif_${today}`;
                if (!localStorage.getItem(notifKey)) {
                    const soonItems = items.filter(item => {
                        if (!item.next_due_date) return false;
                        const due = new Date(item.next_due_date);
                        const diffDays = Math.ceil(( due - new Date() ) / 86400000);
                        return diffDays >= 0 && diffDays <= 30;
                    });
                    if (soonItems.length > 0) {
                        api.post('/notifications/send', {
                            title: `${soonItems.length} PPM Task${soonItems.length > 1 ? 's' : ''} Due Soon`,
                            message: `You have ${soonItems.length} preventive maintenance task${soonItems.length > 1 ? 's' : ''} due in the next 30 days. Check the Maintenance page for details.`,
                            notification_type: 'ppm',
                            link: '/maintenance?tab=ppm',
                        }).then(() => localStorage.setItem(notifKey, '1'))
                            .catch(() => {
                            });
                    }
                }
            })
            .catch(() => { /* PPM endpoint may not be available — silent */
            });

        // Fetch compliance events for ALL users — building-level compliance is relevant to everyone
        api.get('/compliance-items?limit=200')
            .then(res => {
                const items = Array.isArray(res.data) ? res.data : [];
                const syntheticCompliance = items
                    .filter(item => item.due_date)
                    .map(item => ( {
                        id: `compliance-${item.id || item._id}`,
                        title: item.title || item.description || 'Compliance Task',
                        description: item.description || '',
                        event_type: 'compliance',
                        start_date: item.due_date.split('T')[ 0 ] + 'T09:00:00',
                        end_date: item.due_date.split('T')[ 0 ] + 'T10:00:00',
                        location: item.category || '',
                        is_public: true,
                    } ));
                setComplianceEvents(syntheticCompliance);
            })
            .catch(() => { /* silent — may return 403 for non-manager roles */
            });

        // Fetch bookings (move-in/out + amenity) and convert to calendar events
        Promise.allSettled([
            api.get('/move-bookings'),
            api.get('/amenity-bookings'),
        ]).then(([moveRes, amenityRes]) => {
            const moveItems = moveRes.status === 'fulfilled' ? ( Array.isArray(moveRes.value.data) ? moveRes.value.data : [] ) : [];
            const amenityItems = amenityRes.status === 'fulfilled' ? ( Array.isArray(amenityRes.value.data) ? amenityRes.value.data : [] ) : [];

            const synthetic = [
                ...moveItems.filter(b => b.scheduled_date && b.status !== 'cancelled').map(b => ( {
                    id: `booking-move-${b.id || b._id}`,
                    title: `Move ${b.move_type || 'Booking'} — Unit ${b.unit_number || ''}`,
                    description: b.notes || '',
                    event_type: 'booking',
                    start_date: b.scheduled_date.split('T')[ 0 ] + `T${b.start_time || '08:00'}:00`,
                    end_date: b.scheduled_date.split('T')[ 0 ] + `T${b.end_time || '12:00'}:00`,
                    location: `Unit ${b.unit_number || ''}`,
                    is_public: false,
                } )),
                ...amenityItems.filter(b => b.date && b.status !== 'cancelled').map(b => ( {
                    id: `booking-amenity-${b.id || b._id}`,
                    title: `${b.amenity_type || 'Amenity'} Booking${b.unit_number ? ` — Unit ${b.unit_number}` : ''}`,
                    description: b.notes || '',
                    event_type: 'booking',
                    start_date: b.date.split('T')[ 0 ] + `T${b.start_time || '09:00'}:00`,
                    end_date: b.date.split('T')[ 0 ] + `T${b.end_time || '10:00'}:00`,
                    location: b.amenity_type || '',
                    is_public: false,
                } )),
            ];
            setBookingEvents(synthetic);
        });

        // Fetch maintenance requests with scheduled dates
        api.get('/maintenance?limit=100')
            .then(res => {
                const items = Array.isArray(res.data) ? res.data : [];
                const syntheticMaint = items
                    .filter(item => item.scheduled_date || item.scheduled_start)
                    .map(item => ( {
                        id: `maint-${item.id || item._id}`,
                        title: `Maintenance: ${item.title || 'Request'}`,
                        description: item.description || '',
                        event_type: 'maintenance',
                        start_date: ( item.scheduled_date || item.scheduled_start ).split('T')[ 0 ] + 'T09:00:00',
                        end_date: ( item.scheduled_date || item.scheduled_start ).split('T')[ 0 ] + 'T10:00:00',
                        location: item.location || '',
                        is_public: false,
                    } ));
                setMaintenanceCalEvents(syntheticMaint);
            })
            .catch(() => { /* silent */
            });
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/EventsCalendarPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            if (formData.event_type === 'meeting') {
                await api.post('/meetings', {
                    title: formData.title,
                    description: formData.description,
                    meeting_date: formData.start_date,
                    location: formData.location,
                    agenda: parseMultilineList(formData.meeting_agenda),
                    attendees: parseMultilineList(formData.meeting_attendees),
                });
                toast.success('Meeting scheduled');
            } else {
                await api.post('/events', {
                    title: formData.title,
                    description: formData.description,
                    event_type: formData.event_type,
                    start_date: formData.start_date,
                    end_date: formData.end_date || null,
                    location: formData.location,
                    is_recurring: formData.is_recurring,
                    recurrence_rule: formData.is_recurring ? formData.recurrence_rule : null,
                    is_public: formData.is_public,
                });
                toast.success('Event created');
            }
            setDialogOpen(false);
            setFormData({
                title: '', description: '', event_type: 'community', start_date: '',
                end_date: '', location: '', is_recurring: false, recurrence_rule: '', is_public: true,
                meeting_agenda: '', meeting_attendees: '',
            });
            fetchEvents();
        } catch (error) {
            toast.error(formData.event_type === 'meeting' ? 'Failed to schedule meeting' : 'Failed to create event');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: generateLevyDates
     * Path: frontend/src/pages/dashboard/EventsCalendarPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const generateLevyDates = async () => {
        try {
            await api.post(`/events/generate-levy-dates?year=${currentDate.getFullYear()}`);
            toast.success('Levy due dates generated');
            fetchEvents();
        } catch (error) {
            toast.error('Failed to generate levy dates');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: scrapeEvents
     * Path: frontend/src/pages/dashboard/EventsCalendarPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const scrapeEvents = async () => {
        try {
            const response = await api.post('/events/scrape-community');
            toast.info(response.data.note || response.data.message);
        } catch (error) {
            toast.error('Failed to scrape events');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: navigateMonth
     * Path: frontend/src/pages/dashboard/EventsCalendarPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const navigateMonth = (direction) => {
        const newDate = new Date(currentDate);
        newDate.setMonth(newDate.getMonth() + direction);
        setCurrentDate(newDate);
    };
    /**
     * @generated FunctionHeader
     * Function: handleDayClick
     * Path: frontend/src/pages/dashboard/EventsCalendarPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDayClick = (day) => {
        if (!day.day) return;
        setSelectedDay(day);
        setDayPopupOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: openAddEventForDate
     * Path: frontend/src/pages/dashboard/EventsCalendarPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openAddEventForDate = (dateStr) => {
        setDayPopupOpen(false);
        setFormData(prev => ( {...prev, start_date: dateStr ? `${dateStr}T09:00` : ''} ));
        setDialogOpen(true);
    };
    // Generate calendar grid
    /**
     * @generated FunctionHeader
     * Function: getDaysInMonth
     * Path: frontend/src/pages/dashboard/EventsCalendarPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getDaysInMonth = () => {
        const year = currentDate.getFullYear();
        const month = currentDate.getMonth();
        const firstDay = new Date(year, month, 1);
        const lastDay = new Date(year, month + 1, 0);
        const daysInMonth = lastDay.getDate();
        const startingDay = firstDay.getDay();

        const days = [];

        // Previous month padding
        for (let i = 0; i < startingDay; i++) {
            days.push({day: null, events: []});
        }

        // Current month days
        const allEvents = [...events, ...ppmEvents, ...complianceEvents, ...bookingEvents, ...maintenanceCalEvents];
        for (let day = 1; day <= daysInMonth; day++) {
            const dateStr = `${year}-${String(month + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
            const dayEvents = allEvents.filter(e => e.start_date && e.start_date.startsWith(dateStr));
            days.push({day, date: dateStr, events: dayEvents});
        }

        return days;
    };

    const monthName = currentDate.toLocaleString('default', {month: 'long', year: 'numeric'});
    const days = getDaysInMonth();
    const today = new Date().toISOString().split('T')[ 0 ];

    // Upcoming events for sidebar (regular + PPM merged)
    const upcomingEvents = [...events, ...ppmEvents, ...complianceEvents, ...bookingEvents, ...maintenanceCalEvents]
        .filter(e => e.start_date >= today)
        .sort((a, b) => a.start_date.localeCompare(b.start_date))
        .slice(0, 8);

    // Format selected day label for popup
    const selectedDayLabel = selectedDay?.date
        ? new Date(selectedDay.date + 'T12:00:00').toLocaleDateString('en-AU', {
            weekday: 'long', day: 'numeric', month: 'long', year: 'numeric'
        })
        : '';

    return (
        <div className="space-y-6" data-testid="events-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Community Calendar</h1>
                    <p className="text-muted-foreground">Events, AGMs, levy due dates and community activities</p>
                </div>
                <div className="flex gap-2 flex-wrap">
                    {canManage && (
                        <>
                            <Button variant="outline" size="sm" onClick={generateLevyDates}>
                                <DollarSign className="mr-2 h-4 w-4"/>
                                Add Levy Dates
                            </Button>
                            <Button variant="outline" size="sm" onClick={scrapeEvents}>
                                <Users className="mr-2 h-4 w-4"/>
                                Fetch Community Events
                            </Button>
                            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                                <DialogTrigger asChild>
                                    <Button><Plus className="mr-2 h-4 w-4"/>Add Event</Button>
                                </DialogTrigger>
                                <DialogContent>
                                    <DialogHeader>
                                        <DialogTitle>Add Event</DialogTitle>
                                        <DialogDescription>
                                            {formData.event_type === 'meeting'
                                                ? 'Schedule a committee meeting with agenda and attendees so it appears in the meetings workflow and calendar.'
                                                : 'Create a new calendar event'}
                                        </DialogDescription>
                                    </DialogHeader>
                                    <form onSubmit={handleSubmit} className="space-y-4">
                                        <div className="space-y-2">
                                            <Label>Event Title</Label>
                                            <Input value={formData.title} onChange={(e) => setFormData(prev => ( {
                                                ...prev,
                                                title: e.target.value
                                            } ))} required/>
                                        </div>
                                        <div className="grid grid-cols-2 gap-4">
                                            <div className="space-y-2">
                                                <Label>Event Type</Label>
                                                <Select value={formData.event_type}
                                                        onValueChange={(v) => setFormData(prev => ( {
                                                            ...prev,
                                                            event_type: v
                                                        } ))}>
                                                    <SelectTrigger><SelectValue/></SelectTrigger>
                                                    <SelectContent>
                                                        {Object.entries(eventTypeConfig).map(([k, v]) => (
                                                            <SelectItem key={k} value={k}>{v.label}</SelectItem>
                                                        ))}
                                                    </SelectContent>
                                                </Select>
                                            </div>
                                            <div className="space-y-2">
                                                <Label>Location</Label>
                                                <Input value={formData.location}
                                                       onChange={(e) => setFormData(prev => ( {
                                                           ...prev,
                                                           location: e.target.value
                                                       } ))} placeholder="e.g., Community Room"/>
                                            </div>
                                        </div>
                                        <div className="grid grid-cols-2 gap-4">
                                            <div className="space-y-2">
                                                <Label>{formData.event_type === 'meeting' ? 'Meeting Date & Time' : 'Start Date & Time'}</Label>
                                                <Input type="datetime-local" value={formData.start_date}
                                                       onChange={(e) => setFormData(prev => ( {
                                                           ...prev,
                                                           start_date: e.target.value
                                                       } ))} required/>
                                            </div>
                                            {formData.event_type !== 'meeting' && (
                                                <div className="space-y-2">
                                                    <Label>End Date & Time (optional)</Label>
                                                    <Input type="datetime-local" value={formData.end_date}
                                                           onChange={(e) => setFormData(prev => ( {
                                                               ...prev,
                                                               end_date: e.target.value
                                                           } ))}/>
                                                </div>
                                            )}
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Description</Label>
                                            <Textarea value={formData.description}
                                                      onChange={(e) => setFormData(prev => ( {
                                                          ...prev,
                                                          description: e.target.value
                                                      } ))} rows={3}/>
                                        </div>
                                        {formData.event_type === 'meeting' ? (
                                            <>
                                                <div className="space-y-2">
                                                    <Label>Agenda (one item per line)</Label>
                                                    <Textarea
                                                        value={formData.meeting_agenda}
                                                        onChange={(e) => setFormData(prev => ( {
                                                            ...prev,
                                                            meeting_agenda: e.target.value
                                                        } ))}
                                                        rows={4}
                                                        placeholder="Welcome and apologies&#10;Previous minutes&#10;Maintenance update"
                                                    />
                                                </div>
                                                <div className="space-y-2">
                                                    <Label>Attendees (one name per line)</Label>
                                                    <Textarea
                                                        value={formData.meeting_attendees}
                                                        onChange={(e) => setFormData(prev => ( {
                                                            ...prev,
                                                            meeting_attendees: e.target.value
                                                        } ))}
                                                        rows={4}
                                                        placeholder="Chairperson Name&#10;EC Member Name&#10;Strata Manager"
                                                    />
                                                    <p className="text-xs text-muted-foreground">
                                                        You can continue editing the agenda, attendees, and minutes
                                                        later from the Meeting Notes page.
                                                    </p>
                                                </div>
                                            </>
                                        ) : (
                                            <div className="flex items-center gap-4">
                                                <label className="flex items-center gap-2">
                                                    <input type="checkbox" checked={formData.is_recurring}
                                                           onChange={(e) => setFormData(prev => ( {
                                                               ...prev,
                                                               is_recurring: e.target.checked
                                                           } ))} className="rounded"/>
                                                    <span className="text-sm">Recurring event</span>
                                                </label>
                                                {formData.is_recurring && (
                                                    <Select value={formData.recurrence_rule}
                                                            onValueChange={(v) => setFormData(prev => ( {
                                                                ...prev,
                                                                recurrence_rule: v
                                                            } ))}>
                                                        <SelectTrigger className="w-32"><SelectValue
                                                            placeholder="Frequency"/></SelectTrigger>
                                                        <SelectContent>
                                                            <SelectItem value="weekly">Weekly</SelectItem>
                                                            <SelectItem value="monthly">Monthly</SelectItem>
                                                            <SelectItem value="quarterly">Quarterly</SelectItem>
                                                            <SelectItem value="yearly">Yearly</SelectItem>
                                                        </SelectContent>
                                                    </Select>
                                                )}
                                            </div>
                                        )}
                                        <Button type="submit" className="w-full" disabled={submitting}>
                                            {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : null}
                                            {formData.event_type === 'meeting' ? 'Schedule Meeting' : 'Add Event'}
                                        </Button>
                                    </form>
                                </DialogContent>
                            </Dialog>
                        </>
                    )}
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
                {/* Calendar */}
                <div className="lg:col-span-3">
                    <Card className="card-dashboard">
                        <CardHeader className="pb-2">
                            <div className="flex items-center justify-between">
                                <Button variant="ghost" size="icon" onClick={() => navigateMonth(-1)}
                                        aria-label="Previous month">
                                    <ChevronLeft className="h-5 w-5"/>
                                </Button>
                                <CardTitle className="text-xl">{monthName}</CardTitle>
                                <Button variant="ghost" size="icon" onClick={() => navigateMonth(1)}
                                        aria-label="Next month">
                                    <ChevronRight className="h-5 w-5"/>
                                </Button>
                            </div>
                        </CardHeader>
                        <CardContent>
                            {/* Weekday headers */}
                            <div className="grid grid-cols-7 gap-1 mb-2">
                                {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map(day => (
                                    <div key={day}
                                         className="text-center text-sm font-medium text-muted-foreground py-2">{day}</div>
                                ))}
                            </div>

                            {/* Calendar grid */}
                            <div className="grid grid-cols-7 gap-1">
                                {days.map((day, i) => {
                                    const isToday = day.date === today;
                                    const hasEvents = day.events.length > 0;
                                    return (
                                        <div
                                            key={i}
                                            className={`min-h-[100px] p-1.5 border rounded-lg transition-all cursor-pointer ${
                                                day.day === null ? 'bg-muted/20 cursor-default' :
                                                    isToday ? 'bg-primary/10 border-primary ring-1 ring-primary/30' :
                                                        hasEvents ? 'bg-card hover:bg-muted/60 hover:shadow-sm' :
                                                            'bg-card hover:bg-muted/40'
                                            }`}
                                            onClick={() => day.day && handleDayClick(day)}
                                            role={day.day ? 'button' : undefined}
                                            tabIndex={day.day ? 0 : undefined}
                                            onKeyDown={(e) => e.key === 'Enter' && day.day && handleDayClick(day)}
                                        >
                                            {day.day && (
                                                <>
                          <span className={`text-sm font-medium flex items-center justify-center w-6 h-6 rounded-full ${
                              isToday ? 'bg-primary text-primary-foreground' : 'text-foreground'
                          }`}>
                            {day.day}
                          </span>
                                                    {/* Colored event dots */}
                                                    <div className="mt-1.5 flex flex-wrap gap-0.5">
                                                        {day.events.slice(0, 4).map(event => {
                                                            const config = eventTypeConfig[ event.event_type ] || eventTypeConfig.other;
                                                            return (
                                                                <span
                                                                    key={event.id}
                                                                    className={`w-2 h-2 rounded-full flex-shrink-0 ${config.dot}`}
                                                                    title={event.title}
                                                                />
                                                            );
                                                        })}
                                                    </div>
                                                    {/* Show first event title on larger cells */}
                                                    {day.events.length > 0 && (
                                                        <div className="mt-1 space-y-0.5 hidden sm:block">
                                                            {day.events.slice(0, 1).map(event => {
                                                                const config = eventTypeConfig[ event.event_type ] || eventTypeConfig.other;
                                                                return (
                                                                    <div
                                                                        key={event.id}
                                                                        className={`text-[10px] px-1 py-0.5 rounded truncate ${config.color}`}
                                                                    >
                                                                        {event.title}
                                                                    </div>
                                                                );
                                                            })}
                                                            {day.events.length > 1 && (
                                                                <div className="text-[10px] text-muted-foreground pl-1">
                                                                    +{day.events.length - 1} more
                                                                </div>
                                                            )}
                                                        </div>
                                                    )}
                                                </>
                                            )}
                                        </div>
                                    );
                                })}
                            </div>
                        </CardContent>
                    </Card>
                </div>

                {/* Sidebar */}
                <div className="space-y-4">
                    {/* Event Type Legend */}
                    <Card className="card-dashboard">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm">Event Types</CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-2">
                            {Object.entries(eventTypeConfig).map(([key, config]) => (
                                <div key={key} className="flex items-center gap-2">
                                    <div className={`w-3 h-3 rounded-full ${config.dot}`}/>
                                    <span className="text-sm">{config.label}</span>
                                </div>
                            ))}
                        </CardContent>
                    </Card>

                    {/* Upcoming Events */}
                    <Card className="card-dashboard">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm flex items-center gap-2">
                                <Bell className="h-4 w-4"/>
                                Upcoming
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            {upcomingEvents.length === 0 ? (
                                <p className="text-sm text-muted-foreground">No upcoming events</p>
                            ) : (
                                <div className="space-y-3">
                                    {upcomingEvents.map(event => {
                                        const config = eventTypeConfig[ event.event_type ] || eventTypeConfig.other;
                                        const Icon = config.icon;
                                        return (
                                            <div key={event.id} className="flex items-start gap-2">
                                                <div className={`p-1.5 rounded ${config.color.split(' ')[ 0 ]}`}>
                                                    <Icon className={`h-3 w-3 ${config.color.split(' ')[ 1 ]}`}/>
                                                </div>
                                                <div className="flex-1 min-w-0">
                                                    <p className="text-sm font-medium truncate">{event.title}</p>
                                                    <p className="text-xs text-muted-foreground">{formatDateTime(event.start_date)}</p>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </CardContent>
                    </Card>

                    {/* Quick Actions */}
                    {canManage && (
                        <Card className="card-dashboard">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-sm">Quick Actions</CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-2">
                                <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => {
                                    setFormData(prev => ( {
                                        ...prev,
                                        event_type: 'agm',
                                        title: 'Annual General Meeting'
                                    } ));
                                    setDialogOpen(true);
                                }}>
                                    <Users className="mr-2 h-4 w-4"/>
                                    Schedule AGM
                                </Button>
                                <Button variant="outline" size="sm" className="w-full justify-start" onClick={() => {
                                    setFormData(prev => ( {
                                        ...prev,
                                        event_type: 'meeting',
                                        title: 'Executive Committee Meeting'
                                    } ));
                                    setDialogOpen(true);
                                }}>
                                    <Building2 className="mr-2 h-4 w-4"/>
                                    EC Meeting
                                </Button>
                            </CardContent>
                        </Card>
                    )}
                </div>
            </div>

            {/* Day Popup Dialog */}
            <Dialog open={dayPopupOpen} onOpenChange={setDayPopupOpen}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle className="text-lg font-bold">{selectedDayLabel}</DialogTitle>
                        <DialogDescription className="sr-only">Events on this day</DialogDescription>
                    </DialogHeader>
                    <div className="space-y-3 max-h-80 overflow-y-auto py-1">
                        {selectedDay?.events.length === 0 ? (
                            <p className="text-sm text-muted-foreground py-4 text-center">No events on this day.</p>
                        ) : (
                            selectedDay?.events.map(event => {
                                const config = eventTypeConfig[ event.event_type ] || eventTypeConfig.other;
                                const Icon = config.icon;
                                const destination = getEventDestination(event);
                                return (
                                    <div key={event.id}
                                         className={`flex gap-3 p-3 rounded-lg ${config.color.split(' ')[ 0 ]}/30 border border-${config.color.split(' ')[ 0 ].replace('bg-', '')}/20 ${destination ? 'cursor-pointer hover:bg-muted/50 transition-colors' : ''}`}
                                         onClick={destination ? () => openEventDestination(event) : undefined}
                                         onKeyDown={destination ? (e) => e.key === 'Enter' && openEventDestination(event) : undefined}
                                         role={destination ? 'button' : undefined}
                                         tabIndex={destination ? 0 : undefined}>
                                        <div className={`p-2 rounded-lg ${config.color.split(' ')[ 0 ]} flex-shrink-0`}>
                                            <Icon className={`h-4 w-4 ${config.color.split(' ')[ 1 ]}`}/>
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <p className="font-semibold text-sm">{event.title}</p>
                                            {event.description && (
                                                <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{event.description}</p>
                                            )}
                                            {event.location && (
                                                <p className="text-xs text-muted-foreground mt-0.5">📍 {event.location}</p>
                                            )}
                                            <div className="flex items-center gap-2 mt-1">
                                                <Badge
                                                    className={`text-[10px] py-0 ${config.color}`}>{config.label}</Badge>
                                                <span className="text-[10px] text-muted-foreground">
                          {formatDateTime(event.start_date)}
                        </span>
                                            </div>
                                            {destination && (
                                                <div className="mt-2">
                                                    <Button
                                                        type="button"
                                                        variant="outline"
                                                        size="sm"
                                                        className="h-7 px-2 text-xs"
                                                        onClick={(e) => {
                                                            e.stopPropagation();
                                                            openEventDestination(event);
                                                        }}
                                                    >
                                                        {destination.label}
                                                    </Button>
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                );
                            })
                        )}
                    </div>
                    {canManage && (
                        <div className="pt-2 border-t">
                            <Button
                                variant="outline"
                                size="sm"
                                className="w-full"
                                onClick={() => openAddEventForDate(selectedDay?.date)}
                            >
                                <Plus className="mr-2 h-4 w-4"/>
                                Add Event on This Day
                            </Button>
                        </div>
                    )}
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default EventsCalendarPage;
