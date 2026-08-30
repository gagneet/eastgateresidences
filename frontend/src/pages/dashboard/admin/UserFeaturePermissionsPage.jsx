import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import { Badge } from '../../../components/ui/badge';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../components/ui/select';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '../../../components/ui/collapsible';
import {
    CheckCircle2,
    ChevronDown,
    ChevronRight,
    Eye,
    EyeOff,
    Info,
    Loader2,
    Search,
    Shield,
    ToggleLeft,
    ToggleRight,
    Trash2,
    UserCog,
    Users,
    X,
    XCircle
} from 'lucide-react';
import { toast } from 'sonner';

// ─── Constants ───────────────────────────────────────────────────────────────

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

const CATEGORY_META = {
    core: {label: 'Core Features', color: 'bg-blue-50 border-blue-200 text-blue-800'},
    communication: {label: 'Communication', color: 'bg-pink-50 border-pink-200 text-pink-800'},
    financial: {label: 'Financial Management', color: 'bg-green-50 border-green-200 text-green-800'},
    governance: {label: 'Governance & Admin', color: 'bg-purple-50 border-purple-200 text-purple-800'},
    community: {label: 'Community', color: 'bg-yellow-50 border-yellow-200 text-yellow-800'},
    safety: {label: 'Safety & Emergency', color: 'bg-red-50 border-red-200 text-red-800'},
    facilities: {label: 'Facilities Management', color: 'bg-indigo-50 border-indigo-200 text-indigo-800'},
    maintenance: {label: 'Maintenance Intelligence', color: 'bg-teal-50 border-teal-200 text-teal-800'},
    system: {label: 'System Settings', color: 'bg-orange-50 border-orange-200 text-orange-800'},
};

const CATEGORY_ORDER = ['core', 'communication', 'financial', 'governance', 'community', 'safety', 'facilities', 'maintenance', 'system'];

// Override state: null = follow site, true = grant, false = deny
const OVERRIDE_OPTIONS = [
    {
        value: 'site',
        label: 'Site Default',
        icon: ToggleLeft,
        className: 'text-gray-600 bg-gray-50 border-gray-200 hover:bg-gray-100'
    },
    {
        value: 'grant',
        label: 'Grant',
        icon: Eye,
        className: 'text-green-700 bg-green-50 border-green-200 hover:bg-green-100'
    },
    {
        value: 'deny',
        label: 'Deny',
        icon: EyeOff,
        className: 'text-rose-700 bg-rose-50 border-rose-200 hover:bg-rose-100'
    },
];
/**
 * @generated FunctionHeader
 * Function: overrideValue
 * Path: frontend/src/pages/dashboard/admin/UserFeaturePermissionsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function overrideValue(val) {
    if (val === null || val === undefined) return 'site';
    return val ? 'grant' : 'deny';
}
// ─── Component ───────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: UserFeaturePermissionsPage
 * Path: frontend/src/pages/dashboard/admin/UserFeaturePermissionsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const UserFeaturePermissionsPage = () => {
    const {api} = useAuth();
    const [loading, setLoading] = useState(true);
    const [users, setUsers] = useState([]);
    const [features, setFeatures] = useState([]);
    const [selectedUser, setSelectedUser] = useState(null);
    const [userAccess, setUserAccess] = useState([]);
    const [loadingAccess, setLoadingAccess] = useState(false);
    const [searchQuery, setSearchQuery] = useState('');
    const [userSearch, setUserSearch] = useState('');
    const [userRoleFilter, setUserRoleFilter] = useState('all');
    const [showOverridesOnly, setShowOverridesOnly] = useState(false);
    const [categoryFilter, setCategoryFilter] = useState('all');
    const [expandedCategories, setExpandedCategories] = useState({});
    const [updatingKeys, setUpdatingKeys] = useState({});
    const [clearingAll, setClearingAll] = useState(false);

    // ─── Data fetching ──────────────────────────────────────────────────────

    const fetchData = useCallback(async () => {
        try {
            setLoading(true);
            const [usersRes, featuresRes] = await Promise.all([
                api.get('/users'),
                api.get('/feature-toggles/')
            ]);
            setUsers(usersRes.data || []);
            setFeatures(featuresRes.data || []);
            // Auto-expand all categories
            const cats = [...new Set(( featuresRes.data || [] ).map(f => f.category || 'other'))];
            const init = {};
            cats.forEach(c => {
                init[ c ] = true;
            });
            setExpandedCategories(init);
        } catch {
            toast.error('Failed to load data');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);

    const fetchUserAccess = useCallback(async (userId) => {
        try {
            setLoadingAccess(true);
            const res = await api.get(`/feature-toggles/access-summary/${userId}`);
            setUserAccess(res.data || []);
        } catch {
            toast.error('Failed to load user access');
        } finally {
            setLoadingAccess(false);
        }
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: handleSelectUser
     * Path: frontend/src/pages/dashboard/admin/UserFeaturePermissionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSelectUser = async (user) => {
        if (selectedUser?.id === user.id) return;
        setSelectedUser(user);
        setSearchQuery('');
        setShowOverridesOnly(false);
        setCategoryFilter('all');
        await fetchUserAccess(user.id);
    };
    // ─── Override actions ────────────────────────────────────────────────────

    /**
     * @generated FunctionHeader
     * Function: handleSetOverride
     * Path: frontend/src/pages/dashboard/admin/UserFeaturePermissionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSetOverride = async (featureKey, newState) => {
        if (!selectedUser) return;
        try {
            setUpdatingKeys(prev => ( {...prev, [ featureKey ]: true} ));

            if (newState === 'site') {
                // Remove override → back to site default
                const current = userAccess.find(a => a.feature_key === featureKey);
                if (current?.user_override === null) return; // already at site default
                await api.delete(`/feature-toggles/users/${selectedUser.id}/${featureKey}`);
            } else {
                await api.post(`/feature-toggles/users/${selectedUser.id}`, {
                    user_id: selectedUser.id,
                    feature_key: featureKey,
                    is_enabled: newState === 'grant',
                    notes: ''
                });
            }
            await fetchUserAccess(selectedUser.id);
        } catch {
            toast.error('Failed to update access');
        } finally {
            setUpdatingKeys(prev => {
                const s = {...prev};
                delete s[ featureKey ];
                return s;
            });
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleClearAllOverrides
     * Path: frontend/src/pages/dashboard/admin/UserFeaturePermissionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleClearAllOverrides = async () => {
        if (!selectedUser) return;
        const overrides = userAccess.filter(a => a.user_override !== null);
        if (overrides.length === 0) {
            toast.info('No overrides to clear');
            return;
        }
        try {
            setClearingAll(true);
            const results = await Promise.allSettled(overrides.map(a =>
                api.delete(`/feature-toggles/users/${selectedUser.id}/${a.feature_key}`)
            ));
            const failed = results.filter(r => r.status === 'rejected').length;
            await fetchUserAccess(selectedUser.id);
            if (failed > 0) {
                toast.error(`Cleared ${overrides.length - failed} override${overrides.length - failed !== 1 ? 's' : ''}, ${failed} failed`);
            } else {
                toast.success(`Cleared ${overrides.length} override${overrides.length !== 1 ? 's' : ''}`);
            }
        } catch {
            toast.error('Failed to clear overrides');
        } finally {
            setClearingAll(false);
        }
    };

    // ─── Derived data ────────────────────────────────────────────────────────

    const filteredUsers = useMemo(() => users.filter(u => {
        const q = userSearch.toLowerCase();
        const matchSearch = !q || ( u.full_name || '' ).toLowerCase().includes(q) ||
            ( u.email || '' ).toLowerCase().includes(q) || ( u.unit_number || '' ).toLowerCase().includes(q);
        const matchRole = userRoleFilter === 'all' || u.role === userRoleFilter;
        return matchSearch && matchRole;
    }), [users, userSearch, userRoleFilter]);

    const overrideMap = useMemo(() => {
        const m = {};
        userAccess.forEach(a => {
            m[ a.feature_key ] = a;
        });
        return m;
    }, [userAccess]);

    const overrideCount = useMemo(() =>
            userAccess.filter(a => a.user_override !== null).length,
        [userAccess]
    );

    const grantCount = useMemo(() =>
            userAccess.filter(a => a.user_override === true).length,
        [userAccess]
    );

    const denyCount = useMemo(() =>
            userAccess.filter(a => a.user_override === false).length,
        [userAccess]
    );

    // User override count map for user list badges
    const userOverrideCounts = useMemo(() => {
        // We don't pre-load access for all users — only show badge for selected user
        if (!selectedUser) return {};
        return {[ selectedUser.id ]: overrideCount};
    }, [selectedUser, overrideCount]);

    const filteredAccess = useMemo(() => {
        const q = searchQuery.toLowerCase();
        return userAccess.filter(a => {
            if (q && !a.feature_name.toLowerCase().includes(q) && !a.feature_key.toLowerCase().includes(q)) return false;
            if (showOverridesOnly && a.user_override === null) return false;
            if (categoryFilter !== 'all' && a.category !== categoryFilter) return false;
            return true;
        });
    }, [userAccess, searchQuery, showOverridesOnly, categoryFilter]);

    const groupedAccess = useMemo(() => {
        const g = {};
        filteredAccess.forEach(a => {
            const cat = a.category || 'other';
            if (!g[ cat ]) g[ cat ] = [];
            g[ cat ].push(a);
        });
        return g;
    }, [filteredAccess]);

    const sortedCategories = useMemo(() =>
        Object.keys(groupedAccess).sort((a, b) =>
            ( CATEGORY_ORDER.indexOf(a) + 1 || 99 ) - ( CATEGORY_ORDER.indexOf(b) + 1 || 99 )
        ), [groupedAccess]
    );

    const uniqueRoles = [...new Set(users.map(u => u.role))].sort();
    const availableCategories = [...new Set(userAccess.map(a => a.category).filter(Boolean))];
    // ─── Render helpers ──────────────────────────────────────────────────────

    /**
     * @generated FunctionHeader
     * Function: OverrideSelector
     * Path: frontend/src/pages/dashboard/admin/UserFeaturePermissionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const OverrideSelector = ({access}) => {
        const current = overrideValue(access.user_override);
        const isUpdating = updatingKeys[ access.feature_key ];
        return (
            <div className="flex items-center gap-1">
                {OVERRIDE_OPTIONS.map(opt => {
                    const Icon = opt.icon;
                    const isActive = current === opt.value;
                    return (
                        <button
                            key={opt.value}
                            onClick={() => !isActive && handleSetOverride(access.feature_key, opt.value)}
                            disabled={isUpdating}
                            title={opt.label}
                            className={`flex items-center gap-1 px-2 py-1 rounded border text-[11px] font-medium transition-all ${
                                isActive ? opt.className + ' ring-1 ring-current shadow-sm' : 'text-gray-400 bg-white border-gray-200 hover:border-gray-300'
                            } ${isUpdating ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
                        >
                            {isUpdating && current !== opt.value
                                ? <Loader2 className="h-3 w-3 animate-spin"/>
                                : <Icon className="h-3 w-3"/>
                            }
                            <span className="hidden sm:inline">{opt.label}</span>
                        </button>
                    );
                })}
            </div>
        );
    };
    /**
     * @generated FunctionHeader
     * Function: EffectiveBadge
     * Path: frontend/src/pages/dashboard/admin/UserFeaturePermissionsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const EffectiveBadge = ({access}) => {
        if (access.effective_access) {
            return (
                <span
                    className="flex items-center gap-1 text-[10px] font-medium text-green-700 bg-green-50 border border-green-200 px-1.5 py-0.5 rounded-full">
          <CheckCircle2 className="h-2.5 w-2.5"/> Access
        </span>
            );
        }
        return (
            <span
                className="flex items-center gap-1 text-[10px] font-medium text-rose-700 bg-rose-50 border border-rose-200 px-1.5 py-0.5 rounded-full">
        <XCircle className="h-2.5 w-2.5"/> Blocked
      </span>
        );
    };

    // ─── Loading ─────────────────────────────────────────────────────────────

    if (loading) {
        return (
            <div className="max-w-7xl mx-auto p-6 space-y-4">
                <div className="flex items-center gap-3">
                    <div className="p-3 rounded-full bg-primary/10"><UserCog className="h-7 w-7 text-primary"/></div>
                    <div><h1 className="text-2xl font-bold">Feature Access Controls</h1><p
                        className="text-sm text-muted-foreground">Loading…</p></div>
                </div>
                <div className="h-80 skeleton w-full rounded-xl"/>
            </div>
        );
    }

    // ─── Main render ─────────────────────────────────────────────────────────

    return (
        <div className="max-w-7xl mx-auto space-y-5 p-6" data-testid="user-feature-permissions-page">
            {/* Header */}
            <div className="flex items-center gap-3">
                <div className="p-3 rounded-full bg-primary/10">
                    <UserCog className="h-7 w-7 text-primary"/>
                </div>
                <div className="flex-1">
                    <h1 className="text-2xl font-bold">Feature Access Controls</h1>
                    <p className="text-sm text-muted-foreground">
                        Grant or deny individual users access to specific features, overriding the site-wide toggle.
                    </p>
                </div>
            </div>

            {/* Info banner */}
            <div
                className="flex items-start gap-3 p-3 rounded-lg bg-blue-50 border border-blue-200 text-sm text-blue-800">
                <Info className="h-4 w-4 mt-0.5 flex-shrink-0 text-blue-500"/>
                <span>
          <strong>Site Default</strong> — user follows the global feature toggle. &nbsp;
                    <strong>Grant</strong> — user can access even if the feature is globally disabled. &nbsp;
                    <strong>Deny</strong> — user is blocked even if the feature is globally enabled.
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
                                <Input placeholder="Search name, email, unit…" value={userSearch}
                                       onChange={e => setUserSearch(e.target.value)} className="pl-9 h-8 text-sm"/>
                            </div>
                            <Select value={userRoleFilter} onValueChange={setUserRoleFilter}>
                                <SelectTrigger className="h-8 text-sm"><SelectValue/></SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="all">All roles</SelectItem>
                                    {uniqueRoles.map(r => <SelectItem key={r}
                                                                      value={r}>{r.replace(/_/g, ' ')}</SelectItem>)}
                                </SelectContent>
                            </Select>

                            <div className="space-y-1 max-h-[520px] overflow-y-auto pr-1">
                                {filteredUsers.map(user => {
                                    const isSelected = selectedUser?.id === user.id;
                                    const oc = userOverrideCounts[ user.id ];
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
                                                    {oc > 0 && (
                                                        <span
                                                            className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full font-medium">
                              {oc} override{oc !== 1 ? 's' : ''}
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

                {/* ── Right: Feature access matrix ─────────────────────────────── */}
                <div className="lg:col-span-8 space-y-4">
                    {!selectedUser ? (
                        <Card className="border-2 border-dashed">
                            <CardContent className="py-20 text-center">
                                <UserCog className="h-12 w-12 text-muted-foreground/40 mx-auto mb-3"/>
                                <p className="font-medium text-muted-foreground">Select a user to manage feature
                                    access</p>
                                <p className="text-sm text-muted-foreground mt-1">Click any user from the list on the
                                    left</p>
                            </CardContent>
                        </Card>
                    ) : (
                        <>
                            {/* User summary + stats */}
                            <Card className="border-2">
                                <CardContent className="p-4">
                                    <div className="flex items-start justify-between gap-4">
                                        <div>
                                            <h2 className="font-semibold text-lg">{selectedUser.full_name}</h2>
                                            <p className="text-sm text-muted-foreground">{selectedUser.email} ·
                                                Unit {selectedUser.unit_number || '—'}</p>
                                            <span
                                                className={`mt-2 inline-block text-xs font-medium px-2 py-0.5 rounded-full ${ROLE_COLORS[ selectedUser.role ] || 'bg-gray-100'}`}>
                        {selectedUser.role?.replace(/_/g, ' ')}
                      </span>
                                        </div>
                                        <div className="flex-shrink-0 flex flex-col items-end gap-3">
                                            <div className="flex items-center gap-2 text-xs">
                                                {overrideCount > 0 ? (
                                                    <>
                                                        {grantCount > 0 && (
                                                            <span
                                                                className="flex items-center gap-1 text-green-700 bg-green-50 border border-green-200 px-2 py-1 rounded-full">
                                <Eye className="h-3 w-3"/> {grantCount} granted
                              </span>
                                                        )}
                                                        {denyCount > 0 && (
                                                            <span
                                                                className="flex items-center gap-1 text-rose-700 bg-rose-50 border border-rose-200 px-2 py-1 rounded-full">
                                <EyeOff className="h-3 w-3"/> {denyCount} denied
                              </span>
                                                        )}
                                                    </>
                                                ) : (
                                                    <span className="text-muted-foreground text-xs">No overrides</span>
                                                )}
                                            </div>
                                            {overrideCount > 0 && (
                                                <Button variant="outline" size="sm" onClick={handleClearAllOverrides}
                                                        disabled={clearingAll}
                                                        className="text-rose-600 border-rose-200 hover:bg-rose-50 hover:text-rose-700 h-7 text-xs">
                                                    {clearingAll ? <Loader2 className="h-3 w-3 animate-spin mr-1.5"/> :
                                                        <Trash2 className="h-3 w-3 mr-1.5"/>}
                                                    Clear All Overrides
                                                </Button>
                                            )}
                                        </div>
                                    </div>

                                    {selectedUser.role === 'super_admin' && (
                                        <div
                                            className="mt-3 flex items-start gap-2 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                                            <Shield className="h-4 w-4 text-yellow-600 mt-0.5 flex-shrink-0"/>
                                            <p className="text-xs text-yellow-800">This role always has access to all
                                                features. Overrides here have no effect.</p>
                                        </div>
                                    )}
                                </CardContent>
                            </Card>

                            {/* Filters */}
                            <div className="flex items-center gap-3 flex-wrap">
                                <div className="relative flex-1 min-w-[180px]">
                                    <Search
                                        className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground"/>
                                    <Input placeholder="Search features…" value={searchQuery}
                                           onChange={e => setSearchQuery(e.target.value)} className="pl-9 h-8 text-sm"/>
                                </div>
                                <Select value={categoryFilter} onValueChange={setCategoryFilter}>
                                    <SelectTrigger className="h-8 text-sm w-44"><SelectValue
                                        placeholder="All categories"/></SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value="all">All categories</SelectItem>
                                        {availableCategories.sort().map(c => (
                                            <SelectItem key={c} value={c}>{CATEGORY_META[ c ]?.label || c}</SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <button
                                    onClick={() => setShowOverridesOnly(p => !p)}
                                    className={`flex items-center gap-1.5 h-8 px-3 rounded-md border text-xs font-medium transition-all ${
                                        showOverridesOnly
                                            ? 'bg-amber-50 border-amber-300 text-amber-700'
                                            : 'bg-white border-gray-200 text-gray-600 hover:border-gray-300'
                                    }`}
                                >
                                    {showOverridesOnly ? <ToggleRight className="h-3.5 w-3.5"/> :
                                        <ToggleLeft className="h-3.5 w-3.5"/>}
                                    Overrides only
                                </button>
                                {( searchQuery || categoryFilter !== 'all' || showOverridesOnly ) && (
                                    <Button variant="ghost" size="sm" className="h-8 text-xs"
                                            onClick={() => {
                                                setSearchQuery('');
                                                setCategoryFilter('all');
                                                setShowOverridesOnly(false);
                                            }}>
                                        <X className="h-3 w-3 mr-1"/> Clear
                                    </Button>
                                )}
                            </div>

                            {/* Column header */}
                            {loadingAccess ? (
                                <div className="flex items-center justify-center py-12">
                                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground"/>
                                </div>
                            ) : (
                                <div className="space-y-3">
                                    {/* Table header */}
                                    <div
                                        className="hidden md:grid grid-cols-12 gap-2 px-4 py-2 text-[11px] font-semibold text-muted-foreground uppercase tracking-wide bg-muted/40 rounded-lg">
                                        <div className="col-span-4">Feature</div>
                                        <div className="col-span-2 text-center">Site Toggle</div>
                                        <div className="col-span-4 text-center">Override</div>
                                        <div className="col-span-2 text-center">Effective</div>
                                    </div>

                                    {sortedCategories.length === 0 ? (
                                        <Card className="border-dashed">
                                            <CardContent className="py-10 text-center">
                                                <Search className="h-8 w-8 text-muted-foreground/30 mx-auto mb-2"/>
                                                <p className="text-sm text-muted-foreground">No features match your
                                                    filters</p>
                                            </CardContent>
                                        </Card>
                                    ) : (
                                        sortedCategories.map(cat => {
                                            const meta = CATEGORY_META[ cat ] || {
                                                label: cat,
                                                color: 'bg-gray-50 border-gray-200 text-gray-700'
                                            };
                                            const catAccess = groupedAccess[ cat ];
                                            const catOverrides = catAccess.filter(a => a.user_override !== null).length;
                                            const isExpanded = expandedCategories[ cat ] !== false;

                                            return (
                                                <Collapsible
                                                    key={cat}
                                                    open={isExpanded}
                                                    onOpenChange={open => setExpandedCategories(prev => ( {
                                                        ...prev,
                                                        [ cat ]: open
                                                    } ))}
                                                >
                                                    <Card className="border overflow-hidden">
                                                        <CollapsibleTrigger asChild>
                                                            <button
                                                                className={`w-full flex items-center justify-between p-3 text-left transition-colors hover:opacity-90 ${meta.color} border-b`}>
                                                                <div className="flex items-center gap-2">
                                                                    <span
                                                                        className="font-semibold text-sm">{meta.label}</span>
                                                                    <Badge variant="secondary"
                                                                           className="text-[10px]">{catAccess.length}</Badge>
                                                                    {catOverrides > 0 && (
                                                                        <span
                                                                            className="text-[10px] bg-amber-100 text-amber-700 px-1.5 py-0.5 rounded-full font-medium">
                                      {catOverrides} override{catOverrides !== 1 ? 's' : ''}
                                    </span>
                                                                    )}
                                                                </div>
                                                                {isExpanded ? <ChevronDown className="h-4 w-4"/> :
                                                                    <ChevronRight className="h-4 w-4"/>}
                                                            </button>
                                                        </CollapsibleTrigger>

                                                        <CollapsibleContent>
                                                            {catAccess.map((access, idx) => {
                                                                const hasOverride = access.user_override !== null;
                                                                return (
                                                                    <div
                                                                        key={access.feature_key}
                                                                        className={`grid grid-cols-12 gap-2 items-center px-4 py-3 ${
                                                                            idx < catAccess.length - 1 ? 'border-b' : ''
                                                                        } ${hasOverride ? 'bg-amber-50/40' : ''} transition-colors`}
                                                                    >
                                                                        {/* Feature name */}
                                                                        <div className="col-span-12 md:col-span-4">
                                                                            <div className="flex items-center gap-2">
                                                                                <span
                                                                                    className="text-sm font-medium">{access.feature_name}</span>
                                                                                {hasOverride && (
                                                                                    <span
                                                                                        className="text-[9px] font-bold text-amber-700 bg-amber-100 px-1 py-0.5 rounded uppercase tracking-wide">
                                            Override
                                          </span>
                                                                                )}
                                                                            </div>
                                                                            <p className="text-[10px] text-muted-foreground font-mono mt-0.5">{access.feature_key}</p>
                                                                        </div>

                                                                        {/* Site toggle */}
                                                                        <div
                                                                            className="col-span-4 md:col-span-2 flex justify-start md:justify-center">
                                                                            {access.site_wide_enabled ? (
                                                                                <span
                                                                                    className="flex items-center gap-1 text-[10px] text-green-700 bg-green-50 border border-green-200 px-1.5 py-0.5 rounded-full font-medium">
                                          <ToggleRight className="h-2.5 w-2.5"/> On
                                        </span>
                                                                            ) : (
                                                                                <span
                                                                                    className="flex items-center gap-1 text-[10px] text-gray-500 bg-gray-50 border border-gray-200 px-1.5 py-0.5 rounded-full font-medium">
                                          <ToggleLeft className="h-2.5 w-2.5"/> Off
                                        </span>
                                                                            )}
                                                                        </div>

                                                                        {/* Override selector */}
                                                                        <div
                                                                            className="col-span-6 md:col-span-4 flex justify-start md:justify-center">
                                                                            <OverrideSelector access={access}/>
                                                                        </div>

                                                                        {/* Effective access */}
                                                                        <div
                                                                            className="col-span-2 md:col-span-2 flex justify-start md:justify-center">
                                                                            <EffectiveBadge access={access}/>
                                                                        </div>
                                                                    </div>
                                                                );
                                                            })}
                                                        </CollapsibleContent>
                                                    </Card>
                                                </Collapsible>
                                            );
                                        })
                                    )}
                                </div>
                            )}
                        </>
                    )}
                </div>
            </div>
        </div>
    );
};

export default UserFeaturePermissionsPage;
