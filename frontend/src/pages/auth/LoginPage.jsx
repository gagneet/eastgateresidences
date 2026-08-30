import React, { useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../contexts/AuthContext';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Card, CardContent } from '../../components/ui/card';
import { Building2, Clock, Eye, EyeOff, Loader2, Lock, Mail } from 'lucide-react';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: LoginPage
 * Path: frontend/src/pages/auth/LoginPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const LoginPage = () => {
    const router = useRouter();
    const {login} = useAuth();
    const [loading, setLoading] = useState(false);
    const [showPassword, setShowPassword] = useState(false);
    const [pendingBanner, setPendingBanner] = useState(null);
    const [formData, setFormData] = useState({
        email: '',
        password: ''
    });
    /**
     * @generated FunctionHeader
     * Function: handleChange
     * Path: frontend/src/pages/auth/LoginPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleChange = (e) => {
        setFormData(prev => ( {
            ...prev,
            [ e.target.name ]: e.target.value
        } ));
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/auth/LoginPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        setLoading(true);

        try {
            const result = await login(formData.email, formData.password);
            // Check both error and ok fields — NextAuth may return ok:false without an error string
            if (result?.ok === false || result?.error) {
                toast.error('Invalid email or password');
                return;
            }
            // Check if TOTP is required — fetch the NextAuth session after signIn sets the cookie.
            // This cannot use useSession() at render time (SSR context issues), so we do a
            // client-side fetch to /api/auth/session instead.
            const currentSession = typeof window !== 'undefined'
                ? await fetch('/api/auth/session').then(r => r.json()).catch(() => null)
                : null;
            if (currentSession?.totp_required) {
                window.location.href = '/login/totp-challenge';
                return;
            }
            toast.success('Welcome back!');
            const params = typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : null;
            const nextPath = params?.get('next');
            const safeNext = nextPath && nextPath.startsWith('/') ? nextPath : null;
            // Use full page navigation so the browser sends the new session cookie
            // and the server-side session is properly recognised before rendering /dashboard
            window.location.href = safeNext || '/dashboard';
        } catch (error) {
            const msg = error.message || '';
            if (msg.startsWith('PENDING_APPROVAL:')) {
                setPendingBanner(msg.slice('PENDING_APPROVAL:'.length));
            } else {
                setPendingBanner(null);
                toast.error(msg || error.response?.data?.detail || 'Invalid email or password');
            }
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="min-h-screen flex items-center justify-center bg-muted/30 px-4 py-12" data-testid="login-page">
            <div className="w-full max-w-md">
                <div className="text-center mb-8">
                    <Link href="/" className="inline-flex items-center gap-2 mb-6">
                        <Building2 className="h-10 w-10 text-primary"/>
                        <span className="text-2xl font-bold">East Gate Residences</span>
                    </Link>
                    <h1 className="text-2xl font-bold">Welcome Back</h1>
                    <p className="text-muted-foreground">Sign in to your resident account</p>
                </div>

                {pendingBanner && (
                    <div
                        className="mb-4 flex gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800"
                        role="alert"
                        data-testid="pending-approval-banner"
                    >
                        <Clock className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" aria-hidden="true"/>
                        <div>
                            <p className="font-semibold mb-0.5">Account Pending Approval</p>
                            <p>{pendingBanner}</p>
                        </div>
                    </div>
                )}

                <Card className="card-dashboard">
                    <CardContent className="pt-6">
                        <form onSubmit={handleSubmit} className="space-y-4" data-testid="login-form">
                            <div className="space-y-2">
                                <Label htmlFor="email">Email Address</Label>
                                <div className="relative">
                                    <Mail
                                        className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                                    <Input
                                        id="email"
                                        name="email"
                                        type="email"
                                        placeholder="your@email.com"
                                        value={formData.email}
                                        onChange={handleChange}
                                        className="pl-10"
                                        required
                                        data-testid="email-input"
                                    />
                                </div>
                                <p className="text-[11px] text-muted-foreground">
                                    You can sign in with your personal email or your{' '}
                                    <span
                                        className="font-medium text-primary/80">@eastgateresidences.com.au</span> address.
                                </p>
                            </div>

                            <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                    <Label htmlFor="password">Password</Label>
                                    <Link href="/forgot-password" className="text-sm text-primary hover:underline">
                                        Forgot password?
                                    </Link>
                                </div>
                                <div className="relative">
                                    <Lock
                                        className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                                    <Input
                                        id="password"
                                        name="password"
                                        type={showPassword ? 'text' : 'password'}
                                        placeholder="••••••••"
                                        value={formData.password}
                                        onChange={handleChange}
                                        className="pl-10 pr-10"
                                        required
                                        data-testid="password-input"
                                    />
                                    <button
                                        type="button"
                                        onClick={() => setShowPassword(!showPassword)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                                        aria-label={showPassword ? "Hide password" : "Show password"}
                                    >
                                        {showPassword ? <EyeOff className="h-4 w-4"/> : <Eye className="h-4 w-4"/>}
                                    </button>
                                </div>
                            </div>

                            <Button
                                type="submit"
                                className="w-full btn-primary"
                                disabled={loading}
                                data-testid="login-submit"
                            >
                                {loading ? (
                                    <>
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                                        Signing in...
                                    </>
                                ) : (
                                    'Sign In'
                                )}
                            </Button>
                        </form>

                        <div className="mt-6 text-center text-sm">
                            <span className="text-muted-foreground">Don't have an account? </span>
                            <Link href="/register" className="text-primary hover:underline font-medium">
                                Register here
                            </Link>
                        </div>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
};

export default LoginPage;
