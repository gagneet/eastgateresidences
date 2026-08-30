'use client';

import { useEffect, useMemo, useState } from 'react';
import { useAuth } from '@/contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert';
import { AlertCircle, CheckCircle2, Eye, EyeOff, RefreshCw, UserPlus } from 'lucide-react';

const STAFF_ROLES = [
    {
        value: 'strata_manager',
        label: 'Strata Manager',
        description: 'Full building management access — finance, compliance, governance, communications.',
        color: 'bg-blue-500/10 text-blue-700 border-blue-200',
    },
    {
        value: 'real_estate_agent',
        label: 'Real Estate Agent',
        description: 'OwnerHub access to manage properties, tenancies, inspections and rent ledger on behalf of owners.',
        color: 'bg-purple-500/10 text-purple-700 border-purple-200',
    },
    {
        value: 'admin_staff',
        label: 'Admin Staff',
        description: 'Building administration access — notices, parcels, requests, directory, maintenance, community tools.',
        color: 'bg-green-500/10 text-green-700 border-green-200',
    },
    {
        value: 'service_provider',
        label: 'Service Provider',
        description: 'Trade and contractor access for work orders, schedules, and service coordination.',
        color: 'bg-amber-500/10 text-amber-700 border-amber-200',
    },
];
/**
 * @generated FunctionHeader
 * Function: generatePassword
 * Path: frontend/src/pages/dashboard/admin/CreateStaffUserPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function generatePassword() {
    const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789!@#$';
    return Array.from({length: 12}, () => chars[ Math.floor(Math.random() * chars.length) ]).join('');
}
/**
 * @generated FunctionHeader
 * Function: CreateStaffUserPage
 * Path: frontend/src/pages/dashboard/admin/CreateStaffUserPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function CreateStaffUserPage() {
    const {api, user, hasPermission} = useAuth();
    const isSuperAdmin = user?.role === 'super_admin';
    const canManageUsers = hasPermission('can_manage_users');
    const availableRoles = useMemo(
        // Non-super-admin managers are intentionally restricted to service_provider.
        () => STAFF_ROLES.filter(role => isSuperAdmin || role.value === 'service_provider'),
        [isSuperAdmin]
    );

    const [form, setForm] = useState({
        full_name: '',
        email: '',
        password: generatePassword(),
        role: '',
        building_id: '',
        phone: '',
        organisation: '',
        professional_licence: '',
        send_welcome_email: true,
    });
    const [buildings, setBuildings] = useState([]);

    const [showPassword, setShowPassword] = useState(false);
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState(null);
    const [errors, setErrors] = useState({});

    const selectedRole = availableRoles.find(r => r.value === form.role);

    useEffect(() => {
        let mounted = true;
        /**
         * @generated FunctionHeader
         * Function: fetchBuildings
         * Path: frontend/src/pages/dashboard/admin/CreateStaffUserPage.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchBuildings = async () => {
            try {
                const res = await api.get('/buildings/me');
                const nextBuildings = Array.isArray(res.data) ? res.data : [];
                if (!mounted) return;
                setBuildings(nextBuildings);
                setForm(prev => ( {
                    ...prev,
                    building_id: prev.building_id || nextBuildings[ 0 ]?.id || '',
                    role: prev.role || availableRoles[ 0 ]?.value || '',
                } ));
            } catch (err) {
                if (mounted) {
                    setErrors(prev => ( {...prev, building_id: 'Could not load accessible buildings.'} ));
                }
            }
        };

        fetchBuildings();
        return () => {
            mounted = false;
        };
    }, [api, availableRoles]);
    /**
     * @generated FunctionHeader
     * Function: validate
     * Path: frontend/src/pages/dashboard/admin/CreateStaffUserPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const validate = () => {
        const e = {};
        if (!form.full_name.trim()) e.full_name = 'Full name is required.';
        if (!form.email.trim() || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) e.email = 'Valid email is required.';
        if (!form.role) e.role = 'Please select a role.';
        if (!form.building_id) e.building_id = 'Please select a building.';
        if (form.password.length < 8) e.password = 'Password must be at least 8 characters.';
        return e;
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/admin/CreateStaffUserPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        const validationErrors = validate();
        if (Object.keys(validationErrors).length > 0) {
            setErrors(validationErrors);
            return;
        }
        setErrors({});
        setLoading(true);
        setResult(null);

        try {
            const payload = {
                full_name: form.full_name.trim(),
                email: form.email.trim().toLowerCase(),
                password: form.password,
                role: form.role,
                building_id: form.building_id,
                phone: form.phone || undefined,
                organisation: form.organisation || undefined,
                professional_licence: form.professional_licence || undefined,
                send_welcome_email: form.send_welcome_email,
            };
            const res = await api.post('/admin/create-staff-user', payload);
            setResult({success: true, data: res.data});
            // Reset form for next entry
            setForm(prev => ( {
                ...prev,
                full_name: '',
                email: '',
                password: generatePassword(),
                phone: '',
                organisation: '',
                professional_licence: '',
            } ));
        } catch (err) {
            setResult({
                success: false,
                error: err?.response?.data?.detail || 'Failed to create account. Please try again.',
            });
        } finally {
            setLoading(false);
        }
    };

    if (!canManageUsers) {
        return (
            <div className="container max-w-2xl py-8 space-y-6">
                <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4"/>
                    <AlertTitle>Access denied</AlertTitle>
                    <AlertDescription>You are not allowed to create staff accounts.</AlertDescription>
                </Alert>
            </div>
        );
    }

    return (
        <div className="container max-w-2xl py-8 space-y-6">
            <div>
                <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
                    <UserPlus className="h-6 w-6 text-primary"/>
                    Create Staff Account
                </h1>
                <p className="text-muted-foreground mt-1">
                    {isSuperAdmin
                        ? 'Create a new active account for management, admin staff, agent, or service roles.'
                        : 'Create a new active service provider account for your building.'}
                </p>
            </div>

            {result && (
                <Alert variant={result.success ? 'default' : 'destructive'}>
                    {result.success ? <CheckCircle2 className="h-4 w-4"/> : <AlertCircle className="h-4 w-4"/>}
                    <AlertTitle>{result.success ? 'Account Created' : 'Creation Failed'}</AlertTitle>
                    <AlertDescription>
                        {result.success ? (
                            <div className="space-y-1 text-sm">
                                <p><strong>{result.data?.email}</strong> has been created
                                    as <strong>{availableRoles.find(r => r.value === result.data?.role)?.label || result.data?.role}</strong>.
                                </p>
                                {result.data?.welcome_email_sent &&
                                    <p>A welcome email with login credentials has been sent.</p>}
                            </div>
                        ) : (
                            <p>{result.error}</p>
                        )}
                    </AlertDescription>
                </Alert>
            )}

            <Card>
                <CardHeader>
                    <CardTitle>Account Details</CardTitle>
                    <CardDescription>All active staff accounts can log in immediately.</CardDescription>
                </CardHeader>
                <CardContent>
                    <form onSubmit={handleSubmit} className="space-y-5">
                        {/* Role selector */}
                        <div className="space-y-2">
                            <Label htmlFor="role">Role <span className="text-destructive">*</span></Label>
                            <Select value={form.role} onValueChange={v => setForm(p => ( {...p, role: v} ))}>
                                <SelectTrigger id="role">
                                    <SelectValue placeholder="Select staff role…"/>
                                </SelectTrigger>
                                <SelectContent>
                                    {availableRoles.map(r => (
                                        <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            {selectedRole && (
                                <p className="text-xs text-muted-foreground">{selectedRole.description}</p>
                            )}
                            {errors.role && <p className="text-xs text-destructive">{errors.role}</p>}
                        </div>

                        <div className="space-y-2">
                            <Label htmlFor="building_id">Building <span className="text-destructive">*</span></Label>
                            <Select value={form.building_id}
                                    onValueChange={v => setForm(p => ( {...p, building_id: v} ))}>
                                <SelectTrigger id="building_id">
                                    <SelectValue placeholder="Select building…"/>
                                </SelectTrigger>
                                <SelectContent>
                                    {buildings.map(building => (
                                        <SelectItem key={building.id} value={building.id}>
                                            {building.name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            {errors.building_id && <p className="text-xs text-destructive">{errors.building_id}</p>}
                        </div>

                        {/* Role capabilities summary */}
                        {selectedRole && (
                            <div className={`rounded-lg border px-4 py-3 text-sm ${selectedRole.color}`}>
                                <p className="font-medium mb-1">{selectedRole.label} — access summary</p>
                                {form.role === 'strata_manager' && (
                                    <ul className="list-disc list-inside space-y-0.5 text-xs">
                                        <li>Full financial management (budgets, levies, debt recovery)</li>
                                        <li>Governance (meetings, AGM, compliance, strata roll)</li>
                                        <li>Resident & unit management, ownership transfers</li>
                                        <li>Site settings, email config, building configuration</li>
                                        <li>Intelligence dashboards (levy fairness, capital risk)</li>
                                        <li><em>No OwnerHub — not an owner/investor role</em></li>
                                    </ul>
                                )}
                                {form.role === 'real_estate_agent' && (
                                    <ul className="list-disc list-inside space-y-0.5 text-xs">
                                        <li>OwnerHub: Properties, Tenancies, Rent Ledger, Inspections, Property Health
                                            Score, True Cost of Ownership
                                        </li>
                                        <li>Agent Dashboard, Document Library, Maintenance Requests</li>
                                        <li>Resident Directory, Notices, Events</li>
                                        <li><em>No financial management, governance, or admin tools</em></li>
                                    </ul>
                                )}
                                {form.role === 'admin_staff' && (
                                    <ul className="list-disc list-inside space-y-0.5 text-xs">
                                        <li>Notices, Announcements, Events Calendar, Community Chat</li>
                                        <li>Parcel Tracking, Requests &amp; Applications, Maintenance Tracker</li>
                                        <li>Resident Directory, Document Library, Facility Bookings</li>
                                        <li>Pet Register, Building Info & Rules</li>
                                        <li><em>No OwnerHub, financial tools, or management admin</em></li>
                                    </ul>
                                )}
                                {form.role === 'service_provider' && (
                                    <ul className="list-disc list-inside space-y-0.5 text-xs">
                                        <li>Service requests, maintenance updates, work orders, and schedules</li>
                                        <li>Building communication relevant to assigned jobs</li>
                                        <li><em>No resident management, finance, or governance tools</em></li>
                                    </ul>
                                )}
                            </div>
                        )}

                        {/* Name */}
                        <div className="space-y-2">
                            <Label htmlFor="full_name">Full Name <span className="text-destructive">*</span></Label>
                            <Input
                                id="full_name"
                                value={form.full_name}
                                onChange={e => setForm(p => ( {...p, full_name: e.target.value} ))}
                                placeholder="Jane Smith"
                            />
                            {errors.full_name && <p className="text-xs text-destructive">{errors.full_name}</p>}
                        </div>

                        {/* Email */}
                        <div className="space-y-2">
                            <Label htmlFor="email">Email Address <span className="text-destructive">*</span></Label>
                            <Input
                                id="email"
                                type="email"
                                value={form.email}
                                onChange={e => setForm(p => ( {...p, email: e.target.value} ))}
                                placeholder="jane.smith@company.com.au"
                            />
                            {errors.email && <p className="text-xs text-destructive">{errors.email}</p>}
                        </div>

                        {/* Password */}
                        <div className="space-y-2">
                            <Label htmlFor="password">Temporary Password <span
                                className="text-destructive">*</span></Label>
                            <div className="flex gap-2">
                                <div className="relative flex-1">
                                    <Input
                                        id="password"
                                        type={showPassword ? 'text' : 'password'}
                                        value={form.password}
                                        onChange={e => setForm(p => ( {...p, password: e.target.value} ))}
                                        className="pr-10 font-mono"
                                    />
                                    <button
                                        type="button"
                                        onClick={() => setShowPassword(v => !v)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                                    >
                                        {showPassword ? <EyeOff className="h-4 w-4"/> : <Eye className="h-4 w-4"/>}
                                    </button>
                                </div>
                                <Button
                                    type="button"
                                    variant="outline"
                                    size="icon"
                                    onClick={() => setForm(p => ( {...p, password: generatePassword()} ))}
                                    title="Generate new password"
                                >
                                    <RefreshCw className="h-4 w-4"/>
                                </Button>
                            </div>
                            <p className="text-xs text-muted-foreground">Auto-generated. Click 🔄 to regenerate. Staff
                                should change this on first login.</p>
                            {errors.password && <p className="text-xs text-destructive">{errors.password}</p>}
                        </div>

                        {/* Phone */}
                        <div className="space-y-2">
                            <Label htmlFor="phone">Phone</Label>
                            <Input
                                id="phone"
                                value={form.phone}
                                onChange={e => setForm(p => ( {...p, phone: e.target.value} ))}
                                placeholder="+61 4XX XXX XXX"
                            />
                        </div>

                        {/* Organisation */}
                        <div className="space-y-2">
                            <Label htmlFor="organisation">
                                {form.role === 'real_estate_agent' ? 'Agency Name' : form.role === 'strata_manager' ? 'Strata Company' : 'Organisation'}
                            </Label>
                            <Input
                                id="organisation"
                                value={form.organisation}
                                onChange={e => setForm(p => ( {...p, organisation: e.target.value} ))}
                                placeholder="e.g. ACT Strata Group"
                            />
                        </div>

                        {/* Professional Licence */}
                        {( form.role === 'strata_manager' || form.role === 'real_estate_agent' ) && (
                            <div className="space-y-2">
                                <Label htmlFor="professional_licence">
                                    {form.role === 'real_estate_agent' ? 'REA Licence Number' : 'Strata Licence Number'}
                                </Label>
                                <Input
                                    id="professional_licence"
                                    value={form.professional_licence}
                                    onChange={e => setForm(p => ( {...p, professional_licence: e.target.value} ))}
                                    placeholder="e.g. 12345678"
                                />
                            </div>
                        )}

                        {/* Welcome email toggle */}
                        <div className="flex items-center gap-3 rounded-lg border p-3">
                            <Switch
                                id="send_welcome_email"
                                checked={form.send_welcome_email}
                                onCheckedChange={v => setForm(p => ( {...p, send_welcome_email: v} ))}
                            />
                            <div>
                                <Label htmlFor="send_welcome_email" className="cursor-pointer font-medium">
                                    Send welcome email
                                </Label>
                                <p className="text-xs text-muted-foreground">
                                    Email the staff member their login credentials immediately.
                                </p>
                            </div>
                        </div>

                        <Button type="submit" className="w-full" disabled={loading}>
                            {loading ? (
                                <><RefreshCw className="mr-2 h-4 w-4 animate-spin"/>Creating Account…</>
                            ) : (
                                <><UserPlus className="mr-2 h-4 w-4"/>Create Staff Account</>
                            )}
                        </Button>
                    </form>
                </CardContent>
            </Card>

            <Card>
                <CardHeader>
                    <CardTitle className="text-sm">Role Access Summary</CardTitle>
                </CardHeader>
                <CardContent>
                    <div className="grid grid-cols-3 gap-3 text-xs">
                        {availableRoles.map(role => (
                            <div key={role.value} className={`rounded-lg border p-3 ${role.color}`}>
                                <p className="font-semibold mb-1">{role.label}</p>
                                <p className="text-xs opacity-80">{role.description}</p>
                            </div>
                        ))}
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
