import React, { useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import { Switch } from '../../../components/ui/switch';
import { Badge } from '../../../components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../components/ui/select';
import {
    AlertCircle,
    ArrowDownCircle,
    ArrowUpCircle,
    BookOpen,
    CheckCircle2,
    Clock,
    DollarSign,
    FileText,
    Info,
    Key,
    Loader2,
    MessageCircle,
    RotateCcw,
    Save,
    Search,
    Settings,
    Users
} from 'lucide-react';
import { toast } from 'sonner';

// ─── Permission definitions ──────────────────────────────────────────────────

const PERMISSION_CATEGORIES = [
    {
        key: 'documents',
        label: 'Documents',
        icon: FileText,
        color: 'blue',
        permissions: [
            {key: 'can_view_documents', label: 'View Documents', desc: 'Access community documents'},
            {key: 'can_upload_documents', label: 'Upload Documents', desc: 'Upload and add new documents'},
            {
                key: 'can_manage_document_permissions',
                label: 'Manage Document Access',
                desc: 'Control per-document visibility'
            },
        ]
    },
    {
        key: 'financial',
        label: 'Financial',
        icon: DollarSign,
        color: 'green',
        permissions: [
            {key: 'can_view_finances', label: 'View Finances', desc: 'View financial reports and budgets'},
            {key: 'can_manage_finances', label: 'Manage Finances', desc: 'Create and edit financial entries'},
        ]
    },
    {
        key: 'communication',
        label: 'Communication',
        icon: MessageCircle,
        color: 'pink',
        permissions: [
            {key: 'can_chat', label: 'Chat Access', desc: 'Participate in community chat'},
            {key: 'can_post_announcements', label: 'Post Announcements', desc: 'Create community announcements'},
            {
                key: 'can_send_notifications',
                label: 'Send Notifications',
                desc: 'Send levy reminders and system notifications'
            },
            {key: 'can_access_email', label: 'Email Access', desc: 'Access @eastgateresidences.com.au email'},
        ]
    },
    {
        key: 'governance',
        label: 'Governance',
        icon: BookOpen,
        color: 'purple',
        permissions: [
            {key: 'can_view_meetings', label: 'View Meetings', desc: 'Access meeting schedules and minutes'},
            {key: 'can_manage_meetings', label: 'Manage Meetings', desc: 'Create and manage meetings'},
            {key: 'can_create_listings', label: 'Create Listings', desc: 'Post marketplace listings'},
        ]
    },
    {
        key: 'administration',
        label: 'Administration',
        icon: Settings,
        color: 'orange',
        permissions: [
            {key: 'can_manage_users', label: 'Manage Users', desc: 'User administration and account management'},
        ]
    },
    {
        key: 'operations',
        label: 'Operations',
        icon: Clock,
        color: 'indigo',
        permissions: [
            {key: 'can_view_schedule', label: 'View Schedule', desc: 'Access service provider schedules'},
            {
                key: 'can_manage_requests',
                label: 'Manage Requests',
                desc: 'Review and respond to maintenance and support requests'
            },
            {
                key: 'can_manage_access_device_settings',
                label: 'Manage Access Device Settings',
                desc: 'Edit fob, remote and access-card pricing and request rules'
            },
        ]
    },
];

// Role defaults mirroring backend models/user.py DEFAULT_PERMISSIONS
const ROLE_DEFAULTS = {
    super_admin: {
        can_view_documents: true,
        can_upload_documents: true,
        can_manage_document_permissions: true,
        can_view_finances: true,
        can_manage_finances: true,
        can_create_listings: true,
        can_chat: true,
        can_view_meetings: true,
        can_manage_meetings: true,
        can_manage_users: true,
        can_view_schedule: true,
        can_post_announcements: true,
        can_send_notifications: true,
        can_access_email: true,
        can_manage_requests: true,
        can_manage_access_device_settings: true
    },
    strata_admin: {
        can_view_documents: true,
        can_upload_documents: true,
        can_manage_document_permissions: true,
        can_view_finances: true,
        can_manage_finances: true,
        can_create_listings: true,
        can_chat: true,
        can_view_meetings: true,
        can_manage_meetings: true,
        can_manage_users: true,
        can_view_schedule: true,
        can_post_announcements: true,
        can_send_notifications: true,
        can_access_email: true,
        can_manage_requests: true,
        can_manage_access_device_settings: true
    },
    chairman: {
        can_view_documents: true,
        can_upload_documents: true,
        can_manage_document_permissions: true,
        can_view_finances: true,
        can_manage_finances: true,
        can_create_listings: true,
        can_chat: true,
        can_view_meetings: true,
        can_manage_meetings: true,
        can_manage_users: true,
        can_view_schedule: true,
        can_post_announcements: true,
        can_send_notifications: true,
        can_access_email: true,
        can_manage_requests: true,
        can_manage_access_device_settings: true
    },
    ec_member: {
        can_view_documents: true,
        can_upload_documents: true,
        can_manage_document_permissions: false,
        can_view_finances: true,
        can_manage_finances: true,
        can_create_listings: true,
        can_chat: true,
        can_view_meetings: true,
        can_manage_meetings: true,
        can_manage_users: true,
        can_view_schedule: true,
        can_post_announcements: true,
        can_send_notifications: true,
        can_access_email: true,
        can_manage_requests: true,
        can_manage_access_device_settings: false
    },
    strata_manager: {
        can_view_documents: true,
        can_upload_documents: true,
        can_manage_document_permissions: true,
        can_view_finances: true,
        can_manage_finances: true,
        can_create_listings: true,
        can_chat: true,
        can_view_meetings: true,
        can_manage_meetings: true,
        can_manage_users: true,
        can_view_schedule: true,
        can_post_announcements: true,
        can_send_notifications: true,
        can_access_email: true,
        can_manage_requests: true,
        can_manage_access_device_settings: true
    },
    admin_staff: {
        can_view_documents: true,
        can_upload_documents: false,
        can_manage_document_permissions: false,
        can_view_finances: false,
        can_manage_finances: false,
        can_create_listings: false,
        can_chat: true,
        can_view_meetings: true,
        can_manage_meetings: false,
        can_manage_users: false,
        can_view_schedule: true,
        can_post_announcements: false,
        can_send_notifications: false,
        can_access_email: false,
        can_manage_requests: true,
        can_manage_access_device_settings: false
    },
    owner: {
        can_view_documents: true,
        can_upload_documents: false,
        can_manage_document_permissions: false,
        can_view_finances: true,
        can_manage_finances: false,
        can_create_listings: true,
        can_chat: true,
        can_view_meetings: true,
        can_manage_meetings: false,
        can_manage_users: false,
        can_view_schedule: false,
        can_post_announcements: false,
        can_send_notifications: false,
        can_access_email: true,
        can_manage_requests: false,
        can_manage_access_device_settings: false
    },
    tenant: {
        can_view_documents: true,
        can_upload_documents: false,
        can_manage_document_permissions: false,
        can_view_finances: false,
        can_manage_finances: false,
        can_create_listings: true,
        can_chat: true,
        can_view_meetings: true,
        can_manage_meetings: false,
        can_manage_users: false,
        can_view_schedule: false,
        can_post_announcements: false,
        can_send_notifications: false,
        can_access_email: false,
        can_manage_requests: false,
        can_manage_access_device_settings: false
    },
    service_provider: {
        can_view_documents: false,
        can_upload_documents: false,
        can_manage_document_permissions: false,
        can_view_finances: false,
        can_manage_finances: false,
        can_create_listings: false,
        can_chat: true,
        can_view_meetings: false,
        can_manage_meetings: false,
        can_manage_users: false,
        can_view_schedule: true,
        can_post_announcements: false,
        can_send_notifications: false,
        can_access_email: false,
        can_manage_requests: false,
        can_manage_access_device_settings: false
    },
    guest: {
        can_view_documents: false,
        can_upload_documents: false,
        can_manage_document_permissions: false,
        can_view_finances: false,
        can_manage_finances: false,
        can_create_listings: false,
        can_chat: false,
        can_view_meetings: false,
        can_manage_meetings: false,
        can_manage_users: false,
        can_view_schedule: false,
        can_post_announcements: false,
        can_send_notifications: false,
        can_access_email: false,
        can_manage_requests: false,
        can_manage_access_device_settings: false
    },
};

const ROLE_COLORS = {
    super_admin: 'bg-red-100 text-red-800',
    chairman: 'bg-purple-100 text-purple-800',
    ec_member: 'bg-indigo-100 text-indigo-800',
    strata_manager: 'bg-blue-100 text-blue-800',
    admin_staff: 'bg-teal-100 text-teal-800',
    owner: 'bg-green-100 text-green-800',
    tenant: 'bg-yellow-100 text-yellow-800',
    service_provider: 'bg-orange-100 text-orange-800',
    guest: 'bg-gray-100 text-gray-600',
};

const CAT_COLORS = {
    blue: {bg: 'bg-blue-50', border: 'border-blue-200', icon: 'text-blue-600', label: 'bg-blue-100 text-blue-800'},
    green: {
        bg: 'bg-green-50',
        border: 'border-green-200',
        icon: 'text-green-600',
        label: 'bg-green-100 text-green-800'
    },
    pink: {bg: 'bg-pink-50', border: 'border-pink-200', icon: 'text-pink-600', label: 'bg-pink-100 text-pink-800'},
    purple: {
        bg: 'bg-purple-50',
        border: 'border-purple-200',
        icon: 'text-purple-600',
        label: 'bg-purple-100 text-purple-800'
    },
    orange: {
        bg: 'bg-orange-50',
        border: 'border-orange-200',
        icon: 'text-orange-600',
        label: 'bg-orange-100 text-orange-800'
    },
    indigo: {
        bg: 'bg-indigo-50',
        border: 'border-indigo-200',
        icon: 'text-indigo-600',
        label: 'bg-indigo-100 text-indigo-800'
    },
};
// Count how many permissions differ from role defaults for a user
/**
 * @generated FunctionHeader
 * Function: countCustomPermissions
 * Path: frontend/src/pages/dashboard/admin/UserPermissionsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function countCustomPermissions(user) {
    if (!user) return 0;
    const defaults = ROLE_DEFAULTS[ user.role ] || {};
    const perms = user.permissions || {};
    return Object.keys(defaults).filter(k => perms[ k ] !== undefined && perms[ k ] !== defaults[ k ]).length;
}
// ─── Component ───────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: UserPermissionsPage
 * Path: frontend/src/pages/dashboard/admin/UserPermissionsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const UserPermissionsPage = () => {
    const {api} = useAuth();
    const [loading, setLoading] = useState(true);
    const [users, setUsers] = useState([]);
    const [selectedUser, setSelectedUser] = useState(null);
    const [permissions, setPermissions] = useState({});
    const [originalPermissions, setOriginalPermissions] = useState({});
    const [searchQuery, setSearchQuery] = useState('');
    const [roleFilter, setRoleFilter] = useState('all');
    const [saving, setSaving] = useState(false);

    const fetchUsers = React.useCallback(async () => {
        try {
            setLoading(true);
            const res = await api.get('/users');
            setUsers(res.data);
        } catch {
            toast.error('Failed to load users');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchUsers();
    }, [fetchUsers]);
    /**
     * @generated FunctionHeader
     * Function: handleSelectUser
     * Path: frontend/src/pages/dashboard/admin/UserPermissionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSelectUser = (user) => {
        if (selectedUser?.id === user.id) return;
        const perms = {...( user.permissions || {} )};
        setSelectedUser(user);
        setPermissions(perms);
        setOriginalPermissions(perms);
    };
    /**
     * @generated FunctionHeader
     * Function: handlePermissionChange
     * Path: frontend/src/pages/dashboard/admin/UserPermissionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePermissionChange = (key, value) => {
        setPermissions(prev => ( {...prev, [ key ]: value} ));
    };
    /**
     * @generated FunctionHeader
     * Function: handleResetToDefaults
     * Path: frontend/src/pages/dashboard/admin/UserPermissionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleResetToDefaults = () => {
        const defaults = ROLE_DEFAULTS[ selectedUser.role ] || {};
        setPermissions({...defaults});
    };
    /**
     * @generated FunctionHeader
     * Function: handleDiscardChanges
     * Path: frontend/src/pages/dashboard/admin/UserPermissionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDiscardChanges = () => {
        setPermissions({...originalPermissions});
    };
    /**
     * @generated FunctionHeader
     * Function: handleSave
     * Path: frontend/src/pages/dashboard/admin/UserPermissionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSave = async () => {
        if (!selectedUser) return;
        try {
            setSaving(true);
            const customPerms = {};
            const defaults = ROLE_DEFAULTS[ selectedUser.role ] || {};
            Object.keys(permissions).forEach(k => {
                if (permissions[ k ] !== defaults[ k ]) customPerms[ k ] = permissions[ k ];
            });
            await api.put(`/users/${selectedUser.id}`, {custom_permissions: customPerms});
            const updated = {...selectedUser, permissions};
            setSelectedUser(updated);
            setOriginalPermissions({...permissions});
            setUsers(users.map(u => u.id === selectedUser.id ? updated : u));
            toast.success('Permissions saved');
        } catch {
            toast.error('Failed to save permissions');
        } finally {
            setSaving(false);
        }
    };

    const filteredUsers = useMemo(() => users.filter(u => {
        const q = searchQuery.toLowerCase();
        const matchesSearch = !q || ( u.full_name || '' ).toLowerCase().includes(q) ||
            ( u.email || '' ).toLowerCase().includes(q) ||
            ( u.unit_number || '' ).toLowerCase().includes(q);
        const matchesRole = roleFilter === 'all' || u.role === roleFilter;
        return matchesSearch && matchesRole;
    }), [users, searchQuery, roleFilter]);

    const hasChanges = useMemo(() =>
            JSON.stringify(permissions) !== JSON.stringify(originalPermissions),
        [permissions, originalPermissions]
    );

    const changes = useMemo(() => {
        if (!selectedUser) return [];
        const defaults = ROLE_DEFAULTS[ selectedUser.role ] || {};
        return Object.keys(permissions).filter(k => permissions[ k ] !== defaults[ k ]);
    }, [permissions, selectedUser]);
    /**
     * @generated FunctionHeader
     * Function: getPermState
     * Path: frontend/src/pages/dashboard/admin/UserPermissionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getPermState = (key) => {
        if (!selectedUser) return 'default';
        const roleDefault = ( ROLE_DEFAULTS[ selectedUser.role ] || {} )[ key ];
        const current = permissions[ key ];
        if (current === roleDefault) return 'default';
        if (current && !roleDefault) return 'elevated';
        return 'restricted';
    };

    if (loading) {
        return (
            <div className="max-w-7xl mx-auto p-6 space-y-4">
                <div className="flex items-center gap-3">
                    <div className="p-3 rounded-full bg-primary/10"><Key className="h-7 w-7 text-primary"/></div>
                    <div><h1 className="text-2xl font-bold">Access Control</h1><p
                        className="text-sm text-muted-foreground">Loading users...</p></div>
                </div>
                <div className="h-80 skeleton w-full rounded-xl"/>
            </div>
        );
    }

    const uniqueRoles = [...new Set(users.map(u => u.role))].sort();

    return (
        <div className="max-w-7xl mx-auto space-y-5 p-6" data-testid="user-permissions-page">
            {/* Header */}
            <div className="flex items-center gap-3">
                <div className="p-3 rounded-full bg-primary/10">
                    <Key className="h-7 w-7 text-primary"/>
                </div>
                <div className="flex-1">
                    <h1 className="text-2xl font-bold">Access Control Management</h1>
                    <p className="text-sm text-muted-foreground">
                        Customize individual user permissions beyond their role defaults. Changes override role-level
                        settings.
                    </p>
                </div>
            </div>

            {/* Info banner */}
            <div
                className="flex items-start gap-3 p-3 rounded-lg bg-blue-50 border border-blue-200 text-sm text-blue-800">
                <Info className="h-4 w-4 mt-0.5 flex-shrink-0 text-blue-500"/>
                <span>
          Each user inherits permissions from their role. Use this page to <strong>elevate</strong> (grant extra) or <strong>restrict</strong> (revoke) individual permissions for specific users.
        </span>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
                {/* ── Left: User list ─────────────────────────────────────────── */}
                <div className="lg:col-span-4">
                    <Card className="border-2">
                        <CardHeader className="pb-3">
                            <CardTitle className="text-sm font-semibold flex items-center gap-2">
                                <Users className="h-4 w-4"/> Users
                                <Badge variant="secondary" className="ml-auto">{filteredUsers.length}</Badge>
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3 pt-0">
                            <div className="relative">
                                <Search
                                    className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground"/>
                                <Input placeholder="Search name, email, unit…" value={searchQuery}
                                       onChange={e => setSearchQuery(e.target.value)} className="pl-9 h-8 text-sm"/>
                            </div>
                            <Select value={roleFilter} onValueChange={setRoleFilter}>
                                <SelectTrigger className="h-8 text-sm"><SelectValue/></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="all">All roles</SelectItem>
                                    {uniqueRoles.map(r => <SelectItem key={r}
                                                                      value={r}>{r.replace(/_/g, ' ')}</SelectItem>)}
                                </SelectContent>
                            </Select>

                            <div className="space-y-1 max-h-[520px] overflow-y-auto pr-1">
                                {filteredUsers.map(user => {
                                    const overrideCount = countCustomPermissions(user);
                                    const isSelected = selectedUser?.id === user.id;
                                    return (
                                        <button
                                            key={user.id}
                                            onClick={() => handleSelectUser(user)}
                                            className={`w-full text-left px-3 py-2.5 rounded-lg border transition-all ${
                                                isSelected
                                                    ? 'border-primary bg-primary/5 shadow-sm'
                                                    : 'border-transparent hover:border-muted hover:bg-muted/40'
                                            }`}
                                        >
                                            <div className="flex items-center justify-between gap-2">
                                                <div className="min-w-0">
                                                    <p className="text-sm font-medium truncate">{user.full_name}</p>
                                                    <p className="text-xs text-muted-foreground truncate">{user.unit_number || '—'} · {user.email}</p>
                                                </div>
                                                <div className="flex flex-col items-end gap-1 flex-shrink-0">
                          <span
                              className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${ROLE_COLORS[ user.role ] || 'bg-gray-100 text-gray-600'}`}>
                            {user.role?.replace(/_/g, ' ')}
                          </span>
                                                    {overrideCount > 0 && (
                                                        <span
                                                            className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full font-medium">
                              {overrideCount} override{overrideCount !== 1 ? 's' : ''}
                            </span>
                                                    )}
                                                </div>
                                            </div>
                                        </button>
                                    );
                                })}
                                {filteredUsers.length === 0 && (
                                    <p className="text-center text-sm text-muted-foreground py-8">No users found</p>
                                )}
                            </div>
                        </CardContent>
                    </Card>
                </div>

                {/* ── Right: Permissions editor ────────────────────────────────── */}
                <div className="lg:col-span-8 space-y-4">
                    {!selectedUser ? (
                        <Card className="border-2 border-dashed">
                            <CardContent className="py-20 text-center">
                                <Key className="h-12 w-12 text-muted-foreground/40 mx-auto mb-3"/>
                                <p className="font-medium text-muted-foreground">Select a user to manage permissions</p>
                                <p className="text-sm text-muted-foreground mt-1">Click any user from the list on the
                                    left</p>
                            </CardContent>
                        </Card>
                    ) : (
                        <>
                            {/* User summary card */}
                            <Card className="border-2">
                                <CardContent className="p-4">
                                    <div className="flex items-start justify-between gap-4">
                                        <div>
                                            <h2 className="font-semibold text-lg">{selectedUser.full_name}</h2>
                                            <p className="text-sm text-muted-foreground">{selectedUser.email} ·
                                                Unit {selectedUser.unit_number || '—'}</p>
                                            <div className="flex items-center gap-2 mt-2">
                        <span
                            className={`text-xs font-medium px-2 py-0.5 rounded-full ${ROLE_COLORS[ selectedUser.role ] || 'bg-gray-100'}`}>
                          {selectedUser.role?.replace(/_/g, ' ')}
                        </span>
                                                {changes.length > 0 ? (
                                                    <span
                                                        className="text-xs bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full font-medium">
                            {changes.length} pending change{changes.length !== 1 ? 's' : ''}
                          </span>
                                                ) : (
                                                    <span
                                                        className="text-xs text-muted-foreground">No pending changes</span>
                                                )}
                                            </div>
                                        </div>
                                        <div className="flex items-center gap-2 flex-shrink-0">
                                            <Button variant="outline" size="sm" onClick={handleResetToDefaults}
                                                    title="Reset all permissions to this role's defaults">
                                                <RotateCcw className="h-3.5 w-3.5 mr-1.5"/> Role Defaults
                                            </Button>
                                            {hasChanges && (
                                                <Button variant="ghost" size="sm"
                                                        onClick={handleDiscardChanges}>Discard</Button>
                                            )}
                                            <Button size="sm" onClick={handleSave} disabled={!hasChanges || saving}>
                                                {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5"/> :
                                                    <Save className="h-3.5 w-3.5 mr-1.5"/>}
                                                Save
                                            </Button>
                                        </div>
                                    </div>

                                    {selectedUser.role === 'super_admin' && (
                                        <div
                                            className="mt-3 flex items-start gap-2 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                                            <AlertCircle className="h-4 w-4 text-yellow-600 mt-0.5 flex-shrink-0"/>
                                            <p className="text-xs text-yellow-800">Super Admins always have full access.
                                                Custom permissions here have no effect.</p>
                                        </div>
                                    )}
                                </CardContent>
                            </Card>

                            {/* Legend */}
                            <div className="flex items-center gap-4 text-xs text-muted-foreground px-1">
                                <span className="flex items-center gap-1.5"><span
                                    className="w-2.5 h-2.5 rounded-full bg-gray-200 inline-block"/> Role default</span>
                                <span className="flex items-center gap-1.5"><ArrowUpCircle
                                    className="h-3 w-3 text-green-600"/> Elevated above role</span>
                                <span className="flex items-center gap-1.5"><ArrowDownCircle
                                    className="h-3 w-3 text-orange-600"/> Restricted below role</span>
                            </div>

                            {/* Permission categories */}
                            {PERMISSION_CATEGORIES.map(cat => {
                                const colors = CAT_COLORS[ cat.color ];
                                const CatIcon = cat.icon;
                                const catChanges = cat.permissions.filter(p => getPermState(p.key) !== 'default').length;
                                return (
                                    <Card key={cat.key} className={`border ${colors.border}`}>
                                        <CardHeader className={`p-3 rounded-t-lg ${colors.bg}`}>
                                            <div className="flex items-center justify-between">
                                                <div className="flex items-center gap-2">
                                                    <CatIcon className={`h-4 w-4 ${colors.icon}`}/>
                                                    <span className="font-semibold text-sm">{cat.label}</span>
                                                </div>
                                                {catChanges > 0 && (
                                                    <Badge variant="outline"
                                                           className={`text-[10px] ${colors.label} border-current`}>
                                                        {catChanges} overridden
                                                    </Badge>
                                                )}
                                            </div>
                                        </CardHeader>
                                        <CardContent className="p-0">
                                            {cat.permissions.map((perm, idx) => {
                                                const state = getPermState(perm.key);
                                                const roleDefault = ( ROLE_DEFAULTS[ selectedUser.role ] || {} )[ perm.key ];
                                                const current = permissions[ perm.key ];
                                                return (
                                                    <div
                                                        key={perm.key}
                                                        className={`flex items-center justify-between gap-4 px-4 py-3 ${
                                                            idx < cat.permissions.length - 1 ? 'border-b' : ''
                                                        } ${state === 'elevated' ? 'bg-green-50/50' : state === 'restricted' ? 'bg-orange-50/50' : ''}`}
                                                    >
                                                        <div className="flex-1 min-w-0">
                                                            <div className="flex items-center gap-2">
                                                                <span
                                                                    className="text-sm font-medium">{perm.label}</span>
                                                                {state === 'elevated' && (
                                                                    <span
                                                                        className="flex items-center gap-0.5 text-[10px] font-medium text-green-700 bg-green-100 px-1.5 py-0.5 rounded-full">
                                    <ArrowUpCircle className="h-2.5 w-2.5"/> Elevated
                                  </span>
                                                                )}
                                                                {state === 'restricted' && (
                                                                    <span
                                                                        className="flex items-center gap-0.5 text-[10px] font-medium text-orange-700 bg-orange-100 px-1.5 py-0.5 rounded-full">
                                    <ArrowDownCircle className="h-2.5 w-2.5"/> Restricted
                                  </span>
                                                                )}
                                                            </div>
                                                            <p className="text-xs text-muted-foreground mt-0.5">{perm.desc}</p>
                                                            <p className="text-[10px] text-muted-foreground mt-0.5">
                                                                Role default: <span
                                                                className={roleDefault ? 'text-green-600 font-medium' : 'text-gray-400'}>{roleDefault ? 'Granted' : 'Denied'}</span>
                                                            </p>
                                                        </div>
                                                        <div className="flex items-center gap-3 flex-shrink-0">
                                                            {current
                                                                ? <CheckCircle2 className="h-4 w-4 text-green-600"/>
                                                                : <AlertCircle className="h-4 w-4 text-gray-300"/>
                                                            }
                                                            <Switch
                                                                checked={current || false}
                                                                onCheckedChange={v => handlePermissionChange(perm.key, v)}
                                                                disabled={saving}
                                                                className="data-[state=checked]:bg-green-600"
                                                            />
                                                        </div>
                                                    </div>
                                                );
                                            })}
                                        </CardContent>
                                    </Card>
                                );
                            })}

                            {/* Bottom save bar (sticky when has changes) */}
                            {hasChanges && (
                                <Card className="border-2 border-amber-300 bg-amber-50 sticky bottom-4 shadow-lg">
                                    <CardContent className="p-3 flex items-center justify-between gap-3">
                                        <p className="text-sm font-medium text-amber-800">
                                            {changes.length} unsaved
                                            change{changes.length !== 1 ? 's' : ''} for <strong>{selectedUser.full_name}</strong>
                                        </p>
                                        <div className="flex gap-2">
                                            <Button variant="outline" size="sm"
                                                    onClick={handleDiscardChanges}>Discard</Button>
                                            <Button size="sm" onClick={handleSave} disabled={saving}
                                                    className="bg-amber-600 hover:bg-amber-700 text-white">
                                                {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin mr-1.5"/> :
                                                    <Save className="h-3.5 w-3.5 mr-1.5"/>}
                                                Save Changes
                                            </Button>
                                        </div>
                                    </CardContent>
                                </Card>
                            )}
                        </>
                    )}
                </div>
            </div>
        </div>
    );
};

export default UserPermissionsPage;
