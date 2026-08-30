"use client";
import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import {
    BookOpen,
    Calendar,
    CheckCircle2,
    Home,
    LayoutDashboard,
    Loader2,
    LogOut,
    Mail,
    Pencil,
    Phone,
    Save,
    Shield,
    User,
    X
} from 'lucide-react';
import { toast } from 'sonner';
import axios from 'axios';

const API_URL = process.env.NEXT_PUBLIC_BACKEND_URL || '';

// Role display helpers
const ROLE_LABELS = {
    owner: 'Owner',
    tenant: 'Tenant',
    guest: 'Short-Term Guest',
    ec_member: 'EC Member',
    chairman: 'Chairman',
    strata_manager: 'Strata Manager',
    admin_staff: 'Admin Staff',
    super_admin: 'Administrator',
};

const QUICK_GUIDE_MAP = {
    owner: 'owner',
    tenant: 'tenant',
    guest: 'guest',
    ec_member: 'ec',
    strata_admin: 'strata_admin',
    strata_manager: 'strata_manager',
    admin_staff: 'admin_staff',
    super_admin: 'admin',
};

// Approval message by role
const APPROVAL_MSG = {
    owner: 'Your application is being reviewed by the Strata Manager. You will receive an email once ownership is verified and your account is approved.',
    tenant: 'Your application has been sent to the unit owner for confirmation and will then be reviewed by the Strata Manager.',
    guest: 'Your application has been sent to the unit owner. As a short-term guest, approval is typically within 2 hours.',
};
/**
 * @generated FunctionHeader
 * Function: InfoRow
 * Path: frontend/src/pages/auth/RegisterSuccessPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const InfoRow = ({icon: Icon, label, value}) => {
    if (!value) return null;
    return (
        <div className="flex items-start gap-3 py-2 border-b last:border-0">
            <Icon className="h-4 w-4 text-muted-foreground mt-0.5 flex-shrink-0"/>
            <div className="flex-1 min-w-0">
                <p className="text-xs text-muted-foreground">{label}</p>
                <p className="text-sm font-medium truncate">{value}</p>
            </div>
        </div>
    );
};
/**
 * @generated FunctionHeader
 * Function: RegisterSuccessPage
 * Path: frontend/src/pages/auth/RegisterSuccessPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const RegisterSuccessPage = () => {
    const {user, token, logout} = useAuth();
    const router = useRouter();

    const [editing, setEditing] = useState(false);
    const [saving, setSaving] = useState(false);
    const [phone, setPhone] = useState(user?.phone || '');
    const [phoneMobile, setPhoneMobile] = useState(user?.phone_mobile || '');
    const [homeAddress, setHomeAddress] = useState(user?.home_address || '');

    // Redirect to /register if not logged in
    if (!user) {
        if (typeof window !== 'undefined') {
            router.replace('/register');
        }
        return null;
    }

    const isApproved = user?.is_approved;
    /**
     * @generated FunctionHeader
     * Function: handleLogout
     * Path: frontend/src/pages/auth/RegisterSuccessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleLogout = () => {
        logout();
        window.location.href = '/';
    };
    /**
     * @generated FunctionHeader
     * Function: handleSave
     * Path: frontend/src/pages/auth/RegisterSuccessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSave = async () => {
        if (!user?.id) return;
        setSaving(true);
        try {
            await axios.put(
                `${API_URL}/api/users/${user.id}`,
                {phone, phone_mobile: phoneMobile, home_address: homeAddress},
                {headers: {Authorization: `Bearer ${token}`}}
            );
            toast.success('Contact details updated');
            setEditing(false);
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'Failed to save details');
        } finally {
            setSaving(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleCancel
     * Path: frontend/src/pages/auth/RegisterSuccessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleCancel = () => {
        setPhone(user?.phone || '');
        setPhoneMobile(user?.phone_mobile || '');
        setHomeAddress(user?.home_address || '');
        setEditing(false);
    };

    const role = user?.role || 'owner';
    const guideSlug = QUICK_GUIDE_MAP[ role ] || 'owner';
    const quickGuidePath = `/user-guides/quick_role_${guideSlug}.html`;
    const approvalMsg = APPROVAL_MSG[ role ] || 'Your account is pending review. You will be notified once approved.';
    /**
     * @generated FunctionHeader
     * Function: formatDate
     * Path: frontend/src/pages/auth/RegisterSuccessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const formatDate = (dateStr) => {
        if (!dateStr) return null;
        try {
            return new Date(dateStr).toLocaleDateString('en-AU', {
                day: 'numeric', month: 'long', year: 'numeric',
            });
        } catch {
            return dateStr;
        }
    };

    return (
        <div className="min-h-screen flex items-center justify-center bg-muted/30 px-4 py-12">
            <div className="w-full max-w-2xl space-y-5">

                {/* Status card */}
                <Card className="card-dashboard">
                    <CardHeader>
                        <div className="flex items-center gap-3">
                            <div
                                className={`h-10 w-10 rounded-full flex items-center justify-center flex-shrink-0 ${isApproved ? 'bg-green-50' : 'bg-emerald-50'}`}>
                                <CheckCircle2
                                    className={`h-6 w-6 ${isApproved ? 'text-green-600' : 'text-emerald-600'}`}/>
                            </div>
                            <div>
                                <CardTitle>Registration received</CardTitle>
                                <CardDescription>{approvalMsg}</CardDescription>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent className="space-y-4">
                        <div className="flex flex-wrap gap-2">
                            <Badge
                                variant={isApproved ? 'default' : 'secondary'}
                                className={`text-xs uppercase ${isApproved ? 'bg-green-600 text-white' : ''}`}
                            >
                                {isApproved ? 'Approved' : 'Pending review'}
                            </Badge>
                            <Badge variant="outline" className="text-xs uppercase">
                                {ROLE_LABELS[ role ] || role}
                            </Badge>
                        </div>

                        {/* Quick Guide / Dashboard panel */}
                        <div className="rounded-lg border bg-background p-4 space-y-2">
                            {isApproved ? (
                                <>
                                    <p className="text-sm font-medium">You are in</p>
                                    <p className="text-sm text-muted-foreground">
                                        Your account has been approved. You now have full access to the portal.
                                        Head to the dashboard or review your role-specific quick guide to get started.
                                    </p>
                                    <div className="flex flex-wrap gap-2 mt-1">
                                        <Link href="/dashboard">
                                            <Button variant="default" size="sm">
                                                <LayoutDashboard className="mr-2 h-4 w-4"/>
                                                Dashboard
                                            </Button>
                                        </Link>
                                        <a href={quickGuidePath} target="_blank" rel="noreferrer">
                                            <Button variant="outline" size="sm">
                                                <BookOpen className="mr-2 h-4 w-4"/>
                                                {ROLE_LABELS[ role ] || 'Quick'} Quick Guide
                                            </Button>
                                        </a>
                                    </div>
                                </>
                            ) : (
                                <>
                                    <p className="text-sm font-medium">While you wait</p>
                                    <p className="text-sm text-muted-foreground">
                                        All portal features unlock after your account is approved.
                                        In the meantime, review your role-specific quick guide.
                                    </p>
                                    <a href={quickGuidePath} target="_blank" rel="noreferrer">
                                        <Button variant="default" size="sm" className="mt-1">
                                            <BookOpen className="mr-2 h-4 w-4"/>
                                            {ROLE_LABELS[ role ] || 'Quick'} Quick Guide
                                        </Button>
                                    </a>
                                </>
                            )}
                        </div>

                        <div
                            className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pt-1">
                            <p className="text-xs text-muted-foreground">
                                Need help?{' '}
                                <a className="underline" href="mailto:ec@eastgateresidences.com.au">
                                    ec@eastgateresidences.com.au
                                </a>
                            </p>
                            <Button variant="ghost" size="sm" onClick={handleLogout}>
                                <LogOut className="mr-2 h-4 w-4"/>
                                Log out
                            </Button>
                        </div>
                    </CardContent>
                </Card>

                {/* Registration summary */}
                {user && (
                    <Card className="card-dashboard">
                        <CardHeader className="pb-3">
                            <div className="flex items-center justify-between">
                                <div>
                                    <CardTitle className="text-base">Your Registration Details</CardTitle>
                                    <CardDescription>Details submitted with your application</CardDescription>
                                </div>
                                {!editing && (
                                    <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                                        <Pencil className="mr-2 h-3.5 w-3.5"/>
                                        Update contact info
                                    </Button>
                                )}
                            </div>
                        </CardHeader>
                        <CardContent className="space-y-1">
                            {/* Fixed info — cannot be changed while pending */}
                            <InfoRow icon={User} label="Full name" value={user.full_name}/>
                            <InfoRow icon={Mail} label="Email" value={user.email}/>
                            <InfoRow icon={Shield} label="Role" value={ROLE_LABELS[ role ] || role}/>
                            {user.unit_number && (
                                <InfoRow icon={Home} label="Unit / Lot" value={user.unit_number}/>
                            )}
                            {user.end_date && (
                                <InfoRow icon={Calendar} label="Stay end date" value={formatDate(user.end_date)}/>
                            )}

                            {/* Editable contact details */}
                            {editing ? (
                                <div className="pt-3 space-y-3 border-t mt-2">
                                    <p className="text-sm font-medium">Update contact information</p>

                                    <div className="space-y-1">
                                        <Label htmlFor="phone" className="text-xs">Mobile phone</Label>
                                        <div className="relative">
                                            <Phone
                                                className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                                            <Input
                                                id="phone"
                                                value={phone}
                                                onChange={e => setPhone(e.target.value)}
                                                placeholder="0400 000 000"
                                                className="pl-9 h-9 text-sm"
                                            />
                                        </div>
                                    </div>

                                    <div className="space-y-1">
                                        <Label htmlFor="phone_mobile" className="text-xs">Secondary phone
                                            (optional)</Label>
                                        <div className="relative">
                                            <Phone
                                                className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                                            <Input
                                                id="phone_mobile"
                                                value={phoneMobile}
                                                onChange={e => setPhoneMobile(e.target.value)}
                                                placeholder="(02) 0000 0000"
                                                className="pl-9 h-9 text-sm"
                                            />
                                        </div>
                                    </div>

                                    {( role === 'owner' ) && (
                                        <div className="space-y-1">
                                            <Label htmlFor="home_address" className="text-xs">Postal / home address
                                                (optional)</Label>
                                            <div className="relative">
                                                <Home
                                                    className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                                                <Input
                                                    id="home_address"
                                                    value={homeAddress}
                                                    onChange={e => setHomeAddress(e.target.value)}
                                                    placeholder="123 Example St, Suburb ACT 2600"
                                                    className="pl-9 h-9 text-sm"
                                                />
                                            </div>
                                        </div>
                                    )}

                                    <div className="flex gap-2 pt-1">
                                        <Button size="sm" onClick={handleSave} disabled={saving}>
                                            {saving ? <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin"/> :
                                                <Save className="mr-2 h-3.5 w-3.5"/>}
                                            Save
                                        </Button>
                                        <Button size="sm" variant="ghost" onClick={handleCancel} disabled={saving}>
                                            <X className="mr-2 h-3.5 w-3.5"/>
                                            Cancel
                                        </Button>
                                    </div>
                                </div>
                            ) : (
                                <div className="pt-1">
                                    <InfoRow icon={Phone} label="Phone" value={user.phone || user.phone_mobile || '—'}/>
                                </div>
                            )}
                        </CardContent>
                    </Card>
                )}

                {/* What happens next */}
                <Card className="card-dashboard border-l-4 border-l-primary/40">
                    <CardContent className="pt-5 pb-4">
                        <p className="text-sm font-semibold mb-3">What happens next?</p>
                        <ol className="space-y-2 text-sm text-muted-foreground list-none">
                            {role === 'guest' ? (
                                <>
                                    <li className="flex gap-2"><span
                                        className="font-semibold text-primary shrink-0">1.</span>The unit owner receives
                                        an email and approves or declines within 2 hours.
                                    </li>
                                    <li className="flex gap-2"><span
                                        className="font-semibold text-primary shrink-0">2.</span>On approval, you will
                                        receive a welcome email with portal access and the house rules.
                                    </li>
                                    <li className="flex gap-2"><span
                                        className="font-semibold text-primary shrink-0">3.</span>Your account
                                        auto-expires on your stated end date.
                                    </li>
                                </>
                            ) : role === 'tenant' ? (
                                <>
                                    <li className="flex gap-2"><span
                                        className="font-semibold text-primary shrink-0">1.</span>The unit owner reviews
                                        and approves your registration.
                                    </li>
                                    <li className="flex gap-2"><span
                                        className="font-semibold text-primary shrink-0">2.</span>The Strata Manager
                                        performs a final review.
                                    </li>
                                    <li className="flex gap-2"><span
                                        className="font-semibold text-primary shrink-0">3.</span>You receive a welcome
                                        email with full portal access.
                                    </li>
                                </>
                            ) : (
                                <>
                                    <li className="flex gap-2"><span
                                        className="font-semibold text-primary shrink-0">1.</span>The Strata Manager
                                        verifies your ownership against the strata roll.
                                    </li>
                                    <li className="flex gap-2"><span
                                        className="font-semibold text-primary shrink-0">2.</span>You receive a welcome
                                        email with full portal access including levy and financial information.
                                    </li>
                                    <li className="flex gap-2"><span
                                        className="font-semibold text-primary shrink-0">3.</span>If there is any query
                                        about your details you will be contacted via email.
                                    </li>
                                </>
                            )}
                        </ol>
                    </CardContent>
                </Card>

            </div>
        </div>
    );
};

export default RegisterSuccessPage;
