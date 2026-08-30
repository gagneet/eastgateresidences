"use client";
import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select';
import { Card, CardContent } from '../../components/ui/card';
import { Checkbox } from '../../components/ui/checkbox';
import {
    Briefcase,
    Building2,
    CheckCircle2,
    ChevronLeft,
    Eye,
    EyeOff,
    FileText,
    Loader2,
    Lock,
    Mail,
    Phone,
    User,
    Wrench,
} from 'lucide-react';
import { toast } from 'sonner';
import axios from 'axios';

const API_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

const STAFF_ROLES = [
    {
        value: 'strata_manager',
        label: 'Strata Manager',
        description: 'Professional strata management services',
        icon: Building2,
        licenseLabel: 'Strata License #',
        orgLabel: 'Company / Agency Name',
    },
    {
        value: 'admin_staff',
        label: 'Admin Staff',
        description: 'Building administration and resident support',
        icon: User,
        licenseLabel: 'Professional Certification (optional)',
        orgLabel: 'Organisation Name',
    },
    {
        value: 'real_estate_agent',
        label: 'Real Estate Agent',
        description: 'Property management on behalf of owners',
        icon: Briefcase,
        licenseLabel: 'REI License #',
        orgLabel: 'Agency Name',
    },
    {
        value: 'service_provider',
        label: 'Service Provider',
        description: 'Trades and maintenance services',
        icon: Wrench,
        licenseLabel: 'Trade Certification / License (optional)',
        orgLabel: 'Trade / Business Name',
    },
];
/**
 * @generated FunctionHeader
 * Function: StaffRegisterPage
 * Path: frontend/src/pages/auth/StaffRegisterPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const StaffRegisterPage = () => {
    const router = useRouter();

    const [selectedRole, setSelectedRole] = useState(null);
    const [buildings, setBuildings] = useState([]);
    const [loading, setLoading] = useState(false);
    const [submitted, setSubmitted] = useState(false);
    const [showPassword, setShowPassword] = useState(false);
    const [showConfirm, setShowConfirm] = useState(false);
    const [fieldErrors, setFieldErrors] = useState({});

    const [formData, setFormData] = useState({
        full_name: '',
        email: '',
        phone: '',
        password: '',
        confirmPassword: '',
        building_id: '',
        organisation: '',
        license_number: '',
        description: '',
        terms_accepted: false,
    });

    useEffect(() => {
        let mounted = true;
        /**
         * @generated FunctionHeader
         * Function: fetchBuildings
         * Path: frontend/src/pages/auth/StaffRegisterPage.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchBuildings = async () => {
            try {
                const {data} = await axios.get(`${API_URL}/api/auth/buildings`);
                const nextBuildings = Array.isArray(data) ? data : [];
                if (!mounted) return;
                setBuildings(nextBuildings);
                if (nextBuildings.length === 1) {
                    setFormData(prev => ( {...prev, building_id: prev.building_id || nextBuildings[ 0 ].id} ));
                }
            } catch (_) {
                if (mounted) {
                    toast.error('Could not load buildings. Please refresh the page.');
                }
            }
        };
        fetchBuildings();
        return () => {
            mounted = false;
        };
    }, []);
    /**
     * @generated FunctionHeader
     * Function: set
     * Path: frontend/src/pages/auth/StaffRegisterPage.jsx
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
     * Path: frontend/src/pages/auth/StaffRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleChange = (e) => {
        const {name, value, type, checked} = e.target;
        set(name, type === 'checkbox' ? checked : value);
    };
    /**
     * @generated FunctionHeader
     * Function: handleRoleSelect
     * Path: frontend/src/pages/auth/StaffRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRoleSelect = (role) => {
        setSelectedRole(role);
        setFieldErrors({});
    };
    /**
     * @generated FunctionHeader
     * Function: validate
     * Path: frontend/src/pages/auth/StaffRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const validate = () => {
        const errors = {};
        if (!formData.full_name.trim()) errors.full_name = 'Full name is required';
        if (!formData.email.trim()) errors.email = 'Email is required';
        else if (!/\S+@\S+\.\S+/.test(formData.email)) errors.email = 'Invalid email address';
        if (!formData.phone.trim()) errors.phone = 'Phone number is required';
        if (!formData.building_id) errors.building_id = 'Please select the building you support';
        if (!formData.password) errors.password = 'Password is required';
        else if (formData.password.length < 8) errors.password = 'Password must be at least 8 characters';
        if (formData.password !== formData.confirmPassword)
            errors.confirmPassword = 'Passwords do not match';
        if (!formData.terms_accepted) errors.terms_accepted = 'You must accept the terms to register';
        return errors;
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/auth/StaffRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!selectedRole) {
            toast.error('Please select your role');
            return;
        }
        const errors = validate();
        if (Object.keys(errors).length > 0) {
            setFieldErrors(errors);
            toast.error('Please fix the errors below');
            return;
        }
        setLoading(true);
        try {
            const {confirmPassword, license_number, description, ...payload} = formData;
            payload.role = selectedRole.value;
            payload.professional_licence = license_number;
            await axios.post(`${API_URL}/api/auth/register/staff`, payload);
            setSubmitted(true);
        } catch (err) {
            const detail = err?.response?.data?.detail;
            toast.error(detail || 'Registration failed. Please try again.');
        } finally {
            setLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: FieldError
     * Path: frontend/src/pages/auth/StaffRegisterPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const FieldError = ({field}) =>
        fieldErrors[ field ] ? (
            <p className="text-xs text-destructive mt-1" role="alert">{fieldErrors[ field ]}</p>
        ) : null;

    // Success state
    if (submitted) {
        return (
            <div className="min-h-screen flex items-center justify-center bg-muted/30 px-4 py-12">
                <div className="w-full max-w-md text-center">
                    <div className="flex justify-center mb-6">
                        <div className="rounded-full bg-green-100 dark:bg-green-900/30 p-5">
                            <CheckCircle2 className="h-14 w-14 text-green-600 dark:text-green-400"/>
                        </div>
                    </div>
                    <h1 className="text-2xl font-bold mb-3">Application Submitted!</h1>
                    <p className="text-muted-foreground mb-2">
                        Your application for <span className="font-semibold">{selectedRole?.label}</span> access has
                        been received.
                    </p>
                    <p className="text-muted-foreground mb-8">
                        An administrator will review your credentials and activate your account within{'  '}
                        <span className="font-semibold text-foreground">1–2 business days</span>.
                        You will be notified at <span className="font-semibold text-foreground">{formData.email}</span>.
                    </p>
                    <div className="flex flex-col sm:flex-row gap-3 justify-center">
                        <Button asChild>
                            <Link href="/login">Go to Login</Link>
                        </Button>
                        <Button variant="outline" asChild>
                            <Link href="/">Return Home</Link>
                        </Button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="min-h-screen flex items-center justify-center bg-muted/30 px-4 py-12">
            <div className="w-full max-w-xl">

                {/* Header */}
                <div className="text-center mb-8">
                    <Link href="/" className="inline-flex items-center gap-2 mb-6">
                        <Building2 className="h-10 w-10 text-primary"/>
                        <span className="text-2xl font-bold">East Gate Residences</span>
                    </Link>
                    <h1 className="text-2xl font-bold">Staff Registration</h1>
                    <p className="text-muted-foreground mt-1">
                        Apply for staff access to the East Gate Residences platform
                    </p>
                </div>

                <Card className="card-dashboard">
                    <CardContent className="pt-6">
                        <form onSubmit={handleSubmit} className="space-y-6" noValidate>

                            {/* Role cards */}
                            <div>
                                <Label className="text-sm font-semibold mb-3 block">
                                    Select your role <span className="text-destructive">*</span>
                                </Label>
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                    {STAFF_ROLES.map((role) => {
                                        const Icon = role.icon;
                                        const active = selectedRole?.value === role.value;
                                        return (
                                            <button
                                                key={role.value}
                                                type="button"
                                                onClick={() => handleRoleSelect(role)}
                                                className={
                                                    'w-full text-left rounded-lg border p-4 transition-all duration-150 ' +
                                                    ( active
                                                        ? 'border-primary bg-primary/5 ring-2 ring-primary ring-offset-1'
                                                        : 'border-border hover:border-primary/50 hover:bg-muted/40' )
                                                }
                                            >
                                                <div className="flex items-start gap-3">
                                                    <div
                                                        className={"rounded-md p-2 " + ( active ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground' )}>
                                                        <Icon className="h-4 w-4"/>
                                                    </div>
                                                    <div>
                                                        <p className={"text-sm font-semibold " + ( active ? 'text-primary' : '' )}>{role.label}</p>
                                                        <p className="text-xs text-muted-foreground mt-0.5">{role.description}</p>
                                                    </div>
                                                </div>
                                            </button>
                                        );
                                    })}
                                </div>
                            </div>

                            {/* Fields — shown after role selected */}
                            {selectedRole && (
                                <>
                                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">

                                        <div className="space-y-2 sm:col-span-2">
                                            <Label htmlFor="full_name">
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
                                            <Label htmlFor="building_id">
                                                <Building2 className="inline h-3.5 w-3.5 mr-1 mb-0.5"/>
                                                Building <span className="text-destructive">*</span>
                                            </Label>
                                            <Select
                                                value={formData.building_id}
                                                onValueChange={value => set('building_id', value)}
                                            >
                                                <SelectTrigger
                                                    id="building_id"
                                                    className={fieldErrors.building_id ? 'border-destructive' : ''}
                                                >
                                                    <SelectValue placeholder="Select the building you support"/>
                                                </SelectTrigger>
                                                <SelectContent>
                                                    {buildings.map((building) => (
                                                        <SelectItem key={building.id} value={building.id}>
                                                            {building.name}
                                                        </SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                            <FieldError field="building_id"/>
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
                                                placeholder="you@company.com"
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

                                        <div className="space-y-2 sm:col-span-2">
                                            <Label htmlFor="organisation">
                                                {selectedRole.orgLabel}{'  '}
                                                {selectedRole.value !== 'service_provider' && (
                                                    <span className="text-muted-foreground text-xs">(optional)</span>
                                                )}
                                            </Label>
                                            <Input
                                                id="organisation"
                                                name="organisation"
                                                type="text"
                                                placeholder={selectedRole.orgLabel}
                                                value={formData.organisation}
                                                onChange={handleChange}
                                            />
                                        </div>

                                        <div className="space-y-2 sm:col-span-2">
                                            <Label htmlFor="license_number">
                                                <FileText className="inline h-3.5 w-3.5 mr-1 mb-0.5"/>
                                                {selectedRole.licenseLabel}
                                            </Label>
                                            <Input
                                                id="license_number"
                                                name="license_number"
                                                type="text"
                                                placeholder={selectedRole.licenseLabel.replace(' (optional)', '')}
                                                value={formData.license_number}
                                                onChange={handleChange}
                                            />
                                        </div>

                                        <div className="space-y-2 sm:col-span-2">
                                            <Label htmlFor="description">
                                                About / Description <span
                                                className="text-muted-foreground text-xs">(optional)</span>
                                            </Label>
                                            <textarea
                                                id="description"
                                                name="description"
                                                rows={3}
                                                placeholder="Brief description of your services or role…"
                                                value={formData.description}
                                                onChange={handleChange}
                                                className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 resize-none"
                                            />
                                        </div>
                                    </div>

                                    {/* Terms */}
                                    <div className="rounded-lg border bg-muted/30 p-4 space-y-2">
                                        <div className="flex items-start gap-3">
                                            <Checkbox
                                                id="terms_accepted"
                                                checked={formData.terms_accepted}
                                                onCheckedChange={v => set('terms_accepted', Boolean(v))}
                                                className={fieldErrors.terms_accepted ? 'border-destructive' : ''}
                                            />
                                            <Label htmlFor="terms_accepted"
                                                   className="text-sm leading-snug cursor-pointer">
                                                I accept the{'  '}
                                                <Link href="/terms"
                                                      className="text-primary underline underline-offset-2 hover:no-underline font-medium"
                                                      target="_blank">
                                                    Terms of Use
                                                </Link>{'  '}
                                                and acknowledge that my account will require administrator approval
                                                before I can access the platform.
                                                <span className="text-destructive ml-1">*</span>
                                            </Label>
                                        </div>
                                        <FieldError field="terms_accepted"/>
                                    </div>

                                    <Button type="submit" className="w-full" disabled={loading}>
                                        {loading ? (
                                            <>
                                                <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                                                Submitting Application…
                                            </>
                                        ) : (
                                            'Submit Application'
                                        )}
                                    </Button>
                                </>
                            )}

                        </form>
                    </CardContent>
                </Card>

                <div className="mt-6 text-center space-y-2">
                    <p className="text-sm text-muted-foreground">
                        Not a staff member?{'  '}
                        <Link href="/register"
                              className="text-primary font-medium hover:underline inline-flex items-center gap-0.5">
                            <ChevronLeft className="h-3.5 w-3.5"/> Resident registration
                        </Link>
                    </p>
                    <p className="text-sm text-muted-foreground">
                        Already have an account?{'  '}
                        <Link href="/login" className="text-primary font-medium hover:underline">
                            Sign in
                        </Link>
                    </p>
                </div>
            </div>
        </div>
    );
};

export default StaffRegisterPage;
