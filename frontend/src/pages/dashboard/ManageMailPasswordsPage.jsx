import React, { useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Badge } from '../../components/ui/badge';
import { Separator } from '../../components/ui/separator';
import { Switch } from '../../components/ui/switch';
import {
    AlertCircle,
    CheckCircle2,
    Database,
    Eye,
    EyeOff,
    Info,
    Key,
    Loader2,
    Mail,
    RefreshCw,
    Server,
    ShieldCheck,
    User
} from 'lucide-react';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: StatusBadge
 * Path: frontend/src/pages/dashboard/ManageMailPasswordsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const StatusBadge = ({status}) => {
    if (status === 'updated') return <Badge className="bg-green-100 text-green-800 border-green-200">Updated ✓</Badge>;
    if (status === 'error') return <Badge className="bg-red-100 text-red-800 border-red-200">Error ✗</Badge>;
    if (status === 'skipped') return <Badge className="bg-slate-100 text-slate-600 border-slate-200">Skipped</Badge>;
    return null;
};
/**
 * @generated FunctionHeader
 * Function: ResultRow
 * Path: frontend/src/pages/dashboard/ManageMailPasswordsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ResultRow = ({icon: Icon, label, status, detail, color}) => (
    <div className="flex items-start gap-3 py-3">
        <div className={`mt-0.5 p-1.5 rounded-md ${color}`}>
            <Icon className="h-4 w-4"/>
        </div>
        <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
                <span className="text-sm font-medium">{label}</span>
                {status && <StatusBadge status={status}/>}
            </div>
            {detail && <p className="text-xs text-muted-foreground mt-0.5">{detail}</p>}
        </div>
    </div>
);
/**
 * @generated FunctionHeader
 * Function: ManageMailPasswordsPage
 * Path: frontend/src/pages/dashboard/ManageMailPasswordsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ManageMailPasswordsPage = () => {
    const {api} = useAuth();
    const [loading, setLoading] = useState(false);
    const [showPassword, setShowPassword] = useState(false);
    const [resetPortal, setResetPortal] = useState(true);
    const [resetMail, setResetMail] = useState(true);
    const [results, setResults] = useState(null);
    const [formData, setFormData] = useState({identifier: '', new_password: ''});
    /**
     * @generated FunctionHeader
     * Function: generatePassword
     * Path: frontend/src/pages/dashboard/ManageMailPasswordsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const generatePassword = () => {
        const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789!@#$%';
        const pwd = Array.from({length: 16}, () => chars[ Math.floor(Math.random() * chars.length) ]).join('');
        setFormData(prev => ( {...prev, new_password: pwd} ));
        setShowPassword(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/ManageMailPasswordsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        if (!formData.identifier.trim()) {
            toast.error('Enter a user email or mail username');
            return;
        }
        if (!formData.new_password) {
            toast.error('Enter a new password');
            return;
        }
        if (formData.new_password.length < 8) {
            toast.error('Password must be at least 8 characters');
            return;
        }
        if (!resetPortal && !resetMail) {
            toast.error('Select at least one type of password to reset');
            return;
        }

        setLoading(true);
        setResults(null);

        try {
            const response = await api.post('/admin/reset-user-passwords', {
                identifier: formData.identifier.trim(),
                new_password: formData.new_password,
                reset_portal: resetPortal,
                reset_mail: resetMail,
            });

            const data = response.data;
            setResults(data.results);

            const updated = Object.values(data.results || {})
                .filter(v => typeof v === 'object' && v?.status === 'updated').length;

            if (updated > 0) {
                toast.success(`Password reset complete — ${updated} system(s) updated`);
            } else {
                toast.warning('No passwords were changed — check the results below');
            }
        } catch (error) {
            const msg = error.response?.data?.detail || 'Password reset failed';
            toast.error(msg);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="max-w-2xl mx-auto space-y-6" data-testid="manage-mail-passwords-page">
            {/* Header */}
            <div>
                <h1 className="text-2xl font-bold">Reset User Passwords</h1>
                <p className="text-muted-foreground mt-1">
                    Unified reset — portal login, portal mail record, and Migadu mailbox server password
                </p>
            </div>

            {/* What this resets */}
            <Card className="card-dashboard border-blue-200 bg-blue-50/50">
                <CardContent className="p-5 space-y-3">
                    <div className="flex items-center gap-2 text-blue-800 font-medium text-sm">
                        <Info className="h-4 w-4"/>
                        What this page resets
                    </div>
                    <div className="grid grid-cols-1 gap-2 text-sm">
                        <div className="flex items-start gap-2 text-blue-700">
                            <ShieldCheck className="h-4 w-4 mt-0.5 shrink-0"/>
                            <span><strong>Portal login</strong> — the password used to sign in at eastgateresidences.com.au</span>
                        </div>
                        <div className="flex items-start gap-2 text-blue-700">
                            <Database className="h-4 w-4 mt-0.5 shrink-0"/>
                            <span><strong>Mail record (DB)</strong> — the credential stored in the portal for the user's Email Access page</span>
                        </div>
                        <div className="flex items-start gap-2 text-blue-700">
                            <Server className="h-4 w-4 mt-0.5 shrink-0"/>
                            <span><strong>Migadu server</strong> — the actual mailbox password on mail.eastgateresidences.com.au (requires Migadu API key in Email Settings)</span>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* Reset Form */}
            <Card className="card-dashboard">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Key className="h-5 w-5"/>
                        Reset Passwords
                    </CardTitle>
                    <CardDescription>
                        Enter the user's portal email or @eastgateresidences.com.au mail address
                    </CardDescription>
                </CardHeader>
                <CardContent>
                    <form onSubmit={handleSubmit} className="space-y-6">
                        {/* User identifier */}
                        <div className="space-y-2">
                            <Label htmlFor="identifier" className="flex items-center gap-2">
                                <User className="h-4 w-4"/>
                                User Email or Mail Username
                            </Label>
                            <Input
                                id="identifier"
                                type="text"
                                value={formData.identifier}
                                onChange={e => setFormData(prev => ( {...prev, identifier: e.target.value} ))}
                                placeholder="avneet@eastgateresidences.com.au"
                                data-testid="identifier-input"
                                required
                            />
                            <p className="text-xs text-muted-foreground">
                                Portal email (e.g. avneet.rooprai@gmail.com) OR mail username (e.g.
                                avneet@eastgateresidences.com.au)
                            </p>
                        </div>

                        {/* New password */}
                        <div className="space-y-2">
                            <Label htmlFor="new_password" className="flex items-center gap-2">
                                <Key className="h-4 w-4"/>
                                New Password
                            </Label>
                            <div className="flex gap-2">
                                <div className="relative flex-1">
                                    <Input
                                        id="new_password"
                                        type={showPassword ? 'text' : 'password'}
                                        value={formData.new_password}
                                        onChange={e => setFormData(prev => ( {...prev, new_password: e.target.value} ))}
                                        placeholder="Minimum 8 characters"
                                        data-testid="new-password-input"
                                        className="pr-10"
                                        required
                                    />
                                    <button
                                        type="button"
                                        onClick={() => setShowPassword(v => !v)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                                        tabIndex={-1}
                                    >
                                        {showPassword ? <EyeOff className="h-4 w-4"/> : <Eye className="h-4 w-4"/>}
                                    </button>
                                </div>
                                <Button type="button" variant="outline" onClick={generatePassword} className="shrink-0">
                                    <RefreshCw className="h-4 w-4 mr-1.5"/>
                                    Generate
                                </Button>
                            </div>
                            <p className="text-xs text-muted-foreground">
                                The same password will be applied to all selected systems below
                            </p>
                        </div>

                        <Separator/>

                        {/* What to reset toggles */}
                        <div className="space-y-3">
                            <p className="text-sm font-medium">What to reset</p>
                            <div className="flex items-center justify-between py-2">
                                <div className="flex items-center gap-2">
                                    <ShieldCheck className="h-4 w-4 text-muted-foreground"/>
                                    <div>
                                        <p className="text-sm font-medium">Portal Login Password</p>
                                        <p className="text-xs text-muted-foreground">Sign-in password for
                                            eastgateresidences.com.au</p>
                                    </div>
                                </div>
                                <Switch checked={resetPortal} onCheckedChange={setResetPortal}
                                        data-testid="reset-portal-toggle"/>
                            </div>
                            <div className="flex items-center justify-between py-2">
                                <div className="flex items-center gap-2">
                                    <Mail className="h-4 w-4 text-muted-foreground"/>
                                    <div>
                                        <p className="text-sm font-medium">Mail Password (DB + Migadu server)</p>
                                        <p className="text-xs text-muted-foreground">Updates portal record and syncs to
                                            Migadu if API key is configured</p>
                                    </div>
                                </div>
                                <Switch checked={resetMail} onCheckedChange={setResetMail}
                                        data-testid="reset-mail-toggle"/>
                            </div>
                        </div>

                        <Button
                            type="submit"
                            className="w-full"
                            disabled={loading}
                            data-testid="submit-reset-btn"
                        >
                            {loading ? (
                                <><Loader2 className="mr-2 h-4 w-4 animate-spin"/>Resetting Passwords...</>
                            ) : (
                                <><CheckCircle2 className="mr-2 h-4 w-4"/>Reset Passwords</>
                            )}
                        </Button>
                    </form>
                </CardContent>
            </Card>

            {/* Results */}
            {results && (
                <Card className="card-dashboard">
                    <CardHeader>
                        <CardTitle className="text-base flex items-center gap-2">
                            <CheckCircle2 className="h-4 w-4 text-green-600"/>
                            Reset Results — {results.user_name}
                        </CardTitle>
                        <CardDescription>{results.user_email}{results.mail_username && ` · ${results.mail_username}`}</CardDescription>
                    </CardHeader>
                    <CardContent className="divide-y">
                        <ResultRow
                            icon={ShieldCheck}
                            label="Portal Login Password"
                            status={results.portal_login?.status}
                            detail={results.portal_login?.detail}
                            color="bg-blue-100 text-blue-700"
                        />
                        <ResultRow
                            icon={Database}
                            label="Mail Password (MongoDB record)"
                            status={results.mail_db?.status}
                            detail={results.mail_db?.detail}
                            color="bg-purple-100 text-purple-700"
                        />
                        <ResultRow
                            icon={Server}
                            label="Migadu Server Password"
                            status={results.migadu_server?.status}
                            detail={results.migadu_server?.detail}
                            color={results.migadu_server?.status === 'updated' ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-500'}
                        />
                    </CardContent>
                </Card>
            )}

            {/* Help */}
            <Card className="card-dashboard">
                <CardHeader>
                    <CardTitle className="text-sm flex items-center gap-2">
                        <AlertCircle className="h-4 w-4"/>
                        Migadu API not configured?
                    </CardTitle>
                </CardHeader>
                <CardContent className="text-sm text-muted-foreground space-y-2">
                    <p>
                        To enable server-side Migadu password sync, add your Migadu API key in{' '}
                        <strong>Email Settings</strong> (Migadu API section). Get your key from{' '}
                        <a href="https://admin.migadu.com/account/api" target="_blank" rel="noreferrer"
                           className="text-blue-600 underline">
                            admin.migadu.com/account/api
                        </a>.
                    </p>
                    <p>
                        Without the API key, the portal mail record is still updated but the actual Migadu
                        mailbox password must be changed manually via the Migadu admin panel.
                    </p>
                </CardContent>
            </Card>
        </div>
    );
};

export default ManageMailPasswordsPage;
