// @featuretrace:meetings — Executive Committee meeting notes editor and Word export workflow.
// Layer: frontend
// Data flow: MeetingNotesPage.jsx → /meetings, /meetings/{id}/notes, /meetings/{id}/minutes-document → db.meetings (building-scoped).
// Related: backend/server.py meeting notes routes
//          frontend/src/pages/dashboard/MeetingsPage.jsx
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import { Download, FileText, Save, Users } from 'lucide-react';
import { toast } from 'sonner';
import { formatDateTime } from '../../lib/utils';

const STATUS_LABELS = {
    scheduled: {label: 'Scheduled', className: 'bg-blue-100 text-blue-800'},
    completed: {label: 'Completed', className: 'bg-green-100 text-green-800'},
    archived: {label: 'Archived', className: 'bg-slate-100 text-slate-800'},
};
/**
 * @generated FunctionHeader
 * Function: agendaToLines
 * Path: frontend/src/pages/dashboard/MeetingNotesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const agendaToLines = (agenda = []) => agenda.join('\n');
/**
 * @generated FunctionHeader
 * Function: attendeesToLines
 * Path: frontend/src/pages/dashboard/MeetingNotesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const attendeesToLines = (attendees = []) => attendees.join('\n');
/**
 * @generated FunctionHeader
 * Function: MeetingNotesPage
 * Path: frontend/src/pages/dashboard/MeetingNotesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function MeetingNotesPage() {
    const {api} = useAuth();
    const [meetings, setMeetings] = useState([]);
    const [loading, setLoading] = useState(true);
    const [selectedMeetingId, setSelectedMeetingId] = useState('');
    const [agendaText, setAgendaText] = useState('');
    const [attendeesText, setAttendeesText] = useState('');
    const [minutesText, setMinutesText] = useState('');
    const [saving, setSaving] = useState(false);
    const [downloading, setDownloading] = useState(false);
    const [requestedMeetingId, setRequestedMeetingId] = useState('');

    useEffect(() => {
        if (typeof window === 'undefined') return;
        const meetingId = new URLSearchParams(window.location.search).get('meetingId') || '';
        setRequestedMeetingId(meetingId);
    }, []);

    const fetchMeetings = useCallback(async () => {
        try {
            setLoading(true);
            const response = await api.get('/meetings?include_archived=true');
            const nextMeetings = Array.isArray(response.data) ? response.data : [];
            setMeetings(nextMeetings);
            if (requestedMeetingId && nextMeetings.some(meeting => meeting.id === requestedMeetingId)) {
                setSelectedMeetingId(requestedMeetingId);
            } else if (!selectedMeetingId && nextMeetings.length > 0) {
                setSelectedMeetingId(nextMeetings[ 0 ].id);
            }
        } catch (error) {
            console.error('Failed to load meetings for notes:', error);
            toast.error('Failed to load meetings');
        } finally {
            setLoading(false);
        }
    }, [api, requestedMeetingId, selectedMeetingId]);

    useEffect(() => {
        fetchMeetings();
    }, [fetchMeetings]);

    const selectedMeeting = useMemo(
        () => meetings.find(meeting => meeting.id === selectedMeetingId) || null,
        [meetings, selectedMeetingId]
    );

    useEffect(() => {
        if (!selectedMeeting) {
            setAgendaText('');
            setAttendeesText('');
            setMinutesText('');
            return;
        }
        setAgendaText(agendaToLines(selectedMeeting.agenda || []));
        setAttendeesText(attendeesToLines(selectedMeeting.attendees || []));
        setMinutesText(selectedMeeting.minutes || '');
    }, [selectedMeeting]);

    useEffect(() => {
        if (!selectedMeetingId) return;
        if (requestedMeetingId === selectedMeetingId) return;
        if (typeof window === 'undefined') return;
        const nextUrl = new URL(window.location.href);
        nextUrl.searchParams.set('meetingId', selectedMeetingId);
        window.history.replaceState({}, '', nextUrl.toString());
    }, [requestedMeetingId, selectedMeetingId]);

    const agendaList = useMemo(
        () => agendaText
            .split('\n')
            .map(item => item.trim())
            .filter(Boolean),
        [agendaText]
    );

    const attendeeList = useMemo(
        () => attendeesText
            .split('\n')
            .map(item => item.trim())
            .filter(Boolean),
        [attendeesText]
    );
    /**
     * @generated FunctionHeader
     * Function: handleSaveNotes
     * Path: frontend/src/pages/dashboard/MeetingNotesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSaveNotes = async () => {
        if (!selectedMeeting) return;
        if (!minutesText.trim()) {
            toast.error('Meeting notes are required before saving');
            return;
        }

        try {
            setSaving(true);
            const response = await api.put(`/meetings/${selectedMeeting.id}/notes`, {
                agenda: agendaList,
                attendees: attendeeList,
                minutes: minutesText.trim(),
            });
            const updatedMeeting = response.data;
            setMeetings(prev => prev.map(meeting => meeting.id === updatedMeeting.id ? updatedMeeting : meeting));
            toast.success('Meeting notes saved');
        } catch (error) {
            console.error('Failed to save meeting notes:', error);
            toast.error(error.response?.data?.detail || 'Failed to save meeting notes');
        } finally {
            setSaving(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDownloadMinutes
     * Path: frontend/src/pages/dashboard/MeetingNotesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownloadMinutes = async () => {
        if (!selectedMeeting) return;

        try {
            setDownloading(true);
            const response = await api.get(`/meetings/${selectedMeeting.id}/minutes-document`, {
                responseType: 'blob',
            });
            const blob = new Blob([response.data], {type: 'application/msword'});
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            const titleSlug = ( selectedMeeting.title || 'meeting_minutes' )
                .replace(/[^A-Za-z0-9_-]+/g, '_')
                .replace(/^_+|_+$/g, '');
            link.download = `${titleSlug || 'meeting_minutes'}.doc`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);
        } catch (error) {
            console.error('Failed to download meeting minutes document:', error);
            toast.error(error.response?.data?.detail || 'Failed to download minutes document');
        } finally {
            setDownloading(false);
        }
    };

    return (
        <div className="space-y-6">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                <div>
                    <h1 className="text-2xl font-bold">Executive Committee Meeting Notes</h1>
                    <p className="text-muted-foreground">
                        Capture attendees and discussion points for committee meetings, then export a branded Minutes of
                        the Meeting document for the Strata Manager.
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" onClick={fetchMeetings}>
                        Refresh
                    </Button>
                    <Button onClick={handleDownloadMinutes}
                            disabled={!selectedMeeting || !minutesText.trim() || downloading}>
                        <Download className="mr-2 h-4 w-4"/>
                        {downloading ? 'Preparing...' : 'Download Minutes'}
                    </Button>
                </div>
            </div>

            {loading ? (
                <div className="py-12 text-center">Loading...</div>
            ) : meetings.length === 0 ? (
                <Card>
                    <CardContent className="py-16 text-center text-muted-foreground">
                        <FileText className="mx-auto mb-4 h-12 w-12 opacity-50"/>
                        <p>No meetings are available yet. Schedule a meeting from the Committee Meetings page first.</p>
                    </CardContent>
                </Card>
            ) : (
                <div className="grid gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
                    <Card>
                        <CardHeader>
                            <CardTitle className="text-base">Meetings</CardTitle>
                            <CardDescription>Select a meeting to capture notes and attendees.</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            {meetings.map(meeting => (
                                <button
                                    key={meeting.id}
                                    type="button"
                                    onClick={() => setSelectedMeetingId(meeting.id)}
                                    className={`w-full rounded-lg border p-4 text-left transition-colors ${
                                        selectedMeetingId === meeting.id
                                            ? 'border-primary bg-primary/5'
                                            : 'border-border hover:border-primary/50'
                                    }`}
                                >
                                    <div className="flex items-start justify-between gap-2">
                                        <div>
                                            <div className="font-medium">{meeting.title}</div>
                                            <div className="mt-1 text-xs text-muted-foreground">
                                                {formatDateTime(meeting.meeting_date)}
                                            </div>
                                        </div>
                                        <Badge
                                            className={STATUS_LABELS[ meeting.status ]?.className || 'bg-slate-100 text-slate-800'}>
                                            {STATUS_LABELS[ meeting.status ]?.label || meeting.status}
                                        </Badge>
                                    </div>
                                    <div className="mt-2 text-xs text-muted-foreground">{meeting.location}</div>
                                </button>
                            ))}
                        </CardContent>
                    </Card>

                    <Card>
                        <CardHeader>
                            <CardTitle>{selectedMeeting?.title || 'Meeting notes'}</CardTitle>
                            <CardDescription>
                                {selectedMeeting ? `${formatDateTime(selectedMeeting.meeting_date)} · ${selectedMeeting.location}` : 'Select a meeting'}
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-5">
                            <div>
                                <Label htmlFor="agenda">Agenda</Label>
                                <Textarea
                                    id="agenda"
                                    value={agendaText}
                                    onChange={e => setAgendaText(e.target.value)}
                                    className="mt-2"
                                    rows={6}
                                    placeholder="One agenda item per line"
                                />
                                <p className="mt-2 text-xs text-muted-foreground">
                                    Keep one agenda item per line so the exported minutes document stays readable.
                                </p>
                            </div>

                            <div>
                                <Label htmlFor="attendees">
                                    <Users className="mr-2 inline h-4 w-4"/>
                                    Attendees
                                </Label>
                                <Textarea
                                    id="attendees"
                                    value={attendeesText}
                                    onChange={e => setAttendeesText(e.target.value)}
                                    className="mt-2"
                                    rows={6}
                                    placeholder="One attendee per line"
                                />
                                <p className="mt-2 text-xs text-muted-foreground">
                                    Enter one attendee per line. These names will appear in the exported Minutes of the
                                    Meeting document.
                                </p>
                            </div>

                            <div>
                                <Label htmlFor="meeting-notes">Meeting notes</Label>
                                <Textarea
                                    id="meeting-notes"
                                    value={minutesText}
                                    onChange={e => setMinutesText(e.target.value)}
                                    className="mt-2 min-h-[320px]"
                                    placeholder="Write the discussion points, decisions, actions, and next steps..."
                                />
                            </div>

                            <div className="grid gap-4 md:grid-cols-2">
                                <div>
                                    <Label>Recorded agenda</Label>
                                    <div className="mt-2 rounded-lg border bg-muted/20 p-3 text-sm">
                                        {agendaList.length === 0 ? (
                                            <span className="text-muted-foreground">No agenda recorded yet.</span>
                                        ) : (
                                            <ul className="list-disc space-y-1 pl-5">
                                                {agendaList.map(item => (
                                                    <li key={item}>{item}</li>
                                                ))}
                                            </ul>
                                        )}
                                    </div>
                                </div>
                                <div>
                                    <Label>Recorded attendees</Label>
                                    <div className="mt-2 rounded-lg border bg-muted/20 p-3 text-sm">
                                        {attendeeList.length === 0 ? (
                                            <span className="text-muted-foreground">No attendees listed yet.</span>
                                        ) : (
                                            <ul className="list-disc space-y-1 pl-5">
                                                {attendeeList.map(attendee => (
                                                    <li key={attendee}>{attendee}</li>
                                                ))}
                                            </ul>
                                        )}
                                    </div>
                                    <Label className="mt-4 block">Document output</Label>
                                    <div
                                        className="mt-2 rounded-lg border bg-muted/20 p-3 text-sm text-muted-foreground">
                                        The exported Word document includes the building logo when configured, meeting
                                        title, date, location, attendees, agenda, and the saved notes.
                                    </div>
                                </div>
                            </div>

                            <div className="flex flex-wrap gap-2">
                                <Button onClick={handleSaveNotes} disabled={!selectedMeeting || saving}>
                                    <Save className="mr-2 h-4 w-4"/>
                                    {saving ? 'Saving...' : 'Save Notes'}
                                </Button>
                                <Button variant="outline" onClick={handleDownloadMinutes}
                                        disabled={!selectedMeeting || !minutesText.trim() || downloading}>
                                    <Download className="mr-2 h-4 w-4"/>
                                    Download Minutes
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}
        </div>
    );
}
