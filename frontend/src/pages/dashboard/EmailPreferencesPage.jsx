// @featuretrace:email-delivery — User-facing email subscription and HTML/plain-text format preferences.
// Layer: frontend
// Data flow: EmailPreferencesPage -> GET/PUT /notifications/preferences -> db.email_notification_preferences/email_preferences -> send_email_async() (building-scoped).
// Related: backend/routers/communication.py, backend/routers/notifications.py, backend/utils/email.py, docs/architecture/mindmap/email-delivery.md

import React, { useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Label } from '../../components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select';
import { Bell, Mail, Save } from 'lucide-react';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: EmailPreferencesPage
 * Path: frontend/src/pages/dashboard/EmailPreferencesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const EmailPreferencesPage = () => {
    const {api} = useAuth();
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [preferences, setPreferences] = useState({
        notices_enabled: true,
        announcements_enabled: true,
        maintenance_updates_enabled: true,
        discussion_replies_enabled: true,
        levy_reminders_enabled: true,
        tax_reminders_enabled: false,
        water_reminders_enabled: false,
        agm_reminders_enabled: false,
        digest_frequency: 'immediate',
        email_format: 'html'
    });

    useEffect(() => {
        fetchPreferences();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: fetchPreferences
     * Path: frontend/src/pages/dashboard/EmailPreferencesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const fetchPreferences = async () => {
        try {
            const response = await api.get('/notifications/preferences');
            setPreferences(response.data);
        } catch (error) {
            console.error('Failed to fetch preferences:', error);
            toast.error('Failed to load preferences');
        } finally {
            setLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleSave
     * Path: frontend/src/pages/dashboard/EmailPreferencesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSave = async () => {
        setSaving(true);
        try {
            await api.put('/notifications/preferences', preferences);
            toast.success('Preferences saved successfully');
        } catch (error) {
            console.error('Failed to save preferences:', error);
            toast.error('Failed to save preferences');
        } finally {
            setSaving(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: togglePreference
     * Path: frontend/src/pages/dashboard/EmailPreferencesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const togglePreference = (key) => {
        setPreferences({...preferences, [ key ]: !preferences[ key ]});
    };

    if (loading) {
        return (
            <div className="space-y-6">
                <div>
                    <h1 className="text-2xl font-bold">Email Preferences</h1>
                    <p className="text-muted-foreground">Loading...</p>
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-6" data-testid="email-preferences-page">
            <div>
                <h1 className="text-2xl font-bold flex items-center gap-2">
                    <Mail className="h-6 w-6"/>
                    Email Notification Preferences
                </h1>
                <p className="text-muted-foreground">
                    Choose which email notifications you'd like to receive
                </p>
            </div>

            <Card className="card-dashboard">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Bell className="h-5 w-5"/>
                        Notification Types
                    </CardTitle>
                    <CardDescription>
                        Select which types of notifications you want to receive via email
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    {/* Official Notices */}
                    <div className="flex items-start justify-between pb-4 border-b">
                        <div className="space-y-1 flex-1">
                            <Label htmlFor="notices" className="text-base font-medium">
                                Official Notices
                            </Label>
                            <p className="text-sm text-muted-foreground">
                                Receive emails for important official notices posted by management, including legal,
                                financial, and maintenance notices
                            </p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer ml-4">
                            <input
                                type="checkbox"
                                id="notices"
                                checked={preferences.notices_enabled}
                                onChange={() => togglePreference('notices_enabled')}
                                className="sr-only peer"
                            />
                            <div
                                className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-primary/20 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                        </label>
                    </div>

                    {/* Levy Reminders */}
                    <div className="flex items-start justify-between pb-4 border-b">
                        <div className="space-y-1 flex-1">
                            <Label htmlFor="levy_reminders" className="text-base font-medium">
                                Strata Levy Reminders
                            </Label>
                            <p className="text-sm text-muted-foreground">
                                Receive email reminders 14 days before your quarterly strata levy is due (with PDF
                                notice attached)
                            </p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer ml-4">
                            <input
                                type="checkbox"
                                id="levy_reminders"
                                checked={preferences.levy_reminders_enabled}
                                onChange={() => togglePreference('levy_reminders_enabled')}
                                className="sr-only peer"
                            />
                            <div
                                className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-primary/20 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                        </label>
                    </div>

                    {/* Council & Tax Reminders */}
                    <div className="flex items-start justify-between pb-4 border-b">
                        <div className="space-y-1 flex-1">
                            <Label htmlFor="tax_reminders" className="text-base font-medium">
                                Council Rates & Land Tax Reminders
                            </Label>
                            <p className="text-sm text-muted-foreground">
                                Receive reminders for upcoming ACT Revenue Office council rates and land tax payments
                            </p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer ml-4">
                            <input
                                type="checkbox"
                                id="tax_reminders"
                                checked={preferences.tax_reminders_enabled}
                                onChange={() => togglePreference('tax_reminders_enabled')}
                                className="sr-only peer"
                            />
                            <div
                                className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-primary/20 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                        </label>
                    </div>

                    {/* Water Reminders */}
                    <div className="flex items-start justify-between pb-4 border-b">
                        <div className="space-y-1 flex-1">
                            <Label htmlFor="water_reminders" className="text-base font-medium">
                                Icon Water Bill Reminders
                            </Label>
                            <p className="text-sm text-muted-foreground">
                                Receive reminders for upcoming Icon Water utility bill payments
                            </p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer ml-4">
                            <input
                                type="checkbox"
                                id="water_reminders"
                                checked={preferences.water_reminders_enabled}
                                onChange={() => togglePreference('water_reminders_enabled')}
                                className="sr-only peer"
                            />
                            <div
                                className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-primary/20 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                        </label>
                    </div>

                    {/* AGM Reminders */}
                    <div className="flex items-start justify-between pb-4 border-b">
                        <div className="space-y-1 flex-1">
                            <Label htmlFor="agm_reminders" className="text-base font-medium">
                                AGM Session Notes & Reminders
                            </Label>
                            <p className="text-sm text-muted-foreground">
                                Receive reminders for upcoming AGMs and links to session notes/voting
                            </p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer ml-4">
                            <input
                                type="checkbox"
                                id="agm_reminders"
                                checked={preferences.agm_reminders_enabled}
                                onChange={() => togglePreference('agm_reminders_enabled')}
                                className="sr-only peer"
                            />
                            <div
                                className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-primary/20 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                        </label>
                    </div>

                    {/* Announcements */}
                    <div className="flex items-start justify-between pb-4 border-b">
                        <div className="space-y-1 flex-1">
                            <Label htmlFor="announcements" className="text-base font-medium">
                                General Announcements
                            </Label>
                            <p className="text-sm text-muted-foreground">
                                Receive emails for community announcements and updates from the Executive Committee and
                                Strata Manager
                            </p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer ml-4">
                            <input
                                type="checkbox"
                                id="announcements"
                                checked={preferences.announcements_enabled}
                                onChange={() => togglePreference('announcements_enabled')}
                                className="sr-only peer"
                            />
                            <div
                                className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-primary/20 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                        </label>
                    </div>

                    {/* Maintenance Updates */}
                    <div className="flex items-start justify-between pb-4 border-b">
                        <div className="space-y-1 flex-1">
                            <Label htmlFor="maintenance" className="text-base font-medium">
                                Maintenance Request Updates
                            </Label>
                            <p className="text-sm text-muted-foreground">
                                Get notified when your maintenance requests are reviewed, approved, or completed
                            </p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer ml-4">
                            <input
                                type="checkbox"
                                id="maintenance"
                                checked={preferences.maintenance_updates_enabled}
                                onChange={() => togglePreference('maintenance_updates_enabled')}
                                className="sr-only peer"
                            />
                            <div
                                className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-primary/20 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                        </label>
                    </div>

                    {/* Discussion Replies */}
                    <div className="flex items-start justify-between pb-4 border-b">
                        <div className="space-y-1 flex-1">
                            <Label htmlFor="replies" className="text-base font-medium">
                                Discussion Replies
                            </Label>
                            <p className="text-sm text-muted-foreground">
                                Receive notifications when someone replies to your comments on notices and discussions
                            </p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer ml-4">
                            <input
                                type="checkbox"
                                id="replies"
                                checked={preferences.discussion_replies_enabled}
                                onChange={() => togglePreference('discussion_replies_enabled')}
                                className="sr-only peer"
                            />
                            <div
                                className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-primary/20 rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-primary"></div>
                        </label>
                    </div>

                    {/* Digest Frequency */}
                    <div className="pt-2">
                        <Label htmlFor="digest" className="text-base font-medium mb-2 block">
                            Email Frequency
                        </Label>
                        <p className="text-sm text-muted-foreground mb-4">
                            Choose how often you want to receive notification emails
                        </p>
                        <Select
                            value={preferences.digest_frequency}
                            onValueChange={(value) => setPreferences({...preferences, digest_frequency: value})}
                        >
                            <SelectTrigger id="digest" className="w-full md:w-64">
                                <SelectValue/>
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="immediate">Immediate (as they happen)</SelectItem>
                                <SelectItem value="daily">Daily Digest</SelectItem>
                                <SelectItem value="weekly">Weekly Digest</SelectItem>
                                <SelectItem value="never">Never (no emails)</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>

                    {/* Email Format */}
                    <div className="pt-2">
                        <Label htmlFor="email_format" className="text-base font-medium mb-2 block">
                            Email Format
                        </Label>
                        <p className="text-sm text-muted-foreground mb-4">
                            Choose the format used for notification emails sent to your inbox
                        </p>
                        <Select
                            value={preferences.email_format || 'html'}
                            onValueChange={(value) => setPreferences({...preferences, email_format: value})}
                        >
                            <SelectTrigger id="email_format" className="w-full md:w-64">
                                <SelectValue/>
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="html">HTML</SelectItem>
                                <SelectItem value="plain_text">Plain text</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                </CardContent>
            </Card>

            {/* Info Card */}
            <Card className="bg-blue-50 border-blue-200">
                <CardContent className="p-6">
                    <div className="flex gap-3">
                        <Bell className="h-5 w-5 text-blue-600 mt-0.5"/>
                        <div className="space-y-1">
                            <h4 className="font-semibold text-blue-900">About Email Notifications</h4>
                            <p className="text-sm text-blue-800">
                                Email notifications help you stay informed about important community updates.
                                You can adjust these preferences at any time. All users have access to a personal
                                mailbox for receiving notices, announcements, and maintenance confirmations.
                            </p>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* Save Button */}
            <div className="flex justify-end">
                <Button onClick={handleSave} disabled={saving}>
                    <Save className="h-4 w-4 mr-2"/>
                    {saving ? 'Saving...' : 'Save Preferences'}
                </Button>
            </div>
        </div>
    );
};

export default EmailPreferencesPage;
