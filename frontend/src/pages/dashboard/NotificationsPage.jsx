import React, { useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
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
import { Bell, CheckCircle2, Clock, DollarSign, Loader2, Mail, MessageSquare, Send, XCircle } from 'lucide-react';
import { formatDateTime } from '../../lib/utils';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: NotificationsPage
 * Path: frontend/src/pages/dashboard/NotificationsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const NotificationsPage = () => {
    const {api, hasPermission} = useAuth();
    const [notifications, setNotifications] = useState([]);
    const [loading, setLoading] = useState(true);
    const [dialogOpen, setDialogOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [sendingReminder, setSendingReminder] = useState(false);
    const [reminderSettings, setReminderSettings] = useState({enabled: false, reminder_days: [14, 7]});
    const [reminderLog, setReminderLog] = useState([]);
    const [savingSettings, setSavingSettings] = useState(false);
    const [formData, setFormData] = useState({
        title: '',
        message: '',
        notification_type: 'general',
        channels: ['email'],
        recipients: ['all']
    });

    const canSend = hasPermission('can_send_notifications') || hasPermission('can_post_announcements');

    const fetchNotifications = React.useCallback(async () => {
        try {
            const response = await api.get('/notifications');
            setNotifications(response.data);
        } catch (error) {
            console.error('Failed to fetch notifications:', error);
        } finally {
            setLoading(false);
        }
    }, [api]);

    const fetchReminderSettings = React.useCallback(async () => {
        try {
            const [settingsRes, logRes] = await Promise.all([
                api.get('/levy-reminder-settings'),
                api.get('/levy-reminder-log')
            ]);
            setReminderSettings(settingsRes.data);
            setReminderLog(logRes.data || []);
        } catch (error) {
            console.error('Failed to fetch reminder settings:', error);
        }
    }, [api]);

    useEffect(() => {
        fetchNotifications();
        if (canSend) fetchReminderSettings();
    }, [fetchNotifications, fetchReminderSettings, canSend]);
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/NotificationsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            const response = await api.post('/notifications/send', formData);
            toast.success(`Sent to ${response.data.sent_count} recipients`);
            setDialogOpen(false);
            setFormData({
                title: '', message: '', notification_type: 'general',
                channels: ['email'], recipients: ['all']
            });
            fetchNotifications();
        } catch (error) {
            toast.error('Failed to send notification');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: sendLevyReminder
     * Path: frontend/src/pages/dashboard/NotificationsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const sendLevyReminder = async () => {
        setSendingReminder(true);
        try {
            const response = await api.post('/notifications/levy-reminder');
            toast.success(response.data.message);
            fetchNotifications();
        } catch (error) {
            toast.error('Failed to send levy reminder');
        } finally {
            setSendingReminder(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: saveReminderSettings
     * Path: frontend/src/pages/dashboard/NotificationsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const saveReminderSettings = async () => {
        setSavingSettings(true);
        try {
            await api.put('/levy-reminder-settings', reminderSettings);
            toast.success('Automated reminder settings saved');
        } catch (error) {
            toast.error('Failed to save settings');
        } finally {
            setSavingSettings(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: toggleReminderDay
     * Path: frontend/src/pages/dashboard/NotificationsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleReminderDay = (day) => {
        setReminderSettings(prev => ( {
            ...prev,
            reminder_days: prev.reminder_days.includes(day)
                ? prev.reminder_days.filter(d => d !== day)
                : [...prev.reminder_days, day].sort((a, b) => b - a)
        } ));
    };
    /**
     * @generated FunctionHeader
     * Function: toggleChannel
     * Path: frontend/src/pages/dashboard/NotificationsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleChannel = (channel) => {
        setFormData(prev => ( {
            ...prev,
            channels: prev.channels.includes(channel)
                ? prev.channels.filter(c => c !== channel)
                : [...prev.channels, channel]
        } ));
    };

    const stats = {
        total: notifications.length,
        sent: notifications.filter(n => n.status === 'sent').length,
        pending: notifications.filter(n => n.status === 'pending').length,
        failed: notifications.filter(n => n.status === 'failed').length
    };

    return (
        <div className="space-y-6" data-testid="notifications-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Notifications</h1>
                    <p className="text-muted-foreground">Send announcements and levy reminders to residents</p>
                </div>
                {canSend && (
                    <div className="flex gap-2">
                        <Button variant="outline" onClick={sendLevyReminder} disabled={sendingReminder}>
                            {sendingReminder ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> :
                                <DollarSign className="mr-2 h-4 w-4"/>}
                            Send Levy Reminder
                        </Button>
                        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                            <DialogTrigger asChild>
                                <Button><Send className="mr-2 h-4 w-4"/>New Notification</Button>
                            </DialogTrigger>
                            <DialogContent className="max-w-lg">
                                <DialogHeader>
                                    <DialogTitle>Send Notification</DialogTitle>
                                    <DialogDescription>Send a message to residents via email, SMS or
                                        WhatsApp</DialogDescription>
                                </DialogHeader>
                                <form onSubmit={handleSubmit} className="space-y-4">
                                    <div className="space-y-2">
                                        <Label>Subject/Title</Label>
                                        <Input value={formData.title} onChange={(e) => setFormData(prev => ( {
                                            ...prev,
                                            title: e.target.value
                                        } ))} placeholder="e.g., Important Notice" required/>
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Message</Label>
                                        <Textarea value={formData.message} onChange={(e) => setFormData(prev => ( {
                                            ...prev,
                                            message: e.target.value
                                        } ))} rows={4} required/>
                                    </div>
                                    <div className="grid grid-cols-2 gap-4">
                                        <div className="space-y-2">
                                            <Label>Type</Label>
                                            <Select value={formData.notification_type}
                                                    onValueChange={(v) => setFormData(prev => ( {
                                                        ...prev,
                                                        notification_type: v
                                                    } ))}>
                                                <SelectTrigger><SelectValue/></SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="general">General</SelectItem>
                                                    <SelectItem value="levy_reminder">Levy Reminder</SelectItem>
                                                    <SelectItem value="announcement">Announcement</SelectItem>
                                                    <SelectItem value="maintenance">Maintenance</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Recipients</Label>
                                            <Select value={formData.recipients[ 0 ]}
                                                    onValueChange={(v) => setFormData(prev => ( {
                                                        ...prev,
                                                        recipients: [v]
                                                    } ))}>
                                                <SelectTrigger><SelectValue/></SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="all">All Residents</SelectItem>
                                                    <SelectItem value="owners">Owners Only</SelectItem>
                                                    <SelectItem value="tenants">Tenants Only</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Channels</Label>
                                        <div className="flex gap-2">
                                            <Button type="button"
                                                    variant={formData.channels.includes('email') ? 'default' : 'outline'}
                                                    size="sm" onClick={() => toggleChannel('email')}>
                                                <Mail className="mr-2 h-4 w-4"/>Email
                                            </Button>
                                            <Button type="button"
                                                    variant={formData.channels.includes('sms') ? 'default' : 'outline'}
                                                    size="sm" onClick={() => toggleChannel('sms')}>
                                                <MessageSquare className="mr-2 h-4 w-4"/>SMS
                                            </Button>
                                            <Button type="button"
                                                    variant={formData.channels.includes('whatsapp') ? 'default' : 'outline'}
                                                    size="sm" onClick={() => toggleChannel('whatsapp')}>
                                                <MessageSquare className="mr-2 h-4 w-4"/>WhatsApp
                                            </Button>
                                        </div>
                                        <p className="text-xs text-muted-foreground">SMS and WhatsApp require Twilio
                                            configuration</p>
                                    </div>
                                    <Button type="submit" className="w-full"
                                            disabled={submitting || formData.channels.length === 0}>
                                        {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> :
                                            <Send className="mr-2 h-4 w-4"/>}
                                        Send Notification
                                    </Button>
                                </form>
                            </DialogContent>
                        </Dialog>
                    </div>
                )}
            </div>

            {/* Stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <Card className="card-dashboard">
                    <CardContent className="p-4 flex items-center gap-3">
                        <Bell className="h-8 w-8 text-blue-500"/>
                        <div>
                            <p className="text-2xl font-bold">{stats.total}</p>
                            <p className="text-xs text-muted-foreground">Total Sent</p>
                        </div>
                    </CardContent>
                </Card>
                <Card className="card-dashboard">
                    <CardContent className="p-4 flex items-center gap-3">
                        <CheckCircle2 className="h-8 w-8 text-green-500"/>
                        <div>
                            <p className="text-2xl font-bold">{stats.sent}</p>
                            <p className="text-xs text-muted-foreground">Delivered</p>
                        </div>
                    </CardContent>
                </Card>
                <Card className="card-dashboard">
                    <CardContent className="p-4 flex items-center gap-3">
                        <Clock className="h-8 w-8 text-orange-500"/>
                        <div>
                            <p className="text-2xl font-bold">{stats.pending}</p>
                            <p className="text-xs text-muted-foreground">Pending</p>
                        </div>
                    </CardContent>
                </Card>
                <Card className="card-dashboard">
                    <CardContent className="p-4 flex items-center gap-3">
                        <XCircle className="h-8 w-8 text-red-500"/>
                        <div>
                            <p className="text-2xl font-bold">{stats.failed}</p>
                            <p className="text-xs text-muted-foreground">Failed</p>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Automated Levy Reminder Settings */}
            {canSend && (
                <Card className="card-dashboard">
                    <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                            <Clock className="h-5 w-5"/>
                            Automated Levy Reminders
                        </CardTitle>
                        <CardDescription>
                            Configure automatic email reminders before quarterly levy due dates
                        </CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex items-center justify-between">
                            <div>
                                <p className="font-medium">Enable Automated Reminders</p>
                                <p className="text-sm text-muted-foreground">Sends email reminders to all owners before
                                    due dates</p>
                            </div>
                            <Button
                                variant={reminderSettings.enabled ? 'default' : 'outline'}
                                size="sm"
                                onClick={() => setReminderSettings(p => ( {...p, enabled: !p.enabled} ))}
                            >
                                {reminderSettings.enabled ? 'Enabled' : 'Disabled'}
                            </Button>
                        </div>

                        <div className="space-y-2">
                            <Label>Send Reminders (Days Before Due Date)</Label>
                            <div className="flex flex-wrap gap-2">
                                {[30, 21, 14, 7, 3, 1].map(day => (
                                    <Button
                                        key={day}
                                        type="button"
                                        variant={reminderSettings.reminder_days?.includes(day) ? 'default' : 'outline'}
                                        size="sm"
                                        onClick={() => toggleReminderDay(day)}
                                    >
                                        {day} {day === 1 ? 'day' : 'days'}
                                    </Button>
                                ))}
                            </div>
                            <p className="text-xs text-muted-foreground">
                                Selected: {reminderSettings.reminder_days?.sort((a, b) => b - a).join(', ')} days before
                                due date
                            </p>
                        </div>

                        <Button onClick={saveReminderSettings} disabled={savingSettings} className="w-full">
                            {savingSettings ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> :
                                <Bell className="mr-2 h-4 w-4"/>}
                            Save Settings
                        </Button>

                        {reminderLog.length > 0 && (
                            <div className="border-t pt-4 mt-4">
                                <p className="text-sm font-medium mb-2">Recent Auto-Reminders</p>
                                <div className="space-y-2">
                                    {reminderLog.slice(0, 5).map((log, i) => (
                                        <div key={i} className="text-xs flex justify-between p-2 bg-muted/50 rounded">
                                            <span>Due: {log.due_date} ({log.days_before} days before)</span>
                                            <span className="font-medium">{log.sent_count} sent</span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* Notification History */}
            <Card className="card-dashboard">
                <CardHeader>
                    <CardTitle>Notification History</CardTitle>
                    <CardDescription>Recent notifications sent to residents</CardDescription>
                </CardHeader>
                <CardContent>
                    {loading ? (
                        <div className="space-y-4">{[1, 2, 3].map(i => <div key={i}
                                                                            className="skeleton h-16 w-full"/>)}</div>
                    ) : notifications.length === 0 ? (
                        <div className="py-12 text-center">
                            <Bell className="h-12 w-12 text-muted-foreground/50 mx-auto mb-4"/>
                            <p className="text-muted-foreground">No notifications sent yet</p>
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {notifications.map(notif => (
                                <div key={notif.id} className="p-4 border rounded-lg">
                                    <div className="flex items-start justify-between gap-4">
                                        <div className="flex-1">
                                            <div className="flex items-center gap-2 mb-1">
                                                <h3 className="font-semibold">{notif.title}</h3>
                                                <Badge
                                                    variant={notif.status === 'sent' ? 'default' : notif.status === 'failed' ? 'destructive' : 'secondary'}>
                                                    {notif.status}
                                                </Badge>
                                                <Badge variant="outline">{notif.notification_type}</Badge>
                                            </div>
                                            <p className="text-sm text-muted-foreground mb-2 line-clamp-2">{notif.message}</p>
                                            <div className="flex items-center gap-4 text-xs text-muted-foreground">
                                                <span>Sent: {notif.sent_count}</span>
                                                {notif.failed_count > 0 &&
                                                    <span className="text-red-500">Failed: {notif.failed_count}</span>}
                                                <span>Channels: {notif.channels.join(', ')}</span>
                                                <span>{formatDateTime(notif.created_at)}</span>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </CardContent>
            </Card>
        </div>
    );
};

export default NotificationsPage;
