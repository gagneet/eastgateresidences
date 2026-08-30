import React, {useState} from 'react';
import {useRouter} from 'next/navigation';
import {useAuth} from '../../../contexts/AuthContext';
import {Card, CardContent, CardHeader, CardTitle} from '../../../components/ui/card';
import {Button} from '../../../components/ui/button';
import {Input} from '../../../components/ui/input';
import {Label} from '../../../components/ui/label';
import {Building2, ChevronLeft, Loader2} from 'lucide-react';
import {toast} from 'sonner';

const JURISDICTIONS = ['ACT', 'NSW', 'VIC', 'QLD', 'WA', 'SA', 'TAS', 'NT'];

const initialForm = {
    tenant_name: '',
    legal_name: '',
    abn: '',
    jurisdiction: 'ACT',
    primary_admin_email: '',
    primary_admin_first_name: '',
    primary_admin_last_name: '',
    invitation_role: 'strata_admin',
    invitation_expires_days: 14,
};
/**
 * @generated FunctionHeader
 * Function: Field
 * Path: frontend/src/pages/dashboard/admin/NewSmOrganisationPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const Field = ({id, label, required, error, children}) => (
    <div>
        <Label htmlFor={id}>
            {label}{required && <span className="text-destructive ml-0.5" aria-hidden="true">*</span>}
        </Label>
        {children}
        {error && <p role="alert" className="text-xs text-destructive mt-1">{error}</p>}
    </div>
);
/**
 * @generated FunctionHeader
 * Function: NewSmOrganisationPage
 * Path: frontend/src/pages/dashboard/admin/NewSmOrganisationPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const NewSmOrganisationPage = () => {
    const router = useRouter();
    const {api, isAdmin, loading: authLoading} = useAuth();
    const [form, setForm] = useState(initialForm);
    const [errors, setErrors] = useState({});
    const [submitting, setSubmitting] = useState(false);

    if (authLoading) {
        return (
            <div className="flex items-center justify-center min-h-[40vh]">
                <Loader2 className="h-6 w-6 animate-spin text-primary" aria-label="Loading"/>
            </div>
        );
    }
    if (!isAdmin()) {
        return <div className="p-8 text-sm text-muted-foreground">Super admin access required.</div>;
    }
    /**
     * @generated FunctionHeader
     * Function: update
     * Path: frontend/src/pages/dashboard/admin/NewSmOrganisationPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const update = key => e => setForm(f => ({...f, [key]: e.target.value}));
    /**
     * @generated FunctionHeader
     * Function: validate
     * Path: frontend/src/pages/dashboard/admin/NewSmOrganisationPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const validate = () => {
        const e = {};
        if (!form.tenant_name.trim()) e.tenant_name = 'Required.';
        if (!form.primary_admin_email.includes('@')) e.primary_admin_email = 'Valid email required.';
        if (!form.primary_admin_first_name.trim()) e.primary_admin_first_name = 'Required.';
        if (!form.primary_admin_last_name.trim()) e.primary_admin_last_name = 'Required.';
        if (form.abn && !/^\d{11}$/.test(form.abn)) e.abn = 'ABN must be 11 digits, no spaces.';
        setErrors(e);
        return Object.keys(e).length === 0;
    };
    /**
     * @generated FunctionHeader
     * Function: onSubmit
     * Path: frontend/src/pages/dashboard/admin/NewSmOrganisationPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const onSubmit = async ev => {
        ev.preventDefault();
        if (!validate()) return;
        setSubmitting(true);
        try {
            const payload = {
                ...form,
                legal_name: form.legal_name || undefined,
                abn: form.abn || undefined,
                invitation_expires_days: Number(form.invitation_expires_days),
            };
            const res = await api.post('/admin/sm-organisations', payload);
            const {tenant_id, code, warning} = res.data;
            if (code === 'invitation_email_failed') {
                toast.warning(warning || 'Organisation created, but invitation email failed. Resend from the org page.');
            } else {
                toast.success(`Organisation '${form.tenant_name}' created. Invitation sent.`);
            }
            router.push(`/admin/sm-organisations/${tenant_id}`);
        } catch (err) {
            const detail = err?.response?.data?.detail;
            const code = typeof detail === 'object' ? detail?.code : detail;
            if (code === 'abn_already_active') {
                setErrors({abn: 'An active organisation already has this ABN.'});
            } else {
                toast.error(typeof detail === 'string' ? detail : 'Failed to create organisation.');
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
                className="mb-4 -ml-2"
                aria-label="Back to organisations"
            >
                <ChevronLeft className="h-4 w-4 mr-1"/> Back to organisations
            </Button>

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Building2 className="h-5 w-5 text-primary" aria-hidden="true"/>
                        New Strata Management Organisation
                    </CardTitle>
                    <p className="text-sm text-muted-foreground">
                        Creates an SM-managed tenant and sends an invitation to the nominated admin.
                        The admin claims the invitation to log in and onboard their buildings.
                    </p>
                </CardHeader>

                <CardContent>
                    <form onSubmit={onSubmit} className="space-y-6" noValidate>

                        {/* Organisation details */}
                        <fieldset className="space-y-4">
                            <legend className="text-sm font-semibold">Organisation details</legend>

                            <Field id="tenant_name" label="Organisation name" required error={errors.tenant_name}>
                                <Input
                                    id="tenant_name"
                                    data-testid="sm-org-tenant-name"
                                    value={form.tenant_name}
                                    onChange={update('tenant_name')}
                                    placeholder="e.g. Acme Strata Management"
                                    aria-required="true"
                                    aria-invalid={!!errors.tenant_name}
                                    aria-describedby={errors.tenant_name ? 'tenant_name-error' : undefined}
                                    className="mt-1"
                                />
                            </Field>

                            <Field id="legal_name" label="Legal name" error={errors.legal_name}>
                                <Input
                                    id="legal_name"
                                    value={form.legal_name}
                                    onChange={update('legal_name')}
                                    placeholder="e.g. Acme Strata Management Pty Ltd"
                                    className="mt-1"
                                />
                            </Field>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <Field id="abn" label="ABN" error={errors.abn}>
                                    <Input
                                        id="abn"
                                        data-testid="sm-org-abn"
                                        value={form.abn}
                                        onChange={update('abn')}
                                        inputMode="numeric"
                                        maxLength={11}
                                        placeholder="11 digits, no spaces"
                                        aria-invalid={!!errors.abn}
                                        aria-describedby="abn-hint"
                                        className="mt-1 font-mono"
                                    />
                                    <p id="abn-hint" className="text-xs text-muted-foreground mt-1">
                                        Must be unique across all active organisations.
                                    </p>
                                </Field>

                                <Field id="jurisdiction" label="Primary jurisdiction" required>
                                    <select
                                        id="jurisdiction"
                                        data-testid="sm-org-jurisdiction"
                                        value={form.jurisdiction}
                                        onChange={update('jurisdiction')}
                                        aria-required="true"
                                        className="mt-1 h-10 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                                    >
                                        {JURISDICTIONS.map(j => <option key={j} value={j}>{j}</option>)}
                                    </select>
                                </Field>
                            </div>
                        </fieldset>

                        {/* Admin invitation */}
                        <fieldset className="space-y-4 pt-2">
                            <legend className="text-sm font-semibold">
                                Primary admin invitation
                                <span className="font-normal text-muted-foreground ml-2">
                                    — this person receives an invitation email
                                </span>
                            </legend>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <Field
                                    id="primary_admin_first_name"
                                    label="First name"
                                    required
                                    error={errors.primary_admin_first_name}
                                >
                                    <Input
                                        id="primary_admin_first_name"
                                        value={form.primary_admin_first_name}
                                        onChange={update('primary_admin_first_name')}
                                        aria-required="true"
                                        aria-invalid={!!errors.primary_admin_first_name}
                                        className="mt-1"
                                    />
                                </Field>
                                <Field
                                    id="primary_admin_last_name"
                                    label="Last name"
                                    required
                                    error={errors.primary_admin_last_name}
                                >
                                    <Input
                                        id="primary_admin_last_name"
                                        value={form.primary_admin_last_name}
                                        onChange={update('primary_admin_last_name')}
                                        aria-required="true"
                                        aria-invalid={!!errors.primary_admin_last_name}
                                        className="mt-1"
                                    />
                                </Field>
                            </div>

                            <Field
                                id="primary_admin_email"
                                label="Email address"
                                required
                                error={errors.primary_admin_email}
                            >
                                <Input
                                    id="primary_admin_email"
                                    data-testid="sm-org-admin-email"
                                    type="email"
                                    value={form.primary_admin_email}
                                    onChange={update('primary_admin_email')}
                                    aria-required="true"
                                    aria-invalid={!!errors.primary_admin_email}
                                    className="mt-1"
                                />
                            </Field>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <Field id="invitation_role" label="Role">
                                    <select
                                        id="invitation_role"
                                        value={form.invitation_role}
                                        onChange={update('invitation_role')}
                                        className="mt-1 h-10 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                                    >
                                        <option value="strata_admin">Strata Admin</option>
                                        <option value="strata_manager">Strata Manager</option>
                                    </select>
                                </Field>

                                <Field id="invitation_expires_days" label="Invitation expires (days)">
                                    <select
                                        id="invitation_expires_days"
                                        value={form.invitation_expires_days}
                                        onChange={update('invitation_expires_days')}
                                        className="mt-1 h-10 w-full rounded-md border border-input bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                                    >
                                        <option value={7}>7 days</option>
                                        <option value={14}>14 days</option>
                                        <option value={30}>30 days</option>
                                    </select>
                                </Field>
                            </div>
                        </fieldset>

                        <div className="flex items-center justify-end gap-2 pt-2 border-t">
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
                                data-testid="sm-org-submit"
                                disabled={submitting}
                                aria-busy={submitting}
                            >
                                {submitting
                                    ? <><Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true"/> Creating…</>
                                    : 'Create organisation & send invite'
                                }
                            </Button>
                        </div>
                    </form>
                </CardContent>
            </Card>
        </div>
    );
};

export default NewSmOrganisationPage;
