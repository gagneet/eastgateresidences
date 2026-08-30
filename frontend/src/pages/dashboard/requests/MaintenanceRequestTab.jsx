import React from 'react';
import Link from 'next/link';
import { Alert, AlertDescription } from '../../../components/ui/alert';
import { Info } from 'lucide-react';
/**
 * @generated FunctionHeader
 * Function: MaintenanceRequestTab
 * Path: frontend/src/pages/dashboard/requests/MaintenanceRequestTab.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const MaintenanceRequestTab = () => {
    return (
        <div className="space-y-4">
            <Alert>
                <Info className="h-4 w-4"/>
                <AlertDescription>
                    <strong>Maintenance requests are managed on the dedicated Maintenance page.</strong>
                    <br/>
                    Please visit <Link href="/maintenance" className="text-primary hover:underline font-medium">Dashboard
                    → Maintenance</Link> to submit and track maintenance requests.
                </AlertDescription>
            </Alert>

            <div className="p-8 text-center bg-muted/30 rounded-lg">
                <p className="text-muted-foreground">
                    The Maintenance page provides a comprehensive 4-tab interface for:
                </p>
                <ul className="mt-4 space-y-2 text-sm text-muted-foreground">
                    <li>• <strong>Requests:</strong> Submit and track maintenance requests</li>
                    <li>• <strong>Contractors:</strong> Manage service providers</li>
                    <li>• <strong>Purchase Orders:</strong> Create and manage POs</li>
                    <li>• <strong>Invoices:</strong> Track invoices and approvals</li>
                </ul>
                <Link
                    href="/maintenance"
                    className="inline-block mt-6 px-6 py-2 bg-primary text-primary-foreground rounded-full hover:bg-primary/90 transition-colors"
                >
                    Go to Maintenance Page
                </Link>
            </div>
        </div>
    );
};

export default MaintenanceRequestTab;
