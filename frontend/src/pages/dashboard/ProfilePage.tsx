"use client";
// @featuretrace:user-management — the signed-in user's own profile: details, unit, password, email preference, tax summary.
// Layer: frontend
// Data flow: ProfilePage → GET /units, /change-requests/me/pending, POST /auth/change-password,
//            PUT /users/{id} → users + user_units + unit_change_requests (building-scoped).
// Related: backend/routers/auth.py, backend/routers/unit_change_requests.py
//           frontend/src/hooks/useActiveUnit.ts (the tax summary is per-unit; the profile's own
//           unit field is account identity and deliberately does NOT follow the switcher)

import React, {useCallback, useEffect, useState} from 'react';
import {useRouter} from 'next/navigation';
import {useAuth} from '../../contexts/AuthContext';
import {useActiveUnit} from '../../hooks/useActiveUnit';
import {useTaxSummary} from '../../hooks/useTaxSummary';
import {Card, CardContent} from '../../components/ui/card';
import {Button} from '../../components/ui/button';
import {Input} from '../../components/ui/input';
import {Label} from '../../components/ui/label';
import {Avatar, AvatarFallback, AvatarImage} from '../../components/ui/avatar';
import {Separator} from '../../components/ui/separator';
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '../../components/ui/select';
import {Tabs, TabsContent, TabsList, TabsTrigger} from '../../components/ui/tabs';
import {Switch} from '../../components/ui/switch';
import {
    Bell,
    CheckCircle2,
    Clock,
    Download,
    FileText,
    Home,
    Info,
    Loader2,
    Mail,
    MapPin,
    Phone,
    Shield,
    User as UserIcon
} from 'lucide-react';
import {Checkbox} from '../../components/ui/checkbox';
import {getInitials, roleDisplayNames} from '../../lib/utils';
import {toast} from 'sonner';
/**
 * @generated FunctionHeader
 * Function: ProfilePage
 * Path: frontend/src/pages/dashboard/ProfilePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ProfilePage: React.FC = () => {
    const {user, updateProfile, updateEmailPreference, api, hasPermission, selectedYear} = useAuth();
    const {activeUnit} = useActiveUnit();
    const router = useRouter();
    const {loading: downloadingTax, downloadTaxSummary} = useTaxSummary(api);

    const [loading, setLoading] = useState(false);
    const [unitsLoading, setUnitsLoading] = useState(true);
    const [availableUnits, setAvailableUnits] = useState<any[]>([]);
    const [pendingRequest, setPendingRequest] = useState<any>(null);
    const [activeTab, setActiveTab] = useState('personal');

    // Security / Change Password state
    const [pwForm, setPwForm] = useState({current_password: '', new_password: '', confirm_password: ''});
    const [pwLoading, setPwLoading] = useState(false);
    const [emailPrefLoading, setEmailPrefLoading] = useState(false);

    const [formData, setFormData] = useState({
        full_name: user?.full_name || '',
        phone_home: user?.phone_home || user?.phone || '',
        phone_mobile: user?.phone_mobile || '',
        phone_business: user?.phone_business || '',
        unit_number: user?.unit_number || '',
        home_address: user?.home_address || '',
        home_suburb: user?.home_suburb || '',
        home_state: user?.home_state || 'ACT',
        home_postcode: user?.home_postcode || '',
        postal_same_as_home: user?.postal_same_as_home !== undefined ? user.postal_same_as_home : true,
        postal_address: user?.postal_address || '',
        postal_suburb: user?.postal_suburb || '',
        postal_state: user?.postal_state || 'ACT',
        postal_postcode: user?.postal_postcode || '',
        is_managing_agent: user?.is_managing_agent || false,
        is_tenanted: user?.is_tenanted || false,
        change_status: false,
        // Correspondence preferences
        general_correspondence_email: user?.general_correspondence_email !== undefined ? user.general_correspondence_email : true,
        general_correspondence_post: user?.general_correspondence_post || false,
        levy_notices_email: user?.levy_notices_email !== undefined ? user.levy_notices_email : true,
        levy_notices_post: user?.levy_notices_post || false,
        meeting_notices_email: user?.meeting_notices_email !== undefined ? user.meeting_notices_email : true,
        meeting_notices_post: user?.meeting_notices_post || false,
        strata_roll_consent: user?.strata_roll_consent || false
    });

    // Fetch available units
    const fetchUnits = useCallback(async () => {
        try {
            setUnitsLoading(true);
            const response = await api.get('/units');
            setAvailableUnits(response.data || []);
        } catch (error) {
            console.error('Failed to fetch units:', error);
        } finally {
            setUnitsLoading(false);
        }
    }, [api]);

    // Check for pending change request
    const checkPendingRequest = useCallback(async () => {
        try {
            const response = await api.get('/change-requests/me/pending');
            if (response.data?.has_pending) {
                setPendingRequest(response.data.request);
            } else {
                setPendingRequest(null);
            }
        } catch (error) {
            console.error('Failed to check pending request:', error);
        }
    }, [api]);

    useEffect(() => {
        fetchUnits();
        checkPendingRequest();
    }, [fetchUnits, checkPendingRequest]);
    /**
     * @generated FunctionHeader
     * Function: handleChange
     * Path: frontend/src/pages/dashboard/ProfilePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        const {name, value} = e.target;
        setFormData(prev => ({...prev, [name]: value}));
    };
    /**
     * @generated FunctionHeader
     * Function: handleUnitChange
     * Path: frontend/src/pages/dashboard/ProfilePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleUnitChange = (value: string) => {
        setFormData(prev => ({...prev, unit_number: value}));
    };
    /**
     * @generated FunctionHeader
     * Function: handleSwitchChange
     * Path: frontend/src/pages/dashboard/ProfilePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSwitchChange = (name: string, checked: boolean | string) => {
        setFormData(prev => {
            const updated = {...prev, [name]: checked};
            if (name === 'postal_same_as_home' && checked === true) {
                updated.postal_address = '';
                updated.postal_suburb = '';
                updated.postal_state = prev.home_state;
                updated.postal_postcode = '';
            }
            return updated;
        });
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/ProfilePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);

        try {
            const isUnitChange = formData.unit_number !== user?.unit_number;
            await updateProfile(formData);

            if (isUnitChange && !hasPermission('can_manage_users')) {
                toast.info('Unit change request submitted for approval');
            } else {
                toast.success('Profile updated successfully');
            }

            await checkPendingRequest();
        } catch (error: any) {
            toast.error(error.response?.data?.detail || 'Failed to update profile');
        } finally {
            setLoading(false);
        }
    };

    // Only admins can change unit assignment directly.
    // Regular users must use the formal change-request workflow.
    const canChangeUnit = hasPermission('can_manage_users');
    /**
     * @generated FunctionHeader
     * Function: handleDownloadTaxSummary
     * Path: frontend/src/pages/dashboard/ProfilePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDownloadTaxSummary = async () => {
        // The tax summary is a per-unit document — follow the active unit, not the
        // account's default, or a multi-unit owner silently downloads the wrong lot's.
        if (!activeUnit || !selectedYear) return;
        await downloadTaxSummary(activeUnit, selectedYear);
    };
    /**
     * @generated FunctionHeader
     * Function: handleChangePassword
     * Path: frontend/src/pages/dashboard/ProfilePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleChangePassword = async (e: React.SyntheticEvent) => {
        e.preventDefault();
        if (pwForm.new_password !== pwForm.confirm_password) {
            toast.error('New passwords do not match');
            return;
        }
        if (pwForm.new_password.length < 8) {
            toast.error('New password must be at least 8 characters');
            return;
        }
        setPwLoading(true);
        try {
            await api.post('/auth/change-password', {
                current_password: pwForm.current_password,
                new_password: pwForm.new_password,
            });
            toast.success('Password changed successfully');
            setPwForm({current_password: '', new_password: '', confirm_password: ''});
        } catch (err: any) {
            const msg = err?.response?.data?.detail || 'Failed to change password';
            toast.error(msg);
        } finally {
            setPwLoading(false);
        }
    };

    const primaryContactEmail = user?.primary_email || user?.email || '';
    const secondaryContactEmail = user?.secondary_email || '';
    const hasSecondaryContactEmail = Boolean(
        secondaryContactEmail && secondaryContactEmail.toLowerCase() !== primaryContactEmail.toLowerCase()
    );
    /**
     * @generated FunctionHeader
     * Function: handlePromoteSecondaryEmail
     * Path: frontend/src/pages/dashboard/ProfilePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePromoteSecondaryEmail = async () => {
        if (!secondaryContactEmail) return;
        setEmailPrefLoading(true);
        try {
            await updateEmailPreference(secondaryContactEmail);
            toast.success('Primary contact email updated');
        } catch (error: any) {
            toast.error(error.response?.data?.detail || 'Failed to update primary contact email');
        } finally {
            setEmailPrefLoading(false);
        }
    };

    return (
        <div className="max-w-4xl mx-auto space-y-8" data-testid="profile-page">
            <div className="flex flex-col md:flex-row md:items-end justify-between gap-4">
                <div className="space-y-1">
                    <h1 className="text-3xl font-black tracking-tight text-slate-900 uppercase">My Account</h1>
                    <p className="text-muted-foreground">Manage your personal details, addresses and notification
                        preferences.</p>
                </div>
                <div
                    className="flex items-center gap-2 px-3 py-1.5 bg-slate-100 rounded-full text-xs font-bold text-slate-600 border border-slate-200">
                    <Shield className="h-3.5 w-3.5 text-primary"/>
                    {user?.role && roleDisplayNames[user.role]} • Unit {user?.unit_number || 'Unassigned'}
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                <Card className="lg:col-span-1 border-none shadow-xl bg-slate-900 text-white overflow-hidden relative">
                    <div className="absolute top-0 right-0 p-4 opacity-10">
                        <UserIcon className="h-32 w-32 rotate-12"/>
                    </div>
                    <CardContent className="p-8 flex flex-col items-center text-center space-y-6 relative z-10">
                        <div className="relative group">
                            <Avatar
                                className="h-32 w-32 border-4 border-white/10 shadow-2xl transition-transform group-hover:scale-105">
                                <AvatarImage src={user?.profile_image}/>
                                <AvatarFallback className="bg-primary text-white text-4xl font-black">
                                    {getInitials(user?.full_name)}
                                </AvatarFallback>
                            </Avatar>
                            <div
                                className="absolute bottom-1 right-1 bg-green-500 h-6 w-6 rounded-full border-4 border-slate-900 shadow-lg"
                                title="Active Account"/>
                        </div>

                        <div className="space-y-1">
                            <h2 className="text-2xl font-bold">{user?.full_name}</h2>
                            {user?.co_owner_name && (
                                <p className="text-slate-400 text-xs font-medium">Co-owner: {user.co_owner_name}</p>
                            )}
                            {/* Split long emails at @ to avoid both-side truncation */}
                            <p className="text-slate-400 text-sm font-medium break-all leading-tight">
                                {user?.email && user.email.includes('@') ? (
                                    <>
                                        <span className="block">{user.email.split('@')[0]}</span>
                                        <span className="block">@{user.email.split('@')[1]}</span>
                                    </>
                                ) : (
                                    user?.email
                                )}
                            </p>
                        </div>

                        <div className="w-full grid grid-cols-2 gap-2 pt-4">
                            <div className="bg-white/5 rounded-xl p-3 border border-white/10">
                                <p className="text-[10px] text-slate-500 uppercase font-black tracking-widest mb-1">Residency</p>
                                <p className="text-xs font-bold capitalize">{user?.role}</p>
                            </div>
                            <div className="bg-white/5 rounded-xl p-3 border border-white/10">
                                <p className="text-[10px] text-slate-500 uppercase font-black tracking-widest mb-1">Member
                                    Since</p>
                                <p className="text-xs font-bold">{user?.created_at ? new Date(user.created_at).getFullYear() : '—'}</p>
                            </div>
                        </div>

                        {pendingRequest && (
                            <div className="w-full bg-amber-500/20 border border-amber-500/30 rounded-xl p-4 text-left">
                                <p className="text-amber-400 text-[10px] font-black uppercase tracking-tighter flex items-center gap-1 mb-1">
                                    <Clock className="h-3 w-3"/> Pending Request
                                </p>
                                <p className="text-xs text-white leading-tight">
                                    Changing to Unit <strong>{pendingRequest.requested_unit}</strong>
                                </p>
                            </div>
                        )}
                    </CardContent>
                </Card>

                <Card className="lg:col-span-2 border-none shadow-xl">
                    <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
                        <div className="px-6 pt-6">
                            <TabsList className="grid w-full grid-cols-5 bg-slate-100 p-1 rounded-xl h-12">
                                <TabsTrigger value="personal"
                                             className="rounded-lg data-[state=active]:bg-white data-[state=active]:shadow-sm font-bold text-xs uppercase tracking-tight">
                                    <UserIcon className="h-3.5 w-3.5 mr-2"/> Personal
                                </TabsTrigger>
                                <TabsTrigger value="addresses"
                                             className="rounded-lg data-[state=active]:bg-white data-[state=active]:shadow-sm font-bold text-xs uppercase tracking-tight">
                                    <MapPin className="h-3.5 w-3.5 mr-2"/> Addresses
                                </TabsTrigger>
                                <TabsTrigger value="notifications"
                                             className="rounded-lg data-[state=active]:bg-white data-[state=active]:shadow-sm font-bold text-xs uppercase tracking-tight">
                                    <Bell className="h-3.5 w-3.5 mr-2"/> Preferences
                                </TabsTrigger>
                                <TabsTrigger value="documents"
                                             className="rounded-lg data-[state=active]:bg-white data-[state=active]:shadow-sm font-bold text-xs uppercase tracking-tight">
                                    <FileText className="h-3.5 w-3.5 mr-2"/> Documents
                                </TabsTrigger>
                                <TabsTrigger value="security"
                                             className="rounded-lg data-[state=active]:bg-white data-[state=active]:shadow-sm font-bold text-xs uppercase tracking-tight">
                                    <Shield className="h-3.5 w-3.5 mr-2"/> Security
                                </TabsTrigger>
                            </TabsList>
                        </div>

                        <form onSubmit={handleSubmit}>
                            <ScrollArea className="h-full max-h-[600px]">
                                <TabsContent value="personal" className="p-6 space-y-6 mt-0">
                                    <div className="space-y-4">
                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                            <div className="space-y-2">
                                                <Label htmlFor="full_name"
                                                       className="text-xs font-black uppercase text-slate-500">Full
                                                    Name</Label>
                                                <Input id="full_name" name="full_name" value={formData.full_name}
                                                       onChange={handleChange}
                                                       className="bg-slate-50 border-slate-200 focus:bg-white transition-colors"/>
                                            </div>
                                            <div className="space-y-2">
                                                <Label htmlFor="unit_number"
                                                       className="text-xs font-black uppercase text-slate-500">Unit
                                                    Assignment</Label>
                                                {canChangeUnit ? (
                                                    <Select value={formData.unit_number}
                                                            onValueChange={handleUnitChange} disabled={unitsLoading}>
                                                        <SelectTrigger className="bg-slate-50 border-slate-200">
                                                            <SelectValue placeholder="Select Unit"/>
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            {availableUnits.map(u => (
                                                                <SelectItem key={u.unit_number}
                                                                            value={u.unit_number}>{u.unit_number} ({u.unit_type})</SelectItem>
                                                            ))}
                                                        </SelectContent>
                                                    </Select>
                                                ) : (
                                                    <Input
                                                        value={formData.unit_number || 'Unassigned'}
                                                        readOnly
                                                        className="bg-slate-100 border-slate-200 text-slate-600 cursor-not-allowed"
                                                        title="Unit assignment can only be changed via a formal change request"
                                                    />
                                                )}
                                            </div>
                                        </div>

                                        <Separator className="bg-slate-100"/>

                                        {hasSecondaryContactEmail && (
                                            <div className="space-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4">
                                                <h4 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                                                    <Mail className="h-4 w-4 text-primary"/> Contact Emails
                                                </h4>
                                                <div className="grid grid-cols-1 gap-2 text-sm">
                                                    <div className="flex items-center justify-between gap-3 rounded-lg border border-emerald-200 bg-white px-3 py-2">
                                                        <div className="min-w-0">
                                                            <p className="text-[10px] font-black uppercase text-emerald-600">Primary</p>
                                                            <p className="break-all font-semibold text-slate-900">{primaryContactEmail}</p>
                                                        </div>
                                                        <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600"/>
                                                    </div>
                                                    <div className="flex flex-col gap-3 rounded-lg border border-slate-200 bg-white px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
                                                        <div className="min-w-0">
                                                            <p className="text-[10px] font-black uppercase text-slate-500">Secondary</p>
                                                            <p className="break-all font-semibold text-slate-900">{secondaryContactEmail}</p>
                                                        </div>
                                                        <Button
                                                            type="button"
                                                            variant="outline"
                                                            size="sm"
                                                            onClick={handlePromoteSecondaryEmail}
                                                            disabled={emailPrefLoading}
                                                            className="shrink-0"
                                                        >
                                                            {emailPrefLoading ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin"/> : <Mail className="mr-2 h-3.5 w-3.5"/>}
                                                            Make Primary
                                                        </Button>
                                                    </div>
                                                </div>
                                            </div>
                                        )}

                                        <div className="space-y-4">
                                            <h4 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                                                <Phone className="h-4 w-4 text-primary"/> Contact Phone Numbers
                                            </h4>
                                            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                                                <div className="space-y-2">
                                                    <Label htmlFor="phone_mobile"
                                                           className="text-[10px] font-black uppercase text-slate-400">Mobile</Label>
                                                    <Input id="phone_mobile" name="phone_mobile"
                                                           value={formData.phone_mobile} onChange={handleChange}
                                                           placeholder="0400 000 000"/>
                                                </div>
                                                <div className="space-y-2">
                                                    <Label htmlFor="phone_home"
                                                           className="text-[10px] font-black uppercase text-slate-400">Home</Label>
                                                    <Input id="phone_home" name="phone_home" value={formData.phone_home}
                                                           onChange={handleChange}/>
                                                </div>
                                                <div className="space-y-2">
                                                    <Label htmlFor="phone_business"
                                                           className="text-[10px] font-black uppercase text-slate-400">Business</Label>
                                                    <Input id="phone_business" name="phone_business"
                                                           value={formData.phone_business} onChange={handleChange}/>
                                                </div>
                                            </div>
                                        </div>

                                        {/* Co-owner details */}
                                        {user?.co_owner_name && (
                                            <div className="bg-blue-50 rounded-2xl p-5 border border-blue-100 mt-2">
                                                <h4 className="text-sm font-bold text-blue-900 mb-3 flex items-center gap-2">
                                                    <UserIcon className="h-4 w-4 text-blue-500"/> Co-owner Details
                                                </h4>
                                                <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-sm">
                                                    <div>
                                                        <p className="text-[10px] font-black uppercase text-blue-400 mb-0.5">Name</p>
                                                        <p className="font-semibold text-blue-900">{user.co_owner_name}</p>
                                                    </div>
                                                    {user?.co_owner_email && (
                                                        <div>
                                                            <p className="text-[10px] font-black uppercase text-blue-400 mb-0.5">Email</p>
                                                            <p className="font-semibold text-blue-900">{user.co_owner_email}</p>
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        )}

                                        {user?.role === 'tenant' && (
                                            <div className="bg-primary/5 rounded-2xl p-6 border border-primary/10 mt-6">
                                                <div className="flex items-center justify-between mb-4">
                                                    <div className="space-y-0.5">
                                                        <h4 className="text-sm font-bold text-primary flex items-center gap-2 uppercase tracking-tight">
                                                            Property Status
                                                        </h4>
                                                        <p className="text-xs text-slate-500">Update your current
                                                            relationship with this unit.</p>
                                                    </div>
                                                    <Switch
                                                        checked={formData.change_status}
                                                        onCheckedChange={(checked) => handleSwitchChange('change_status', checked)}
                                                    />
                                                </div>

                                                <div
                                                    className={`space-y-3 transition-opacity ${!formData.change_status ? 'opacity-40 pointer-events-none' : ''}`}>
                                                    <div
                                                        className="flex items-center justify-between p-3 bg-white rounded-xl border border-slate-200">
                                                        <Label htmlFor="is_managing_agent"
                                                               className="text-xs font-bold text-slate-700">I am the
                                                            managing agent</Label>
                                                        <Checkbox id="is_managing_agent"
                                                                  checked={formData.is_managing_agent}
                                                                  onCheckedChange={(c) => handleSwitchChange('is_managing_agent', c)}/>
                                                    </div>
                                                    <div
                                                        className="flex items-center justify-between p-3 bg-white rounded-xl border border-slate-200">
                                                        <Label htmlFor="is_tenanted"
                                                               className="text-xs font-bold text-slate-700">This
                                                            property is tenanted</Label>
                                                        <Checkbox id="is_tenanted" checked={formData.is_tenanted}
                                                                  onCheckedChange={(c) => handleSwitchChange('is_tenanted', c)}/>
                                                    </div>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                </TabsContent>

                                <TabsContent value="addresses" className="p-6 space-y-8 mt-0">
                                    <div className="space-y-4">
                                        <h4 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                                            <Home className="h-4 w-4 text-primary"/> Home Address
                                        </h4>
                                        <div className="space-y-4">
                                            <div className="space-y-2">
                                                <Label htmlFor="home_address"
                                                       className="text-[10px] font-black uppercase text-slate-400">Street
                                                    Address</Label>
                                                <Input id="home_address" name="home_address"
                                                       value={formData.home_address} onChange={handleChange}
                                                       placeholder="Unit/Street No, Street Name"/>
                                            </div>
                                            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                                                <div className="space-y-2">
                                                    <Label htmlFor="home_suburb"
                                                           className="text-[10px] font-black uppercase text-slate-400">Suburb</Label>
                                                    <Input id="home_suburb" name="home_suburb"
                                                           value={formData.home_suburb} onChange={handleChange}/>
                                                </div>
                                                <div className="space-y-2">
                                                    <Label htmlFor="home_state"
                                                           className="text-[10px] font-black uppercase text-slate-400">State</Label>
                                                    <Select value={formData.home_state}
                                                            onValueChange={(v) => handleSwitchChange('home_state', v)}>
                                                        <SelectTrigger><SelectValue/></SelectTrigger>
                                                        <SelectContent>
                                                            {['ACT', 'NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT'].map(s =>
                                                                <SelectItem key={s} value={s}>{s}</SelectItem>)}
                                                        </SelectContent>
                                                    </Select>
                                                </div>
                                                <div className="space-y-2">
                                                    <Label htmlFor="home_postcode"
                                                           className="text-[10px] font-black uppercase text-slate-400">Postcode</Label>
                                                    <Input id="home_postcode" name="home_postcode"
                                                           value={formData.home_postcode} onChange={handleChange}
                                                           maxLength={4}/>
                                                </div>
                                            </div>
                                        </div>
                                    </div>

                                    <Separator className="bg-slate-100"/>

                                    <div className="space-y-4">
                                        <div className="flex items-center justify-between">
                                            <h4 className="text-sm font-bold text-slate-900 flex items-center gap-2">
                                                <Mail className="h-4 w-4 text-primary"/> Postal Address
                                            </h4>
                                            <div
                                                className="flex items-center gap-2 bg-slate-50 px-3 py-1.5 rounded-lg border">
                                                <Label htmlFor="postal_same"
                                                       className="text-[10px] font-black uppercase text-slate-500">Same
                                                    as home</Label>
                                                <Switch id="postal_same" checked={formData.postal_same_as_home}
                                                        onCheckedChange={(c) => handleSwitchChange('postal_same_as_home', c)}/>
                                            </div>
                                        </div>

                                        {!formData.postal_same_as_home && (
                                            <div
                                                className="space-y-4 animate-in fade-in slide-in-from-top-2 duration-300">
                                                <div className="space-y-2">
                                                    <Label htmlFor="postal_address"
                                                           className="text-[10px] font-black uppercase text-slate-400">Street
                                                        / PO Box</Label>
                                                    <Input id="postal_address" name="postal_address"
                                                           value={formData.postal_address} onChange={handleChange}/>
                                                </div>
                                                <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
                                                    <Input name="postal_suburb" value={formData.postal_suburb}
                                                           onChange={handleChange} placeholder="Suburb"/>
                                                    <Select value={formData.postal_state}
                                                            onValueChange={(v) => handleSwitchChange('postal_state', v)}>
                                                        <SelectTrigger><SelectValue/></SelectTrigger>
                                                        <SelectContent>
                                                            {['ACT', 'NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT'].map(s =>
                                                                <SelectItem key={s} value={s}>{s}</SelectItem>)}
                                                        </SelectContent>
                                                    </Select>
                                                    <Input name="postal_postcode" value={formData.postal_postcode}
                                                           onChange={handleChange} placeholder="Postcode"
                                                           maxLength={4}/>
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                </TabsContent>

                                <TabsContent value="notifications" className="p-6 space-y-6 mt-0">
                                    <div className="space-y-4">
                                        <div className="flex items-center gap-3 mb-6">
                                            <div className="p-3 bg-blue-50 rounded-2xl">
                                                <Bell className="h-6 w-6 text-blue-600"/>
                                            </div>
                                            <div>
                                                <h4 className="text-lg font-black text-slate-900 uppercase tracking-tight">Correspondence
                                                    Settings</h4>
                                                <p className="text-xs text-muted-foreground">Select how you want to
                                                    receive official community notices.</p>
                                            </div>
                                        </div>

                                        <div className="grid grid-cols-1 gap-4">
                                            <div
                                                className="p-5 bg-primary/5 rounded-2xl border-2 border-primary/10 mb-2">
                                                <div
                                                    className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                                                    <div className="space-y-1">
                                                        <h5 className="font-bold text-slate-900 flex items-center gap-2">
                                                            <ShieldCheck className="h-4 w-4 text-primary"/> Strata Roll
                                                            Disclosure
                                                        </h5>
                                                        <p className="text-xs text-muted-foreground max-w-sm">Include my
                                                            contact details in the formal Corporate Register (Strata
                                                            Roll) viewable by management.</p>
                                                    </div>
                                                    <div className="flex items-center gap-2">
                                                        <Label
                                                            className="text-[10px] font-black uppercase text-slate-400">{formData.strata_roll_consent ? 'Consent Given' : 'Consent Withheld'}</Label>
                                                        <Switch checked={formData.strata_roll_consent}
                                                                onCheckedChange={(c) => handleSwitchChange('strata_roll_consent', c)}/>
                                                    </div>
                                                </div>
                                            </div>

                                            {[
                                                {
                                                    id: 'general',
                                                    title: 'General Correspondence',
                                                    desc: 'Building updates, community news, and general announcements.',
                                                    email: 'general_correspondence_email',
                                                    post: 'general_correspondence_post'
                                                },
                                                {
                                                    id: 'levy',
                                                    title: 'Levy & Financial Notices',
                                                    desc: 'Quarterly contribution notices and financial statements.',
                                                    email: 'levy_notices_email',
                                                    post: 'levy_notices_post',
                                                    restricted: user?.role === 'tenant'
                                                },
                                                {
                                                    id: 'meeting',
                                                    title: 'Meeting & AGM Notices',
                                                    desc: 'Agendas, minutes, and official notices of meetings.',
                                                    email: 'meeting_notices_email',
                                                    post: 'meeting_notices_post',
                                                    restricted: user?.role === 'tenant'
                                                }
                                            ].map(section => !section.restricted && (
                                                <div key={section.id}
                                                     className="p-5 bg-white rounded-2xl border-2 border-slate-100 hover:border-slate-200 transition-all shadow-sm">
                                                    <div
                                                        className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                                                        <div className="space-y-1">
                                                            <h5 className="font-bold text-slate-900">{section.title}</h5>
                                                            <p className="text-xs text-muted-foreground max-w-sm">{section.desc}</p>
                                                        </div>
                                                        <div className="flex items-center gap-6">
                                                            <div className="flex items-center gap-2">
                                                                <Label
                                                                    className="text-[10px] font-black uppercase text-slate-400">Email</Label>
                                                                <Switch checked={(formData as any)[section.email]}
                                                                        onCheckedChange={(c) => handleSwitchChange(section.email, c)}/>
                                                            </div>
                                                            <div className="flex items-center gap-2">
                                                                <Label
                                                                    className="text-[10px] font-black uppercase text-slate-400">Post</Label>
                                                                <Switch checked={(formData as any)[section.post]}
                                                                        onCheckedChange={(c) => handleSwitchChange(section.post, c)}/>
                                                            </div>
                                                        </div>
                                                    </div>
                                                </div>
                                            ))}
                                        </div>

                                        <div
                                            className="mt-8 p-4 bg-slate-900 text-white rounded-2xl relative overflow-hidden">
                                            <div className="absolute right-[-20px] bottom-[-20px] opacity-10">
                                                <CheckCircle2 className="h-24 w-24"/>
                                            </div>
                                            <div className="flex items-start gap-3 relative z-10">
                                                <ShieldCheck className="h-5 w-5 text-primary mt-0.5"/>
                                                <div className="space-y-1">
                                                    <p className="text-xs font-bold uppercase tracking-widest text-primary">Compliance
                                                        Note</p>
                                                    <p className="text-[11px] text-slate-300 leading-relaxed">
                                                        Under ACT Strata legislation, you must maintain at least one
                                                        valid method of service for official notices.
                                                        If you disable both Email and Post, you will default to Email
                                                        service.
                                                    </p>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </TabsContent>

                                <TabsContent value="documents" className="p-6 space-y-6 mt-0">
                                    <div className="space-y-4">
                                        <div className="flex items-center gap-3 mb-6">
                                            <div className="p-3 bg-purple-50 rounded-2xl">
                                                <FileText className="h-6 w-6 text-purple-600"/>
                                            </div>
                                            <div>
                                                <h4 className="text-lg font-black text-slate-900 uppercase tracking-tight">My
                                                    Documents</h4>
                                                <p className="text-xs text-muted-foreground">Access your personal
                                                    statements and tax summaries.</p>
                                            </div>
                                        </div>

                                        <Card
                                            className="border-2 border-slate-100 shadow-none rounded-2xl overflow-hidden">
                                            <CardContent className="p-0">
                                                <div
                                                    className="p-6 flex items-center justify-between hover:bg-slate-50 transition-colors">
                                                    <div className="flex items-center gap-4">
                                                        <div className="p-3 bg-emerald-50 text-emerald-600 rounded-xl">
                                                            <FileText size={24}/>
                                                        </div>
                                                        <div>
                                                            <p className="font-bold text-slate-900">Annual Tax
                                                                Summary</p>
                                                            <p className="text-xs text-slate-500">Financial
                                                                Year {selectedYear || '—'}</p>
                                                        </div>
                                                    </div>
                                                    <Button
                                                        type="button"
                                                        onClick={handleDownloadTaxSummary}
                                                        disabled={downloadingTax}
                                                        className="rounded-xl font-bold px-6"
                                                    >
                                                        {downloadingTax ?
                                                            <Loader2 className="h-4 w-4 animate-spin mr-2"/> :
                                                            <Download className="h-4 w-4 mr-2"/>}
                                                        Download PDF
                                                    </Button>
                                                </div>
                                            </CardContent>
                                        </Card>

                                        <div className="p-4 bg-blue-50 border border-blue-100 rounded-2xl flex gap-3">
                                            <Info className="h-5 w-5 text-blue-600 shrink-0"/>
                                            <p className="text-xs text-blue-800 leading-relaxed">
                                                Building-wide documents like AGM Minutes, Insurance Policies, and the
                                                Strata Plan are available in the
                                                <button type="button"
                                                        onClick={() => router.push('/documents')}
                                                        className="font-bold underline ml-1">Document Library</button>.
                                            </p>
                                        </div>
                                    </div>
                                </TabsContent>

                                <TabsContent value="security" className="p-6 space-y-6 mt-0">
                                    <div className="space-y-4">
                                        <div>
                                            <h3 className="font-black text-sm uppercase text-slate-700 mb-1">Change
                                                Password</h3>
                                            <p className="text-xs text-slate-500">Update your password. Changes are
                                                logged for security auditing.</p>
                                        </div>
                                        <Separator/>
                                        <div className="space-y-4 max-w-md">
                                            <div className="space-y-2">
                                                <Label htmlFor="current_password"
                                                       className="text-xs font-black uppercase text-slate-500">Current
                                                    Password</Label>
                                                <Input
                                                    id="current_password"
                                                    type="password"
                                                    autoComplete="current-password"
                                                    value={pwForm.current_password}
                                                    onChange={e => setPwForm(f => ({
                                                        ...f,
                                                        current_password: e.target.value
                                                    }))}
                                                    className="bg-slate-50 border-slate-200 focus:bg-white transition-colors"
                                                />
                                            </div>
                                            <div className="space-y-2">
                                                <Label htmlFor="new_password"
                                                       className="text-xs font-black uppercase text-slate-500">New
                                                    Password</Label>
                                                <Input
                                                    id="new_password"
                                                    type="password"
                                                    autoComplete="new-password"
                                                    value={pwForm.new_password}
                                                    onChange={e => setPwForm(f => ({
                                                        ...f,
                                                        new_password: e.target.value
                                                    }))}
                                                    className="bg-slate-50 border-slate-200 focus:bg-white transition-colors"
                                                    minLength={8}
                                                />
                                                <p className="text-xs text-slate-400">Minimum 8 characters</p>
                                            </div>
                                            <div className="space-y-2">
                                                <Label htmlFor="confirm_password"
                                                       className="text-xs font-black uppercase text-slate-500">Confirm
                                                    New Password</Label>
                                                <Input
                                                    id="confirm_password"
                                                    type="password"
                                                    autoComplete="new-password"
                                                    value={pwForm.confirm_password}
                                                    onChange={e => setPwForm(f => ({
                                                        ...f,
                                                        confirm_password: e.target.value
                                                    }))}
                                                    className="bg-slate-50 border-slate-200 focus:bg-white transition-colors"
                                                />
                                            </div>
                                            <Button type="button" onClick={handleChangePassword} disabled={pwLoading}
                                                    className="px-8 shadow-lg shadow-primary/20">
                                                {pwLoading ? <Loader2 className="h-4 w-4 animate-spin mr-2"/> :
                                                    <Shield className="h-4 w-4 mr-2"/>}
                                                Update Password
                                            </Button>
                                        </div>
                                        <Separator/>
                                        <div className="p-4 bg-amber-50 border border-amber-100 rounded-2xl flex gap-3">
                                            <Shield className="h-5 w-5 text-amber-600 shrink-0 mt-0.5"/>
                                            <div>
                                                <p className="text-xs font-bold text-amber-800">Password Change
                                                    Audit</p>
                                                <p className="text-xs text-amber-700 leading-relaxed mt-1">All password
                                                    changes are logged with timestamp, IP address, and method. If you
                                                    suspect unauthorized access, contact your Strata Manager
                                                    immediately.</p>
                                            </div>
                                        </div>
                                    </div>
                                </TabsContent>
                            </ScrollArea>

                            {activeTab !== 'security' && (
                                <div className="px-6 py-4 bg-slate-50 border-t flex justify-end gap-3 rounded-b-xl">
                                    <Button type="button" variant="ghost" onClick={() => window.location.reload()}>Discard
                                        Changes</Button>
                                    <Button type="submit" disabled={loading}
                                            className="px-8 shadow-lg shadow-primary/20">
                                        {loading ? <Loader2 className="h-4 w-4 animate-spin"/> : 'Save Profile'}
                                    </Button>
                                </div>
                            )}
                        </form>
                    </Tabs>
                </Card>
            </div>
        </div>
    );
};
/**
 * @generated FunctionHeader
 * Function: ShieldCheck
 * Path: frontend/src/pages/dashboard/ProfilePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ShieldCheck: React.FC<{ className?: string }> = ({className}) => (
    <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10"/>
        <path d="m9 12 2 2 4-4"/>
    </svg>
);
/**
 * @generated FunctionHeader
 * Function: ScrollArea
 * Path: frontend/src/pages/dashboard/ProfilePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ScrollArea: React.FC<{ children: React.ReactNode, className?: string }> = ({children, className}) => (
    <div className={`overflow-y-auto ${className}`}>
        {children}
    </div>
);

export default ProfilePage;
