import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle
} from '@/components/ui/dialog';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { Input } from '@/components/ui/input';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useAuth } from '@/contexts/AuthContext';
import { toast } from 'sonner';
import {
    Archive,
    Bell,
    Calendar,
    CheckCircle,
    ExternalLink,
    Home,
    Mail,
    RefreshCw,
    RotateCcw,
    User,
    XCircle,
} from 'lucide-react';
/**
 * @generated FunctionHeader
 * Function: ExpiredAccountsPage
 * Path: frontend/src/pages/dashboard/admin/ExpiredAccountsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function ExpiredAccountsPage() {
    const {api} = useAuth();
    const router = useRouter();

    const [expiredUsers, setExpiredUsers] = useState([]);
    const [archivedUsers, setArchivedUsers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [selectedUser, setSelectedUser] = useState(null);
    const [reactivateDialog, setReactivateDialog] = useState(false);
    const [newExpirationDate, setNewExpirationDate] = useState('');
    const [reactivationReason, setReactivationReason] = useState('');
    const [processing, setProcessing] = useState(false);
    const [activeTab, setActiveTab] = useState('expired');
    const [restoreDialog, setRestoreDialog] = useState(false);
    const [restoreUser, setRestoreUser] = useState(null);
    const [restoreReason, setRestoreReason] = useState('');
    const [restoring, setRestoring] = useState(false);

    // ── Data fetching ─────────────────────────────────────────────────────────

    const fetchData = useCallback(async () => {
        setLoading(true);
        try {
            const [expiredRes, archivedRes] = await Promise.allSettled([
                api.get('/admin/expired-users'),
                api.get('/admin/archived-users'),
            ]);
            if (expiredRes.status === 'fulfilled') setExpiredUsers(expiredRes.value.data || []);
            if (archivedRes.status === 'fulfilled') setArchivedUsers(archivedRes.value.data || []);
            if (expiredRes.status === 'rejected') toast.error('Could not load expired accounts');
        } catch (err) {
            console.error('Failed to fetch expired/archived users:', err);
            toast.error('Failed to load accounts');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);
    // ── Reactivation ──────────────────────────────────────────────────────────

    /**
     * @generated FunctionHeader
     * Function: openReactivateDialog
     * Path: frontend/src/pages/dashboard/admin/ExpiredAccountsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openReactivateDialog = (user) => {
        setSelectedUser(user);
        const oneYearFromNow = new Date();
        oneYearFromNow.setFullYear(oneYearFromNow.getFullYear() + 1);
        setNewExpirationDate(oneYearFromNow.toISOString().split('T')[ 0 ]);
        setReactivationReason('');
        setReactivateDialog(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handleReactivate
     * Path: frontend/src/pages/dashboard/admin/ExpiredAccountsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleReactivate = async () => {
        if (!selectedUser || !newExpirationDate) return;
        if (!reactivationReason.trim()) {
            toast.error('Please provide a reason for reactivation');
            return;
        }
        try {
            setProcessing(true);
            await api.post('/admin/reactivate-user', {
                user_unit_id: selectedUser.user_unit_id,
                new_expiration_date: new Date(newExpirationDate).toISOString(),
                reason: reactivationReason,
            });
            toast.success('Account reactivated successfully');
            setReactivateDialog(false);
            fetchData();
        } catch (error) {
            toast.error(error.response?.data?.detail || 'Failed to reactivate account');
        } finally {
            setProcessing(false);
        }
    };
    // ── Restore archived user ─────────────────────────────────────────────────

    /**
     * @generated FunctionHeader
     * Function: openRestoreDialog
     * Path: frontend/src/pages/dashboard/admin/ExpiredAccountsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openRestoreDialog = (user) => {
        setRestoreUser(user);
        setRestoreReason('');
        setRestoreDialog(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handleRestore
     * Path: frontend/src/pages/dashboard/admin/ExpiredAccountsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRestore = async () => {
        if (!restoreUser) return;
        setRestoring(true);
        try {
            await api.post(`/users/${restoreUser.user_id}/restore`, {reason: restoreReason || undefined});
            toast.success(`${restoreUser.full_name}'s account has been restored and is now active`);
            setRestoreDialog(false);
            fetchData();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to restore account');
        } finally {
            setRestoring(false);
        }
    };
    // ── Style helpers ─────────────────────────────────────────────────────────

    /**
     * @generated FunctionHeader
     * Function: getRoleColor
     * Path: frontend/src/pages/dashboard/admin/ExpiredAccountsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getRoleColor = (role) => ( {
        tenant: 'bg-blue-100 text-blue-800',
        guest: 'bg-purple-100 text-purple-800',
        owner: 'bg-green-100 text-green-800',
    }[ role ] || 'bg-gray-100 text-gray-800' );
    /**
     * @generated FunctionHeader
     * Function: getExpiredBadge
     * Path: frontend/src/pages/dashboard/admin/ExpiredAccountsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getExpiredBadge = (daysSince) => {
        if (daysSince <= 7) return <Badge className="bg-amber-100 text-amber-800">Recently Expired
            ({daysSince}d)</Badge>;
        if (daysSince <= 30) return <Badge className="bg-orange-100 text-orange-800">Expired {daysSince}d ago</Badge>;
        return <Badge className="bg-red-100 text-red-800">Expired {daysSince}d ago</Badge>;
    };
    // ── Sub-components ────────────────────────────────────────────────────────

    /**
     * @generated FunctionHeader
     * Function: ExpiredUserCard
     * Path: frontend/src/pages/dashboard/admin/ExpiredAccountsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const ExpiredUserCard = ({user}) => {
        const hasRenewal = user.has_renewal_request;
        const isActive = user.is_active;
        return (
            <Card className="mb-4">
                <CardHeader>
                    <div className="flex justify-between items-start">
                        <div>
                            <CardTitle className="flex items-center gap-2">
                                <User className="h-5 w-5"/>{user.full_name}
                            </CardTitle>
                            <CardDescription>{user.email}</CardDescription>
                        </div>
                        <div className="flex gap-2 items-start flex-col">
                            <Badge className={getRoleColor(user.role_at_unit)}>
                                {user.role_at_unit?.toUpperCase()}
                            </Badge>
                            {getExpiredBadge(user.days_since_expiration)}
                            {!isActive && <Badge className="bg-gray-100 text-gray-800">Deactivated</Badge>}
                        </div>
                    </div>
                </CardHeader>
                <CardContent>
                    <div className="space-y-4">
                        <div className="flex items-center gap-2 p-2 bg-gray-50 rounded">
                            <Home className="h-4 w-4 text-gray-500"/>
                            <span className="font-medium">{user.unit_number}</span>
                        </div>
                        <div className="p-3 bg-red-50 rounded flex items-center gap-2 text-sm">
                            <Calendar className="h-4 w-4 text-red-500 shrink-0"/>
                            <div>
                                <div className="font-medium text-red-800">
                                    Expired: {new Date(user.expiration_date).toLocaleDateString()}
                                </div>
                                <div className="text-red-600 text-xs mt-1">{user.days_since_expiration} days ago</div>
                            </div>
                        </div>
                        {user.role_at_unit === 'tenant' && (
                            <div
                                className={`p-3 rounded flex items-center gap-2 ${hasRenewal ? 'bg-blue-50' : 'bg-gray-50'}`}>
                                {hasRenewal
                                    ? <><CheckCircle className="h-4 w-4 text-blue-600"/><span
                                        className="text-sm text-blue-800">Has pending renewal request</span></>
                                    : <><XCircle className="h-4 w-4 text-gray-500"/><span
                                        className="text-sm text-gray-600">No renewal request submitted</span></>}
                            </div>
                        )}
                        <div className="flex gap-2 pt-4 border-t">
                            <Button onClick={() => openReactivateDialog(user)} className="flex-1">
                                <RefreshCw className="h-4 w-4 mr-2"/>Reactivate Account
                            </Button>
                            <Button variant="outline" disabled title="Email functionality coming soon">
                                <Mail className="h-4 w-4 mr-2"/>Send Reminder
                            </Button>
                        </div>
                    </div>
                </CardContent>
            </Card>
        );
    };
    /**
     * @generated FunctionHeader
     * Function: ArchivedUserCard
     * Path: frontend/src/pages/dashboard/admin/ExpiredAccountsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const ArchivedUserCard = ({user}) => (
        <Card className={`mb-4 ${user.has_return_request ? 'border-amber-300 shadow-md' : 'opacity-80'}`}>
            <CardHeader>
                <div className="flex justify-between items-start">
                    <div>
                        <CardTitle className="flex items-center gap-2">
                            <Archive className="h-5 w-5 text-gray-500"/>{user.full_name}
                        </CardTitle>
                        <CardDescription>{user.email}</CardDescription>
                    </div>
                    <div className="flex gap-2 flex-col items-end">
                        <Badge className={getRoleColor(user.role)}>{user.role?.toUpperCase()}</Badge>
                        {user.has_return_request ? (
                            <Badge
                                className="bg-amber-100 text-amber-800 border border-amber-400 flex items-center gap-1">
                                <Bell className="h-3 w-3"/> Return Requested
                            </Badge>
                        ) : (
                            <Badge className="bg-gray-100 text-gray-600 border border-gray-300">Archived</Badge>
                        )}
                    </div>
                </div>
            </CardHeader>
            <CardContent>
                <div className="space-y-2 text-sm text-gray-600">
                    {user.unit_number && (
                        <div className="flex items-center gap-2">
                            <Home className="h-4 w-4"/><span>{user.unit_number}</span>
                        </div>
                    )}
                    {user.archived_at && (
                        <div className="flex items-center gap-2">
                            <Calendar className="h-4 w-4"/>
                            <span>Archived: {new Date(user.archived_at).toLocaleDateString()} ({user.days_since_archived}d ago)</span>
                        </div>
                    )}
                    {user.has_return_request && user.return_requested_at && (
                        <div
                            className="p-2 bg-amber-50 border border-amber-200 rounded text-xs text-amber-800 flex items-center gap-1">
                            <Bell className="h-3 w-3 shrink-0"/>
                            Return requested on {new Date(user.return_requested_at).toLocaleDateString()} — user tried
                            to re-register
                        </div>
                    )}
                    {user.archived_reason && (
                        <div className="p-2 bg-gray-50 rounded text-xs">
                            Archived reason: {user.archived_reason.replace(/_/g, ' ')}
                        </div>
                    )}
                </div>
                <div className="flex gap-2 pt-3 mt-3 border-t">
                    <Button
                        size="sm"
                        className="flex-1 bg-green-600 hover:bg-green-700 text-white"
                        onClick={() => openRestoreDialog(user)}
                    >
                        <RotateCcw className="h-4 w-4 mr-2"/>
                        Restore Account
                    </Button>
                </div>
            </CardContent>
        </Card>
    );

    // ── Derived counts ────────────────────────────────────────────────────────

    const expiredTenants = expiredUsers.filter(u => u.role_at_unit === 'tenant');
    const expiredGuests = expiredUsers.filter(u => u.role_at_unit === 'guest');

    // ── Render ────────────────────────────────────────────────────────────────

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-3xl font-bold">Expired &amp; Archived Accounts</h1>
                <p className="text-gray-600 mt-2">
                    Manage tenant and guest accounts that have expired, and users who have been archived.
                </p>
            </div>

            {/* ── Summary Cards (clickable) ──────────────────────────────────── */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
                {/* Total expired */}
                <Card
                    className="cursor-pointer hover:shadow-md transition-shadow border-l-4 border-l-red-400"
                    onClick={() => setActiveTab('expired')}
                    role="button"
                    aria-label="View all expired accounts"
                    data-testid="card-total-expired"
                >
                    <CardHeader className="pb-2">
                        <CardTitle className="text-base flex items-center justify-between">
                            Total Expired
                            <ExternalLink className="h-4 w-4 text-muted-foreground"/>
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="text-3xl font-bold">{expiredUsers.length}</div>
                        <p className="text-sm text-gray-500 mt-1">Accounts requiring attention</p>
                    </CardContent>
                </Card>

                {/* Expired tenants */}
                <Card
                    className="cursor-pointer hover:shadow-md transition-shadow border-l-4 border-l-blue-400"
                    onClick={() => setActiveTab('expired')}
                    role="button"
                    aria-label="View expired tenants"
                    data-testid="card-expired-tenants"
                >
                    <CardHeader className="pb-2">
                        <CardTitle className="text-base flex items-center justify-between">
                            Expired Tenants
                            <ExternalLink className="h-4 w-4 text-muted-foreground"/>
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="text-3xl font-bold text-blue-600">{expiredTenants.length}</div>
                        <p className="text-sm text-gray-500 mt-1">
                            {expiredTenants.filter(t => t.has_renewal_request).length} with renewal requests
                        </p>
                    </CardContent>
                </Card>

                {/* Expired guests */}
                <Card
                    className="cursor-pointer hover:shadow-md transition-shadow border-l-4 border-l-purple-400"
                    onClick={() => setActiveTab('expired')}
                    role="button"
                    aria-label="View expired guests"
                    data-testid="card-expired-guests"
                >
                    <CardHeader className="pb-2">
                        <CardTitle className="text-base flex items-center justify-between">
                            Expired Guests
                            <ExternalLink className="h-4 w-4 text-muted-foreground"/>
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="text-3xl font-bold text-purple-600">{expiredGuests.length}</div>
                        <p className="text-sm text-gray-500 mt-1">Guest periods ended</p>
                    </CardContent>
                </Card>

                {/* Archived users */}
                <Card
                    className="cursor-pointer hover:shadow-md transition-shadow border-l-4 border-l-gray-400"
                    onClick={() => setActiveTab('archived')}
                    role="button"
                    aria-label="View archived users"
                    data-testid="card-archived"
                >
                    <CardHeader className="pb-2">
                        <CardTitle className="text-base flex items-center justify-between">
                            Archived Users
                            <ExternalLink className="h-4 w-4 text-muted-foreground"/>
                        </CardTitle>
                    </CardHeader>
                    <CardContent>
                        <div className="text-3xl font-bold text-gray-600">{archivedUsers.length}</div>
                        <p className="text-sm text-gray-500 mt-1">Previous owners &amp; tenants</p>
                    </CardContent>
                </Card>
            </div>

            {/* ── Tabbed content ──────────────────────────────────────────────── */}
            {loading ? (
                <div className="text-center py-12">
                    <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-gray-900 mx-auto"></div>
                    <p className="mt-4 text-gray-600">Loading accounts…</p>
                </div>
            ) : (
                <Tabs value={activeTab} onValueChange={setActiveTab}>
                    <TabsList>
                        <TabsTrigger value="expired">
                            Expired ({expiredUsers.length})
                        </TabsTrigger>
                        <TabsTrigger value="archived">
                            Archived ({archivedUsers.length})
                        </TabsTrigger>
                    </TabsList>

                    {/* ── Expired tab ────────────────────────────────────────────── */}
                    <TabsContent value="expired" className="mt-6">
                        {expiredUsers.length === 0 ? (
                            <Card>
                                <CardContent className="py-12 text-center">
                                    <CheckCircle className="h-12 w-12 mx-auto text-green-400 mb-4"/>
                                    <p className="text-gray-600">No expired accounts found</p>
                                    <p className="text-sm text-gray-500 mt-2">All tenant and guest accounts are up to
                                        date</p>
                                </CardContent>
                            </Card>
                        ) : (
                            <div className="space-y-6">
                                {expiredTenants.length > 0 && (
                                    <div>
                                        <h2 className="text-xl font-semibold mb-4 flex items-center gap-2">
                                            <User className="h-5 w-5 text-blue-600"/>
                                            Expired Tenants ({expiredTenants.length})
                                        </h2>
                                        {expiredTenants
                                            .sort((a, b) => b.days_since_expiration - a.days_since_expiration)
                                            .map(user => <ExpiredUserCard key={user.user_id} user={user}/>)}
                                    </div>
                                )}
                                {expiredGuests.length > 0 && (
                                    <div>
                                        <h2 className="text-xl font-semibold mb-4 flex items-center gap-2">
                                            <User className="h-5 w-5 text-purple-600"/>
                                            Expired Guests ({expiredGuests.length})
                                        </h2>
                                        {expiredGuests
                                            .sort((a, b) => b.days_since_expiration - a.days_since_expiration)
                                            .map(user => <ExpiredUserCard key={user.user_id} user={user}/>)}
                                    </div>
                                )}
                            </div>
                        )}
                    </TabsContent>

                    {/* ── Archived tab ───────────────────────────────────────────── */}
                    <TabsContent value="archived" className="mt-6">
                        {archivedUsers.length === 0 ? (
                            <Card>
                                <CardContent className="py-12 text-center">
                                    <Archive className="h-12 w-12 mx-auto text-gray-400 mb-4"/>
                                    <p className="text-gray-600">No archived users</p>
                                    <p className="text-sm text-gray-500 mt-2">
                                        Archive previous owners or tenants from the User Management page
                                    </p>
                                    <Button
                                        variant="outline" className="mt-4"
                                        onClick={() => router.push('/admin/users')}
                                    >
                                        Go to User Management
                                    </Button>
                                </CardContent>
                            </Card>
                        ) : (
                            <div>
                                <p className="text-sm text-gray-500 mb-4">
                                    Archived users have been removed from active platform access.
                                    Their records are retained for compliance.
                                </p>
                                {archivedUsers.map(user => (
                                    <ArchivedUserCard key={user.user_id} user={user}/>
                                ))}
                            </div>
                        )}
                    </TabsContent>
                </Tabs>
            )}

            {/* ── Reactivate Dialog ────────────────────────────────────────────── */}
            <Dialog open={reactivateDialog} onOpenChange={setReactivateDialog}>
                <DialogContent className="max-w-2xl">
                    <DialogHeader>
                        <DialogTitle>Reactivate Account</DialogTitle>
                        <DialogDescription>
                            {selectedUser && (
                                <>Reactivate {selectedUser.full_name} ({selectedUser.role_at_unit}) for
                                    Unit {selectedUser.unit_number}</>
                            )}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4">
                        <div className="p-4 bg-gray-50 rounded">
                            <Label className="text-sm font-semibold">Current Status</Label>
                            <div className="mt-2 text-sm space-y-1">
                                <div><strong>Expired
                                    on:</strong> {selectedUser && new Date(selectedUser.expiration_date).toLocaleDateString()}
                                </div>
                                <div><strong>Days since expiration:</strong> {selectedUser?.days_since_expiration}</div>
                            </div>
                        </div>

                        <div>
                            <Label htmlFor="new-expiration">New Expiration Date <span className="text-red-500">*</span></Label>
                            <Input
                                id="new-expiration"
                                type="date"
                                value={newExpirationDate}
                                onChange={(e) => setNewExpirationDate(e.target.value)}
                                min={new Date().toISOString().split('T')[ 0 ]}
                                className="mt-2"
                            />
                        </div>

                        <div>
                            <Label htmlFor="reason">Reason for Reactivation <span
                                className="text-red-500">*</span></Label>
                            <Textarea
                                id="reason"
                                value={reactivationReason}
                                onChange={(e) => setReactivationReason(e.target.value)}
                                placeholder="e.g., Landlord confirmed lease renewal, manual extension approved by committee…"
                                rows={4}
                                className="mt-2"
                            />
                        </div>

                        <div className="p-4 bg-blue-50 rounded">
                            <Label className="text-sm font-semibold text-blue-800">Reactivation Summary:</Label>
                            <ul className="mt-2 text-sm text-blue-700 space-y-1">
                                <li>• Account will be reactivated immediately</li>
                                <li>• User will regain access to the portal</li>
                                <li>• New expiration
                                    date: {newExpirationDate ? new Date(newExpirationDate).toLocaleDateString() : 'Not set'}</li>
                                <li>• Account will auto-disable again after new expiration if not renewed</li>
                                {selectedUser?.role_at_unit === 'tenant' && <li>• Renewal flags will be reset</li>}
                            </ul>
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setReactivateDialog(false)} disabled={processing}>
                            Cancel
                        </Button>
                        <Button
                            onClick={handleReactivate}
                            disabled={processing || !newExpirationDate || !reactivationReason.trim()}
                        >
                            {processing ? 'Reactivating…' : 'Reactivate Account'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* ── Restore Archived User Dialog ─────────────────────────────────── */}
            <Dialog open={restoreDialog} onOpenChange={setRestoreDialog}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <RotateCcw className="h-5 w-5 text-green-600"/>
                            Restore Archived Account
                        </DialogTitle>
                        <DialogDescription>
                            {restoreUser && (
                                <>
                                    Restore <strong>{restoreUser.full_name}</strong>{' '}
                                    ({restoreUser.role?.toUpperCase()}{restoreUser.unit_number ? ` · ${restoreUser.unit_number}` : ''}).
                                    Their account will become active immediately and they can log in with their original
                                    password.
                                </>
                            )}
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-2">
                        {restoreUser?.has_return_request && (
                            <div
                                className="p-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800 flex items-start gap-2">
                                <Bell className="h-4 w-4 mt-0.5 shrink-0"/>
                                <span>
                  This user attempted to re-register on{' '}
                                    <strong>{new Date(restoreUser.return_requested_at).toLocaleDateString()}</strong>.
                  Restoring will allow them to log in with their existing credentials.
                </span>
                            </div>
                        )}

                        <div className="space-y-2">
                            <Label htmlFor="restore-reason">Reason for Restoring <span
                                className="text-gray-400 font-normal">(optional)</span></Label>
                            <Textarea
                                id="restore-reason"
                                value={restoreReason}
                                onChange={(e) => setRestoreReason(e.target.value)}
                                placeholder="e.g., Owner returned after selling and buying back, previous tenant renewed lease…"
                                rows={3}
                            />
                        </div>

                        <div className="p-3 bg-green-50 border border-green-200 rounded-lg text-sm text-green-800">
                            <ul className="space-y-1">
                                <li>• Account status set to <strong>Active &amp; Approved</strong></li>
                                <li>• Unit association restored</li>
                                <li>• User can log in immediately with their original password</li>
                                <li>• Archive history is preserved in the audit log</li>
                            </ul>
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setRestoreDialog(false)} disabled={restoring}>
                            Cancel
                        </Button>
                        <Button
                            className="bg-green-600 hover:bg-green-700 text-white"
                            onClick={handleRestore}
                            disabled={restoring}
                        >
                            {restoring ? 'Restoring…' : 'Restore Account'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
