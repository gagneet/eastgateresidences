// @featuretrace:user-management — User management page: list, filter, approve, archive, elevate users.
// Layer: frontend
// Data flow: UsersPage.jsx → GET /api/users?status=...&role=... → server.py:get_users →
//             db.memberships (building-scoped) ⋈ db.users (global) →
//             user_to_response() [utils/permissions.py] → UserResponse
//            Secondary (cutover): list_active_users_for_scheme() [db_postgres/repos/identity_repo.py]
// Related: backend/server.py (GET /users ~line 2177)
//           backend/utils/permissions.py (user_to_response — canonical display-name logic)
//           backend/services/owner_service.py (_get_all_unit_owners — full_name fallback)
//           backend/models/user.py (UserResponse, UserCreate)
// Toggle: (none — always visible to can_manage_users roles)
// Collection: memberships, users (global)
// Table: core.users, core.user_units
// Tests: tests/backend/test_users.py
// ⚠️ KNOWN DATA RISK: full_name field MUST be set at write time.
//    user_to_response() falls back to "first_name last_name" when full_name is blank,
//    but the DB field should always be populated. Use the data repair script if missing:
//    scripts/data_repair/repair_user_full_names.py

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Badge } from '../../components/ui/badge';
import { Avatar, AvatarFallback } from '../../components/ui/avatar';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import { Tabs, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow, } from '../../components/ui/table';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '../../components/ui/dropdown-menu';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '../../components/ui/dialog';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import {
    AlertTriangle,
    Archive,
    ArrowDownCircle,
    ArrowUpCircle,
    Building,
    CheckCircle,
    Copy,
    HelpCircle,
    Info,
    Mail,
    MoreHorizontal,
    Pencil,
    Search,
    Send,
    Shield,
    UserCheck,
    UserPlus,
    Users,
    UserX,
    XCircle,
} from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger, } from '../../components/ui/tooltip';
import { formatDate, getInitials, roleDisplayNames } from '../../lib/utils';
import { toast } from 'sonner';

// ─── Constants ──────────────────────────────────────────────────────────────

// Roles that already carry elevated access and therefore cannot be granted a
// TEMPORARY elevation. This is an authorisation set, not a tab grouping — the two
// happened to share a constant until 2026-08-27 and must not be merged again:
// narrowing the tab grouping while this stayed joined to it would have made
// strata_admin, strata_manager and admin_staff elevatable.
const MANAGEMENT_ROLES = ['super_admin', 'strata_admin', 'ec_member', 'strata_manager', 'admin_staff'];

// Tab groupings answer a different question: who does this person act FOR?
// GOVERNANCE is the owners corporation's own side — the committee it elects and
// the platform operator. SERVICE is everyone engaged to service the building,
// which includes the strata management company's own staff (strata_admin,
// strata_manager, admin_staff) alongside external providers. A strata manager is
// a supplier to the owners corporation, not a member of it.
const GOVERNANCE_ROLES = ['super_admin', 'ec_member'];
const STRATA_SERVICE_ROLES = ['strata_admin', 'strata_manager', 'admin_staff', 'service_provider'];

const ROLE_GROUPS = [
    {key: 'management', label: 'Management', roles: GOVERNANCE_ROLES},
    {key: 'owners', label: 'Owners', roles: ['owner']},
    {key: 'residents', label: 'Tenants & Guests', roles: ['tenant', 'guest']},
    {key: 'agents', label: 'Real Estate Agents', roles: ['real_estate_agent']},
    {key: 'service', label: 'Service', roles: STRATA_SERVICE_ROLES},
];

const ELEVATION_DURATIONS = [
    {value: 1, label: '1 day'},
    {value: 2, label: '2 days'},
    {value: 3, label: '3 days'},
    {value: 5, label: '5 days (maximum)'},
];

const REQUEST_INFO_REASONS = [
    {value: 'wrong_unit', label: 'Wrong Unit Entered'},
    {value: 'wrong_user_type', label: 'Wrong User Type Selected'},
];

const ARCHIVE_REASONS = [
    {value: 'no_longer_active', label: 'No Longer Active (moved out)'},
    {value: 'superseded_by_owner', label: 'Superseded by New Owner'},
    {value: 'superseded_by_tenant', label: 'Superseded by New Tenant'},
    {value: 'other', label: 'Other'},
];

const STATUS_FILTERS = [
    {value: 'default', label: 'Active & Pending'},
    {value: 'pending_owner_approval', label: 'Awaiting Owner Approval'},
    {value: 'info_requested', label: 'Info Requested'},
    {value: 'archived', label: 'Archived (Former Owners/Tenants)'},
];

const ARCHIVE_REASON_LABELS = {
    owner_transfer_complete: 'Ownership transferred',
    deleted_by_admin: 'Removed by admin',
    archived_by_admin: 'Archived by admin',
    no_longer_active: 'No longer active',
    superseded_by_owner: 'Superseded by new owner',
    superseded_by_tenant: 'Superseded by new tenant',
};
/**
 * @generated FunctionHeader
 * Function: normaliseName
 * Path: frontend/src/pages/dashboard/UsersPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const normaliseName = (name) => (
    ( name || '' )
        .toLowerCase()
        .replace(/[^a-z\s]/g, ' ')
        .split(/\s+/)
        .filter(Boolean)
);
/**
 * @generated FunctionHeader
 * Function: isSimilarName
 * Path: frontend/src/pages/dashboard/UsersPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const isSimilarName = (a, b) => {
    if (!a || !b) return true;
    const tokensA = normaliseName(a);
    const tokensB = normaliseName(b);
    if (tokensA.length === 0 || tokensB.length === 0) return true;
    const sortedA = [...tokensA].sort().join(' ');
    const sortedB = [...tokensB].sort().join(' ');
    if (sortedA === sortedB) return true;
    const setB = new Set(tokensB);
    const overlap = tokensA.filter(t => setB.has(t)).length;
    const minLen = Math.min(new Set(tokensA).size, setB.size);
    return overlap >= Math.ceil(minLen * 0.6);
};
/**
 * @generated FunctionHeader
 * Function: UsersPage
 * Path: frontend/src/pages/dashboard/UsersPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const UsersPage = () => {
    const {api, user: currentUser, isAdmin, hasFeatureAccess, hasPermission} = useAuth();
    const ownerNameVerificationEnabled = hasFeatureAccess('owner_name_verification');
    const canManageUsers = hasPermission('can_manage_users');
    const canManageRoles = isAdmin();
    // Issuing a resident sign-up link provisions building access, so the backend
    // (_can_send_resident_invite) restricts it to these three roles — narrower
    // than can_manage_users, which ec_member and admin_staff also hold. Gating the
    // button on can_manage_users would show it to users the API then 403s.
    //
    // `effective_role ?? role` mirrors the convention used across the app
    // (DashboardLayout, RequestsPage, …). In practice `role` alone is sufficient:
    // user_to_response() already collapses any temporary elevation into `role`
    // (utils/permissions.py), and UserResponse carries no effective_role field.
    // The first operand is kept for consistency with those call sites, not because
    // it is expected to be populated.
    const canSendResidentInvite = ['super_admin', 'strata_admin', 'strata_manager']
        .includes(currentUser?.effective_role || currentUser?.role);
    const [users, setUsers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [searchTerm, setSearchTerm] = useState('');
    const [groupTab, setGroupTab] = useState('management');
    const [statusFilter, setStatusFilter] = useState('default');
    const [unitOwnerMap, setUnitOwnerMap] = useState({});
    const [inviteUnits, setInviteUnits] = useState([]);

    // Dialog states
    const [requestInfoDialog, setRequestInfoDialog] = useState({open: false, user: null});
    const [requestInfoReason, setRequestInfoReason] = useState('wrong_unit');
    const [requestInfoSubmitting, setRequestInfoSubmitting] = useState(false);
    const [requestProfileDialog, setRequestProfileDialog] = useState({open: false, user: null});
    const [requestProfileSubmitting, setRequestProfileSubmitting] = useState(false);
    const [archiveDialog, setArchiveDialog] = useState({open: false, user: null});
    const [archiveReason, setArchiveReason] = useState('no_longer_active');
    const [archiveSubmitting, setArchiveSubmitting] = useState(false);
    const [elevateDialog, setElevateDialog] = useState({open: false, user: null});
    const [elevationDays, setElevationDays] = useState(1);
    const [elevationSubmitting, setElevationSubmitting] = useState(false);
    const [ownerDecisionDialog, setOwnerDecisionDialog] = useState({
        open: false,
        userId: null,
        userName: '',
        action: 'reject',
        notes: ''
    });
    const [ownerDecisionSubmitting, setOwnerDecisionSubmitting] = useState(false);
    const [residentInviteDialog, setResidentInviteDialog] = useState({open: false});
    const [residentInviteSubmitting, setResidentInviteSubmitting] = useState(false);
    const [residentInviteResult, setResidentInviteResult] = useState(null);
    const [residentInviteForm, setResidentInviteForm] = useState({
        role: 'owner',
        unit_number: '',
        full_name: '',
        email: '',
        phone: '',
        note: '',
        expires_days: 14,
    });

    // Edit user details dialog (super_admin / strata_manager with can_manage_users)
    const [editUserDialog, setEditUserDialog] = useState({open: false, user: null});
    const [editUserForm, setEditUserForm] = useState({
        full_name: '', first_name: '', last_name: '', email: '', phone: '', phone_mobile: '', unit_number: ''
    });
    const [editUserSubmitting, setEditUserSubmitting] = useState(false);

    const fetchUsers = useCallback(async () => {
        try {
            const params = new URLSearchParams();
            if (statusFilter !== 'default') params.set('status', statusFilter);
            const response = await api.get(`/users?${params.toString()}`);
            setUsers(response.data);
        } catch (error) {
            console.error('Failed to fetch users:', error);
            toast.error('Failed to load users');
        } finally {
            setLoading(false);
        }
    }, [api, statusFilter]);

    useEffect(() => {
        fetchUsers();
    }, [fetchUsers]);

    useEffect(() => {
        if (typeof window === 'undefined') return;
        const params = new URLSearchParams(window.location.search);
        const tab = params.get('tab');
        if (tab && ROLE_GROUPS.some(g => g.key === tab)) {
            setGroupTab(tab);
        }
        const search = params.get('search');
        if (search) {
            setSearchTerm(search);
        }
        const status = params.get('status');
        if (status && STATUS_FILTERS.some(f => f.value === status)) {
            setStatusFilter(status);
        }
    }, []);

    const fetchUnitOwners = useCallback(async () => {
        try {
            const response = await api.get('/units?limit=200');
            const nextMap = {};
            ( response.data || [] ).forEach((unit) => {
                if (unit?.unit_number) {
                    nextMap[ unit.unit_number ] = {
                        primary: unit.owner_name || '',
                        secondary: unit.owner_name_b || '',
                    };
                }
            });
            setInviteUnits(response.data || []);
            setUnitOwnerMap(nextMap);
        } catch (error) {
            console.warn('Failed to load unit owner names:', error);
        }
    }, [api]);

    useEffect(() => {
        fetchUnitOwners();
    }, [fetchUnitOwners]);
    /**
     * @generated FunctionHeader
     * Function: updateUserRole
     * Path: frontend/src/pages/dashboard/UsersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateUserRole = async (userId, newRole) => {
        try {
            await api.put(`/users/${userId}`, {role: newRole});
            toast.success('User role updated');
            fetchUsers();
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Failed to update user role');
        }
    };

    const handleCreateResidentInvite = async () => {
        if (!residentInviteForm.unit_number || !residentInviteForm.full_name.trim()) {
            toast.error('Unit number and name are required');
            return;
        }
        setResidentInviteSubmitting(true);
        setResidentInviteResult(null);
        try {
            const payload = {
                ...residentInviteForm,
                full_name: residentInviteForm.full_name.trim(),
                email: residentInviteForm.email.trim() || undefined,
                phone: residentInviteForm.phone.trim() || undefined,
                note: residentInviteForm.note.trim() || undefined,
                expires_days: Number(residentInviteForm.expires_days) || 14,
            };
            const {data} = await api.post('/auth/registration-invites', payload);
            setResidentInviteResult(data);
            toast.success(data.email_sent ? 'Invite sent' : 'Invite link generated');
            if (data.invite_url && navigator?.clipboard) {
                await navigator.clipboard.writeText(data.invite_url).catch(() => {});
            }
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Failed to create invite');
        } finally {
            setResidentInviteSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: updateEcPosition
     * Path: frontend/src/pages/dashboard/UsersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateEcPosition = async (userId, ecPosition) => {
        try {
            await api.put(`/users/${userId}`, {ec_position: ecPosition});
            toast.success(ecPosition ? 'EC position updated' : 'EC position removed');
            fetchUsers();
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Failed to update EC position');
        }
    };

    /**
     * Who currently holds each single-holder EC office, keyed by position.
     *
     * CHAIRMAN, TREASURER and SECRETARY are offices — one person each. MEMBER is not:
     * a committee has several, so it is deliberately absent from this map and never
     * blocked.
     *
     * Without this the menu only disabled the position the CLICKED user already held,
     * so the same office could be handed to a second person and the first holder was
     * never mentioned. The two would then both render a Chairman badge, and nothing in
     * the UI said which one the backend would treat as chairman.
     */
    const ecOfficeHolders = React.useMemo(() => {
        const holders = {};
        (users || []).forEach(u => {
            const pos = u.ec_position;
            if (pos && pos !== 'MEMBER') holders[pos] = u;
        });
        return holders;
    }, [users]);
    /**
     * @generated FunctionHeader
     * Function: toggleUserStatus
     * Path: frontend/src/pages/dashboard/UsersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleUserStatus = async (userId, currentStatus) => {
        try {
            await api.put(`/users/${userId}`, {is_active: !currentStatus});
            toast.success(currentStatus ? 'User deactivated' : 'User activated');
            fetchUsers();
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Failed to update user status');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: approveUser
     * Path: frontend/src/pages/dashboard/UsersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const approveUser = async (userId) => {
        try {
            await api.put(`/users/${userId}`, {is_approved: true});
            toast.success('User approved successfully');
            fetchUsers();
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Failed to approve user');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: revokeApproval
     * Path: frontend/src/pages/dashboard/UsersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const revokeApproval = async (userId) => {
        try {
            await api.put(`/users/${userId}`, {is_approved: false});
            toast.success('Approval revoked');
            fetchUsers();
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Failed to revoke approval');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleElevateConfirm
     * Path: frontend/src/pages/dashboard/UsersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleElevateConfirm = async () => {
        if (!elevateDialog.user) return;
        setElevationSubmitting(true);
        try {
            await api.post(`/users/${elevateDialog.user.id}/elevate`, {duration_days: elevationDays});
            toast.success(`${elevateDialog.user.full_name} elevated to EC Member for ${elevationDays} day(s)`);
            setElevateDialog({open: false, user: null});
            fetchUsers();
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Failed to elevate user');
        } finally {
            setElevationSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleRevokeElevation
     * Path: frontend/src/pages/dashboard/UsersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRevokeElevation = async (userId, userName) => {
        try {
            await api.delete(`/users/${userId}/elevate`);
            toast.success(`Elevation revoked for ${userName}`);
            fetchUsers();
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Failed to revoke elevation');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleRequestInfoConfirm
     * Path: frontend/src/pages/dashboard/UsersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRequestInfoConfirm = async () => {
        if (!requestInfoDialog.user) return;
        setRequestInfoSubmitting(true);
        try {
            await api.post(`/users/${requestInfoDialog.user.id}/request-info`, {
                reason: requestInfoReason,
            });
            toast.success('Info request sent');
            setRequestInfoDialog({open: false, user: null});
            fetchUsers();
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Failed to send info request');
        } finally {
            setRequestInfoSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleRequestProfileConfirm
     * Path: frontend/src/pages/dashboard/UsersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRequestProfileConfirm = async () => {
        if (!requestProfileDialog.user) return;
        setRequestProfileSubmitting(true);
        try {
            await api.post(`/users/${requestProfileDialog.user.id}/request-profile-info`);
            toast.success('Profile info request sent');
            setRequestProfileDialog({open: false, user: null});
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Failed to send profile info request');
        } finally {
            setRequestProfileSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleArchiveConfirm
     * Path: frontend/src/pages/dashboard/UsersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleArchiveConfirm = async () => {
        if (!archiveDialog.user) return;
        setArchiveSubmitting(true);
        try {
            await api.post(`/users/${archiveDialog.user.id}/archive`, {
                reason: archiveReason,
            });
            toast.success('User archived');
            setArchiveDialog({open: false, user: null});
            fetchUsers();
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Failed to archive user');
        } finally {
            setArchiveSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleOwnerDecisionConfirm
     * Path: frontend/src/pages/dashboard/UsersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleOwnerDecisionConfirm = async () => {
        const {userId, action, notes} = ownerDecisionDialog;
        if (!userId) return;
        setOwnerDecisionSubmitting(true);
        try {
            await api.post(`/users/${userId}/owner-decision`, {action, notes: notes || undefined});
            toast.success(action === 'approve' ? 'Registration approved — admin activation required.' : 'Registration declined.');
            setOwnerDecisionDialog({open: false, userId: null, userName: '', action: 'reject', notes: ''});
            fetchUsers();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Action failed');
        } finally {
            setOwnerDecisionSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: openEditUserDialog
     * Path: frontend/src/pages/dashboard/UsersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openEditUserDialog = (u) => {
        setEditUserForm({
            full_name:    u.full_name    || '',
            first_name:   u.first_name  || '',
            last_name:    u.last_name   || '',
            email:        u.email       || '',
            phone:        u.phone       || '',
            phone_mobile: u.phone_mobile || '',
            unit_number:  u.unit_number || '',
        });
        setEditUserDialog({open: true, user: u});
    };
    /**
     * @generated FunctionHeader
     * Function: handleEditUserSave
     * Path: frontend/src/pages/dashboard/UsersPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleEditUserSave = async () => {
        const { user } = editUserDialog;
        if (!user) return;
        setEditUserSubmitting(true);
        try {
            // Only send changed fields — backend uses null-exclusion diff
            const payload = {};
            if (editUserForm.full_name   !== (user.full_name    || '')) payload.full_name    = editUserForm.full_name.trim()   || undefined;
            if (editUserForm.first_name  !== (user.first_name   || '')) payload.first_name   = editUserForm.first_name.trim()  || undefined;
            if (editUserForm.last_name   !== (user.last_name    || '')) payload.last_name    = editUserForm.last_name.trim()   || undefined;
            if (editUserForm.email       !== (user.email        || '')) payload.email        = editUserForm.email.trim()       || undefined;
            if (editUserForm.phone       !== (user.phone        || '')) payload.phone        = editUserForm.phone.trim()       || undefined;
            if (editUserForm.phone_mobile !== (user.phone_mobile || '')) payload.phone_mobile = editUserForm.phone_mobile.trim() || undefined;
            if (editUserForm.unit_number !== (user.unit_number  || '')) payload.unit_number  = editUserForm.unit_number.trim() || undefined;

            if (Object.keys(payload).length === 0) {
                toast('No changes detected.');
                setEditUserDialog({open: false, user: null});
                return;
            }
            await api.put(`/users/${user.id}`, payload);
            toast.success(`${editUserForm.full_name || user.full_name} — profile updated.`);
            setEditUserDialog({open: false, user: null});
            fetchUsers();
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to update user details');
        } finally {
            setEditUserSubmitting(false);
        }
    };

    // Count users per group for tab badges (unfiltered by search)
    // Management tab excludes elevated users — they remain in their original role tab
    const groupCounts = useMemo(() => {
        const counts = {};
        ROLE_GROUPS.forEach(g => {
            counts[ g.key ] = users.filter(u => {
                if (!g.roles.includes(u.role)) return false;
                if (g.key === 'management' && u.is_elevated) return false;
                return true;
            }).length;
        });
        return counts;
    }, [users]);

    const groupRoles = ROLE_GROUPS.find(g => g.key === groupTab)?.roles ?? [];

    const filteredUsers = users.filter(u => {
        // When a non-default status filter is active, show all matching users regardless of role group
        if (statusFilter === 'default') {
            if (!groupRoles.includes(u.role)) return false;
            if (groupTab === 'management' && u.is_elevated) return false;
        }
        const term = searchTerm.toLowerCase();
        if (!term) return true;
        return (
            ( u.full_name || '' ).toLowerCase().includes(term) ||
            ( u.email || '' ).toLowerCase().includes(term) ||
            ( u.unit_number || '' ).toLowerCase().includes(term)
        );
    });

    const roleStyles = {
        super_admin: 'bg-purple-50 text-purple-700 border-purple-200',
        strata_admin: 'bg-indigo-50 text-indigo-700 border-indigo-200',
        ec_member: 'bg-blue-50   text-blue-700   border-blue-200',
        strata_manager: 'bg-cyan-50   text-cyan-700   border-cyan-200',
        admin_staff: 'bg-teal-50   text-teal-700   border-teal-200',
        owner: 'bg-green-50  text-green-700  border-green-200',
        tenant: 'bg-amber-50  text-amber-700  border-amber-200',
        real_estate_agent: 'bg-rose-50   text-rose-700   border-rose-200',
        service_provider: 'bg-orange-50 text-orange-700 border-orange-200',
        guest: 'bg-slate-50  text-slate-700  border-slate-200',
    };

    return (
        <div className="container max-w-7xl py-8 space-y-8" data-testid="users-page">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                    <div className="flex items-center gap-3">
                        <h1 className="text-3xl font-bold tracking-tight">Resident Management</h1>
                        <TooltipProvider>
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Button variant="ghost" size="icon" className="h-6 w-6 rounded-full p-0">
                                        <Info className="h-5 w-5 text-indigo-500"/>
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent className="max-w-md p-4">
                                    <div className="space-y-2">
                                        <p className="font-bold text-sm">About Resident Management</p>
                                        <p className="text-xs leading-relaxed">
                                            This management console allows administrators to audit all registered
                                            residents, approve new membership requests, and control system-wide access
                                            permissions.
                                        </p>
                                        <p className="text-xs leading-relaxed">
                                            Use the <strong>Administrative Actions</strong> menu (ellipsis) to
                                            transition user roles, request profile updates, or archive records for
                                            residents who have moved out.
                                        </p>
                                    </div>
                                </TooltipContent>
                            </Tooltip>
                        </TooltipProvider>
                    </div>
                    <p className="text-muted-foreground mt-1 text-sm font-medium">Audit, approve, and manage community
                        access permissions.</p>
                </div>
                <div className="flex items-center gap-3">
                    {canSendResidentInvite && (
                        <Button
                            className="shadow-sm"
                            onClick={() => {
                                setResidentInviteResult(null);
                                setResidentInviteDialog({open: true});
                            }}
                        >
                            <Send className="mr-2 h-4 w-4"/> Send Sign-up Link
                        </Button>
                    )}
                    <Button variant="outline" className="shadow-sm"
                            onClick={() => window.location.href = '/admin/expired-accounts'}>
                        <Archive className="mr-2 h-4 w-4"/> Archived Records
                    </Button>
                </div>
            </div>

            <Card className="border-none shadow-xl bg-card/60 backdrop-blur-md overflow-hidden">
                <CardHeader className="bg-muted/30 pb-0 border-b">
                    {/* Role group tabs */}
                    <Tabs value={groupTab} onValueChange={setGroupTab} className="w-full">
                        <TabsList
                            className="w-full justify-start rounded-none border-b-0 bg-transparent h-12 px-0 gap-0">
                            {ROLE_GROUPS.map(g => (
                                <TabsTrigger
                                    key={g.key}
                                    value={g.key}
                                    className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none h-12 px-5 font-semibold text-sm"
                                >
                                    {g.label}
                                    <span
                                        className="ml-2 bg-muted text-muted-foreground rounded-full px-2 py-0.5 text-[10px] font-bold tabular-nums">
                    {groupCounts[ g.key ] ?? 0}
                  </span>
                                </TabsTrigger>
                            ))}
                        </TabsList>
                    </Tabs>
                    {/* Search + status filter row */}
                    <div className="flex flex-col lg:flex-row gap-4 lg:items-center justify-between py-4">
                        <div className="relative flex-1 max-w-md">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                            <Input
                                placeholder="Search by name, email or unit..."
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                                className="pl-10 bg-background/50 border-muted-foreground/20 focus:border-primary/50"
                            />
                        </div>
                        <div className="flex flex-wrap gap-3">
                            <Select value={statusFilter} onValueChange={setStatusFilter}>
                                <SelectTrigger className="w-[180px] bg-background/50">
                                    <SelectValue placeholder="Active & Pending"/>
                                </SelectTrigger>
                                <SelectContent>
                                    {STATUS_FILTERS.map(f => (
                                        <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>
                </CardHeader>

                <CardContent className="p-0">
                    {loading ? (
                        <div className="p-8 space-y-4">
                            {[1, 2, 3, 4].map(i => <div key={i}
                                                        className="h-16 w-full bg-muted/20 animate-pulse rounded-lg"/>)}
                        </div>
                    ) : filteredUsers.length === 0 ? (
                        <div className="py-20 text-center">
                            <div
                                className="bg-muted/20 h-16 w-16 rounded-full flex items-center justify-center mx-auto mb-4">
                                <Users className="h-8 w-8 text-muted-foreground/40"/>
                            </div>
                            <h3 className="text-lg font-medium">No residents found</h3>
                            <p className="text-muted-foreground">Try adjusting your search or filters.</p>
                        </div>
                    ) : (
                        <div className="overflow-x-auto">
                            <Table>
                                <TableHeader className="bg-muted/10">
                                    <TableRow>
                                        <TableHead className="py-4 pl-6">Resident</TableHead>
                                        <TableHead>Unit</TableHead>
                                        <TableHead>System Role</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Member Since</TableHead>
                                        <TableHead className="w-[80px]"></TableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {filteredUsers.map((u) => (
                                        <TableRow key={u.id}
                                                  className={`group hover:bg-muted/30 transition-colors${u.status === 'archived' ? ' opacity-60' : ''}`}>
                                            <TableCell className="py-4 pl-6">
                                                <div className="flex items-center gap-3">
                                                    <Avatar
                                                        className="h-10 w-10 border-2 border-background shadow-sm group-hover:scale-105 transition-transform">
                                                        <AvatarFallback
                                                            className="bg-primary/5 text-primary font-bold text-xs">
                                                            {getInitials(u.full_name)}
                                                        </AvatarFallback>
                                                    </Avatar>
                                                    <div>
                                                        <div className="flex items-center gap-2">
                                                            <p className="font-semibold text-sm leading-none">{u.full_name}</p>
                                                            {ownerNameVerificationEnabled && u.role === 'owner' && (
                                                                u.is_name_flagged ||
                                                                ( u.unit_number && unitOwnerMap[ u.unit_number ] &&
                                                                    !isSimilarName(u.full_name, unitOwnerMap[ u.unit_number ].primary) &&
                                                                    ( !unitOwnerMap[ u.unit_number ].secondary ||
                                                                        !isSimilarName(u.full_name, unitOwnerMap[ u.unit_number ].secondary) ) )
                                                            ) && (
                                                                <TooltipProvider>
                                                                    <Tooltip>
                                                                        <TooltipTrigger asChild>
                                                                            <AlertTriangle
                                                                                className="h-3.5 w-3.5 text-amber-600"/>
                                                                        </TooltipTrigger>
                                                                        <TooltipContent>
                                                                            <p className="text-xs">
                                                                                Owner name mismatch.
                                                                                {u.unit_number && unitOwnerMap[ u.unit_number ]
                                                                                    ? ` Strata roll: ${[
                                                                                        unitOwnerMap[ u.unit_number ].primary,
                                                                                        unitOwnerMap[ u.unit_number ].secondary,
                                                                                    ].filter(Boolean).join(' / ')}`
                                                                                    : ' Name does not match strata roll records.'}
                                                                            </p>
                                                                        </TooltipContent>
                                                                    </Tooltip>
                                                                </TooltipProvider>
                                                            )}
                                                        </div>
                                                        <p className="text-xs text-muted-foreground mt-1 flex items-center gap-1">
                                                            <Mail className="h-2.5 w-2.5"/> {u.email}
                                                        </p>
                                                    </div>
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <div className="flex items-center gap-1.5 font-medium text-sm">
                                                    <Building className="h-3.5 w-3.5 text-muted-foreground"/>
                                                    {u.unit_number || 'N/A'}
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <div className="flex flex-col gap-1">
                                                    <div className="flex items-center gap-1.5 flex-wrap">
                                                        <Badge variant="outline"
                                                               className={`font-semibold text-[10px] uppercase tracking-wider py-0.5 ${roleStyles[ u.role ] || ''}`}>
                                                            {roleDisplayNames[ u.role ] || u.role}
                                                        </Badge>
                                                        {/* MEMBER is rendered too: assigning it previously produced no
                                                            visible change at all, so the action looked like it had failed. */}
                                                        {u.ec_position && (
                                                            <Badge variant="secondary"
                                                                   className="text-[9px] uppercase tracking-wider py-0.5 bg-blue-50 text-blue-700 border-blue-200">
                                                                {u.ec_position}
                                                            </Badge>
                                                        )}
                                                    </div>
                                                    {u.is_elevated && u.temp_elevation && (
                                                        <div className="flex items-center gap-1">
                                                            <Badge variant="outline"
                                                                   className="font-semibold text-[10px] uppercase tracking-wider py-0.5 bg-orange-50 text-orange-700 border-orange-300 gap-1">
                                                                <ArrowUpCircle className="h-2.5 w-2.5"/>
                                                                Elevated
                                                            </Badge>
                                                            <span className="text-[9px] text-orange-600 font-medium">
                                until {new Date(u.temp_elevation.expires_at).toLocaleDateString('en-AU', {
                                                                day: 'numeric',
                                                                month: 'short'
                                                            })}
                              </span>
                                                        </div>
                                                    )}
                                                </div>
                                            </TableCell>
                                            <TableCell>
                                                <div className="flex items-center gap-2">
                                                    {u.status === 'archived' ? (
                                                        <div className="flex flex-col gap-0.5">
                                                            <Badge
                                                                className="bg-slate-400 hover:bg-slate-400 text-white border-none text-[10px] uppercase w-fit">Archived</Badge>
                                                            {u.archived_reason && (
                                                                <span
                                                                    className="text-[10px] text-muted-foreground leading-tight">
                                                                    {ARCHIVE_REASON_LABELS[ u.archived_reason ] || u.archived_reason}
                                                                    {u.archived_at && ` · ${formatDate(u.archived_at)}`}
                                                                </span>
                                                            )}
                                                        </div>
                                                    ) : u.status === 'pending_owner_approval' ? (
                                                        <Badge
                                                            className="bg-sky-500 hover:bg-sky-500 text-white border-none text-[10px] uppercase">Awaiting
                                                            Owner</Badge>
                                                    ) : !u.is_approved ? (
                                                        <Badge
                                                            className="bg-amber-500 hover:bg-amber-500 text-white border-none text-[10px] uppercase">Pending
                                                            Review</Badge>
                                                    ) : u.is_active ? (
                                                        <div className="flex items-center gap-1.5">
                                                            <span className="h-1.5 w-1.5 rounded-full bg-emerald-500"/>
                                                            <span
                                                                className="text-xs font-medium text-emerald-600">Active</span>
                                                        </div>
                                                    ) : (
                                                        <div className="flex items-center gap-1.5">
                                                            <span className="h-1.5 w-1.5 rounded-full bg-slate-300"/>
                                                            <span
                                                                className="text-xs font-medium text-slate-500">Deactivated</span>
                                                        </div>
                                                    )}
                                                </div>
                                            </TableCell>
                                            <TableCell className="text-xs text-muted-foreground">
                                                {formatDate(u.created_at)}
                                            </TableCell>
                                            <TableCell className="pr-6">
                                                {u.id !== currentUser?.id && canManageUsers && u.status !== 'archived' && (
                                                    <DropdownMenu>
                                                        <DropdownMenuTrigger asChild>
                                                            <Button variant="ghost" size="icon"
                                                                    className="h-8 w-8 hover:bg-muted group-hover:visible group-focus-visible:visible"
                                                                    aria-label="More actions">
                                                                <MoreHorizontal className="h-4 w-4"/>
                                                            </Button>
                                                        </DropdownMenuTrigger>
                                                        <DropdownMenuContent align="end" className="w-56">
                                                            <DropdownMenuLabel>Administrative
                                                                Actions</DropdownMenuLabel>
                                                            <DropdownMenuSeparator/>

                                                            {!u.is_approved ? (
                                                                <DropdownMenuItem onClick={() => approveUser(u.id)}
                                                                                  className="text-emerald-600 focus:text-emerald-600 focus:bg-emerald-50">
                                                                    <UserCheck className="mr-2 h-4 w-4"/> Approve
                                                                    Membership
                                                                </DropdownMenuItem>
                                                            ) : (
                                                                <DropdownMenuItem onClick={() => revokeApproval(u.id)}
                                                                                  className="text-amber-600">
                                                                    <UserX className="mr-2 h-4 w-4"/> Revoke Approval
                                                                </DropdownMenuItem>
                                                            )}

                                                            <DropdownMenuItem onClick={() => setRequestInfoDialog({
                                                                open: true,
                                                                user: u
                                                            })}>
                                                                <HelpCircle className="mr-2 h-4 w-4"/> Request Info
                                                                Change
                                                            </DropdownMenuItem>

                                                            <DropdownMenuItem onClick={() => setRequestProfileDialog({
                                                                open: true,
                                                                user: u
                                                            })}>
                                                                <UserPlus className="mr-2 h-4 w-4"/> Request Profile
                                                                Info
                                                            </DropdownMenuItem>

                                                            {canManageRoles && (
                                                                <>
                                                                    <DropdownMenuSeparator/>
                                                                    <DropdownMenuLabel
                                                                        className="text-[10px] uppercase text-muted-foreground">Transition
                                                                        Role</DropdownMenuLabel>
                                                                    {Object.keys(roleDisplayNames).map(role => (
                                                                        <DropdownMenuItem key={role}
                                                                                          onClick={() => updateUserRole(u.id, role)}
                                                                                          disabled={u.role === role}>
                                                                            <Shield
                                                                                className="mr-2 h-4 w-4"/> To {roleDisplayNames[ role ]}
                                                                        </DropdownMenuItem>
                                                                    ))}

                                                                    {['ec_member', 'strata_admin'].includes(u.role) && (
                                                                        <>
                                                                            <DropdownMenuSeparator/>
                                                                            <DropdownMenuLabel
                                                                                className="text-[10px] uppercase text-muted-foreground">EC
                                                                                Position</DropdownMenuLabel>
                                                                            {[
                                                                                {key: 'CHAIRMAN', label: 'Chairman'},
                                                                                {key: 'TREASURER', label: 'Treasurer'},
                                                                                {key: 'SECRETARY', label: 'Secretary'},
                                                                                {key: 'MEMBER', label: 'Member'},
                                                                            ].map(pos => {
                                                                                const holder = ecOfficeHolders[pos.key];
                                                                                const heldByOther = holder && holder.id !== u.id;
                                                                                return (
                                                                                    <DropdownMenuItem
                                                                                        key={pos.key}
                                                                                        onClick={() => updateEcPosition(u.id, pos.key)}
                                                                                        disabled={u.ec_position === pos.key || !!heldByOther}
                                                                                        title={heldByOther
                                                                                            ? `Held by ${holder.full_name} — remove it from them first`
                                                                                            : undefined}>
                                                                                        <Shield className="mr-2 h-4 w-4"/>
                                                                                        <span className="flex-1">{pos.label}</span>
                                                                                        {heldByOther && (
                                                                                            <span className="ml-2 text-[10px] text-muted-foreground truncate max-w-[9rem]">
                                                                                                {holder.full_name}
                                                                                            </span>
                                                                                        )}
                                                                                    </DropdownMenuItem>
                                                                                );
                                                                            })}
                                                                            {u.ec_position && (
                                                                                <DropdownMenuItem
                                                                                    onClick={() => updateEcPosition(u.id, null)}
                                                                                    className="text-destructive focus:text-destructive">
                                                                                    <Shield className="mr-2 h-4 w-4"/>
                                                                                    Remove position
                                                                                </DropdownMenuItem>
                                                                            )}
                                                                        </>
                                                                    )}
                                                                </>
                                                            )}

                                                            {currentUser?.role === 'super_admin' && !MANAGEMENT_ROLES.includes(u.role) && (
                                                                <>
                                                                    <DropdownMenuSeparator/>
                                                                    <DropdownMenuLabel
                                                                        className="text-[10px] uppercase text-muted-foreground">Temporary
                                                                        Elevation</DropdownMenuLabel>
                                                                    {u.is_elevated ? (
                                                                        <DropdownMenuItem
                                                                            onClick={() => handleRevokeElevation(u.id, u.full_name)}
                                                                            className="text-orange-600 focus:text-orange-600 focus:bg-orange-50"
                                                                        >
                                                                            <ArrowDownCircle
                                                                                className="mr-2 h-4 w-4"/> Revoke EC
                                                                            Elevation
                                                                        </DropdownMenuItem>
                                                                    ) : (
                                                                        <DropdownMenuItem
                                                                            onClick={() => {
                                                                                setElevationDays(1);
                                                                                setElevateDialog({open: true, user: u});
                                                                            }}
                                                                            className="text-orange-600 focus:text-orange-600 focus:bg-orange-50"
                                                                        >
                                                                            <ArrowUpCircle
                                                                                className="mr-2 h-4 w-4"/> Elevate to EC
                                                                            Member
                                                                        </DropdownMenuItem>
                                                                    )}
                                                                </>
                                                            )}

                                                            {canManageUsers && u.status === 'pending_owner_approval' && (
                                                                <>
                                                                    <DropdownMenuSeparator/>
                                                                    <DropdownMenuLabel
                                                                        className="text-[10px] uppercase text-muted-foreground">Owner
                                                                        Override</DropdownMenuLabel>
                                                                    <DropdownMenuItem
                                                                        onClick={() => setOwnerDecisionDialog({
                                                                            open: true,
                                                                            userId: u.id,
                                                                            userName: u.full_name,
                                                                            action: 'approve',
                                                                            notes: ''
                                                                        })}
                                                                        className="text-emerald-600 focus:text-emerald-600 focus:bg-emerald-50"
                                                                    >
                                                                        <CheckCircle className="mr-2 h-4 w-4"/> Approve
                                                                        (bypass owner)
                                                                    </DropdownMenuItem>
                                                                    <DropdownMenuItem
                                                                        onClick={() => setOwnerDecisionDialog({
                                                                            open: true,
                                                                            userId: u.id,
                                                                            userName: u.full_name,
                                                                            action: 'reject',
                                                                            notes: ''
                                                                        })}
                                                                        className="text-red-600 focus:text-red-600 focus:bg-red-50"
                                                                    >
                                                                        <XCircle className="mr-2 h-4 w-4"/> Reject
                                                                        (bypass owner)
                                                                    </DropdownMenuItem>
                                                                </>
                                                            )}

                                                            {canManageUsers && (
                                                                <>
                                                                    <DropdownMenuSeparator/>
                                                                    <DropdownMenuItem
                                                                        data-testid={`edit-user-${u.id}`}
                                                                        onClick={() => openEditUserDialog(u)}
                                                                    >
                                                                        <Pencil className="mr-2 h-4 w-4"/> Edit Details
                                                                    </DropdownMenuItem>
                                                                </>
                                                            )}

                                                            <DropdownMenuSeparator/>
                                                            <DropdownMenuItem
                                                                onClick={() => setArchiveDialog({open: true, user: u})}
                                                                className="text-destructive focus:bg-destructive/10">
                                                                <Archive className="mr-2 h-4 w-4"/> Archive Account
                                                            </DropdownMenuItem>
                                                        </DropdownMenuContent>
                                                    </DropdownMenu>
                                                )}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Edit User Details — super_admin / strata_manager (can_manage_users) */}
            <Dialog open={editUserDialog.open}
                    onOpenChange={(open) => !open && setEditUserDialog({open: false, user: null})}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <Pencil className="h-4 w-4"/> Edit User Details
                        </DialogTitle>
                        <DialogDescription>
                            Update profile fields for <strong>{editUserDialog.user?.email}</strong>.
                            Changes take effect immediately.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="grid grid-cols-2 gap-4 py-4">
                        <div className="space-y-1.5">
                            <Label htmlFor="edit-first-name">First Name</Label>
                            <Input
                                id="edit-first-name"
                                value={editUserForm.first_name}
                                onChange={e => setEditUserForm(f => ({...f, first_name: e.target.value}))}
                                placeholder="First name"
                            />
                        </div>
                        <div className="space-y-1.5">
                            <Label htmlFor="edit-last-name">Last Name</Label>
                            <Input
                                id="edit-last-name"
                                value={editUserForm.last_name}
                                onChange={e => setEditUserForm(f => ({...f, last_name: e.target.value}))}
                                placeholder="Last name"
                            />
                        </div>
                        <div className="col-span-2 space-y-1.5">
                            <Label htmlFor="edit-full-name">
                                Display Name <span className="text-muted-foreground text-xs">(overrides first+last)</span>
                            </Label>
                            <Input
                                id="edit-full-name"
                                value={editUserForm.full_name}
                                onChange={e => setEditUserForm(f => ({...f, full_name: e.target.value}))}
                                placeholder="Full display name"
                            />
                        </div>
                        <div className="col-span-2 space-y-1.5">
                            <Label htmlFor="edit-email">Email Address</Label>
                            <Input
                                id="edit-email"
                                type="email"
                                value={editUserForm.email}
                                onChange={e => setEditUserForm(f => ({...f, email: e.target.value}))}
                                placeholder="email@example.com"
                            />
                        </div>
                        <div className="space-y-1.5">
                            <Label htmlFor="edit-phone">Phone</Label>
                            <Input
                                id="edit-phone"
                                value={editUserForm.phone}
                                onChange={e => setEditUserForm(f => ({...f, phone: e.target.value}))}
                                placeholder="+61 4xx xxx xxx"
                            />
                        </div>
                        <div className="space-y-1.5">
                            <Label htmlFor="edit-phone-mobile">Mobile</Label>
                            <Input
                                id="edit-phone-mobile"
                                value={editUserForm.phone_mobile}
                                onChange={e => setEditUserForm(f => ({...f, phone_mobile: e.target.value}))}
                                placeholder="+61 4xx xxx xxx"
                            />
                        </div>
                        <div className="col-span-2 space-y-1.5">
                            <Label htmlFor="edit-unit">Unit Number</Label>
                            <Input
                                id="edit-unit"
                                value={editUserForm.unit_number}
                                onChange={e => setEditUserForm(f => ({...f, unit_number: e.target.value}))}
                                placeholder="e.g. TH042"
                            />
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="ghost"
                                onClick={() => setEditUserDialog({open: false, user: null})}>
                            Cancel
                        </Button>
                        <Button
                            onClick={handleEditUserSave}
                            disabled={editUserSubmitting}
                            data-testid="edit-user-save"
                        >
                            {editUserSubmitting ? 'Saving...' : 'Save Changes'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={residentInviteDialog.open}
                    onOpenChange={(open) => {
                        if (!open) {
                            setResidentInviteDialog({open: false});
                            setResidentInviteResult(null);
                        }
                    }}>
                <DialogContent className="sm:max-w-lg">
                    <DialogHeader>
                        <DialogTitle>Send Building Sign-up Link</DialogTitle>
                        <DialogDescription>
                            Generate a building-specific registration link with the unit and resident type prefilled.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 py-2">
                        <div className="space-y-1.5">
                            <Label>Resident Type</Label>
                            <Select
                                value={residentInviteForm.role}
                                onValueChange={value => setResidentInviteForm(f => ({...f, role: value}))}
                            >
                                <SelectTrigger><SelectValue/></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="owner">Owner</SelectItem>
                                    <SelectItem value="tenant">Tenant</SelectItem>
                                    <SelectItem value="guest">Guest</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="space-y-1.5">
                            <Label>Unit Number</Label>
                            <Select
                                value={residentInviteForm.unit_number}
                                onValueChange={value => setResidentInviteForm(f => ({...f, unit_number: value}))}
                            >
                                <SelectTrigger><SelectValue placeholder="Select unit"/></SelectTrigger>
                                <SelectContent className="max-h-72">
                                    {inviteUnits.map(unit => (
                                        <SelectItem key={unit.unit_number} value={unit.unit_number}>
                                            Unit {unit.unit_number}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="sm:col-span-2 space-y-1.5">
                            <Label>Name</Label>
                            <Input
                                value={residentInviteForm.full_name}
                                onChange={e => setResidentInviteForm(f => ({...f, full_name: e.target.value}))}
                                placeholder="Resident name"
                            />
                        </div>
                        <div className="space-y-1.5">
                            <Label>Email</Label>
                            <Input
                                type="email"
                                value={residentInviteForm.email}
                                onChange={e => setResidentInviteForm(f => ({...f, email: e.target.value}))}
                                placeholder="name@example.com"
                            />
                        </div>
                        <div className="space-y-1.5">
                            <Label>Phone</Label>
                            <Input
                                value={residentInviteForm.phone}
                                onChange={e => setResidentInviteForm(f => ({...f, phone: e.target.value}))}
                                placeholder="+61 4xx xxx xxx"
                            />
                        </div>
                        <div className="space-y-1.5">
                            <Label>Expires</Label>
                            <Select
                                value={String(residentInviteForm.expires_days)}
                                onValueChange={value => setResidentInviteForm(f => ({
                                    ...f,
                                    expires_days: Number(value)
                                }))}
                            >
                                <SelectTrigger><SelectValue/></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="7">7 days</SelectItem>
                                    <SelectItem value="14">14 days</SelectItem>
                                    <SelectItem value="30">30 days</SelectItem>
                                    <SelectItem value="60">60 days</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="sm:col-span-2 space-y-1.5">
                            <Label>Internal Note</Label>
                            <Textarea
                                value={residentInviteForm.note}
                                onChange={e => setResidentInviteForm(f => ({...f, note: e.target.value}))}
                                placeholder="Optional note for audit context"
                                rows={3}
                            />
                        </div>
                        {residentInviteResult?.invite_url && (
                            <div className="sm:col-span-2 rounded-lg border bg-muted/30 p-3 space-y-2">
                                <Label>Sign-up Link</Label>
                                <div className="flex gap-2">
                                    <Input readOnly value={residentInviteResult.invite_url}/>
                                    <Button
                                        type="button"
                                        variant="outline"
                                        size="icon"
                                        aria-label="Copy sign-up link"
                                        onClick={() => {
                                            navigator?.clipboard?.writeText(residentInviteResult.invite_url);
                                            toast.success('Link copied');
                                        }}
                                    >
                                        <Copy className="h-4 w-4"/>
                                    </Button>
                                </div>
                            </div>
                        )}
                    </div>
                    <DialogFooter>
                        <Button variant="ghost"
                                onClick={() => setResidentInviteDialog({open: false})}>
                            Close
                        </Button>
                        <Button onClick={handleCreateResidentInvite} disabled={residentInviteSubmitting}>
                            {residentInviteSubmitting ? 'Creating...' : 'Create Link'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Reusable Dialogs for Actions */}
            <Dialog open={requestInfoDialog.open}
                    onOpenChange={(open) => !open && setRequestInfoDialog({open: false, user: null})}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>Request Info Change</DialogTitle>
                        <DialogDescription>
                            Prompt <strong>{requestInfoDialog.user?.full_name}</strong> to correct their registration
                            details.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-4">
                        <div className="space-y-2">
                            <Label>Reason for update request</Label>
                            <Select value={requestInfoReason} onValueChange={setRequestInfoReason}>
                                <SelectTrigger><SelectValue/></SelectTrigger>
                                <SelectContent>
                                    {REQUEST_INFO_REASONS.map(r => <SelectItem key={r.value}
                                                                               value={r.value}>{r.label}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="ghost"
                                onClick={() => setRequestInfoDialog({open: false, user: null})}>Cancel</Button>
                        <Button onClick={handleRequestInfoConfirm} disabled={requestInfoSubmitting}>
                            {requestInfoSubmitting ? 'Sending...' : 'Send Notification'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={requestProfileDialog.open}
                    onOpenChange={(open) => !open && setRequestProfileDialog({open: false, user: null})}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>Request Profile Info</DialogTitle>
                        <DialogDescription>
                            Ask <strong>{requestProfileDialog.user?.full_name}</strong> to provide more details in their
                            profile.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-4">
                        <div className="rounded-lg border bg-muted/30 p-3 text-xs text-muted-foreground">
                            This sends an email and a bell notification with a link to their profile page.
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="ghost"
                                onClick={() => setRequestProfileDialog({open: false, user: null})}>Cancel</Button>
                        <Button onClick={handleRequestProfileConfirm} disabled={requestProfileSubmitting}>
                            {requestProfileSubmitting ? 'Sending...' : 'Send Request'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            <Dialog open={archiveDialog.open}
                    onOpenChange={(open) => !open && setArchiveDialog({open: false, user: null})}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle className="text-destructive">Archive Resident Record</DialogTitle>
                        <DialogDescription>
                            Archive <strong>{archiveDialog.user?.full_name}</strong>. They will lose access to the
                            portal immediately.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-4">
                        <div className="space-y-2">
                            <Label>Removal Reason</Label>
                            <Select value={archiveReason} onValueChange={setArchiveReason}>
                                <SelectTrigger><SelectValue/></SelectTrigger>
                                <SelectContent>
                                    {ARCHIVE_REASONS.map(r => <SelectItem key={r.value}
                                                                          value={r.value}>{r.label}</SelectItem>)}
                                </SelectContent>
                            </Select>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="ghost"
                                onClick={() => setArchiveDialog({open: false, user: null})}>Cancel</Button>
                        <Button variant="destructive" onClick={handleArchiveConfirm} disabled={archiveSubmitting}>
                            {archiveSubmitting ? 'Archiving...' : 'Archive Resident'}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Temporary Elevation Dialog */}
            <Dialog open={elevateDialog.open}
                    onOpenChange={(open) => !open && setElevateDialog({open: false, user: null})}>
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle className="flex items-center gap-2">
                            <ArrowUpCircle className="h-5 w-5 text-orange-500"/>
                            Temporary EC Member Elevation
                        </DialogTitle>
                        <DialogDescription>
                            Grant <strong>{elevateDialog.user?.full_name}</strong> temporary EC Member access.
                            Their actual role stays unchanged — they will appear as <em>Elevated</em> in the system.
                        </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4 py-4">
                        <div className="space-y-2">
                            <Label>Duration</Label>
                            <Select value={String(elevationDays)} onValueChange={(v) => setElevationDays(Number(v))}>
                                <SelectTrigger><SelectValue/></SelectTrigger>
                                <SelectContent>
                                    {ELEVATION_DURATIONS.map(d => (
                                        <SelectItem key={d.value} value={String(d.value)}>{d.label}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                        </div>
                        <div
                            className="rounded-lg bg-orange-50 border border-orange-200 p-3 text-xs text-orange-700 space-y-1">
                            <p className="font-semibold">What this grants:</p>
                            <ul className="list-disc list-inside space-y-0.5 text-orange-600">
                                <li>EC Member-level permissions for {elevationDays} day(s)</li>
                                <li>Access automatically expires — no action needed</li>
                                <li>User is <strong>not</strong> listed under Management tab</li>
                                <li>You can revoke at any time from this menu</li>
                            </ul>
                        </div>
                    </div>
                    <DialogFooter>
                        <Button variant="ghost"
                                onClick={() => setElevateDialog({open: false, user: null})}>Cancel</Button>
                        <Button
                            className="bg-orange-500 hover:bg-orange-600 text-white"
                            onClick={handleElevateConfirm}
                            disabled={elevationSubmitting}
                        >
                            {elevationSubmitting ? 'Elevating...' : `Elevate for ${elevationDays} day(s)`}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>

            {/* Owner Override Decision Dialog */}
            <Dialog
                open={ownerDecisionDialog.open}
                onOpenChange={(open) => !open && setOwnerDecisionDialog({
                    open: false,
                    userId: null,
                    userName: '',
                    action: 'reject',
                    notes: ''
                })}
            >
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle
                            className={`flex items-center gap-2 ${ownerDecisionDialog.action === 'approve' ? 'text-emerald-700' : 'text-red-700'}`}>
                            {ownerDecisionDialog.action === 'approve'
                                ? <><CheckCircle className="h-5 w-5"/> Approve Registration (Owner Bypass)</>
                                : <><XCircle className="h-5 w-5"/> Reject Registration (Owner Bypass)</>
                            }
                        </DialogTitle>
                        <DialogDescription>
                            You are acting as Strata Manager
                            to {ownerDecisionDialog.action === 'approve' ? 'approve' : 'reject'}
                            <strong>{ownerDecisionDialog.userName}</strong>'s registration, bypassing the unit owner
                            approval step.
                        </DialogDescription>
                    </DialogHeader>
                    {ownerDecisionDialog.action === 'approve' ? (
                        <div
                            className="bg-emerald-50 border border-emerald-200 rounded-lg p-4 text-sm text-emerald-800">
                            After approval the account will still require standard admin activation (is_approved). The
                            unit owner will be informed.
                        </div>
                    ) : (
                        <div className="space-y-3">
                            <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-800">
                                The registration will be declined and the applicant will be notified.
                            </div>
                            <div>
                                <label className="text-sm font-medium text-slate-700 mb-1 block">Reason
                                    (optional)</label>
                                <textarea
                                    className="w-full border border-slate-200 rounded-md p-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-primary"
                                    rows={3}
                                    placeholder="e.g. Owner was contacted and did not authorise this person."
                                    value={ownerDecisionDialog.notes}
                                    onChange={(e) => setOwnerDecisionDialog(d => ( {...d, notes: e.target.value} ))}
                                />
                            </div>
                        </div>
                    )}
                    <DialogFooter>
                        <Button
                            variant="ghost"
                            onClick={() => setOwnerDecisionDialog({
                                open: false,
                                userId: null,
                                userName: '',
                                action: 'reject',
                                notes: ''
                            })}
                            disabled={ownerDecisionSubmitting}
                        >
                            Cancel
                        </Button>
                        <Button
                            className={ownerDecisionDialog.action === 'approve' ? 'bg-emerald-600 hover:bg-emerald-700 text-white' : 'bg-red-600 hover:bg-red-700 text-white'}
                            onClick={handleOwnerDecisionConfirm}
                            disabled={ownerDecisionSubmitting}
                        >
                            {ownerDecisionSubmitting
                                ? ( ownerDecisionDialog.action === 'approve' ? 'Approving…' : 'Declining…' )
                                : ( ownerDecisionDialog.action === 'approve' ? 'Confirm Approval' : 'Confirm Decline' )
                            }
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default UsersPage;
