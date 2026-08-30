import React, { useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { AlertCircle, CheckCircle2, Eye, FileText, Save, Shield } from 'lucide-react';
import { toast } from 'sonner';
import RichTextEditor from '../../components/shared/RichTextEditor';
/**
 * @generated FunctionHeader
 * Function: AdminConsolePage
 * Path: frontend/src/pages/dashboard/AdminConsolePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const AdminConsolePage = () => {
    const {api, selectedBuilding} = useAuth();
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [privacyContent, setPrivacyContent] = useState('');
    const [termsContent, setTermsContent] = useState('');

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchContent
         * Path: frontend/src/pages/dashboard/AdminConsolePage.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchContent = async () => {
            try {
                const [privacyRes, termsRes] = await Promise.all([
                    api.get('/legal-pages/privacy-policy'),
                    api.get('/legal-pages/terms-of-use')
                ]);
                setPrivacyContent(privacyRes.data.content || '');
                setTermsContent(termsRes.data.content || '');
            } catch (error) {
                console.error('Failed to fetch legal pages:', error);
                toast.error('Failed to load legal content');
            } finally {
                setLoading(false);
            }
        };
        fetchContent();
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: savePrivacyPolicy
     * Path: frontend/src/pages/dashboard/AdminConsolePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const savePrivacyPolicy = async () => {
        setSaving(true);
        try {
            await api.put('/legal-pages/privacy-policy', {content: privacyContent});
            toast.success('Privacy Policy updated successfully');
        } catch (error) {
            toast.error('Failed to update Privacy Policy');
        } finally {
            setSaving(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: saveTermsOfUse
     * Path: frontend/src/pages/dashboard/AdminConsolePage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const saveTermsOfUse = async () => {
        setSaving(true);
        try {
            await api.put('/legal-pages/terms-of-use', {content: termsContent});
            toast.success('Terms of Use updated successfully');
        } catch (error) {
            toast.error('Failed to update Terms of Use');
        } finally {
            setSaving(false);
        }
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[400px]">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"></div>
            </div>
        );
    }

    return (
        <div className="container max-w-6xl py-8 space-y-8" data-testid="admin-console-page">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b pb-6">
                <div className="flex items-center gap-4">
                    <div className="p-3 rounded-xl bg-primary/10 border border-primary/20 shadow-sm">
                        <Shield className="h-8 w-8 text-primary"/>
                    </div>
                    <div>
                        <h1 className="text-3xl font-bold tracking-tight text-foreground">Legal & Privacy Console</h1>
                        <p className="text-muted-foreground mt-1">Manage public governance documents and user
                            agreements.</p>
                    </div>
                </div>
            </div>

            <Tabs defaultValue="privacy" className="w-full">
                <TabsList className="grid w-full grid-cols-2 lg:w-[400px] h-12 bg-muted/50 p-1">
                    <TabsTrigger value="privacy"
                                 className="data-[state=active]:bg-background data-[state=active]:shadow-sm">Privacy
                        Policy</TabsTrigger>
                    <TabsTrigger value="terms"
                                 className="data-[state=active]:bg-background data-[state=active]:shadow-sm">Terms of
                        Use</TabsTrigger>
                </TabsList>

                <TabsContent value="privacy" className="mt-8 animate-in fade-in-50 duration-500">
                    <div className="grid gap-6 lg:grid-cols-3">
                        <Card
                            className="lg:col-span-2 border-none shadow-lg overflow-hidden bg-card/60 backdrop-blur-md">
                            <CardHeader className="bg-muted/30 pb-6 border-b">
                                <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                        <FileText className="h-5 w-5 text-primary"/>
                                        <CardTitle>Edit Privacy Policy</CardTitle>
                                    </div>
                                    <Badge variant="outline" className="font-mono text-[10px]">HTML Content</Badge>
                                </div>
                            </CardHeader>
                            <CardContent className="pt-6 space-y-6">
                                <div className="space-y-4">
                                    <p className="text-sm text-muted-foreground leading-relaxed">
                                        Define how {selectedBuilding?.name || 'your building'} handles resident PII (Personally Identifiable
                                        Information).
                                        Formatting is handled via the Tiptap editor below.
                                    </p>
                                    <RichTextEditor
                                        content={privacyContent}
                                        onChange={setPrivacyContent}
                                    />
                                </div>
                                <div className="flex flex-wrap gap-3 pt-4">
                                    <Button
                                        onClick={savePrivacyPolicy}
                                        disabled={saving}
                                        className="min-w-[140px] shadow-sm"
                                        data-testid="save-privacy-btn"
                                    >
                                        {saving ? (
                                            <>
                                                <div
                                                    className="animate-spin mr-2 h-4 w-4 border-b-2 border-white rounded-full"></div>
                                                Saving...</>
                                        ) : (
                                            <><Save className="mr-2 h-4 w-4"/>Save Privacy Policy</>
                                        )}
                                    </Button>
                                    <Button
                                        variant="outline"
                                        onClick={() => window.open('/privacy', '_blank')}
                                        className="shadow-sm bg-background/50 hover:bg-muted"
                                    >
                                        <Eye className="mr-2 h-4 w-4"/>
                                        Live Preview
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>

                        <div className="space-y-6">
                            <Card className="border-blue-200/50 bg-blue-50/50 dark:bg-blue-950/20 shadow-sm">
                                <CardHeader className="pb-3">
                                    <CardTitle
                                        className="text-sm font-semibold flex items-center gap-2 text-blue-700 dark:text-blue-400">
                                        <AlertCircle className="h-4 w-4"/>
                                        Resident Impact
                                    </CardTitle>
                                </CardHeader>
                                <CardContent>
                                    <p className="text-xs text-blue-600/80 dark:text-blue-300/80 leading-relaxed">
                                        Updates to the Privacy Policy are visible on the public registration page.
                                        Residents are required to accept these terms before creating an account.
                                    </p>
                                </CardContent>
                            </Card>

                            <Card className="border-none shadow-sm bg-muted/30">
                                <CardHeader>
                                    <CardTitle className="text-sm font-semibold">Editing Tips</CardTitle>
                                </CardHeader>
                                <CardContent className="text-xs text-muted-foreground space-y-3">
                                    <div className="flex gap-2">
                                        <CheckCircle2 className="h-3 w-3 text-green-500 mt-0.5"/>
                                        <span>Use <strong>Heading 1</strong> for section titles.</span>
                                    </div>
                                    <div className="flex gap-2">
                                        <CheckCircle2 className="h-3 w-3 text-green-500 mt-0.5"/>
                                        <span>Bullet points are best for lists of data types collected.</span>
                                    </div>
                                </CardContent>
                            </Card>
                        </div>
                    </div>
                </TabsContent>

                <TabsContent value="terms" className="mt-8 animate-in fade-in-50 duration-500">
                    <div className="grid gap-6 lg:grid-cols-3">
                        <Card
                            className="lg:col-span-2 border-none shadow-lg overflow-hidden bg-card/60 backdrop-blur-md">
                            <CardHeader className="bg-muted/30 pb-6 border-b">
                                <div className="flex items-center justify-between">
                                    <div className="flex items-center gap-2">
                                        <FileText className="h-5 w-5 text-primary"/>
                                        <CardTitle>Edit Terms of Use</CardTitle>
                                    </div>
                                    <Badge variant="outline" className="font-mono text-[10px]">HTML Content</Badge>
                                </div>
                            </CardHeader>
                            <CardContent className="pt-6 space-y-6">
                                <div className="space-y-4">
                                    <p className="text-sm text-muted-foreground leading-relaxed">
                                        Define the rules, regulations, and expected behavior for residents using the
                                        {selectedBuilding?.name || 'building'} digital platform.
                                    </p>
                                    <RichTextEditor
                                        content={termsContent}
                                        onChange={setTermsContent}
                                    />
                                </div>
                                <div className="flex flex-wrap gap-3 pt-4">
                                    <Button
                                        onClick={saveTermsOfUse}
                                        disabled={saving}
                                        className="min-w-[140px] shadow-sm"
                                        data-testid="save-terms-btn"
                                    >
                                        {saving ? (
                                            <>
                                                <div
                                                    className="animate-spin mr-2 h-4 w-4 border-b-2 border-white rounded-full"></div>
                                                Saving...</>
                                        ) : (
                                            <><Save className="mr-2 h-4 w-4"/>Save Terms of Use</>
                                        )}
                                    </Button>
                                    <Button
                                        variant="outline"
                                        onClick={() => window.open('/terms', '_blank')}
                                        className="shadow-sm bg-background/50 hover:bg-muted"
                                    >
                                        <Eye className="mr-2 h-4 w-4"/>
                                        Live Preview
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>

                        <div className="space-y-6">
                            <Card className="border-amber-200/50 bg-amber-50/50 dark:bg-amber-950/20 shadow-sm">
                                <CardHeader className="pb-3">
                                    <CardTitle
                                        className="text-sm font-semibold flex items-center gap-2 text-amber-700 dark:text-amber-400">
                                        <AlertCircle className="h-4 w-4"/>
                                        Compliance Note
                                    </CardTitle>
                                </CardHeader>
                                <CardContent>
                                    <p className="text-xs text-amber-600/80 dark:text-amber-300/80 leading-relaxed">
                                        New tenants and guests must explicitly acknowledge these terms and the Building
                                        By-Laws before their account can be approved.
                                    </p>
                                </CardContent>
                            </Card>
                        </div>
                    </div>
                </TabsContent>
            </Tabs>
        </div>
    );
};
// Simple Badge component for local use if not available in project
/**
 * @generated FunctionHeader
 * Function: Badge
 * Path: frontend/src/pages/dashboard/AdminConsolePage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const Badge = ({children, variant = 'default', className = ''}) => {
    const styles = variant === 'outline'
        ? 'border border-border text-muted-foreground'
        : 'bg-primary text-primary-foreground';
    return (
        <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${styles} ${className}`}>
      {children}
    </span>
    );
};

export default AdminConsolePage;
