'use client';

import { useCallback, useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { useAuth } from '@/contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import {
    AlertCircle,
    ArrowLeft,
    Building2,
    CalendarDays,
    CheckCircle2,
    Loader2,
    PlusCircle,
    RefreshCw,
    Shield,
    Trash2,
} from 'lucide-react';

const ROLE_CONFIG = {
    strata_manager: {label: 'Strata Manager', color: 'bg-blue-100 text-blue-800 border-blue-200'},
    real_estate_agent: {label: 'Real Estate Agent', color: 'bg-purple-100 text-purple-800 border-purple-200'},
    admin_staff: {label: 'Admin Staff', color: 'bg-green-100 text-green-800 border-green-200'},
};

const ELEVATABLE_ROLES = [
    {
        value: 'owner',
        label: 'Owner',
        description: 'Access own financial data, submit requests, view property info.',
        color: 'bg-emerald-100 text-emerald-800',
    },
    {
        value: 'ec_member',
        label: 'EC Member',
        description: 'Vote on committee motions, view financials, review work orders.',
        color: 'bg-indigo-100 text-indigo-800',
    },
    {
        value: 'secretary',
        label: 'Secretary',
        description: 'Manage meetings, finalize minutes, committee governance.',
        color: 'bg-teal-100 text-teal-800',
    },
    {
        value: 'treasurer',
        label: 'Treasurer',
        description: 'Financial management, approve invoices, levy oversight.',
        color: 'bg-rose-100 text-rose-800',
    },
    {
        value: 'strata_admin',
        label: 'Strata Admin',
        description: 'Organisation administration access for assigned buildings.',
        color: 'bg-amber-100 text-amber-800',
    },
];
/**
 * @generated FunctionHeader
 * Function: formatDate
 * Path: frontend/src/pages/dashboard/admin/StaffUserDetailPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function formatDate(iso) {
    if (!iso) return '—';
    return new Date(iso).toLocaleDateString('en-AU', {day: 'numeric', month: 'short', year: 'numeric'});
}
/**
 * @generated FunctionHeader
 * Function: ProfileField
 * Path: frontend/src/pages/dashboard/admin/StaffUserDetailPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function ProfileField({label, value}) {
    if (!value) return null;
    return (
        <div>
            <dt className="text-xs text-gray-500 font-medium uppercase tracking-wide">{label}</dt>
            <dd className="text-sm text-gray-900 mt-0.5">{value}</dd>
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: StaffUserDetailPage
 * Path: frontend/src/pages/dashboard/admin/StaffUserDetailPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function StaffUserDetailPage({userId: propUserId} = {}) {
    const params = useParams();
    const userId = propUserId || params?.id;
    const router = useRouter();
    const {api, isAdmin, loading: authLoading} = useAuth();

    const [user, setUser] = useState(null);
    const [buildings, setBuildings] = useState([]);
    const [loading, setLoading] = useState(!!userId);  // don't load if no userId
    const [error, setError] = useState(null);

    // Assign role modal
    const [showAssignModal, setShowAssignModal] = useState(false);
    const [assignForm, setAssignForm] = useState({
        role_name: '',
        building_id: '',
        start_date: '',
        end_date: '',
        notes: '',
    });
    const [assigning, setAssigning] = useState(false);
    const [assignError, setAssignError] = useState(null);
    const [assignSuccess, setAssignSuccess] = useState(null);

    // Revoke confirmation
    const [revokingId, setRevokingId] = useState(null);
    const [revokeLoading, setRevokeLoading] = useState(false);

    const fetchUser = useCallback(async () => {
        if (!userId) return;
        setLoading(true);
        setError(null);
        try {
            const [userRes, bldgRes] = await Promise.all([
                api.get(`/admin/staff-users/${userId}`),
                api.get('/admin/staff-users/buildings'),
            ]);
            setUser(userRes.data);
            setBuildings(bldgRes.data || []);
        } catch (err) {
            setError(err?.response?.data?.detail || 'Failed to load staff user.');
        } finally {
            setLoading(false);
        }
    }, [api, userId]);

    useEffect(() => {
        if (userId) fetchUser();
        else setLoading(false);
    }, [fetchUser, userId]);
    /**
     * @generated FunctionHeader
     * Function: handleAssign
     * Path: frontend/src/pages/dashboard/admin/StaffUserDetailPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAssign = async () => {
        setAssignError(null);
        if (!assignForm.role_name || !assignForm.building_id) {
            setAssignError('Role and building are required.');
            return;
        }
        setAssigning(true);
        try {
            await api.post(`/admin/staff-users/${userId}/building-roles`, {
                role_name: assignForm.role_name,
                building_id: assignForm.building_id,
                start_date: assignForm.start_date || null,
                end_date: assignForm.end_date || null,
                notes: assignForm.notes || null,
            });
            setAssignSuccess(`Role '${assignForm.role_name}' assigned successfully.`);
            setAssignForm({role_name: '', building_id: '', start_date: '', end_date: '', notes: ''});
            await fetchUser();
            setTimeout(() => {
                setShowAssignModal(false);
                setAssignSuccess(null);
            }, 1500);
        } catch (err) {
            setAssignError(err?.response?.data?.detail || 'Failed to assign role.');
        } finally {
            setAssigning(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleRevoke
     * Path: frontend/src/pages/dashboard/admin/StaffUserDetailPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRevoke = async (assignmentId) => {
        setRevokeLoading(true);
        try {
            await api.delete(`/admin/staff-users/${userId}/building-roles/${assignmentId}`);
            await fetchUser();
        } catch (err) {
            setError(err?.response?.data?.detail || 'Failed to revoke role.');
        } finally {
            setRevokeLoading(false);
            setRevokingId(null);
        }
    };

    // Auth must resolve before isAdmin() is meaningful — otherwise a real super
    // admin sees "access required" on first paint, before the session loads.
    if (authLoading) {
        return (
            <div className="flex justify-center items-center min-h-[300px]">
                <Loader2 className="h-8 w-8 animate-spin text-gray-400"/>
            </div>
        );
    }

    if (!isAdmin()) {
        return (
            <div className="p-6">
                <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4"/>
                    <AlertDescription>Super Admin access required.</AlertDescription>
                </Alert>
            </div>
        );
    }

    if (loading) {
        return (
            <div className="flex justify-center items-center min-h-[300px]">
                <Loader2 className="h-8 w-8 animate-spin text-gray-400"/>
            </div>
        );
    }

    if (error && !user) {
        return (
            <div className="p-6 max-w-4xl mx-auto space-y-4">
                <Button variant="ghost" size="sm" onClick={() => router.back()}>
                    <ArrowLeft className="h-4 w-4 mr-1"/> Back
                </Button>
                <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4"/>
                    <AlertDescription>{error}</AlertDescription>
                </Alert>
            </div>
        );
    }

    // Group role assignments by building
    const assignmentsByBuilding = {};
    ( user?.role_assignments || [] ).forEach(ra => {
        const bkey = ra.building_id || '__unscoped__';
        if (!assignmentsByBuilding[ bkey ]) assignmentsByBuilding[ bkey ] = [];
        assignmentsByBuilding[ bkey ].push(ra);
    });
    /**
     * @generated FunctionHeader
     * Function: buildingName
     * Path: frontend/src/pages/dashboard/admin/StaffUserDetailPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const buildingName = (bid) => {
        const b = buildings.find(b => b.id === bid);
        return b ? b.name : bid;
    };

    return (
        <div className="p-6 max-w-5xl mx-auto space-y-6">
            {/* Nav */}
            <div className="flex items-center gap-3">
                <Button variant="ghost" size="sm" onClick={() => router.push('/admin/staff-users')}>
                    <ArrowLeft className="h-4 w-4 mr-1"/>
                    Staff Users
                </Button>
                <span className="text-gray-300">/</span>
                <span className="text-sm text-gray-600 truncate">{user?.full_name}</span>
            </div>

            {error && (
                <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4"/>
                    <AlertDescription>{error}</AlertDescription>
                </Alert>
            )}

            {/* Profile card */}
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-start justify-between gap-4">
                        <div className="flex items-center gap-4">
                            <div
                                className="h-14 w-14 rounded-full bg-slate-100 flex items-center justify-center text-slate-600 font-bold text-xl">
                                {( user?.full_name || 'U' ).charAt(0).toUpperCase()}
                            </div>
                            <div>
                                <CardTitle className="text-xl">{user?.full_name}</CardTitle>
                                <div className="flex items-center gap-2 mt-1 flex-wrap">
                                    <Badge
                                        variant="outline"
                                        className={ROLE_CONFIG[ user?.role ]?.color}
                                    >
                                        {ROLE_CONFIG[ user?.role ]?.label || user?.role}
                                    </Badge>
                                    <Badge variant={user?.is_active ? 'default' : 'secondary'}>
                                        {user?.is_active ? 'Active' : 'Inactive'}
                                    </Badge>
                                    {user?.ec_position && (
                                        <Badge className="bg-amber-100 text-amber-800">
                                            {user.ec_position}
                                        </Badge>
                                    )}
                                </div>
                            </div>
                        </div>
                        <Button variant="outline" size="sm" onClick={fetchUser}>
                            <RefreshCw className="h-4 w-4"/>
                        </Button>
                    </div>
                </CardHeader>
                <CardContent>
                    <dl className="grid grid-cols-2 md:grid-cols-4 gap-4">
                        <ProfileField label="Email" value={user?.email}/>
                        <ProfileField label="Phone" value={user?.phone}/>
                        <ProfileField label="Organisation" value={user?.organisation}/>
                        <ProfileField label="Professional Licence" value={user?.professional_licence}/>
                        <ProfileField label="Status" value={user?.status}/>
                        <ProfileField label="Created" value={formatDate(user?.created_at)}/>
                        <ProfileField label="Last Login" value={formatDate(user?.last_login_at)}/>
                    </dl>
                </CardContent>
            </Card>

            {/* Buildings assigned */}
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                        <div>
                            <CardTitle className="text-base flex items-center gap-2">
                                <Building2 className="h-4 w-4 text-gray-500"/>
                                Building Assignments
                            </CardTitle>
                            <CardDescription className="text-xs mt-0.5">
                                Buildings this staff member has access to.
                            </CardDescription>
                        </div>
                    </div>
                </CardHeader>
                <CardContent>
                    {user?.buildings?.length === 0 ? (
                        <p className="text-sm text-gray-400 italic">
                            No buildings assigned yet. Assign a role below to add building access.
                        </p>
                    ) : (
                        <div className="flex flex-wrap gap-2">
                            {user.buildings.map(b => (
                                <Badge key={b.id} variant="outline" className="text-sm py-1 px-3">
                                    <Building2 className="h-3 w-3 mr-1.5"/>
                                    {b.name}
                                </Badge>
                            ))}
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Role assignments */}
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-center justify-between">
                        <div>
                            <CardTitle className="text-base flex items-center gap-2">
                                <Shield className="h-4 w-4 text-gray-500"/>
                                Elevated Role Assignments
                            </CardTitle>
                            <CardDescription className="text-xs mt-0.5">
                                Additional roles granted per building. Without these, the staff member
                                has their standard professional access only.
                            </CardDescription>
                        </div>
                        <Button size="sm" onClick={() => {
                            setAssignError(null);
                            setAssignSuccess(null);
                            setAssignForm({role_name: '', building_id: '', start_date: '', end_date: '', notes: ''});
                            setShowAssignModal(true);
                        }}>
                            <PlusCircle className="h-4 w-4 mr-1"/>
                            Assign Role
                        </Button>
                    </div>
                </CardHeader>
                <CardContent>
                    {user?.role_assignments?.length === 0 ? (
                        <div className="text-center py-8">
                            <Shield className="h-8 w-8 mx-auto text-gray-200 mb-2"/>
                            <p className="text-sm text-gray-400">
                                No elevated roles. This staff member operates with their base professional access.
                            </p>
                            <Button
                                variant="outline"
                                size="sm"
                                className="mt-3"
                                onClick={() => {
                                    setAssignError(null);
                                    setAssignSuccess(null);
                                    setShowAssignModal(true);
                                }}
                            >
                                <PlusCircle className="h-4 w-4 mr-1"/>
                                Assign First Role
                            </Button>
                        </div>
                    ) : (
                        <div className="space-y-4">
                            {Object.entries(assignmentsByBuilding).map(([bid, assignments]) => (
                                <div key={bid}>
                                    <div className="flex items-center gap-2 mb-2">
                                        <Building2 className="h-4 w-4 text-gray-400"/>
                                        <span className="text-sm font-medium text-gray-700">
                                            {bid === '__unscoped__' ? 'Unscoped' : buildingName(bid)}
                                        </span>
                                    </div>
                                    <div className="space-y-2 pl-6">
                                        {assignments.map(ra => (
                                            <div
                                                key={ra.id}
                                                className="flex items-center justify-between bg-gray-50 rounded-lg px-4 py-2.5 border border-gray-100"
                                            >
                                                <div className="flex items-center gap-3">
                                                    <Badge className={
                                                        ELEVATABLE_ROLES.find(r => r.value === ra.role_name.toLowerCase())?.color || 'bg-gray-100'
                                                    }>
                                                        {ELEVATABLE_ROLES.find(r => r.value === ra.role_name.toLowerCase())?.label || ra.role_name}
                                                    </Badge>
                                                    <div className="text-xs text-gray-500 flex items-center gap-1">
                                                        <CalendarDays className="h-3 w-3"/>
                                                        {formatDate(ra.start_date)}
                                                        {ra.end_date && ` → ${formatDate(ra.end_date)}`}
                                                    </div>
                                                    {ra.notes && (
                                                        <span
                                                            className="text-xs text-gray-400 italic truncate max-w-32">
                                                            {ra.notes}
                                                        </span>
                                                    )}
                                                </div>
                                                <Button
                                                    variant="ghost"
                                                    size="sm"
                                                    className="text-red-500 hover:text-red-700 hover:bg-red-50 h-7 w-7 p-0"
                                                    onClick={() => setRevokingId(ra.id)}
                                                    disabled={revokeLoading}
                                                >
                                                    <Trash2 className="h-3.5 w-3.5"/>
                                                </Button>
                                            </div>
                                        ))}
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Assign Role Modal */}
            <Dialog open={showAssignModal} onOpenChange={setShowAssignModal}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <PlusCircle className="h-5 w-5 text-slate-600"/>
                            Assign Elevated Role
                        </DialogTitle>
                        <DialogDescription>
                            Grant <strong>{user?.full_name}</strong> an additional role for a specific building.
                            This does not change their base staff role. Chairperson is an EC appointment, not the
                            Strata Admin role.
                        </DialogDescription>
                    </DialogHeader>

                    <div className="space-y-4 py-2">
                        {assignError && (
                            <Alert variant="destructive" className="py-2">
                                <AlertCircle className="h-4 w-4"/>
                                <AlertDescription>{assignError}</AlertDescription>
                            </Alert>
                        )}
                        {assignSuccess && (
                            <Alert className="py-2 border-green-200 bg-green-50">
                                <CheckCircle2 className="h-4 w-4 text-green-600"/>
                                <AlertDescription className="text-green-700">{assignSuccess}</AlertDescription>
                            </Alert>
                        )}

                        <div className="space-y-1.5">
                            <Label>Role <span className="text-red-500">*</span></Label>
                            <Select
                                value={assignForm.role_name}
                                onValueChange={v => setAssignForm(f => ( {...f, role_name: v} ))}
                            >
                                <SelectTrigger>
                                    <SelectValue placeholder="Select a role…"/>
                                </SelectTrigger>
                                <SelectContent>
                                    {ELEVATABLE_ROLES.map(r => (
                                        <SelectItem key={r.value} value={r.value}>
                                            <div>
                                                <div className="font-medium">{r.label}</div>
                                                <div className="text-xs text-gray-400">{r.description}</div>
                                            </div>
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        <div className="space-y-1.5">
                            <Label>Building <span className="text-red-500">*</span></Label>
                            <Select
                                value={assignForm.building_id}
                                onValueChange={v => setAssignForm(f => ( {...f, building_id: v} ))}
                            >
                                <SelectTrigger>
                                    <SelectValue placeholder="Select a building…"/>
                                </SelectTrigger>
                                <SelectContent>
                                    {buildings.map(b => (
                                        <SelectItem key={b.id} value={b.id}>
                                            {b.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>

                        <div className="grid grid-cols-2 gap-3">
                            <div className="space-y-1.5">
                                <Label>Start Date</Label>
                                <Input
                                    type="date"
                                    value={assignForm.start_date}
                                    onChange={e => setAssignForm(f => ( {...f, start_date: e.target.value} ))}
                                />
                            </div>
                            <div className="space-y-1.5">
                                <Label>End Date</Label>
                                <Input
                                    type="date"
                                    value={assignForm.end_date}
                                    onChange={e => setAssignForm(f => ( {...f, end_date: e.target.value} ))}
                                    min={assignForm.start_date}
                                />
                            </div>
                        </div>

                        <div className="space-y-1.5">
                            <Label>Notes (optional)</Label>
                            <Input
                                placeholder="e.g. EC election result, AGM resolution…"
                                value={assignForm.notes}
                                onChange={e => setAssignForm(f => ( {...f, notes: e.target.value} ))}
                            />
                        </div>
                    </div>

                    <DialogFooter>
                        <Button variant="outline" onClick={() => setShowAssignModal(false)}>
                            Cancel
                        </Button>
                        <Button onClick={handleAssign} disabled={assigning || !!assignSuccess}>
                            {assigning ? (
                                <><Loader2 className="h-4 w-4 mr-1 animate-spin"/>Assigning…</>
                            ) : (
                                <><PlusCircle className="h-4 w-4 mr-1"/>Assign Role</>
                            )}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Revoke Confirmation Dialog */}
            <Dialog open={!!revokingId} onOpenChange={() => setRevokingId(null)}>
                <DialogContent className="sm:max-w-sm">
                    <DialogHeader>
                        <DialogTitle>Revoke Role Assignment?</DialogTitle>
                        <DialogDescription>
                            This will remove the role assignment. The record is preserved for audit.
                            The staff member's base role is not affected.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button variant="outline" onClick={() => setRevokingId(null)}>
                            Cancel
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={() => handleRevoke(revokingId)}
                            disabled={revokeLoading}
                        >
                            {revokeLoading ? (
                                <><Loader2 className="h-4 w-4 mr-1 animate-spin"/>Revoking…</>
                            ) : 'Revoke'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}
