import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Badge } from '../../components/ui/badge';
import { AlertCircle, ExternalLink, Eye, EyeOff, Key, Loader2, Lock, Mail, User } from 'lucide-react';
import { toast } from 'sonner';
import { Alert, AlertDescription } from '../../components/ui/alert';
/**
 * @generated FunctionHeader
 * Function: EmailAccessPage
 * Path: frontend/src/pages/dashboard/EmailAccessPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const EmailAccessPage = () => {
    const {api, user, hasPermission, selectedBuilding} = useAuth();
    const buildingLabel = selectedBuilding?.name || 'Building';
    const [mailAccess, setMailAccess] = useState(null);
    const [loading, setLoading] = useState(true);
    const [showPassword, setShowPassword] = useState(false);
    const [error, setError] = useState(null);

    const fetchMailAccess = useCallback(async () => {
        if (!hasPermission('can_access_email')) {
            setError('Email access is only available to property owners.');
            setLoading(false);
            return;
        }

        try {
            setLoading(true);
            const response = await api.get('/mail/access');
            setMailAccess(response.data);
            setError(null);
        } catch (error) {
            console.error('Failed to fetch mail access:', error);
            if (error.response?.status === 403) {
                setError('Email access is only available to property owners.');
            } else if (error.response?.status === 404) {
                setError('Mail account not configured for your profile. Please contact the administrator.');
            } else {
                setError('Failed to load mail access information.');
            }
            toast.error('Failed to load email access');
        } finally {
            setLoading(false);
        }
    }, [api, hasPermission]);

    useEffect(() => {
        fetchMailAccess();
    }, [fetchMailAccess]);
    /**
     * @generated FunctionHeader
     * Function: handleOpenMail
     * Path: frontend/src/pages/dashboard/EmailAccessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleOpenMail = () => {
        if (!mailAccess) return;

        // Open mail client in new tab
        window.open(mailAccess.mail_url, '_blank');

        // Show toast with login reminder
        toast.success('Email client opened in new tab', {
            description: 'Use your credentials to log in if prompted.'
        });
    };
    /**
     * @generated FunctionHeader
     * Function: copyToClipboard
     * Path: frontend/src/pages/dashboard/EmailAccessPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const copyToClipboard = (text, label) => {
        navigator.clipboard.writeText(text);
        toast.success(`${label} copied to clipboard`);
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[400px]" data-testid="email-access-loading">
                <div className="text-center">
                    <Loader2 className="h-8 w-8 animate-spin text-primary mx-auto mb-4"/>
                    <p className="text-muted-foreground">Loading email access...</p>
                </div>
            </div>
        );
    }

    if (error || !hasPermission('can_access_email')) {
        return (
            <div className="space-y-6" data-testid="email-access-page">
                <div>
                    <h1 className="text-2xl font-bold">{buildingLabel} Mail</h1>
                    <p className="text-muted-foreground">Access your community email account</p>
                </div>

                <Alert variant="destructive">
                    <AlertCircle className="h-4 w-4"/>
                    <AlertDescription>
                        {error || 'You do not have permission to access email.'}
                    </AlertDescription>
                </Alert>
            </div>
        );
    }

    return (
        <div className="space-y-6" data-testid="email-access-page">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold">{buildingLabel} Mail</h1>
                    <p className="text-muted-foreground">Access your community email account</p>
                </div>
                <Badge variant="success" className="bg-green-50 text-green-700 border-green-200">
                    <Lock className="h-3 w-3 mr-1"/>
                    Secure Access
                </Badge>
            </div>

            <Card className="card-dashboard">
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <Mail className="h-5 w-5"/>
                        Email Account Details
                    </CardTitle>
                    <CardDescription>
                        Your personal {buildingLabel} email account credentials
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-6">
                    {/* Email Username */}
                    <div className="space-y-2">
                        <div className="flex items-center justify-between">
                            <label className="text-sm font-medium flex items-center gap-2">
                                <User className="h-4 w-4 text-muted-foreground"/>
                                Email Address
                            </label>
                            <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => copyToClipboard(mailAccess.mail_username, 'Email address')}
                            >
                                Copy
                            </Button>
                        </div>
                        <div className="p-3 bg-muted rounded-lg font-mono text-sm">
                            {mailAccess.mail_username}
                        </div>
                    </div>

                    {/* Email Password */}
                    <div className="space-y-2">
                        <div className="flex items-center justify-between">
                            <label className="text-sm font-medium flex items-center gap-2">
                                <Key className="h-4 w-4 text-muted-foreground"/>
                                Password
                            </label>
                            <div className="flex items-center gap-2">
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => setShowPassword(!showPassword)}
                                >
                                    {showPassword ? (
                                        <>
                                            <EyeOff className="h-4 w-4 mr-1"/>
                                            Hide
                                        </>
                                    ) : (
                                        <>
                                            <Eye className="h-4 w-4 mr-1"/>
                                            Show
                                        </>
                                    )}
                                </Button>
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() => copyToClipboard(mailAccess.mail_password, 'Password')}
                                >
                                    Copy
                                </Button>
                            </div>
                        </div>
                        <div className="p-3 bg-muted rounded-lg font-mono text-sm">
                            {showPassword ? mailAccess.mail_password : '••••••••'}
                        </div>
                    </div>

                    {/* Access Button */}
                    <div className="pt-4 border-t">
                        <Button
                            onClick={handleOpenMail}
                            size="lg"
                            className="w-full"
                            data-testid="open-email-btn"
                        >
                            <ExternalLink className="mr-2 h-5 w-5"/>
                            Open {buildingLabel} Mail
                        </Button>
                        <p className="text-xs text-muted-foreground text-center mt-2">
                            Opens in a new tab • Log in with your credentials if prompted
                        </p>
                    </div>
                </CardContent>
            </Card>

            {/* Security Info */}
            <Card className="border-blue-200 bg-blue-50/50">
                <CardContent className="pt-6">
                    <div className="flex gap-4">
                        <AlertCircle className="h-5 w-5 text-blue-600 flex-shrink-0 mt-0.5"/>
                        <div className="space-y-2 text-sm">
                            <p className="font-medium text-blue-900">Security Information</p>
                            <ul className="text-blue-700 space-y-1">
                                <li>• Your email password is securely stored and encrypted</li>
                                <li>• Never share your email credentials with anyone</li>
                                <li>• Email access is restricted to verified property owners only</li>
                                <li>• Contact the admin if you need to change your password</li>
                            </ul>
                        </div>
                    </div>
                </CardContent>
            </Card>

            {/* Quick Help */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-lg">How to Access Your Email</CardTitle>
                </CardHeader>
                <CardContent>
                    <ol className="space-y-3 text-sm">
                        <li className="flex gap-3">
              <span
                  className="flex items-center justify-center w-6 h-6 rounded-full bg-primary text-white text-xs font-bold flex-shrink-0">
                1
              </span>
                            <span>Click the "Open {buildingLabel} Mail" button above</span>
                        </li>
                        <li className="flex gap-3">
              <span
                  className="flex items-center justify-center w-6 h-6 rounded-full bg-primary text-white text-xs font-bold flex-shrink-0">
                2
              </span>
                            <span>A new tab will open with the email client login page</span>
                        </li>
                        <li className="flex gap-3">
              <span
                  className="flex items-center justify-center w-6 h-6 rounded-full bg-primary text-white text-xs font-bold flex-shrink-0">
                3
              </span>
                            <span>Enter your email address and password shown above</span>
                        </li>
                        <li className="flex gap-3">
              <span
                  className="flex items-center justify-center w-6 h-6 rounded-full bg-primary text-white text-xs font-bold flex-shrink-0">
                4
              </span>
                            <span>Access your inbox and start managing your emails</span>
                        </li>
                    </ol>
                </CardContent>
            </Card>
        </div>
    );
};

export default EmailAccessPage;
