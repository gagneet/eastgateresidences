"use client";

import React, {useCallback, useEffect, useState} from 'react';
import {useParams, useRouter} from 'next/navigation';
import Link from 'next/link';
import {useAuth} from '@/contexts/AuthContext';
import {Card, CardContent, CardHeader, CardTitle} from '@/components/ui/card';
import {Badge} from '@/components/ui/badge';
import {Button} from '@/components/ui/button';
import {Separator} from '@/components/ui/separator';
import {
    ArrowLeft,
    Building2,
    Calendar,
    CheckCircle2,
    Clock,
    FileText,
    Loader2,
    MapPin,
    User,
    Wrench,
} from 'lucide-react';
import {formatDate} from '@/lib/utils';
import {toast} from 'sonner';

const statusConfig: Record<string, { label: string; color: string }> = {
    submitted: {label: 'Submitted', color: 'bg-blue-100 text-blue-800'},
    under_review: {label: 'Under Review', color: 'bg-yellow-100 text-yellow-800'},
    approved: {label: 'Approved', color: 'bg-green-100 text-green-800'},
    in_progress: {label: 'In Progress', color: 'bg-orange-100 text-orange-800'},
    completed: {label: 'Completed', color: 'bg-gray-100 text-gray-800'},
    rejected: {label: 'Rejected', color: 'bg-red-100 text-red-800'},
};

const priorityConfig: Record<string, { label: string; color: string }> = {
    low: {label: 'Low', color: 'bg-gray-100 text-gray-800'},
    medium: {label: 'Medium', color: 'bg-yellow-100 text-yellow-800'},
    high: {label: 'High', color: 'bg-orange-100 text-orange-800'},
    urgent: {label: 'Urgent', color: 'bg-red-100 text-red-800'},
};
/**
 * Maintenance request detail page.
 * Reached from notification links: /maintenance/{id}
 */
export default function MaintenanceDetailPage() {
    const params = useParams<{ id: string }>();
    const id = params?.id;
    const router = useRouter();
    const {api} = useAuth();

    const [request, setRequest] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchRequest = useCallback(async () => {
        if (!id) return;
        try {
            setLoading(true);
            const res = await api.get(`/maintenance/${id}`);
            setRequest(res.data);
        } catch (err: any) {
            const detail = err?.response?.data?.detail || 'Failed to load maintenance request';
            const status = err?.response?.status;
            if (status === 404) {
                setError('This maintenance request was not found or may have been removed.');
            } else if (status === 403) {
                setError('You do not have permission to view this maintenance request.');
            } else {
                setError(detail);
                toast.error(detail);
            }
        } finally {
            setLoading(false);
        }
    }, [api, id]);

    useEffect(() => {
        fetchRequest();
    }, [fetchRequest]);

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[400px]">
                <Loader2 className="h-8 w-8 animate-spin text-primary"/>
            </div>
        );
    }

    if (error) {
        return (
            <div className="max-w-2xl mx-auto space-y-6 py-12 text-center">
                <div className="bg-red-50 border border-red-200 rounded-xl p-8">
                    <Wrench className="h-12 w-12 text-red-300 mx-auto mb-4"/>
                    <h2 className="text-lg font-semibold text-red-800 mb-2">Unable to load request</h2>
                    <p className="text-red-600 text-sm mb-6">{error}</p>
                    <Button variant="outline" asChild>
                        <Link href="/maintenance">
                            <ArrowLeft className="mr-2 h-4 w-4"/> Back to Maintenance
                        </Link>
                    </Button>
                </div>
            </div>
        );
    }

    if (!request) return null;

    const status = statusConfig[request.status] || {label: request.status, color: 'bg-gray-100 text-gray-800'};
    const priority = priorityConfig[request.priority] || priorityConfig.medium;

    return (
        <div className="max-w-3xl mx-auto space-y-6">
            {/* Back navigation */}
            <Button variant="ghost" size="sm" asChild className="text-muted-foreground">
                <Link href="/maintenance">
                    <ArrowLeft className="mr-2 h-4 w-4"/> Back to Maintenance
                </Link>
            </Button>

            {/* Header card */}
            <Card className="card-dashboard">
                <CardHeader>
                    <div className="flex items-start justify-between gap-4">
                        <div className="space-y-1">
                            <CardTitle className="text-xl">{request.title}</CardTitle>
                            <p className="text-xs text-muted-foreground font-mono">ID: {request.id}</p>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            <Badge className={status.color}>{status.label}</Badge>
                            <Badge className={priority.color}>{priority.label} Priority</Badge>
                        </div>
                    </div>
                </CardHeader>
                <CardContent className="space-y-6">
                    {/* Description */}
                    {request.description && (
                        <div>
                            <h3 className="text-sm font-semibold mb-2 flex items-center gap-2">
                                <FileText className="h-4 w-4"/> Description
                            </h3>
                            <p className="text-sm text-muted-foreground whitespace-pre-wrap">{request.description}</p>
                        </div>
                    )}

                    <Separator/>

                    {/* Details grid */}
                    <div className="grid grid-cols-2 gap-4">
                        <div className="flex items-center gap-2 text-sm">
                            <Building2 className="h-4 w-4 text-muted-foreground flex-shrink-0"/>
                            <span className="font-medium">Category:</span>
                            <span
                                className="capitalize text-muted-foreground">{request.category?.replace(/_/g, ' ')}</span>
                        </div>
                        {request.location && (
                            <div className="flex items-center gap-2 text-sm">
                                <MapPin className="h-4 w-4 text-muted-foreground flex-shrink-0"/>
                                <span className="font-medium">Location:</span>
                                <span className="text-muted-foreground">{request.location}</span>
                            </div>
                        )}
                        <div className="flex items-center gap-2 text-sm">
                            <Calendar className="h-4 w-4 text-muted-foreground flex-shrink-0"/>
                            <span className="font-medium">Submitted:</span>
                            <span className="text-muted-foreground">{formatDate(request.created_at)}</span>
                        </div>
                        {request.submitted_by_name && (
                            <div className="flex items-center gap-2 text-sm">
                                <User className="h-4 w-4 text-muted-foreground flex-shrink-0"/>
                                <span className="font-medium">Submitted by:</span>
                                <span className="text-muted-foreground">{request.submitted_by_name}</span>
                            </div>
                        )}
                        {request.assigned_contractor_name && (
                            <div className="flex items-center gap-2 text-sm">
                                <Wrench className="h-4 w-4 text-muted-foreground flex-shrink-0"/>
                                <span className="font-medium">Assigned to:</span>
                                <span className="text-muted-foreground">{request.assigned_contractor_name}</span>
                            </div>
                        )}
                        {request.resolved_at && (
                            <div className="flex items-center gap-2 text-sm">
                                <CheckCircle2 className="h-4 w-4 text-muted-foreground flex-shrink-0"/>
                                <span className="font-medium">Resolved:</span>
                                <span className="text-muted-foreground">{formatDate(request.resolved_at)}</span>
                            </div>
                        )}
                        {request.updated_at && (
                            <div className="flex items-center gap-2 text-sm">
                                <Clock className="h-4 w-4 text-muted-foreground flex-shrink-0"/>
                                <span className="font-medium">Last updated:</span>
                                <span className="text-muted-foreground">{formatDate(request.updated_at)}</span>
                            </div>
                        )}
                    </div>

                    {/* Admin note */}
                    {request.admin_note && (
                        <>
                            <Separator/>
                            <div className="bg-amber-50 border border-amber-200 rounded-lg p-4">
                                <h3 className="text-sm font-semibold text-amber-800 mb-1">Management Note</h3>
                                <p className="text-sm text-amber-700">{request.admin_note}</p>
                            </div>
                        </>
                    )}

                    {/* Workflow status guide */}
                    <Separator/>
                    <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                        <h3 className="text-sm font-semibold text-blue-800 mb-3">Request Workflow</h3>
                        <ol className="space-y-2 text-xs text-blue-700">
                            {[
                                {step: 'Submitted', desc: 'Your request has been received'},
                                {step: 'Under Review', desc: 'Management is reviewing the request'},
                                {step: 'Approved / Assigned', desc: 'Approved and assigned to a contractor'},
                                {step: 'In Progress', desc: 'Work is underway'},
                                {step: 'Completed', desc: 'Work completed — awaiting your confirmation'},
                            ].map(({step, desc}) => (
                                <li key={step} className="flex items-start gap-2">
                                    <span className="font-bold min-w-[110px]">{step}:</span>
                                    <span>{desc}</span>
                                </li>
                            ))}
                        </ol>
                    </div>

                    <div className="flex justify-end">
                        <Button variant="outline" asChild>
                            <Link href="/maintenance">
                                <ArrowLeft className="mr-2 h-4 w-4"/> View All Requests
                            </Link>
                        </Button>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
