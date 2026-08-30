'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/contexts/AuthContext';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import {
    AlertCircle,
    Building2,
    ChevronRight,
    Loader2,
    RefreshCw,
    Search,
    UserCheck,
    UserPlus,
    Users,
    UserX,
} from 'lucide-react';

const ROLE_CONFIG = {
    strata_manager: {label: 'Strata Manager', color: 'bg-blue-100 text-blue-800 border-blue-200'},
    real_estate_agent: {label: 'Real Estate Agent', color: 'bg-purple-100 text-purple-800 border-purple-200'},
    admin_staff: {label: 'Admin Staff', color: 'bg-green-100 text-green-800 border-green-200'},
    service_provider: {label: 'Service Provider', color: 'bg-amber-100 text-amber-800 border-amber-200'},
};

const ELEVATION_ROLE_COLORS = {
    chairman: 'bg-amber-100 text-amber-800',
    treasurer: 'bg-rose-100 text-rose-800',
    secretary: 'bg-teal-100 text-teal-800',
    ec_member: 'bg-indigo-100 text-indigo-800',
    owner: 'bg-emerald-100 text-emerald-800',
};
/**
 * @generated FunctionHeader
 * Function: StaffUsersPage
 * Path: frontend/src/pages/dashboard/admin/StaffUsersPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function StaffUsersPage() {
    const {api, isAdmin, loading: authLoading} = useAuth();
    const router = useRouter();

    const [staffUsers, setStaffUsers] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [roleFilter, setRoleFilter] = useState('all');

    const fetchStaff = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const params = new URLSearchParams();
            if (roleFilter !== 'all') params.set('role', roleFilter);
            const res = await api.get(`/admin/staff-users?${params}`);
            setStaffUsers(res.data || []);
        } catch (err) {
            setError(err?.response?.data?.detail || 'Failed to load staff users.');
        } finally {
            setLoading(false);
        }
    }, [api, roleFilter]);

    useEffect(() => {
        fetchStaff();
    }, [fetchStaff]);

    const filtered = staffUsers.filter(u => {
        if (!searchQuery) return true;
        const q = searchQuery.toLowerCase();
        return (
            u.full_name?.toLowerCase().includes(q) ||
            u.email?.toLowerCase().includes(q) ||
            u.organisation?.toLowerCase().includes(q)
        );
    });

    const stats = {
        total: staffUsers.length,
        strata_manager: staffUsers.filter(u => u.role === 'strata_manager').length,
        real_estate_agent: staffUsers.filter(u => u.role === 'real_estate_agent').length,
        admin_staff: staffUsers.filter(u => u.role === 'admin_staff').length,
        service_provider: staffUsers.filter(u => u.role === 'service_provider').length,
        with_elevated: staffUsers.filter(u => u.role_assignments?.length > 0).length,
    };

    // Auth must resolve before isAdmin() is meaningful — otherwise a real super
    // admin sees "access required" on first paint.
    if (authLoading) {
        return (
            <div className="flex justify-center py-12">
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

    return (
        <div className="p-6 max-w-7xl mx-auto space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-gray-900 flex items-center gap-2">
                        <Users className="h-7 w-7 text-slate-600"/>
                        Staff User Management
                    </h1>
                    <p className="text-sm text-gray-500 mt-1">
                        Manage professional staff and service provider accounts with their building role assignments.
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button variant="outline" size="sm" onClick={fetchStaff} disabled={loading}>
                        <RefreshCw className={`h-4 w-4 mr-1 ${loading ? 'animate-spin' : ''}`}/>
                        Refresh
                    </Button>
                    <Button
                        size="sm"
                        onClick={() => router.push('/admin/create-staff-user')}
                    >
                        <UserPlus className="h-4 w-4 mr-1"/>
                        Add Staff User
                    </Button>
                </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
                {[
                    {label: 'Total Staff', value: stats.total, icon: Users, color: 'text-slate-600'},
                    {label: 'Strata Managers', value: stats.strata_manager, icon: Building2, color: 'text-blue-600'},
                    {
                        label: 'Real Estate Agents',
                        value: stats.real_estate_agent,
                        icon: UserCheck,
                        color: 'text-purple-600'
                    },
                    {label: 'Admin Staff', value: stats.admin_staff, icon: Users, color: 'text-green-600'},
                    {
                        label: 'Service Providers',
                        value: stats.service_provider,
                        icon: UserCheck,
                        color: 'text-amber-600'
                    },
                    {
                        label: 'With Elevated Roles',
                        value: stats.with_elevated,
                        icon: UserCheck,
                        color: 'text-amber-600'
                    },
                ].map(stat => (
                    <Card key={stat.label} className="text-center p-4">
                        <stat.icon className={`h-6 w-6 mx-auto mb-1 ${stat.color}`}/>
                        <div className="text-2xl font-bold text-gray-900">{stat.value}</div>
                        <div className="text-xs text-gray-500">{stat.label}</div>
                    </Card>
                ))}
            </div>

            {/* Filters */}
            <div className="flex gap-3 items-center">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400"/>
                    <Input
                        className="pl-9"
                        placeholder="Search by name, email or organisation…"
                        value={searchQuery}
                        onChange={e => setSearchQuery(e.target.value)}
                    />
                </div>
                <Select value={roleFilter} onValueChange={setRoleFilter}>
                    <SelectTrigger className="w-48">
                        <SelectValue placeholder="Filter by role"/>
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All roles</SelectItem>
                        <SelectItem value="strata_manager">Strata Manager</SelectItem>
                        <SelectItem value="real_estate_agent">Real Estate Agent</SelectItem>
                        <SelectItem value="admin_staff">Admin Staff</SelectItem>
                        <SelectItem value="service_provider">Service Provider</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            {/* Error */}
            {error && (
                <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4"/>
                    <AlertDescription>{error}</AlertDescription>
                </Alert>
            )}

            {/* Loading */}
            {loading && (
                <div className="flex justify-center py-12">
                    <Loader2 className="h-8 w-8 animate-spin text-gray-400"/>
                </div>
            )}

            {/* Empty */}
            {!loading && !error && filtered.length === 0 && (
                <Card>
                    <CardContent className="py-12 text-center">
                        <Users className="h-12 w-12 mx-auto text-gray-300 mb-3"/>
                        <p className="text-gray-500">
                            {staffUsers.length === 0
                                ? 'No staff users found. Add your first staff member.'
                                : 'No staff users match your search.'}
                        </p>
                        {staffUsers.length === 0 && (
                            <Button
                                className="mt-3"
                                onClick={() => router.push('/admin/create-staff-user')}
                            >
                                <UserPlus className="h-4 w-4 mr-1"/>
                                Add Staff User
                            </Button>
                        )}
                    </CardContent>
                </Card>
            )}

            {/* Staff list */}
            {!loading && filtered.length > 0 && (
                <div className="space-y-3">
                    {filtered.map(user => (
                        <Card
                            key={user.id}
                            className="cursor-pointer hover:shadow-md transition-shadow border hover:border-slate-300"
                            onClick={() => router.push(`/admin/staff-users/${user.id}`)}
                        >
                            <CardContent className="p-4">
                                <div className="flex items-start justify-between gap-4">
                                    {/* Avatar + identity */}
                                    <div className="flex items-start gap-3 min-w-0">
                                        <div
                                            className="h-10 w-10 rounded-full bg-slate-100 flex items-center justify-center text-slate-600 font-semibold text-sm shrink-0">
                                            {( user.full_name || 'U' ).charAt(0).toUpperCase()}
                                        </div>
                                        <div className="min-w-0">
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <span className="font-semibold text-gray-900 truncate">
                                                    {user.full_name}
                                                </span>
                                                <Badge
                                                    variant="outline"
                                                    className={`text-xs shrink-0 ${ROLE_CONFIG[ user.role ]?.color}`}
                                                >
                                                    {ROLE_CONFIG[ user.role ]?.label || user.role}
                                                </Badge>
                                                {!user.is_active && (
                                                    <Badge variant="outline"
                                                           className="text-xs text-red-600 border-red-200">
                                                        Inactive
                                                    </Badge>
                                                )}
                                            </div>
                                            <div className="text-sm text-gray-500 truncate">{user.email}</div>
                                            {user.organisation && (
                                                <div
                                                    className="text-xs text-gray-400 truncate">{user.organisation}</div>
                                            )}
                                        </div>
                                    </div>

                                    {/* Buildings + elevated roles */}
                                    <div className="flex items-center gap-4 shrink-0">
                                        <div className="text-right hidden sm:block">
                                            {user.buildings?.length > 0 ? (
                                                <div className="flex items-center gap-1 text-sm text-gray-600">
                                                    <Building2 className="h-3.5 w-3.5"/>
                                                    <span>{user.buildings.length} building{user.buildings.length !== 1 ? 's' : ''}</span>
                                                </div>
                                            ) : (
                                                <div className="flex items-center gap-1 text-sm text-amber-600">
                                                    <UserX className="h-3.5 w-3.5"/>
                                                    <span>No building</span>
                                                </div>
                                            )}
                                            {user.role_assignments?.length > 0 && (
                                                <div className="flex gap-1 mt-1 flex-wrap justify-end max-w-48">
                                                    {[...new Set(user.role_assignments.map(r => r.role_name.toLowerCase()))].slice(0, 3).map(rn => (
                                                        <Badge key={rn}
                                                               className={`text-xs ${ELEVATION_ROLE_COLORS[ rn ] || 'bg-gray-100'}`}>
                                                            {rn}
                                                        </Badge>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                        <ChevronRight className="h-5 w-5 text-gray-300"/>
                                    </div>
                                </div>
                            </CardContent>
                        </Card>
                    ))}
                </div>
            )}
        </div>
    );
}
