// @featuretrace:email-delivery — Admin communications workspace for manual email, delivery infrastructure, and credentials.
// Layer: frontend
// Data flow: AdminCommunicationsPage -> ManualEmailPage/EmailSettingsPage/ManageMailPasswordsPage -> communication + email-settings + admin password APIs.
// Related: backend/routers/communication.py, backend/routers/settings.py, backend/routers/auth.py

import React, {useEffect, useMemo, useState} from 'react';
import {Send} from 'lucide-react';
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../../../components/ui/tabs';
import {useAuth} from '../../../contexts/AuthContext';
import EmailSettingsPage from '../EmailSettingsPage';
import ManageMailPasswordsPage from '../ManageMailPasswordsPage';
import ManualEmailPage from '../ManualEmailPage';

// Keep this aligned with backend/routers/communication.py::send_manual_email.
// Email infrastructure and credential reset retain the old super-admin-only UI boundary.
const COMPOSER_ROLES = ['super_admin', 'strata_manager', 'ec_member'];

const AdminCommunicationsPage = () => {
    const {hasFeatureAccess, selectedBuilding, user} = useAuth();
    const role = user?.role || '';
    const hasBuilding = !!selectedBuilding;
    const tabs = useMemo(() => [
        {
            value: 'composer',
            label: 'Composer',
            show: hasBuilding && COMPOSER_ROLES.includes(role),
            component: ManualEmailPage
        },
        {
            value: 'infrastructure',
            label: 'Infrastructure',
            show: hasBuilding && role === 'super_admin' && hasFeatureAccess('email_settings'),
            component: EmailSettingsPage
        },
        {
            value: 'credentials',
            label: 'Credentials',
            show: role === 'super_admin' && hasFeatureAccess('manage_mail_passwords'),
            component: ManageMailPasswordsPage
        }
    ].filter(tab => tab.show), [hasBuilding, hasFeatureAccess, role]);

    const [activeTab, setActiveTab] = useState(tabs[0]?.value || 'composer');

    useEffect(() => {
        if (tabs.length > 0 && !tabs.some(tab => tab.value === activeTab)) {
            setActiveTab(tabs[0].value);
        }
    }, [activeTab, tabs]);

    if (tabs.length === 0) {
        return (
            <div className="space-y-6" data-testid="admin-communications-page">
                <div>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <Send className="h-6 w-6"/>
                        Admin Communications
                    </h1>
                    <p className="text-muted-foreground">No admin communication tools are available.</p>
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-6" data-testid="admin-communications-page">
            <div>
                <h1 className="text-2xl font-bold flex items-center gap-2">
                    <Send className="h-6 w-6"/>
                    Admin Communications
                </h1>
                <p className="text-muted-foreground">Send emails and manage communication infrastructure</p>
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

export default AdminCommunicationsPage;
