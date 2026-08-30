// @featuretrace:user-management — Public self-service registration page: invite prefill, building/unit selection, by-law acceptance, account creation.
// Layer: frontend
// Data flow: RegisterPage.jsx (public, unauthenticated /register) → GET /auth/registration-invites/{token},
//             GET /auth/buildings, GET /units/all, GET /by-laws/current → AuthContext.register()
//             → POST /auth/register [routers/auth.py:801] → db.users (global) + db.memberships /
//             db.user_units / db.resident_registration_invites (scope param: building|global)
//             → router.push('/register/success')
// Related: frontend/src/app/(public)/register/page.tsx (App Router wrapper)
//           frontend/src/contexts/AuthContext.tsx (register() — posts the payload)
//           backend/routers/auth.py (POST /auth/register:801, GET /auth/registration-invites/{token}:750, GET /auth/buildings:3499)
//           backend/server.py (GET /units/all:10096, GET /by-laws/current:10241)
//           backend/models/user.py (UserCreate, AuthResponse — request/response contract)
// Toggle: (none — registration is public by design)
// Guard: POST /auth/register uses get_optional_building (never raises; falls back to DEFAULT_BUILDING_ID
//         when the caller has no building context) and is rate limited by
//         @rate_limit("rate_limit_register", 5) → db.site_settings id="rate_limits".
// Collection: users (global), memberships, user_units, resident_registration_invites
// Table: core.users, core.user_units (identity_core cutover path)
// Tests: tests/frontend/unit/pages/auth/RegisterPage.test.jsx
//         tests/frontend/unit/pages/auth/RegisterPage.multiunit.test.tsx
//         tests/backend/test_register_endpoint.py
//         tests/backend/test_registration_flow.py
//         tests/backend/test_resident_registration_invites.py
"use client";
import React, { useCallback, useEffect, useState } from 'react';
import {getApiErrorDetail} from '@/lib/api-error';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Card, CardContent } from '../../components/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '../../components/ui/dialog';
import { Checkbox } from '../../components/ui/checkbox';
import {
    Building2,
    Calendar,
    ChevronRight,
    Eye,
    EyeOff,
    FileText,
    Home,
    Loader2,
    Lock,
    Mail,
    Phone,
    Plus,
    Trash2,
    User,
} from 'lucide-react';
import { toast } from 'sonner';
import axios from 'axios';

const API_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

const ROLES = [
    {value: 'owner', label: 'Owner', description: 'I own a unit in this building'},
    {value: 'tenant', label: 'Tenant', description: 'I am renting a unit'},
    {value: 'guest', label: 'Short-term Guest', description: 'Temporary stay (e.g. Airbnb)'},
];
/**
 * @generated FunctionHeader
 * Function: Skeleton
 * Path: frontend/src/pages/auth/RegisterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const Skeleton = ({className = ''}) => (
    <div className={`animate-pulse bg-muted rounded ${className}`}/>
);
/**
 * @generated FunctionHeader
 * Function: UnitSkeleton
 * Path: frontend/src/pages/auth/RegisterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const UnitSkeleton = () => (
    <div className="space-y-2">
        <Skeleton className="h-4 w-24"/>
        <Skeleton className="h-10 w-full"/>
    </div>
);
/**
 * @generated FunctionHeader
 * Function: RegisterPage
 * Path: frontend/src/pages/auth/RegisterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const RegisterPage = () => {
    const router = useRouter();
    const searchParams = useSearchParams();
    const {register} = useAuth();
    // useSearchParams() can be null outside a Suspense boundary / during
    // prerender, so guard rather than letting the whole sign-up page throw.
    const inviteToken = searchParams?.get('invite') || '';

    const [allUnits, setAllUnits] = useState([]);
    const [building, setBuilding] = useState(null);
    const [allBuildings, setAllBuildings] = useState([]);
    const [invitePrefill, setInvitePrefill] = useState(null);
    const [byLawsContent, setByLawsContent] = useState(null);

    const [loadingUnits, setLoadingUnits] = useState(true);
    const [loadingBuilding, setLoadingBuilding] = useState(true);
    const [loading, setLoading] = useState(false);
    const [showPassword, setShowPassword] = useState(false);
    const [showConfirm, setShowConfirm] = useState(false);
    const [showByLawsModal, setShowByLawsModal] = useState(false);
    const [fieldErrors, setFieldErrors] = useState({});

    const [formData, setFormData] = useState({
        role: '',
        unit_number: '',
        full_name: '',
        email: '',
        phone: '',
        password: '',
        confirmPassword: '',
        by_laws_acknowledged: false,
        tenancy_start_date: '',
        landlord_name: '',
        check_in_date: '',
        check_out_date: '',
    });
    const [ownsMultipleUnits, setOwnsMultipleUnits] = useState(false);
    const [additionalUnitNumbers, setAdditionalUnitNumbers] = useState(['']);
    const [alreadyRegistered, setAlreadyRegistered] = useState(null); // {message, unit_number}

    const fetchRegistrationInvite = useCallback(async () => {
        if (!inviteToken) return false;
        try {
            setLoadingBuilding(true);
            const {data} = await axios.get(`${API_URL}/api/auth/registration-invites/${encodeURIComponent(inviteToken)}`);
            const inviteBuilding = data?.building;
            if (!inviteBuilding?.id) throw new Error('Missing invite building');
            setInvitePrefill(data);
            setAllBuildings([inviteBuilding]);
            setBuilding(inviteBuilding);
            setFormData(prev => ({
                ...prev,
                role: data.role || 'owner',
                unit_number: data.unit_number || '',
                full_name: data.full_name || '',
                email: data.email || '',
                phone: data.phone || '',
            }));
            return true;
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'This registration link is invalid or has expired.');
            setInvitePrefill(null);
            return false;
        } finally {
            setLoadingBuilding(false);
        }
    }, [inviteToken]);

    const fetchBuilding = useCallback(async () => {
        try {
            setLoadingBuilding(true);
            const {data} = await axios.get(`${API_URL}/api/auth/buildings`);
            if (Array.isArray(data) && data.length > 0) {
                setAllBuildings(data);
                setBuilding(data[ 0 ]); // auto-select first (single-building default)
            } else {
                setLoadingUnits(false); // no buildings → units will never load
            }
        } catch (_) {
            setLoadingUnits(false); // building fetch failed → units will never load
        } finally {
            setLoadingBuilding(false);
        }
    }, []);

    const fetchUnits = useCallback(async (buildingId) => {
        try {
            setLoadingUnits(true);
            const params = buildingId ? `?building_id=${encodeURIComponent(buildingId)}` : '';
            const {data} = await axios.get(`${API_URL}/api/units/all${params}`);
            setAllUnits(Array.isArray(data) ? data : []);
        } catch (_) {
            toast.error('Could not load units. Please refresh the page.');
        } finally {
            setLoadingUnits(false);
        }
    }, []);

    const fetchByLaws = useCallback(async () => {
        try {
            const {data} = await axios.get(`${API_URL}/api/by-laws/current`);
            if (data && !data.detail) setByLawsContent(data);
        } catch (_) {
            // optional — silently skip
        }
    }, []);

    useEffect(() => {
        (async () => {
            const loadedInvite = await fetchRegistrationInvite();
            if (!loadedInvite) await fetchBuilding();
        })();
        fetchByLaws();
    }, [fetchBuilding, fetchByLaws, fetchRegistrationInvite]);

    // Re-fetch units scoped to the selected building; reset all unit-related form state.
    useEffect(() => {
        if (building?.id) {
            fetchUnits(building.id);
            setFormData(prev => ( {...prev, unit_number: invitePrefill?.unit_number || ''} ));
            setAdditionalUnitNumbers(['']);
            setOwnsMultipleUnits(false);
        }
    }, [building?.id, fetchUnits, invitePrefill?.unit_number]);

    const apartments = allUnits.filter(u => u.unit_type === 'apartment');
    const townhouses = allUnits.filter(u => u.unit_type === 'townhouse');
    const others = allUnits.filter(u => u.unit_type !== 'apartment' && u.unit_type !== 'townhouse');
    const hasUnits = allUnits.length > 0;
    /**
     * @generated FunctionHeader
     * Function: set
     * Path: frontend/src/pages/auth/RegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const set = (field, value) => {
        setFormData(prev => ( {...prev, [ field ]: value} ));
        setFieldErrors(prev => ( {...prev, [ field ]: undefined} ));
    };
    /**
     * @generated FunctionHeader
     * Function: handleChange
     * Path: frontend/src/pages/auth/RegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleChange = (e) => {
        const {name, value, type, checked} = e.target;
        if (name === 'email') setAlreadyRegistered(null);
        set(name, type === 'checkbox' ? checked : value);
    };
    /**
     * @generated FunctionHeader
     * Function: handleRoleChange
     * Path: frontend/src/pages/auth/RegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRoleChange = (value) => {
        setFormData(prev => ( {
            ...prev,
            role: value,
            unit_number: '',
            by_laws_acknowledged: false,
            tenancy_start_date: '',
            landlord_name: '',
            check_in_date: '',
            check_out_date: '',
        } ));
        setOwnsMultipleUnits(false);
        setAdditionalUnitNumbers(['']);
        setFieldErrors({});
        setAlreadyRegistered(null);
    };
    /**
     * @generated FunctionHeader
     * Function: handleAdditionalUnitChange
     * Path: frontend/src/pages/auth/RegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAdditionalUnitChange = (index, value) => {
        setAdditionalUnitNumbers(prev => {
            const next = [...prev];
            next[ index ] = value;
            return next;
        });
    };
    /**
     * @generated FunctionHeader
     * Function: addAdditionalUnitSlot
     * Path: frontend/src/pages/auth/RegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const addAdditionalUnitSlot = () => {
        if (additionalUnitNumbers.length < 9) {
            setAdditionalUnitNumbers(prev => [...prev, '']);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: removeAdditionalUnitSlot
     * Path: frontend/src/pages/auth/RegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const removeAdditionalUnitSlot = (index) => {
        setAdditionalUnitNumbers(prev => prev.filter((_, i) => i !== index));
    };
    /**
     * @generated FunctionHeader
     * Function: validate
     * Path: frontend/src/pages/auth/RegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const validate = () => {
        const errors = {};
        if (!formData.full_name.trim()) errors.full_name = 'Full name is required';
        if (!formData.email.trim()) errors.email = 'Email is required';
        else if (!/\S+@\S+\.\S+/.test(formData.email)) errors.email = 'Invalid email address';
        if (!formData.phone.trim()) errors.phone = 'Phone number is required';
        if (!formData.unit_number) errors.unit_number = 'Please select your unit';
        if (!formData.password) errors.password = 'Password is required';
        else if (formData.password.length < 8) errors.password = 'Password must be at least 8 characters';
        if (formData.password !== formData.confirmPassword)
            errors.confirmPassword = 'Passwords do not match';
        if (formData.role === 'guest') {
            if (!formData.check_in_date) errors.check_in_date = 'Check-in date is required';
            if (!formData.check_out_date) errors.check_out_date = 'Check-out date is required';
            if (formData.check_in_date && formData.check_out_date) {
                const inDate = new Date(formData.check_in_date + 'T00:00:00');
                const outDate = new Date(formData.check_out_date + 'T00:00:00');
                if (outDate <= inDate) errors.check_out_date = 'Check-out must be after check-in';
                const days = Math.round(( outDate - inDate ) / 86400000);
                if (days >= 365) errors.check_out_date = 'Guest stays must be less than 365 days';
            }
        }
        if (!formData.by_laws_acknowledged)
            errors.by_laws_acknowledged = 'You must acknowledge the by-laws to register';
        return errors;
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/auth/RegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        const errors = validate();
        if (Object.keys(errors).length > 0) {
            setFieldErrors(errors);
            toast.error('Please fix the errors below');
            return;
        }
        setLoading(true);
        try {
            const payload = {...formData};
            delete payload.confirmPassword;
            if (formData.role === 'guest') {
                // UserCreate.end_date stores checkout; check_in_date has no backend field
                payload.end_date = payload.check_out_date || null;
                delete payload.check_in_date;
                delete payload.check_out_date;
            } else {
                delete payload.check_in_date;
                delete payload.check_out_date;
            }
            if (formData.role !== 'tenant') {
                delete payload.tenancy_start_date;
                delete payload.landlord_name;
            }
            if (formData.role === 'owner' && ownsMultipleUnits) {
                payload.additional_unit_numbers = additionalUnitNumbers.filter(u => u.trim());
            }
            if (inviteToken) {
                payload.invite_token = inviteToken;
            }
            await register(payload, building?.id);
            const unitSuffix = ( formData.role === 'owner' && ownsMultipleUnits && additionalUnitNumbers.filter(u => u.trim()).length > 0 )
                ? ` for ${1 + additionalUnitNumbers.filter(u => u.trim()).length} units`
                : '';
            const msg =
                formData.role === 'owner'
                    ? `Your account${unitSuffix} is pending approval and ownership verification.`
                    : formData.role === 'tenant'
                        ? 'Your account is pending approval. You will be notified once approved.'
                        : 'Welcome! Your guest account has been created.';
            toast.success(`Registration successful! ${msg}`);
            router.push('/register/success');
        } catch (err) {
            // Read through getApiErrorDetail: the API wraps a conflict as
            // { error: { code, message, metadata } }, not { detail: {...} }. Reading
            // `data.detail.code` yields undefined for every branch below, so all five
            // messages fell through to the generic toast and the "Reset password" banner
            // never appeared — the 409 UX existed but was unreachable.
            const {code, message, metadata} = getApiErrorDetail(err);
            const unitNumber = metadata.unit_number || '';
            // Always clear a stale already-registered banner before showing the new error.
            setAlreadyRegistered(null);
            const BANNER_VARIANTS = {
                already_registered: 'amber',        // offers Sign in + Reset password
                pending_now_approved: 'green',
                pending_approval: 'blue',
                archived_user_return_request: 'blue',
            };
            if (BANNER_VARIANTS[code]) {
                setAlreadyRegistered({
                    message,
                    unit_number: code === 'archived_user_return_request' ? '' : unitNumber,
                    variant: BANNER_VARIANTS[code],
                });
            } else if (code === 'owner_exists_add_unit') {
                toast.error(message || 'You already have an owner account. Please log in and add additional units from your Profile page.', {duration: 8000});
            } else {
                toast.error(message || 'Registration failed. Please try again.');
            }
        } finally {
            setLoading(false);
        }
    };

    const today = new Date().toISOString().split('T')[ 0 ];
    const roleSelected = !!formData.role;
    const showByLawsSection = !!byLawsContent;
    /**
     * @generated FunctionHeader
     * Function: FieldError
     * Path: frontend/src/pages/auth/RegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const FieldError = ({field}) =>
        fieldErrors[ field ] ? (
            <p className="text-xs text-destructive mt-1" role="alert">{fieldErrors[ field ]}</p>
        ) : null;

    return (
        <div className="min-h-screen flex items-center justify-center bg-muted/30 px-4 py-12"
             data-testid="register-page">
            <div className="w-full max-w-lg">
                <div className="text-center mb-8">
                    <Link href="/" className="inline-flex items-center gap-2 mb-6">
                        <Building2 className="h-10 w-10 text-primary"/>
                        <span className="text-2xl font-bold">{building?.name || 'East Gate'}</span>
                    </Link>
                    <h1 className="text-2xl font-bold">Create Your Account</h1>
                    <p className="text-muted-foreground mt-1">Join the {building?.name || 'building'} community</p>
                </div>

                <Card className="card-dashboard">
                    <CardContent className="pt-6">
                        <form onSubmit={handleSubmit} className="space-y-6" data-testid="register-form" noValidate>

                            {/* Step 1: Role */}
                            <div className="space-y-2">
                                <Label htmlFor="role">
                                    I am a… <span className="text-destructive">*</span>
                                </Label>
                                <Select value={formData.role} onValueChange={handleRoleChange} disabled={!!invitePrefill}>
                                    <SelectTrigger id="role" data-testid="role-select">
                                        <SelectValue placeholder="Select your role"/>
                                    </SelectTrigger>
                                    <SelectContent>
                                        {ROLES.map(r => (
                                            <SelectItem key={r.value} value={r.value}>
                                                <span className="font-medium">{r.label}</span>
                                                <span
                                                    className="text-muted-foreground text-xs ml-2">— {r.description}</span>
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>

                            {/* Step 2: Building */}
                            {roleSelected && (
                                <div className="space-y-2">
                                    <p className="text-xs font-semibold text-primary uppercase tracking-wide flex items-center gap-1">
                                        <Building2 className="h-3.5 w-3.5"/> Registering for
                                    </p>
                                    {loadingBuilding ? (
                                        <Skeleton className="h-14 w-full rounded-lg"/>
                                    ) : invitePrefill ? (
                                        <div
                                            className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 flex items-start gap-3">
                                            <Building2 className="h-5 w-5 text-primary mt-0.5 shrink-0"/>
                                            <div>
                                                <p className="font-semibold text-sm">{building?.name}</p>
                                                <p className="text-xs text-muted-foreground">{building?.address}</p>
                                            </div>
                                        </div>
                                    ) : allBuildings.length > 1 ? (
                                        /* Multi-building: show selectable cards */
                                        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                                            {allBuildings.map(b => (
                                                <button
                                                    key={b.id}
                                                    type="button"
                                                    onClick={() => setBuilding(b)}
                                                    className={`rounded-lg border px-4 py-3 text-left transition-colors ${
                                                        building?.id === b.id
                                                            ? 'border-primary bg-primary/10 ring-1 ring-primary'
                                                            : 'border-border hover:border-primary/40 hover:bg-muted/50'
                                                    }`}
                                                >
                                                    <p className="font-semibold text-sm">{b.name}</p>
                                                    <p className="text-xs text-muted-foreground">{b.address}</p>
                                                </button>
                                            ))}
                                        </div>
                                    ) : (
                                        /* Single building: info banner */
                                        <div
                                            className="rounded-lg border border-primary/20 bg-primary/5 px-4 py-3 flex items-start gap-3">
                                            <Building2 className="h-5 w-5 text-primary mt-0.5 shrink-0"/>
                                            <div>
                                                {building ? (
                                                    <>
                                                        <p className="font-semibold text-sm">{building.name}</p>
                                                        <p className="text-xs text-muted-foreground">{building.address}</p>
                                                    </>
                                                ) : (
                                                    <>
                                                        <p className="font-semibold text-sm">Selected building</p>
                                                        <p className="text-xs text-muted-foreground">Building details will appear after loading.</p>
                                                    </>
                                                )}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* Step 3: Unit */}
                            {roleSelected && (
                                <div className="space-y-2">
                                    {loadingUnits ? (
                                        <UnitSkeleton/>
                                    ) : (
                                        <>
                                            <Label htmlFor="unit_number">
                                                <Home className="inline h-3.5 w-3.5 mr-1 mb-0.5"/>
                                                Unit Number <span className="text-destructive">*</span>
                                            </Label>
                                            {!hasUnits ? (
                                                <p className="text-sm text-muted-foreground">No units available. Please
                                                    contact the administrator.</p>
                                            ) : (
                                                <Select value={formData.unit_number}
                                                        onValueChange={v => set('unit_number', v)}
                                                        disabled={!!invitePrefill}>
                                                    <SelectTrigger
                                                        id="unit_number"
                                                        data-testid="unit-select"
                                                        className={fieldErrors.unit_number ? 'border-destructive' : ''}
                                                    >
                                                        <SelectValue placeholder="Select your unit"/>
                                                    </SelectTrigger>
                                                    <SelectContent className="max-h-72">
                                                        {apartments.length > 0 && (
                                                            <>
                                                                <SelectItem
                                                                    value="__apt_header__"
                                                                    disabled
                                                                    className="text-xs font-semibold uppercase tracking-wider text-muted-foreground py-1.5 opacity-100"
                                                                >
                                                                    — Apartments ({apartments.length}) —
                                                                </SelectItem>
                                                                {apartments.map(u => (
                                                                    <SelectItem key={u.unit_number}
                                                                                value={u.unit_number}>
                                                                        <span
                                                                            className="font-medium">Unit {u.unit_number}</span>
                                                                        {u.address && (
                                                                            <span
                                                                                className="text-muted-foreground text-xs ml-2">{u.address}</span>
                                                                        )}
                                                                    </SelectItem>
                                                                ))}
                                                            </>
                                                        )}
                                                        {townhouses.length > 0 && (
                                                            <>
                                                                <SelectItem
                                                                    value="__th_header__"
                                                                    disabled
                                                                    className="text-xs font-semibold uppercase tracking-wider text-muted-foreground py-1.5 opacity-100"
                                                                >
                                                                    — Townhouses ({townhouses.length}) —
                                                                </SelectItem>
                                                                {townhouses.map(u => (
                                                                    <SelectItem key={u.unit_number}
                                                                                value={u.unit_number}>
                                                                        <span
                                                                            className="font-medium">Unit {u.unit_number}</span>
                                                                        {u.address && (
                                                                            <span
                                                                                className="text-muted-foreground text-xs ml-2">{u.address}</span>
                                                                        )}
                                                                    </SelectItem>
                                                                ))}
                                                            </>
                                                        )}
                                                        {others.map(u => (
                                                            <SelectItem key={u.unit_number} value={u.unit_number}>
                                                                Unit {u.unit_number}
                                                            </SelectItem>
                                                        ))}
                                                    </SelectContent>
                                                </Select>
                                            )}
                                            <FieldError field="unit_number"/>
                                        </>
                                    )}
                                </div>
                            )}

                            {/* Multi-unit checkbox — owners only, shown once primary unit is selected */}
                            {formData.role === 'owner' && formData.unit_number && hasUnits && (
                                <div className="rounded-lg border bg-muted/20 p-4 space-y-3"
                                     data-testid="multi-unit-section">
                                    <div className="flex items-start gap-3">
                                        <Checkbox
                                            id="owns_multiple_units"
                                            checked={ownsMultipleUnits}
                                            onCheckedChange={v => {
                                                setOwnsMultipleUnits(Boolean(v));
                                                if (!v) setAdditionalUnitNumbers(['']);
                                            }}
                                            data-testid="multi-unit-checkbox"
                                        />
                                        <Label htmlFor="owns_multiple_units"
                                               className="text-sm leading-snug cursor-pointer">
                                            <Home className="inline h-3.5 w-3.5 mr-1 mb-0.5 text-primary"/>
                                            I own additional units in this building complex
                                            <span className="block text-xs text-muted-foreground mt-0.5">
                                                Tick this if you own more than one lot in this Owner Corporation
                                            </span>
                                        </Label>
                                    </div>

                                    {ownsMultipleUnits && (
                                        <div className="space-y-2 pt-1">
                                            <p className="text-xs font-medium text-muted-foreground">Additional unit(s)
                                                you own:</p>
                                            {additionalUnitNumbers.map((un, idx) => (
                                                <div key={idx} className="flex items-center gap-2">
                                                    <Select
                                                        value={un}
                                                        onValueChange={v => handleAdditionalUnitChange(idx, v)}
                                                    >
                                                        <SelectTrigger
                                                            data-testid={`additional-unit-select-${idx}`}
                                                            aria-label={`Additional unit ${idx + 1}`}
                                                            className="flex-1"
                                                        >
                                                            <SelectValue placeholder="Select additional unit"/>
                                                        </SelectTrigger>
                                                        <SelectContent className="max-h-64">
                                                            {allUnits
                                                                .filter(u => u.unit_number !== formData.unit_number && !additionalUnitNumbers.filter((_, i) => i !== idx).includes(u.unit_number))
                                                                .map(u => (
                                                                    <SelectItem key={u.unit_number}
                                                                                value={u.unit_number}>
                                                                        <span
                                                                            className="font-medium">Unit {u.unit_number}</span>
                                                                        {u.unit_type && <span
                                                                            className="text-muted-foreground text-xs ml-2">({u.unit_type})</span>}
                                                                    </SelectItem>
                                                                ))}
                                                        </SelectContent>
                                                    </Select>
                                                    {additionalUnitNumbers.length > 1 && (
                                                        <Button
                                                            type="button"
                                                            variant="ghost"
                                                            size="icon"
                                                            aria-label={`Remove additional unit ${idx + 1}`}
                                                            onClick={() => removeAdditionalUnitSlot(idx)}
                                                        >
                                                            <Trash2 className="h-4 w-4 text-destructive"
                                                                    aria-hidden="true"/>
                                                        </Button>
                                                    )}
                                                </div>
                                            ))}
                                            {additionalUnitNumbers.length < 9 && (
                                                <Button
                                                    type="button"
                                                    variant="outline"
                                                    size="sm"
                                                    className="w-full mt-1"
                                                    onClick={addAdditionalUnitSlot}
                                                    data-testid="add-another-unit-btn"
                                                >
                                                    <Plus className="h-3.5 w-3.5 mr-1" aria-hidden="true"/>
                                                    Add another unit
                                                </Button>
                                            )}
                                            <p className="text-xs text-muted-foreground pt-1">
                                                An administrator will verify your ownership of these units after
                                                registration.
                                            </p>
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* Step 4: Personal details + role-specific fields */}
                            {roleSelected && formData.unit_number && (
                                <>
                                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                        <div className="space-y-2 sm:col-span-2">
                                            <Label htmlFor="full_name">
                                                <User className="inline h-3.5 w-3.5 mr-1 mb-0.5"/>
                                                Full Name <span className="text-destructive">*</span>
                                            </Label>
                                            <Input
                                                id="full_name"
                                                name="full_name"
                                                type="text"
                                                placeholder="Your full legal name"
                                                value={formData.full_name}
                                                onChange={handleChange}
                                                className={fieldErrors.full_name ? 'border-destructive' : ''}
                                                autoComplete="name"
                                            />
                                            <FieldError field="full_name"/>
                                        </div>

                                        <div className="space-y-2">
                                            <Label htmlFor="email">
                                                <Mail className="inline h-3.5 w-3.5 mr-1 mb-0.5"/>
                                                Email <span className="text-destructive">*</span>
                                            </Label>
                                            <Input
                                                id="email"
                                                name="email"
                                                type="email"
                                                placeholder="you@example.com"
                                                value={formData.email}
                                                onChange={handleChange}
                                                className={fieldErrors.email ? 'border-destructive' : ''}
                                                autoComplete="email"
                                            />
                                            <FieldError field="email"/>
                                        </div>

                                        <div className="space-y-2">
                                            <Label htmlFor="phone">
                                                <Phone className="inline h-3.5 w-3.5 mr-1 mb-0.5"/>
                                                Phone <span className="text-destructive">*</span>
                                            </Label>
                                            <Input
                                                id="phone"
                                                name="phone"
                                                type="tel"
                                                placeholder="0400 000 000"
                                                value={formData.phone}
                                                onChange={handleChange}
                                                className={fieldErrors.phone ? 'border-destructive' : ''}
                                                autoComplete="tel"
                                            />
                                            <FieldError field="phone"/>
                                        </div>

                                        <div className="space-y-2">
                                            <Label htmlFor="password">
                                                <Lock className="inline h-3.5 w-3.5 mr-1 mb-0.5"/>
                                                Password <span className="text-destructive">*</span>
                                            </Label>
                                            <div className="relative">
                                                <Input
                                                    id="password"
                                                    name="password"
                                                    type={showPassword ? 'text' : 'password'}
                                                    placeholder="Min. 8 characters"
                                                    value={formData.password}
                                                    onChange={handleChange}
                                                    className={"pr-10 " + ( fieldErrors.password ? 'border-destructive' : '' )}
                                                    autoComplete="new-password"
                                                />
                                                <button
                                                    type="button"
                                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                                                    onClick={() => setShowPassword(p => !p)}
                                                    aria-label={showPassword ? 'Hide password' : 'Show password'}
                                                >
                                                    {showPassword ? <EyeOff className="h-4 w-4"/> :
                                                        <Eye className="h-4 w-4"/>}
                                                </button>
                                            </div>
                                            <FieldError field="password"/>
                                        </div>

                                        <div className="space-y-2">
                                            <Label htmlFor="confirmPassword">
                                                <Lock className="inline h-3.5 w-3.5 mr-1 mb-0.5"/>
                                                Confirm Password <span className="text-destructive">*</span>
                                            </Label>
                                            <div className="relative">
                                                <Input
                                                    id="confirmPassword"
                                                    name="confirmPassword"
                                                    type={showConfirm ? 'text' : 'password'}
                                                    placeholder="Repeat your password"
                                                    value={formData.confirmPassword}
                                                    onChange={handleChange}
                                                    className={"pr-10 " + ( fieldErrors.confirmPassword ? 'border-destructive' : '' )}
                                                    autoComplete="new-password"
                                                />
                                                <button
                                                    type="button"
                                                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                                                    onClick={() => setShowConfirm(p => !p)}
                                                    aria-label={showConfirm ? 'Hide password' : 'Show password'}
                                                >
                                                    {showConfirm ? <EyeOff className="h-4 w-4"/> :
                                                        <Eye className="h-4 w-4"/>}
                                                </button>
                                            </div>
                                            <FieldError field="confirmPassword"/>
                                        </div>
                                    </div>

                                    {formData.role === 'tenant' && (
                                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                            <div className="space-y-2">
                                                <Label htmlFor="tenancy_start_date">
                                                    <Calendar className="inline h-3.5 w-3.5 mr-1 mb-0.5"/>
                                                    Tenancy Start Date <span
                                                    className="text-muted-foreground text-xs">(optional)</span>
                                                </Label>
                                                <Input
                                                    id="tenancy_start_date"
                                                    name="tenancy_start_date"
                                                    type="date"
                                                    value={formData.tenancy_start_date}
                                                    onChange={handleChange}
                                                />
                                            </div>
                                            <div className="space-y-2">
                                                <Label htmlFor="landlord_name">
                                                    <User className="inline h-3.5 w-3.5 mr-1 mb-0.5"/>
                                                    Landlord / Owner Name <span
                                                    className="text-muted-foreground text-xs">(optional)</span>
                                                </Label>
                                                <Input
                                                    id="landlord_name"
                                                    name="landlord_name"
                                                    type="text"
                                                    placeholder="Name of property owner"
                                                    value={formData.landlord_name}
                                                    onChange={handleChange}
                                                />
                                            </div>
                                        </div>
                                    )}

                                    {formData.role === 'guest' && (
                                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                            <div className="space-y-2">
                                                <Label htmlFor="check_in_date">
                                                    <Calendar className="inline h-3.5 w-3.5 mr-1 mb-0.5"/>
                                                    Check-in Date <span className="text-destructive">*</span>
                                                </Label>
                                                <Input
                                                    id="check_in_date"
                                                    name="check_in_date"
                                                    type="date"
                                                    min={today}
                                                    value={formData.check_in_date}
                                                    onChange={handleChange}
                                                    className={fieldErrors.check_in_date ? 'border-destructive' : ''}
                                                />
                                                <FieldError field="check_in_date"/>
                                            </div>
                                            <div className="space-y-2">
                                                <Label htmlFor="check_out_date">
                                                    <Calendar className="inline h-3.5 w-3.5 mr-1 mb-0.5"/>
                                                    Check-out Date <span className="text-destructive">*</span>
                                                </Label>
                                                <Input
                                                    id="check_out_date"
                                                    name="check_out_date"
                                                    type="date"
                                                    min={formData.check_in_date || today}
                                                    value={formData.check_out_date}
                                                    onChange={handleChange}
                                                    className={fieldErrors.check_out_date ? 'border-destructive' : ''}
                                                />
                                                <FieldError field="check_out_date"/>
                                            </div>
                                        </div>
                                    )}

                                    <div className="rounded-lg border bg-muted/30 p-4 space-y-3">
                                        <div className="flex items-start gap-3">
                                            <Checkbox
                                                id="by_laws_acknowledged"
                                                checked={formData.by_laws_acknowledged}
                                                onCheckedChange={v => set('by_laws_acknowledged', Boolean(v))}
                                                className={fieldErrors.by_laws_acknowledged ? 'border-destructive' : ''}
                                            />
                                            <Label htmlFor="by_laws_acknowledged"
                                                   className="text-sm leading-snug cursor-pointer">
                                                I have read and agree to abide by the{' '}
                                                {showByLawsSection ? (
                                                    <button
                                                        type="button"
                                                        className="text-primary underline underline-offset-2 hover:no-underline font-medium"
                                                        onClick={() => setShowByLawsModal(true)}
                                                    >
                                                        Strata By-Laws
                                                    </button>
                                                ) : (
                                                    <span className="font-medium">Strata By-Laws</span>
                                                )}{'  '}
                                                and{'  '}
                                                <Link
                                                    href="/terms"
                                                    className="text-primary underline underline-offset-2 hover:no-underline font-medium"
                                                    target="_blank"
                                                >
                                                    Terms of Use
                                                </Link>{'  '}
                                                of the {building?.name || 'strata'} community.
                                                <span className="text-destructive ml-1">*</span>
                                            </Label>
                                        </div>
                                        <FieldError field="by_laws_acknowledged"/>
                                    </div>

                                    {alreadyRegistered && ( () => {
                                        const v = alreadyRegistered.variant || 'amber';
                                        const styles = {
                                            amber: {wrap: 'border-amber-200 bg-amber-50', text: 'text-amber-900'},
                                            green: {wrap: 'border-green-200 bg-green-50', text: 'text-green-900'},
                                            blue: {wrap: 'border-blue-200 bg-blue-50', text: 'text-blue-900'},
                                        }[ v ];
                                        return (
                                            <div
                                                className={`rounded-lg border ${styles.wrap} p-4 space-y-3`}
                                                role="alert"
                                                data-testid="already-registered-banner"
                                                data-variant={v}
                                            >
                                                <p className={`text-sm font-medium ${styles.text}`}>
                                                    {alreadyRegistered.message}
                                                </p>
                                                {v !== 'blue' && (
                                                    <div className="flex flex-wrap gap-2">
                                                        <Link
                                                            href="/login"
                                                            className="inline-flex items-center gap-1 rounded-full bg-primary px-4 py-1.5 text-xs font-semibold text-white hover:bg-primary/90"
                                                        >
                                                            Sign in
                                                        </Link>
                                                        {v === 'amber' && (
                                                            <Link
                                                                href={`/forgot-password?email=${encodeURIComponent(formData.email)}`}
                                                                className="inline-flex items-center gap-1 rounded-full border border-primary px-4 py-1.5 text-xs font-semibold text-primary hover:bg-primary/5"
                                                            >
                                                                Reset password
                                                            </Link>
                                                        )}
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    } )()}

                                    {!alreadyRegistered && (
                                        <Button
                                            type="submit"
                                            className="w-full"
                                            disabled={loading}
                                            data-testid="register-submit"
                                        >
                                            {loading ? (
                                                <>
                                                    <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                                                    Creating Account…
                                                </>
                                            ) : (
                                                'Create Account'
                                            )}
                                        </Button>
                                    )}
                                </>
                            )}

                            {roleSelected && !formData.unit_number && !loadingUnits && hasUnits && (
                                <p className="text-center text-sm text-muted-foreground flex items-center justify-center gap-1">
                                    <ChevronRight className="h-4 w-4 text-primary"/>
                                    Select your unit above to continue
                                </p>
                            )}

                        </form>
                    </CardContent>
                </Card>

                <div className="mt-6 text-center space-y-3">
                    <p className="text-sm text-muted-foreground">
                        Already have an account?{'  '}
                        <Link href="/login" className="text-primary font-medium hover:underline">
                            Sign in
                        </Link>
                    </p>
                    <div className="border-t pt-3">
                        <p className="text-sm text-muted-foreground">
                            Are you a Strata Manager, Admin Staff, or Real Estate Agent?{'  '}
                            <Link
                                href="/register/staff"
                                className="text-primary font-medium hover:underline inline-flex items-center gap-0.5"
                            >
                                Register as Staff <ChevronRight className="h-3.5 w-3.5"/>
                            </Link>
                        </p>
                    </div>
                </div>
            </div>

            {showByLawsSection && (
                <Dialog open={showByLawsModal} onOpenChange={setShowByLawsModal}>
                    <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
                        <DialogHeader>
                            <DialogTitle className="flex items-center gap-2">
                                <FileText className="h-5 w-5 text-primary"/>
                                Strata By-Laws{building?.name ? ` — ${building.name}` : ''}
                            </DialogTitle>
                            <DialogDescription>
                                Please read these by-laws carefully before acknowledging.
                            </DialogDescription>
                        </DialogHeader>
                        <div className="flex-1 overflow-y-auto pr-2 space-y-4 text-sm leading-relaxed">
                            {byLawsContent?.content ? (
                                <div className="whitespace-pre-wrap">{byLawsContent.content}</div>
                            ) : byLawsContent?.sections ? (
                                byLawsContent.sections.map((section, i) => (
                                    <div key={i}>
                                        {section.title && <h3 className="font-semibold mb-1">{section.title}</h3>}
                                        <p>{section.content || section.text || JSON.stringify(section)}</p>
                                    </div>
                                ))
                            ) : (
                                <p className="text-muted-foreground">By-laws document is available from the strata
                                    manager on request.</p>
                            )}
                        </div>
                        <DialogFooter>
                            <Button
                                onClick={() => {
                                    set('by_laws_acknowledged', true);
                                    setShowByLawsModal(false);
                                }}
                            >
                                I Acknowledge the By-Laws
                            </Button>
                            <Button variant="outline" onClick={() => setShowByLawsModal(false)}>
                                Close
                            </Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>
            )}
        </div>
    );
};

export default RegisterPage;
