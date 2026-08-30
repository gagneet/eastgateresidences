import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../../components/ui/card';
import { Button } from '../../../components/ui/button';
import { Input } from '../../../components/ui/input';
import { Label } from '../../../components/ui/label';
import { toast } from 'sonner';
import { Building2, ChevronLeft, Loader2 } from 'lucide-react';

const JURISDICTIONS = ['ACT', 'NSW', 'VIC', 'QLD', 'WA', 'SA', 'TAS', 'NT'];

const initialForm = {
    scheme_number: '',
    scheme_name: '',
    scheme_legal_name: '',
    scheme_abn: '',
    jurisdiction: 'ACT',
    ec_admin_email: '',
    ec_admin_first_name: '',
    ec_admin_last_name: '',
    invitation_role: 'strata_admin',
    invitation_expires_days: 14,
};
/**
 * @generated FunctionHeader
 * Function: NewSelfManagedSchemePage
 * Path: frontend/src/pages/dashboard/admin/NewSelfManagedSchemePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const NewSelfManagedSchemePage = () => {
    const router = useRouter();
    const {api, isAdmin, loading} = useAuth();
    const [form, setForm] = useState(initialForm);
    const [submitting, setSubmitting] = useState(false);
    const [errors, setErrors] = useState({});

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[40vh]">
                <Loader2 className="h-6 w-6 animate-spin text-primary"/>
            </div>
        );
    }
    if (!isAdmin()) {
        return (
            <div className="p-8">
                <p className="text-sm text-muted-foreground">Super admin access required.</p>
            </div>
        );
    }
    /**
     * @generated FunctionHeader
     * Function: update
     * Path: frontend/src/pages/dashboard/admin/NewSelfManagedSchemePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const update = (key) => (e) => setForm({...form, [ key ]: e.target.value});
    /**
     * @generated FunctionHeader
     * Function: validate
     * Path: frontend/src/pages/dashboard/admin/NewSelfManagedSchemePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const validate = () => {
        const e = {};
        if (!form.scheme_number.trim()) e.scheme_number = 'Required.';
        if (!form.scheme_name.trim()) e.scheme_name = 'Required.';
        if (!form.ec_admin_email.includes('@')) e.ec_admin_email = 'Valid email required.';
        if (!form.ec_admin_first_name.trim()) e.ec_admin_first_name = 'Required.';
        if (!form.ec_admin_last_name.trim()) e.ec_admin_last_name = 'Required.';
        if (form.scheme_abn && !/^\d{11}$/.test(form.scheme_abn)) e.scheme_abn = 'ABN must be 11 digits.';
        setErrors(e);
        return Object.keys(e).length === 0;
    };
    /**
     * @generated FunctionHeader
     * Function: onSubmit
     * Path: frontend/src/pages/dashboard/admin/NewSelfManagedSchemePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const onSubmit = async (ev) => {
        ev.preventDefault();
        if (!validate()) return;

        setSubmitting(true);
        try {
            const payload = {
                ...form,
                scheme_legal_name: form.scheme_legal_name || undefined,
                scheme_abn: form.scheme_abn || undefined,
                invitation_expires_days: Number(form.invitation_expires_days),
            };
            const res = await api.post('/admin/sm-organisations/self-managed', payload);
            const {tenant_id, scheme_id, code, warning} = res.data;
            if (code === 'invitation_email_failed') {
                toast.warning(warning || 'Created, but invitation email failed. Resend from the org page.');
            } else {
                toast.success(`Self-managed scheme '${form.scheme_name}' created.`);
            }
            router.push(`/admin/sm-organisations/${tenant_id}`);
        } catch (err) {
            const detail = err?.response?.data?.detail;
            const code = detail?.code || detail || err?.message;
            const msg = detail?.detail || err?.response?.data?.message || 'Failed to create scheme.';
            // Map known backend error codes to inline field errors
            if (code === 'abn_already_active') {
                setErrors({scheme_abn: 'An active organisation already has this ABN.'});
            } else if (code === 'scheme_number_taken_in_jurisdiction') {
                setErrors({scheme_number: `Scheme already exists in ${form.jurisdiction}.`});
            } else if (code === 'self_managed_already_has_scheme') {
                toast.error('Self-managed tenant already has a scheme.');
            } else {
                toast.error(msg);
            }
        } finally {
            setSubmitting(false);
        }
    };

    return (
        <div className="p-6 max-w-3xl">
            <Button
                variant="ghost"
                onClick={() => router.push('/admin/sm-organisations')}
                className="mb-4"
            >
                <ChevronLeft className="mr-1 h-4 w-4"/> Back to organisations
            </Button>

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Building2 className="h-5 w-5 text-primary"/>
                        New Self-Managed Scheme
                    </CardTitle>
                    <p className="text-sm text-muted-foreground">
                        Creates a self-managed tenant + scheme + EC admin invitation in one
                        atomic call. Use this when an Owners Corporation runs its own affairs
                        without a Strata Management firm.
                    </p>
                </CardHeader>
                <CardContent>
                    <form onSubmit={onSubmit} className="space-y-5">
                        <fieldset className="space-y-4">
                            <legend className="text-sm font-semibold">Scheme details</legend>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <Label htmlFor="scheme_number">Plan number<span
                                        className="text-destructive">*</span></Label>
                                    <Input
                                        id="scheme_number"
                                        data-testid="sm-self-managed-scheme-number"
                                        value={form.scheme_number}
                                        onChange={update('scheme_number')}
                                        placeholder="e.g. 13195"
                                        aria-invalid={!!errors.scheme_number}
                                    />
                                    {errors.scheme_number && (
                                        <p role="alert"
                                           className="text-xs text-destructive mt-1">{errors.scheme_number}</p>
                                    )}
                                </div>
                                <div>
                                    <Label htmlFor="jurisdiction">Jurisdiction<span
                                        className="text-destructive">*</span></Label>
                                    <select
                                        id="jurisdiction"
                                        data-testid="sm-self-managed-jurisdiction"
                                        value={form.jurisdiction}
                                        onChange={update('jurisdiction')}
                                        className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                                    >
                                        {JURISDICTIONS.map(j => <option key={j} value={j}>{j}</option>)}
                                    </select>
                                </div>
                            </div>

                            <div>
                                <Label htmlFor="scheme_name">Scheme name<span
                                    className="text-destructive">*</span></Label>
                                <Input
                                    id="scheme_name"
                                    data-testid="sm-self-managed-scheme-name"
                                    value={form.scheme_name}
                                    onChange={update('scheme_name')}
                                    placeholder="e.g. East Gate Residences"
                                    aria-invalid={!!errors.scheme_name}
                                />
                                {errors.scheme_name && (
                                    <p role="alert" className="text-xs text-destructive mt-1">{errors.scheme_name}</p>
                                )}
                            </div>

                            <div>
                                <Label htmlFor="scheme_legal_name">Legal name</Label>
                                <Input
                                    id="scheme_legal_name"
                                    value={form.scheme_legal_name}
                                    onChange={update('scheme_legal_name')}
                                    placeholder="e.g. The Owners — Units Plan 13195"
                                />
                            </div>

                            <div>
                                <Label htmlFor="scheme_abn">ABN</Label>
                                <Input
                                    id="scheme_abn"
                                    value={form.scheme_abn}
                                    onChange={update('scheme_abn')}
                                    inputMode="numeric"
                                    placeholder="11 digits, no spaces"
                                    aria-describedby="scheme_abn_help"
                                    aria-invalid={!!errors.scheme_abn}
                                />
                                <p id="scheme_abn_help" className="text-xs text-muted-foreground mt-1">
                                    Australian Business Number — 11 digits if applicable.
                                </p>
                                {errors.scheme_abn && (
                                    <p role="alert" className="text-xs text-destructive mt-1">{errors.scheme_abn}</p>
                                )}
                            </div>
                        </fieldset>

                        <fieldset className="space-y-4 pt-2">
                            <legend className="text-sm font-semibold">EC admin (first user)</legend>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <Label htmlFor="ec_admin_first_name">First name<span
                                        className="text-destructive">*</span></Label>
                                    <Input
                                        id="ec_admin_first_name"
                                        value={form.ec_admin_first_name}
                                        onChange={update('ec_admin_first_name')}
                                        aria-invalid={!!errors.ec_admin_first_name}
                                    />
                                    {errors.ec_admin_first_name && (
                                        <p role="alert"
                                           className="text-xs text-destructive mt-1">{errors.ec_admin_first_name}</p>
                                    )}
                                </div>
                                <div>
                                    <Label htmlFor="ec_admin_last_name">Last name<span
                                        className="text-destructive">*</span></Label>
                                    <Input
                                        id="ec_admin_last_name"
                                        value={form.ec_admin_last_name}
                                        onChange={update('ec_admin_last_name')}
                                        aria-invalid={!!errors.ec_admin_last_name}
                                    />
                                    {errors.ec_admin_last_name && (
                                        <p role="alert"
                                           className="text-xs text-destructive mt-1">{errors.ec_admin_last_name}</p>
                                    )}
                                </div>
                            </div>

                            <div>
                                <Label htmlFor="ec_admin_email">Email<span className="text-destructive">*</span></Label>
                                <Input
                                    id="ec_admin_email"
                                    data-testid="sm-self-managed-ec-email"
                                    type="email"
                                    value={form.ec_admin_email}
                                    onChange={update('ec_admin_email')}
                                    aria-invalid={!!errors.ec_admin_email}
                                />
                                {errors.ec_admin_email && (
                                    <p role="alert"
                                       className="text-xs text-destructive mt-1">{errors.ec_admin_email}</p>
                                )}
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <Label htmlFor="invitation_role">Role</Label>
                                    <select
                                        id="invitation_role"
                                        value={form.invitation_role}
                                        onChange={update('invitation_role')}
                                        className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                                    >
                                        <option value="strata_admin">Strata Admin</option>
                                        <option value="strata_manager">Strata Manager</option>
                                    </select>
                                </div>
                                <div>
                                    <Label htmlFor="invitation_expires_days">Invitation expires (days)</Label>
                                    <select
                                        id="invitation_expires_days"
                                        value={form.invitation_expires_days}
                                        onChange={update('invitation_expires_days')}
                                        className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
                                    >
                                        <option value={7}>7</option>
                                        <option value={14}>14</option>
                                        <option value={30}>30</option>
                                    </select>
                                </div>
                            </div>
                        </fieldset>

                        <div className="flex items-center justify-end gap-2 pt-4 border-t">
                            <Button
                                type="button"
                                variant="ghost"
                                onClick={() => router.push('/admin/sm-organisations')}
                                disabled={submitting}
                            >
                                Cancel
                            </Button>
                            <Button
                                type="submit"
                                data-testid="sm-self-managed-submit"
                                disabled={submitting}
                            >
                                {submitting ? (
                                    <><Loader2 className="mr-2 h-4 w-4 animate-spin"/> Creating…</>
                                ) : 'Create self-managed scheme'}
                            </Button>
                        </div>
                    </form>
                </CardContent>
            </Card>
        </div>
    );
};

export default NewSelfManagedSchemePage;
