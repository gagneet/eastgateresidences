// @featuretrace:email-delivery — User communications workspace for mailbox access and notification preferences.
// Layer: frontend
// Data flow: CommunicationsWorkspacePage (building-scoped) -> EmailAccessPage/EmailPreferencesPage
//            -> /mail/access + /notifications/preferences. Also supports ?tab= deep-linking.
// Related: backend/routers/auth.py, backend/routers/communication.py, frontend/src/pages/dashboard/EmailAccessPage.jsx

import React, {useEffect, useMemo, useState} from 'react';
import {useSearchParams} from 'next/navigation';
import {Mail} from 'lucide-react';
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../../components/ui/tabs';
import {useAuth} from '../../contexts/AuthContext';
import EmailAccessPage from './EmailAccessPage';
import EmailPreferencesPage from './EmailPreferencesPage';

const CommunicationsWorkspacePage = () => {
    const {hasFeatureAccess, hasPermission} = useAuth();
    const searchParams = useSearchParams();
    const tabs = useMemo(() => [
        {
            value: 'preferences',
            label: 'Preferences',
            show: hasFeatureAccess('email_preferences'),
            component: EmailPreferencesPage
        },
        {
            value: 'mailbox',
            label: 'Mailbox',
            show: hasFeatureAccess('webmail') && hasPermission('can_access_email'),
            component: EmailAccessPage
        }
    ].filter(tab => tab.show), [hasFeatureAccess, hasPermission]);

    // Deep-link support (e.g. /my-communications?tab=preferences from email footers/
    // notification digests) — falls back to the first available tab when the param is
    // absent or doesn't match an enabled tab.
    const requestedTab = searchParams?.get('tab');
    const [activeTab, setActiveTab] = useState(requestedTab || tabs[0]?.value || 'preferences');

    useEffect(() => {
        if (tabs.length > 0 && !tabs.some(tab => tab.value === activeTab)) {
            setActiveTab(tabs[0].value);
        }
    }, [activeTab, tabs]);

    if (tabs.length === 0) {
        return (
            <div className="space-y-6" data-testid="communications-workspace-page">
                <div>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <Mail className="h-6 w-6"/>
                        Communications
                    </h1>
                    <p className="text-muted-foreground">No communication tools are available for your account.</p>
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-6" data-testid="communications-workspace-page">
            <div>
                <h1 className="text-2xl font-bold flex items-center gap-2">
                    <Mail className="h-6 w-6"/>
                    Communications
                </h1>
                <p className="text-muted-foreground">Manage your mailbox access and email notification preferences</p>
            </div>

            <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-6">
                <TabsList>
                    {tabs.map(tab => (
                        <TabsTrigger key={tab.value} value={tab.value}>
                            {tab.label}
                        </TabsTrigger>
                    ))}
                </TabsList>

                {tabs.map(tab => {
                    const Component = tab.component;
                    return (
                        <TabsContent key={tab.value} value={tab.value} className="mt-0">
                            {activeTab === tab.value ? <Component/> : null}
                        </TabsContent>
                    );
                })}
            </Tabs>
        </div>
    );
};

export default CommunicationsWorkspacePage;
