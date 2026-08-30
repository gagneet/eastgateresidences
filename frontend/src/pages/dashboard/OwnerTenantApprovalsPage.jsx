import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { Avatar, AvatarFallback } from '../../components/ui/avatar';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '../../components/ui/dialog';
import { Textarea } from '../../components/ui/textarea';
import { AlertCircle, CheckCircle, Clock, Home, Mail, Phone, RefreshCw, Users, XCircle } from 'lucide-react';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: OwnerTenantApprovalsPage
 * Path: frontend/src/pages/dashboard/OwnerTenantApprovalsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function OwnerTenantApprovalsPage() {
    const {api, user} = useAuth();
    const [registrations, setRegistrations] = useState([]);
    const [loading, setLoading] = useState(true);
    const [approveDialog, setApproveDialog] = useState({open: false, userId: null, userName: ''});
    const [rejectDialog, setRejectDialog] = useState({open: false, userId: null, userName: '', notes: ''});
    const [submitting, setSubmitting] = useState(false);

    const fetchRegistrations = useCallback(async () => {
        try {
            setLoading(true);
            const res = await api.get('/owner/pending-registrations');
            setRegistrations(res.data || []);
        } catch (err) {
            if (err.response?.status !== 403) {
                toast.error('Failed to load pending registrations');
            }
            setRegistrations([]);
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchRegistrations();
    }, [fetchRegistrations]);
    /**
     * @generated FunctionHeader
     * Function: handleApprove
     * Path: frontend/src/pages/dashboard/OwnerTenantApprovalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleApprove = async () => {
        if (!approveDialog.userId) return;
        try {
            setSubmitting(true);
            await api.post(`/users/${approveDialog.userId}/owner-decision`, {action: 'approve'});
            toast.success(`${approveDialog.userName} has been approved. The Strata Manager has been notified.`);
            setApproveDialog({open: false, userId: null, userName: ''});
            fetchRegistrations();
        } catch (err) {
            toast.error(err.response?.data?.detail || 'Failed to approve registration');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleReject
     * Path: frontend/src/pages/dashboard/OwnerTenantApprovalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleReject = async () => {
        if (!rejectDialog.userId) return;
        try {
            setSubmitting(true);
            await api.post(`/users/${rejectDialog.userId}/owner-decision`, {
                action: 'reject',
                notes: rejectDialog.notes || 'Rejected by unit owner',
            });
            toast.success(`${rejectDialog.userName}'s registration has been declined.`);
            setRejectDialog({open: false, userId: null, userName: '', notes: ''});
            fetchRegistrations();
        } catch (err) {
            toast.error(err.response?.data?.detail || 'Failed to reject registration');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: getRoleBadge
     * Path: frontend/src/pages/dashboard/OwnerTenantApprovalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getRoleBadge = (role) => {
        const styles = {
            tenant: 'bg-blue-100 text-blue-800',
            guest: 'bg-purple-100 text-purple-800',
        };
        return (
            <Badge className={`${styles[ role ] || 'bg-slate-100 text-slate-800'} capitalize text-xs font-semibold`}>
                {role}
            </Badge>
        );
    };
    /**
     * @generated FunctionHeader
     * Function: formatDate
     * Path: frontend/src/pages/dashboard/OwnerTenantApprovalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const formatDate = (iso) => {
        if (!iso) return '—';
        return new Date(iso).toLocaleDateString('en-AU', {day: '2-digit', month: 'short', year: 'numeric'});
    };
    /**
     * @generated FunctionHeader
     * Function: getInitials
     * Path: frontend/src/pages/dashboard/OwnerTenantApprovalsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getInitials = (name = '') =>
        name.split(' ').map((n) => n[ 0 ]).join('').toUpperCase().slice(0, 2);

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-slate-900">Tenant &amp; Guest Approvals</h1>
                    <p className="text-slate-500 text-sm mt-1">
                        Review and approve new tenant or guest registrations for your unit before they're activated by
                        the Strata Manager.
                    </p>
                </div>
                <Button variant="outline" size="sm" onClick={fetchRegistrations} disabled={loading} className="gap-2">
                    <RefreshCw size={14} className={loading ? 'animate-spin' : ''}/>
                    Refresh
                </Button>
            </div>

            {/* Info banner */}
            <div
                className="flex items-start gap-3 p-4 bg-blue-50 border border-blue-200 rounded-xl text-sm text-blue-800">
                <AlertCircle size={16} className="mt-0.5 shrink-0"/>
                <div>
                    <strong>Two-step approval process:</strong> Your approval is required first before the Strata
                    Manager can activate the account.
                    Once you approve, the Strata Manager will be notified automatically.
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                <Card className="border shadow-sm">
                    <CardContent className="p-5 flex items-center gap-4">
                        <div className="p-3 rounded-xl bg-amber-100">
                            <Clock size={20} className="text-amber-600"/>
                        </div>
                        <div>
                            <p className="text-2xl font-bold text-slate-900">{registrations.length}</p>
                            <p className="text-xs text-slate-500 font-medium uppercase tracking-wide">Pending Review</p>
                        </div>
                    </CardContent>
                </Card>
                <Card className="border shadow-sm">
                    <CardContent className="p-5 flex items-center gap-4">
                        <div className="p-3 rounded-xl bg-blue-100">
                            <Users size={20} className="text-blue-600"/>
                        </div>
                        <div>
                            <p className="text-2xl font-bold text-slate-900">
                                {registrations.filter((r) => r.role === 'tenant').length}
                            </p>
                            <p className="text-xs text-slate-500 font-medium uppercase tracking-wide">Tenants</p>
                        </div>
                    </CardContent>
                </Card>
                <Card className="border shadow-sm">
                    <CardContent className="p-5 flex items-center gap-4">
                        <div className="p-3 rounded-xl bg-purple-100">
                            <Users size={20} className="text-purple-600"/>
                        </div>
                        <div>
                            <p className="text-2xl font-bold text-slate-900">
                                {registrations.filter((r) => r.role === 'guest').length}
                            </p>
                            <p className="text-xs text-slate-500 font-medium uppercase tracking-wide">Guests</p>
                        </div>
                    </CardContent>
                </Card>
            </div>

            {/* Registration cards */}
            {loading ? (
                <div className="flex items-center justify-center py-16 text-slate-400">
                    <RefreshCw size={24} className="animate-spin mr-3"/>
                    Loading pending registrations…
                </div>
            ) : registrations.length === 0 ? (
                <Card className="border shadow-sm">
                    <CardContent className="py-16 flex flex-col items-center justify-center text-slate-400 gap-3">
                        <CheckCircle size={40} className="text-green-400"/>
                        <p className="font-semibold text-slate-600">No pending approvals</p>
                        <p className="text-sm">All registrations for your unit have been reviewed.</p>
                    </CardContent>
                </Card>
            ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
                    {registrations.map((reg) => (
                        <Card key={reg.id} className="border shadow-sm hover:shadow-md transition-shadow">
                            <CardHeader className="pb-3">
                                <div className="flex items-start justify-between">
                                    <div className="flex items-center gap-3">
                                        <Avatar className="h-10 w-10">
                                            <AvatarFallback className="bg-slate-200 text-slate-700 font-bold text-sm">
                                                {getInitials(reg.full_name)}
                                            </AvatarFallback>
                                        </Avatar>
                                        <div>
                                            <p className="font-semibold text-slate-900 leading-tight">{reg.full_name}</p>
                                            <div className="mt-1">{getRoleBadge(reg.role)}</div>
                                        </div>
                                    </div>
                                    <Badge variant="outline"
                                           className="text-amber-600 border-amber-300 bg-amber-50 text-xs">
                                        Pending
                                    </Badge>
                                </div>
                            </CardHeader>
                            <CardContent className="space-y-2 pt-0">
                                <div className="flex items-center gap-2 text-sm text-slate-600">
                                    <Mail size={13} className="text-slate-400 shrink-0"/>
                                    <span className="truncate">{reg.email}</span>
                                </div>
                                {reg.phone && (
                                    <div className="flex items-center gap-2 text-sm text-slate-600">
                                        <Phone size={13} className="text-slate-400 shrink-0"/>
                                        <span>{reg.phone}</span>
                                    </div>
                                )}
                                <div className="flex items-center gap-2 text-sm text-slate-600">
                                    <Home size={13} className="text-slate-400 shrink-0"/>
                                    <span>Unit {reg.unit_number}</span>
                                </div>
                                <div className="flex items-center gap-2 text-sm text-slate-500">
                                    <Clock size={13} className="text-slate-400 shrink-0"/>
                                    <span>Registered {formatDate(reg.created_at)}</span>
                                </div>

                                <div className="flex gap-2 pt-3">
                                    <Button
                                        size="sm"
                                        className="flex-1 bg-green-600 hover:bg-green-700 text-white gap-1"
                                        onClick={() => setApproveDialog({
                                            open: true,
                                            userId: reg.id,
                                            userName: reg.full_name
                                        })}
                                    >
                                        <CheckCircle size={13}/>
                                        Approve
                                    </Button>
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        className="flex-1 text-red-600 border-red-200 hover:bg-red-50 gap-1"
                                        onClick={() => setRejectDialog({
                                            open: true,
                                            userId: reg.id,
                                            userName: reg.full_name,
                                            notes: ''
                                        })}
                                    >
                                        <XCircle size={13}/>
                                        Decline
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            )}

            {/* Approve Dialog */}
            <Dialog open={approveDialog.open}
                    onOpenChange={(o) => !o && setApproveDialog({open: false, userId: null, userName: ''})}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <CheckCircle size={18} className="text-green-600"/>
                            Approve Registration
                        </DialogTitle>
                        <DialogDescription>
                            You're about to approve <strong>{approveDialog.userName}</strong>'s registration. The Strata
                            Manager will be notified to complete the activation.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="bg-green-50 border border-green-200 rounded-lg p-4 text-sm text-green-800">
                        After your approval, this person will still need to be activated by the Strata Manager before
                        they can access the portal.
                    </div>
                    <DialogFooter>
                        <Button variant="outline"
                                onClick={() => setApproveDialog({open: false, userId: null, userName: ''})}
                                disabled={submitting}>
                            Cancel
                        </Button>
                        <Button className="bg-green-600 hover:bg-green-700" onClick={handleApprove}
                                disabled={submitting}>
                            {submitting ? 'Approving…' : 'Confirm Approval'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Reject Dialog */}
            <Dialog
                open={rejectDialog.open}
                onOpenChange={(o) => !o && setRejectDialog({open: false, userId: null, userName: '', notes: ''})}
            >
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <XCircle size={18} className="text-red-600"/>
                            Decline Registration
                        </DialogTitle>
                        <DialogDescription>
                            You're declining <strong>{rejectDialog.userName}</strong>'s registration request. They will
                            be notified and their account will be archived.
                        </DialogDescription>
                    </DialogHeader>
                    <div>
                        <label className="text-sm font-medium text-slate-700 mb-2 block">
                            Reason (optional — not shown to the applicant)
                        </label>
                        <Textarea
                            placeholder="e.g. I did not authorise this person to live in my unit."
                            rows={3}
                            value={rejectDialog.notes}
                            onChange={(e) => setRejectDialog((d) => ( {...d, notes: e.target.value} ))}
                        />
                    </div>
                    <DialogFooter>
                        <Button
                            variant="outline"
                            onClick={() => setRejectDialog({open: false, userId: null, userName: '', notes: ''})}
                            disabled={submitting}
                        >
                            Cancel
                        </Button>
                        <Button variant="destructive" onClick={handleReject} disabled={submitting}>
                            {submitting ? 'Declining…' : 'Decline Registration'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
