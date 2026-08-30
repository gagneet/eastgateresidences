// @featuretrace:meetings — Committee meetings list, editing, status updates, and minutes linkage.
// Layer: frontend
// Data flow: MeetingsPage.tsx → /meetings, /meetings/{id} → db.meetings (building-scoped).
// Related: backend/server.py meeting routes
//          frontend/src/pages/dashboard/MeetingNotesPage.jsx
"use client";

import React, {useCallback, useEffect, useState} from 'react';
import {useAuth} from '../../contexts/AuthContext';
import {Card, CardContent} from '../../components/ui/card';
import {Button} from '../../components/ui/button';
import {Input} from '../../components/ui/input';
import {Badge} from '../../components/ui/badge';
import {Label} from '../../components/ui/label';
import {Textarea} from '../../components/ui/textarea';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '../../components/ui/dialog';
import {Archive, ArchiveRestore, Calendar, FilePenLine, FileText, Loader2, MapPin, Plus} from 'lucide-react';
import {formatDateTime, statusConfig} from '../../lib/utils';
import {toast} from 'sonner';
import { getApiErrorDetail } from '@/lib/api-error';

const emptyForm = {
    title: '',
    description: '',
    meeting_date: '',
    location: '',
    agenda: '',
    attendees: '',
};
/**
 * @generated FunctionHeader
 * Function: listToText
 * Path: frontend/src/pages/dashboard/MeetingsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const listToText = (items?: string[]) => (Array.isArray(items) ? items.join('\n') : '');
/**
 * @generated FunctionHeader
 * Function: textToList
 * Path: frontend/src/pages/dashboard/MeetingsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const textToList = (value: string) => value.split('\n').map(item => item.trim()).filter(Boolean);
/**
 * @generated FunctionHeader
 * Function: MeetingsPage
 * Path: frontend/src/pages/dashboard/MeetingsPage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const MeetingsPage: React.FC = () => {
    const {api, hasPermission} = useAuth();
    const [meetings, setMeetings] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [showArchived, setShowArchived] = useState(false);
    const [editingMeetingId, setEditingMeetingId] = useState<string | null>(null);
    const [formData, setFormData] = useState(emptyForm);
    const [highlightedMeetingId, setHighlightedMeetingId] = useState<string>('');

    useEffect(() => {
        if (typeof window === 'undefined') return;
        const meetingId = new URLSearchParams(window.location.search).get('meetingId') || '';
        setHighlightedMeetingId(meetingId);
    }, []);

    const fetchMeetings = useCallback(async () => {
        try {
            setLoading(true);
            const url = showArchived ? '/meetings?include_archived=true' : '/meetings';
            const response = await api.get(url);
            setMeetings(response.data || []);
        } catch (error) {
            console.error('Failed to fetch meetings:', error);
        } finally {
            setLoading(false);
        }
    }, [api, showArchived]);

    useEffect(() => {
        fetchMeetings();
    }, [fetchMeetings]);
    /**
     * @generated FunctionHeader
     * Function: openCreateDialog
     * Path: frontend/src/pages/dashboard/MeetingsPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openCreateDialog = () => {
        setEditingMeetingId(null);
        setFormData(emptyForm);
        setDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: openEditDialog
     * Path: frontend/src/pages/dashboard/MeetingsPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openEditDialog = (meeting: any) => {
        setEditingMeetingId(meeting.id);
        setFormData({
            title: meeting.title || '',
            description: meeting.description || '',
            meeting_date: meeting.meeting_date ? meeting.meeting_date.slice(0, 16) : '',
            location: meeting.location || '',
            agenda: listToText(meeting.agenda),
            attendees: listToText(meeting.attendees),
        });
        setDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: openMeetingNotes
     * Path: frontend/src/pages/dashboard/MeetingsPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openMeetingNotes = (meetingId: string) => {
        if (typeof window === 'undefined') return;
        window.location.assign(`/governance/meetings/notes?meetingId=${encodeURIComponent(meetingId)}`);
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/MeetingsPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);

        try {
            const data = {
                ...formData,
                agenda: textToList(formData.agenda),
                attendees: textToList(formData.attendees),
            };

            if (editingMeetingId) {
                await api.put(`/meetings/${editingMeetingId}`, data);
                toast.success('Meeting updated successfully');
            } else {
                await api.post('/meetings', data);
                toast.success('Meeting scheduled successfully');
            }
            setDialogOpen(false);
            setEditingMeetingId(null);
            setFormData(emptyForm);
            fetchMeetings();
        } catch (error) {
            // Surface the backend's own reason. The agenda and attendee lists are capped
            // (50 / 200 items) and an over-long paste returns HTTP 422 naming the field —
            // a generic "Failed to update meeting" left no way to discover that.
            const { message } = getApiErrorDetail(error);
            const fallback = editingMeetingId ? 'Failed to update meeting' : 'Failed to schedule meeting';
            toast.error(message ? `${fallback}: ${message}` : fallback);
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: updateMeetingStatus
     * Path: frontend/src/pages/dashboard/MeetingsPage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateMeetingStatus = async (meetingId: string, status: string, title = '') => {
        try {
            await api.put(`/meetings/${meetingId}?status=${status}`);
            toast.success(status === 'archived' ? 'Meeting archived' : 'Meeting status updated');

            if (status === 'completed' && title.toUpperCase().includes('AGM')) {
                try {
                    await api.post('/agm/trigger-alert');
                    toast.info('AGM completed alert sent to Super Admin');
                } catch (alertError) {
                    console.error('Failed to send AGM alert:', alertError);
                }
            }

            fetchMeetings();
        } catch (error) {
            // Same swallow as handleSubmit had — surface the backend's reason.
            const { message } = getApiErrorDetail(error);
            toast.error(message ? `Failed to update meeting: ${message}` : 'Failed to update meeting');
        }
    };

    return (
        <div className="space-y-6" data-testid="meetings-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Meetings</h1>
                    <p className="text-muted-foreground">EC meetings and community events</p>
                    <p className="text-xs text-muted-foreground mt-1">Use Meeting Notes after scheduling to refine
                        agenda, attendees, and minutes for distribution.</p>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                    {hasPermission('can_manage_meetings') && (
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setShowArchived(v => !v)}
                        >
                            {showArchived ? <ArchiveRestore className="mr-2 h-4 w-4"/> :
                                <Archive className="mr-2 h-4 w-4"/>}
                            {showArchived ? 'Hide Archived' : 'Show Archived'}
                        </Button>
                    )}
                    {hasPermission('can_manage_meetings') && (
                        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                            <DialogTrigger asChild>
                                <Button data-testid="schedule-meeting-btn" onClick={openCreateDialog}>
                                    <Plus className="mr-2 h-4 w-4"/>
                                    Schedule Meeting
                                </Button>
                            </DialogTrigger>
                            <DialogContent>
                                <DialogHeader>
                                    <DialogTitle>{editingMeetingId ? 'Edit Meeting' : 'Schedule Meeting'}</DialogTitle>
                                    <DialogDescription>
                                        {editingMeetingId
                                            ? 'Update the meeting details, agenda, and attendees.'
                                            : 'Create a new EC or community meeting'}
                                    </DialogDescription>
                                </DialogHeader>
                                <form onSubmit={handleSubmit} className="space-y-4" data-testid="meeting-form">
                                    <div className="space-y-2">
                                        <Label htmlFor="title">Title</Label>
                                        <Input
                                            id="title"
                                            value={formData.title}
                                            onChange={(e) => setFormData(prev => ({...prev, title: e.target.value}))}
                                            placeholder="e.g., Monthly EC Meeting"
                                            required
                                            data-testid="meeting-title-input"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="meeting_date">Date & Time</Label>
                                        <Input
                                            id="meeting_date"
                                            type="datetime-local"
                                            value={formData.meeting_date}
                                            onChange={(e) => setFormData(prev => ({
                                                ...prev,
                                                meeting_date: e.target.value
                                            }))}
                                            required
                                            data-testid="meeting-date-input"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="location">Location</Label>
                                        <Input
                                            id="location"
                                            value={formData.location}
                                            onChange={(e) => setFormData(prev => ({...prev, location: e.target.value}))}
                                            placeholder="e.g., Building Common Room"
                                            required
                                            data-testid="meeting-location-input"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="description">Description</Label>
                                        <Textarea
                                            id="description"
                                            value={formData.description}
                                            onChange={(e) => setFormData(prev => ({
                                                ...prev,
                                                description: e.target.value
                                            }))}
                                            placeholder="Meeting details..."
                                            data-testid="meeting-description-input"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="agenda">Agenda (one item per line)</Label>
                                        <Textarea
                                            id="agenda"
                                            value={formData.agenda}
                                            onChange={(e) => setFormData(prev => ({...prev, agenda: e.target.value}))}
                                            placeholder="Item 1&#10;Item 2&#10;Item 3"
                                            rows={4}
                                            data-testid="meeting-agenda-input"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label htmlFor="attendees">Attendees (one name per line)</Label>
                                        <Textarea
                                            id="attendees"
                                            value={formData.attendees}
                                            onChange={(e) => setFormData(prev => ({
                                                ...prev,
                                                attendees: e.target.value
                                            }))}
                                            placeholder="Chairperson&#10;EC Member&#10;Strata Manager"
                                            rows={4}
                                        />
                                    </div>
                                    <Button type="submit" className="w-full" disabled={submitting}>
                                        {submitting ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                                                {editingMeetingId ? 'Saving...' : 'Scheduling...'}
                                            </>
                                        ) : (
                                            editingMeetingId ? 'Save Meeting Changes' : 'Schedule Meeting'
                                        )}
                                    </Button>
                                </form>
                            </DialogContent>
                        </Dialog>
                    )}
                </div>
            </div>

            {loading ? (
                <div className="space-y-4">
                    {[1, 2, 3].map((i) => (
                        <Card key={i} className="card-dashboard">
                            <CardContent className="p-6">
                                <div className="skeleton h-6 w-1/3 mb-4"/>
                                <div className="skeleton h-4 w-full mb-2"/>
                                <div className="skeleton h-4 w-2/3"/>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            ) : meetings.length === 0 ? (
                <Card className="card-dashboard">
                    <CardContent className="py-16 text-center">
                        <Calendar className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                        <h3 className="text-lg font-medium mb-2">No Meetings Scheduled</h3>
                        <p className="text-muted-foreground">
                            There are no upcoming meetings at this time.
                        </p>
                    </CardContent>
                </Card>
            ) : (
                <div className="space-y-4">
                    {meetings.map((meeting) => {
                        const status = statusConfig[meeting.status as keyof typeof statusConfig] || statusConfig.scheduled;
                        return (
                            <Card
                                key={meeting.id}
                                className={`card-dashboard ${highlightedMeetingId === meeting.id ? 'ring-2 ring-primary/40 border-primary' : ''}`}
                                data-testid={`meeting-${meeting.id}`}
                            >
                                <CardContent className="p-6">
                                    <div className="flex items-start justify-between gap-4 mb-4">
                                        <div>
                                            <div className="flex items-center gap-2 mb-2">
                                                <h3 className="text-lg font-semibold">{meeting.title}</h3>
                                                <Badge className={status.class}>{status.label}</Badge>
                                            </div>
                                            <div className="flex flex-wrap gap-4 text-sm text-muted-foreground">
                        <span className="flex items-center gap-1">
                          <Calendar className="h-4 w-4"/>
                            {formatDateTime(meeting.meeting_date)}
                        </span>
                                                <span className="flex items-center gap-1">
                          <MapPin className="h-4 w-4"/>
                                                    {meeting.location}
                        </span>
                                            </div>
                                        </div>
                                        <div className="flex gap-2">
                                            {hasPermission('can_manage_meetings') && (
                                                <>
                                                    <Button
                                                        variant="outline"
                                                        size="sm"
                                                        onClick={() => openEditDialog(meeting)}
                                                    >
                                                        <FilePenLine className="mr-2 h-4 w-4"/>
                                                        Edit
                                                    </Button>
                                                    <Button
                                                        variant="outline"
                                                        size="sm"
                                                        onClick={() => openMeetingNotes(meeting.id)}
                                                    >
                                                        <FileText className="mr-2 h-4 w-4"/>
                                                        Meeting Notes
                                                    </Button>
                                                </>
                                            )}
                                            {hasPermission('can_manage_meetings') && meeting.status === 'scheduled' && (
                                                <Button
                                                    variant="outline"
                                                    size="sm"
                                                    onClick={() => updateMeetingStatus(meeting.id, 'completed', meeting.title)}
                                                >
                                                    Mark Complete
                                                </Button>
                                            )}
                                            {hasPermission('can_manage_meetings') && meeting.status === 'completed' && (
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="text-muted-foreground hover:text-foreground"
                                                    onClick={() => updateMeetingStatus(meeting.id, 'archived', meeting.title)}
                                                    title="Archive this meeting"
                                                >
                                                    <Archive className="h-4 w-4"/>
                                                </Button>
                                            )}
                                            {hasPermission('can_manage_meetings') && meeting.status === 'archived' && (
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="text-muted-foreground hover:text-foreground"
                                                    onClick={() => updateMeetingStatus(meeting.id, 'completed', meeting.title)}
                                                    title="Unarchive meeting"
                                                >
                                                    <ArchiveRestore className="h-4 w-4"/>
                                                </Button>
                                            )}
                                        </div>
                                    </div>

                                    {meeting.description && (
                                        <p className="text-muted-foreground mb-4">{meeting.description}</p>
                                    )}

                                    {meeting.agenda?.length > 0 && (
                                        <div className="mb-4">
                                            <h4 className="font-medium mb-2">Agenda</h4>
                                            <ul className="list-disc list-inside space-y-1 text-sm text-muted-foreground">
                                                {meeting.agenda.map((item: string, index: number) => (
                                                    <li key={index}>{item}</li>
                                                ))}
                                            </ul>
                                        </div>
                                    )}

                                    {meeting.minutes && (
                                        <div className="mt-4 p-4 bg-muted rounded-lg">
                                            <h4 className="font-medium mb-2 flex items-center gap-2">
                                                <FileText className="h-4 w-4"/>
                                                Meeting Minutes
                                            </h4>
                                            <p className="text-sm whitespace-pre-wrap">{meeting.minutes}</p>
                                        </div>
                                    )}
                                </CardContent>
                            </Card>
                        );
                    })}
                </div>
            )}
        </div>
    );
};

export default MeetingsPage;
