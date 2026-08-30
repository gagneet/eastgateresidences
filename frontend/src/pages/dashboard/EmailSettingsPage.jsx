// @featuretrace:email-delivery — Admin email provider, sender identity, SMTP/API credentials, and diagnostics UI.
// Layer: frontend
// Data flow: EmailSettingsPage -> GET/PUT/POST /email-settings -> db.email_settings -> send_email_async() provider selection (global).
// Related: backend/routers/settings.py, backend/utils/email.py, frontend/src/pages/dashboard/EmailPreferencesPage.jsx, docs/architecture/mindmap/email-delivery.md

import React, { useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger, } from '../../components/ui/tabs';
import { AlertCircle, CheckCircle2, Globe, Key, Loader2, Mail, Send, Server, ShieldCheck, Zap } from 'lucide-react';
import { toast } from 'sonner';
/**
 * @generated FunctionHeader
 * Function: EmailSettingsPage
 * Path: frontend/src/pages/dashboard/EmailSettingsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const EmailSettingsPage = () => {
    const {api} = useAuth();
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [testing, setTesting] = useState(false);
    const [testEmail, setTestEmail] = useState('');
    const [settings, setSettings] = useState({
        provider: 'resend',
        resend_api_key: '',
        sendgrid_api_key: '',
        smtp_host: '',
        smtp_port: 587,
        smtp_security: 'tls',
        smtp_user: '',
        smtp_password: '',
        sender_email: '',
        sender_name: '',
        migadu_api_key: '',
        migadu_admin_email: '',
        migadu_domain: 'eastgateresidences.com.au',
        migadu_configured: false,
    });

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchSettings
         * Path: frontend/src/pages/dashboard/EmailSettingsPage.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchSettings = async () => {
            try {
                const response = await api.get('/email-settings');
                setSettings(prev => ( {...prev, ...response.data} ));
            } catch (error) {
                console.error('Failed to fetch settings:', error);
                toast.error('Failed to load email configurations');
            } finally {
                setLoading(false);
            }
        };
        fetchSettings();
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: handleSave
     * Path: frontend/src/pages/dashboard/EmailSettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSave = async () => {
        setSaving(true);
        try {
            await api.put('/email-settings', settings);
            toast.success('Email infrastructure settings updated');
        } catch (error) {
            toast.error('Failed to save settings');
        } finally {
            setSaving(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleTest
     * Path: frontend/src/pages/dashboard/EmailSettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleTest = async () => {
        if (!testEmail) {
            toast.error('Please enter a test email address');
            return;
        }

        setTesting(true);
        try {
            const response = await api.post(`/email-settings/test?to_email=${encodeURIComponent(testEmail)}`);
            if (response.data.success) {
                toast.success(`Infrastructure verified! Test email sent via ${response.data.provider}`);
            } else {
                toast.error(response.data.error || 'Infrastructure verification failed');
            }
        } catch (error) {
            toast.error('Connection timeout or delivery failure');
        } finally {
            setTesting(false);
        }
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[400px]">
                <div className="animate-pulse flex flex-col items-center gap-4">
                    <Mail className="h-10 w-10 text-muted-foreground/40"/>
                    <p className="text-sm text-muted-foreground">Loading mail configuration...</p>
                </div>
            </div>
        );
    }

    return (
        <div className="container max-w-5xl py-8 space-y-8" data-testid="email-settings-page">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b pb-6">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Email Infrastructure</h1>
                    <p className="text-muted-foreground mt-1">Configure SMTP and API gateways for reliable community
                        communication.</p>
                </div>
                <Button onClick={handleSave} disabled={saving} className="min-w-[140px] shadow-md"
                        data-testid="save-email-settings-btn">
                    {saving ? (
                        <><Loader2 className="mr-2 h-4 w-4 animate-spin"/>Saving...</>
                    ) : (
                        <><ShieldCheck className="mr-2 h-4 w-4"/>Save Changes</>
                    )}
                </Button>
            </div>

            <div className="grid gap-8 lg:grid-cols-12">
                <div className="lg:col-span-8 space-y-6">
                    {/* Provider Selection */}
                    <Card className="border-none shadow-lg bg-card/60 backdrop-blur-sm overflow-hidden">
                        <CardHeader className="bg-muted/30 border-b pb-6">
                            <div className="flex items-center gap-2">
                                <Globe className="h-5 w-5 text-primary"/>
                                <CardTitle>Global Identity</CardTitle>
                            </div>
                            <CardDescription>Define how residents see emails from the platform.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-6">
                            <div className="grid gap-6 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="sender_name">Display Name</Label>
                                    <Input
                                        id="sender_name"
                                        value={settings.sender_name}
                                        onChange={(e) => setSettings(prev => ( {
                                            ...prev,
                                            sender_name: e.target.value
                                        } ))}
                                        placeholder="East Gate Residences"
                                    />
                                    <p className="text-[10px] text-muted-foreground">The "From" name in user
                                        inboxes.</p>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="sender_email">Reply-To Address</Label>
                                    <Input
                                        id="sender_email"
                                        type="email"
                                        value={settings.sender_email}
                                        onChange={(e) => setSettings(prev => ( {
                                            ...prev,
                                            sender_email: e.target.value
                                        } ))}
                                        placeholder="admin@eastgateresidences.com.au"
                                    />
                                    <p className="text-[10px] text-muted-foreground">Authenticated sender address.</p>
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    {/* Provider Tabs */}
                    <Tabs value={settings.provider} className="w-full"
                          onValueChange={(v) => setSettings(prev => ( {...prev, provider: v} ))}>
                        <TabsList className="grid w-full grid-cols-3 h-14 p-1.5 bg-muted/50 rounded-xl">
                            <TabsTrigger value="resend"
                                         className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-md gap-2">
                                <Zap className="h-4 w-4 text-amber-500"/> Resend
                            </TabsTrigger>
                            <TabsTrigger value="sendgrid"
                                         className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-md gap-2">
                                <Mail className="h-4 w-4 text-blue-500"/> SendGrid
                            </TabsTrigger>
                            <TabsTrigger value="smtp"
                                         className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-md gap-2">
                                <Server className="h-4 w-4 text-slate-500"/> SMTP
                            </TabsTrigger>
                        </TabsList>

                        <TabsContent value="resend" className="mt-6 animate-in slide-in-from-bottom-2 duration-300">
                            <Card className="border-none shadow-lg bg-card/60 backdrop-blur-sm">
                                <CardHeader>
                                    <div className="flex items-center justify-between">
                                        <CardTitle className="text-lg">Resend API Gateway</CardTitle>
                                        <Badge
                                            className="bg-amber-100 text-amber-700 hover:bg-amber-100 border-amber-200">Recommended</Badge>
                                    </div>
                                    <CardDescription>Modern, developer-friendly email for high
                                        deliverability.</CardDescription>
                                </CardHeader>
                                <CardContent className="space-y-4">
                                    <div className="space-y-2">
                                        <div className="flex items-center justify-between">
                                            <Label htmlFor="resend_api_key" className="flex items-center gap-2">
                                                <Key className="h-3 w-3 text-muted-foreground"/> API Key
                                            </Label>
                                            <a href="https://resend.com/api-keys" target="_blank"
                                               rel="noopener noreferrer"
                                               className="text-[10px] text-primary hover:underline">Get Key</a>
                                        </div>
                                        <Input
                                            id="resend_api_key"
                                            type="password"
                                            value={settings.resend_api_key}
                                            onChange={(e) => setSettings(prev => ( {
                                                ...prev,
                                                resend_api_key: e.target.value
                                            } ))}
                                            placeholder="re_xxxxxxxxxxxx"
                                            className="font-mono"
                                        />
                                    </div>
                                </CardContent>
                            </Card>
                        </TabsContent>

                        <TabsContent value="sendgrid" className="mt-6 animate-in slide-in-from-bottom-2 duration-300">
                            <Card className="border-none shadow-lg bg-card/60 backdrop-blur-sm">
                                <CardHeader>
                                    <CardTitle className="text-lg">SendGrid Configuration</CardTitle>
                                    <CardDescription>Enterprise-grade delivery via Twilio SendGrid.</CardDescription>
                                </CardHeader>
                                <CardContent className="space-y-4">
                                    <div className="space-y-2">
                                        <div className="flex items-center justify-between">
                                            <Label htmlFor="sendgrid_api_key" className="flex items-center gap-2">
                                                <Key className="h-3 w-3 text-muted-foreground"/> API Key
                                            </Label>
                                            <a href="https://app.sendgrid.com/settings/api_keys" target="_blank"
                                               rel="noopener noreferrer"
                                               className="text-[10px] text-primary hover:underline">Get Key</a>
                                        </div>
                                        <Input
                                            id="sendgrid_api_key"
                                            type="password"
                                            value={settings.sendgrid_api_key}
                                            onChange={(e) => setSettings(prev => ( {
                                                ...prev,
                                                sendgrid_api_key: e.target.value
                                            } ))}
                                            placeholder="SG.xxxxxxxxxxxx"
                                            className="font-mono"
                                        />
                                    </div>
                                </CardContent>
                            </Card>
                        </TabsContent>

                        <TabsContent value="smtp" className="mt-6 animate-in slide-in-from-bottom-2 duration-300">
                            <Card className="border-none shadow-lg bg-card/60 backdrop-blur-sm">
                                <CardHeader>
                                    <CardTitle className="text-lg">Custom SMTP Server</CardTitle>
                                    <CardDescription>Connect to your own mail server (e.g. Migadu, Google
                                        Workspace).</CardDescription>
                                </CardHeader>
                                <CardContent className="space-y-6">
                                    {/* Migadu Quick Action */}
                                    <Button
                                        variant="outline"
                                        size="sm"
                                        className="w-full border-primary/20 bg-primary/5 hover:bg-primary/10 text-primary transition-all h-12"
                                        onClick={() => setSettings(prev => ( {
                                            ...prev,
                                            provider: 'smtp',
                                            smtp_host: 'smtp.migadu.com',
                                            smtp_port: 465,
                                            smtp_security: 'ssl',
                                            smtp_user: 'noreply@eastgateresidences.com.au',
                                            sender_email: 'noreply@eastgateresidences.com.au'
                                        } ))}
                                    >
                                        <CheckCircle2 className="mr-2 h-4 w-4"/> Load Migadu Preset
                                    </Button>

                                    <div className="grid gap-4 md:grid-cols-3">
                                        <div className="md:col-span-2 space-y-2">
                                            <Label htmlFor="smtp_host">Host</Label>
                                            <Input
                                                id="smtp_host"
                                                value={settings.smtp_host}
                                                onChange={(e) => setSettings(prev => ( {
                                                    ...prev,
                                                    smtp_host: e.target.value
                                                } ))}
                                                placeholder="smtp.example.com"
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <Label htmlFor="smtp_port">Port</Label>
                                            <Input
                                                id="smtp_port"
                                                type="number"
                                                value={settings.smtp_port}
                                                onChange={(e) => setSettings(prev => ( {
                                                    ...prev,
                                                    smtp_port: parseInt(e.target.value)
                                                } ))}
                                            />
                                        </div>
                                    </div>

                                    <div className="grid gap-4 md:grid-cols-2">
                                        <div className="space-y-2">
                                            <Label htmlFor="smtp_user">SMTP Username</Label>
                                            <Input
                                                id="smtp_user"
                                                value={settings.smtp_user}
                                                onChange={(e) => setSettings(prev => ( {
                                                    ...prev,
                                                    smtp_user: e.target.value
                                                } ))}
                                                placeholder="noreply@domain.com"
                                            />
                                        </div>
                                        <div className="space-y-2">
                                            <Label htmlFor="smtp_password">SMTP Password</Label>
                                            <Input
                                                id="smtp_password"
                                                type="password"
                                                value={settings.smtp_password}
                                                onChange={(e) => setSettings(prev => ( {
                                                    ...prev,
                                                    smtp_password: e.target.value
                                                } ))}
                                                placeholder="••••••••"
                                            />
                                        </div>
                                    </div>
                                </CardContent>
                            </Card>
                        </TabsContent>
                    </Tabs>

                    {/* Migadu API section — below provider tabs, always visible */}
                    <Card className="border-none shadow-md mt-6">
                        <CardHeader className="pb-3">
                            <div className="flex items-center justify-between">
                                <CardTitle className="text-sm font-semibold flex items-center gap-2">
                                    Migadu API (Mailbox Password Sync)
                                    {settings.migadu_configured
                                        ? <span
                                            className="text-[10px] bg-green-100 text-green-700 px-2 py-0.5 rounded-full font-medium">Configured ✓</span>
                                        : <span
                                            className="text-[10px] bg-amber-100 text-amber-700 px-2 py-0.5 rounded-full font-medium">Not configured</span>
                                    }
                                </CardTitle>
                                <a href="https://admin.migadu.com/account/api" target="_blank" rel="noreferrer"
                                   className="text-[10px] text-primary hover:underline">Get API Key ↗</a>
                            </div>
                            <p className="text-[10px] text-muted-foreground mt-1">
                                Required for "Reset User Passwords" to sync mailbox passwords directly on the Migadu
                                server.
                                Without this, only the portal record is updated.
                            </p>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="migadu_admin_email"
                                       className="text-[10px] uppercase font-bold text-muted-foreground">Migadu Account
                                    Email</Label>
                                <Input
                                    id="migadu_admin_email"
                                    type="email"
                                    value={settings.migadu_admin_email}
                                    onChange={(e) => setSettings(prev => ( {
                                        ...prev,
                                        migadu_admin_email: e.target.value
                                    } ))}
                                    placeholder="admin@eastgateresidences.com.au"
                                />
                                <p className="text-[10px] text-muted-foreground">Your Migadu account login email (used
                                    for API Basic Auth)</p>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="migadu_api_key"
                                       className="text-[10px] uppercase font-bold text-muted-foreground">Migadu API
                                    Key</Label>
                                <Input
                                    id="migadu_api_key"
                                    type="password"
                                    value={settings.migadu_api_key}
                                    onChange={(e) => setSettings(prev => ( {...prev, migadu_api_key: e.target.value} ))}
                                    placeholder={settings.migadu_configured ? 'Saved (enter to change)' : '••••••••'}
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="migadu_domain"
                                       className="text-[10px] uppercase font-bold text-muted-foreground">Mail
                                    Domain</Label>
                                <Input
                                    id="migadu_domain"
                                    type="text"
                                    value={settings.migadu_domain}
                                    onChange={(e) => setSettings(prev => ( {...prev, migadu_domain: e.target.value} ))}
                                    placeholder="eastgateresidences.com.au"
                                />
                            </div>
                        </CardContent>
                    </Card>
                </div>

                <div className="lg:col-span-4 space-y-6">
                    <Card className="border-none shadow-md bg-muted/30">
                        <CardHeader className="pb-3">
                            <div className="flex items-center gap-2">
                                <Send className="h-4 w-4 text-primary"/>
                                <CardTitle className="text-sm font-semibold">Test Connection</CardTitle>
                            </div>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <p className="text-xs text-muted-foreground">Verify your credentials and network
                                connectivity before saving.</p>
                            <div className="space-y-2">
                                <Label htmlFor="test_email"
                                       className="text-[10px] uppercase font-bold text-muted-foreground">Recipient</Label>
                                <Input
                                    id="test_email"
                                    type="email"
                                    value={testEmail}
                                    onChange={(e) => setTestEmail(e.target.value)}
                                    placeholder="admin@silverfox.com"
                                    className="bg-background"
                                />
                            </div>
                            <Button
                                onClick={handleTest}
                                disabled={testing}
                                variant="secondary"
                                className="w-full shadow-sm"
                            >
                                {testing ? (
                                    <><Loader2 className="mr-2 h-4 w-4 animate-spin"/> Testing...</>
                                ) : (
                                    'Run Diagnostics'
                                )}
                            </Button>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-sm bg-blue-50/50 dark:bg-blue-950/20">
                        <CardContent className="pt-6">
                            <div className="flex gap-3">
                                <AlertCircle className="h-5 w-5 text-blue-500 shrink-0 mt-0.5"/>
                                <div className="space-y-2">
                                    <p className="text-xs font-semibold text-blue-700 dark:text-blue-300">Compliance
                                        Warning</p>
                                    <p className="text-[10px] leading-relaxed text-blue-600/80 dark:text-blue-300/70">
                                        Ensure your sender email matches your domain (eastgateresidences.com.au) to
                                        avoid being flagged as spam by resident mail providers.
                                    </p>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            </div>
        </div>
    );
};
// Simple Badge component for local use
/**
 * @generated FunctionHeader
 * Function: Badge
 * Path: frontend/src/pages/dashboard/EmailSettingsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const Badge = ({children, className = ''}) => (
    <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium border ${className}`}>
    {children}
  </span>
);

export default EmailSettingsPage;
