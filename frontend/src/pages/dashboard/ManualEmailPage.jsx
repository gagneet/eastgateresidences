import React, { useCallback, useEffect, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Badge } from '../../components/ui/badge';
import { AlertCircle, Building2, Loader2, Send, Users, X } from 'lucide-react';
import { toast } from 'sonner';
import RichTextEditor from '../../components/shared/RichTextEditor';
import { cn } from '../../lib/utils';
/**
 * @generated FunctionHeader
 * Function: ManualEmailPage
 * Path: frontend/src/pages/dashboard/ManualEmailPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ManualEmailPage = () => {
    const {api, user} = useAuth();
    const [loading, setLoading] = useState(true);
    const [sending, setSending] = useState(false);
    const [recipients, setRecipients] = useState({groups: [], units: []});

    const [formData, setFormData] = useState({
        target_roles: [],
        target_units: [],
        subject: '',
        content: '',
        priority: 'normal'
    });

    const fetchData = useCallback(async () => {
        try {
            const response = await api.get('/communication/recipients');
            setRecipients(response.data);
        } catch (error) {
            console.error('Failed to fetch recipients:', error);
            toast.error('Failed to load recipient list');
        } finally {
            setLoading(false);
        }
    }, [api]);

    useEffect(() => {
        fetchData();
    }, [fetchData]);
    /**
     * @generated FunctionHeader
     * Function: toggleTarget
     * Path: frontend/src/pages/dashboard/ManualEmailPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleTarget = (type, id) => {
        const field = type === 'group' ? 'target_roles' : 'target_units';
        setFormData(prev => {
            const current = prev[ field ];
            if (current.includes(id)) {
                return {...prev, [ field ]: current.filter(item => item !== id)};
            } else {
                return {...prev, [ field ]: [...current, id]};
            }
        });
    };
    /**
     * @generated FunctionHeader
     * Function: handleSend
     * Path: frontend/src/pages/dashboard/ManualEmailPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSend = async () => {
        if (!formData.subject || !formData.content) {
            toast.error('Please provide both subject and content');
            return;
        }

        if (formData.target_roles.length === 0 && formData.target_units.length === 0) {
            toast.error('Please select at least one recipient group or unit');
            return;
        }

        setSending(true);
        try {
            const response = await api.post('/communication/send-manual-email', formData);
            if (response.data.success) {
                toast.success(`Email sent successfully to ${response.data.sent_count} recipients`);
                setFormData({
                    target_roles: [],
                    target_units: [],
                    subject: '',
                    content: '',
                    priority: 'normal'
                });
            }
        } catch (error) {
            console.error('Failed to send email:', error);
            toast.error(error.response?.data?.detail || 'Failed to send manual email');
        } finally {
            setSending(false);
        }
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center min-h-[400px]">
                <Loader2 className="h-8 w-8 animate-spin text-primary"/>
            </div>
        );
    }

    return (
        <div className="container max-w-5xl py-8 space-y-8" data-testid="manual-email-page">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 border-b pb-6">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Manual Communication</h1>
                    <p className="text-muted-foreground mt-1">Send direct emails to residents and groups using the
                        community SMTP.</p>
                </div>
                <Button
                    onClick={handleSend}
                    disabled={sending || !formData.subject || ( !formData.target_roles.length && !formData.target_units.length )}
                    className="min-w-[140px] shadow-md"
                >
                    {sending ? (
                        <><Loader2 className="mr-2 h-4 w-4 animate-spin"/>Sending...</>
                    ) : (
                        <><Send className="mr-2 h-4 w-4"/>Send Email</>
                    )}
                </Button>
            </div>

            <div className="grid gap-8 lg:grid-cols-12">
                {/* Recipient Selection */}
                <div className="lg:col-span-4 space-y-6">
                    <Card className="border-none shadow-lg bg-card/60 backdrop-blur-sm">
                        <CardHeader className="pb-4">
                            <div className="flex items-center gap-2">
                                <Users className="h-5 w-5 text-primary"/>
                                <CardTitle className="text-lg">Recipients</CardTitle>
                            </div>
                            <CardDescription>Select groups or individual units.</CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-6">
                            {/* Groups */}
                            <div className="space-y-3">
                                <Label className="text-xs font-bold uppercase tracking-wider text-muted-foreground">User
                                    Groups</Label>
                                <div className="flex flex-wrap gap-2">
                                    {recipients.groups.map(group => (
                                        <Button
                                            key={group.id}
                                            variant={formData.target_roles.includes(group.id) ? 'default' : 'outline'}
                                            size="sm"
                                            className="rounded-full text-xs"
                                            onClick={() => toggleTarget('group', group.id)}
                                        >
                                            {group.name}
                                        </Button>
                                    ))}
                                </div>
                            </div>

                            {/* Units */}
                            <div className="space-y-3 pt-4 border-t">
                                <div className="flex items-center justify-between">
                                    <Label
                                        className="text-xs font-bold uppercase tracking-wider text-muted-foreground flex items-center gap-1.5">
                                        <Building2 className="h-3 w-3"/> Individual Units
                                    </Label>
                                    <span
                                        className="text-[10px] text-muted-foreground">{formData.target_units.length} selected</span>
                                </div>
                                <div
                                    className="grid grid-cols-3 gap-2 max-h-[300px] overflow-y-auto pr-2 custom-scrollbar">
                                    {recipients.units.map(unit => (
                                        <Button
                                            key={unit.id}
                                            variant={formData.target_units.includes(unit.id) ? 'default' : 'outline'}
                                            size="sm"
                                            className={cn(
                                                "h-8 text-[10px] font-mono",
                                                formData.target_units.includes(unit.id) ? "bg-primary border-primary" : "hover:border-primary/50"
                                            )}
                                            onClick={() => toggleTarget('unit', unit.id)}
                                        >
                                            {unit.id}
                                        </Button>
                                    ))}
                                </div>
                            </div>

                            {formData.target_roles.length > 0 || formData.target_units.length > 0 ? (
                                <div className="pt-4 border-t flex flex-wrap gap-1.5">
                                    <p className="text-[10px] text-muted-foreground w-full mb-1">Targeting Summary:</p>
                                    {formData.target_roles.map(r => (
                                        <Badge key={r} variant="secondary" className="text-[9px] gap-1">
                                            {recipients.groups.find(g => g.id === r)?.name}
                                            <X className="h-2 w-2 cursor-pointer"
                                               onClick={() => toggleTarget('group', r)}/>
                                        </Badge>
                                    ))}
                                    {formData.target_units.length > 0 && (
                                        <Badge variant="outline" className="text-[9px]">
                                            {formData.target_units.length} Units Selected
                                        </Badge>
                                    )}
                                </div>
                            ) : (
                                <div
                                    className="p-3 rounded-lg border border-dashed border-muted-foreground/30 text-center">
                                    <p className="text-[10px] text-muted-foreground italic">No recipients selected</p>
                                </div>
                            )}
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-sm bg-blue-50/50 dark:bg-blue-950/20">
                        <CardContent className="pt-6">
                            <div className="flex gap-3">
                                <AlertCircle className="h-5 w-5 text-blue-500 shrink-0 mt-0.5"/>
                                <div className="space-y-2">
                                    <p className="text-xs font-semibold text-blue-700 dark:text-blue-300">Privacy &
                                        PII</p>
                                    <p className="text-[10px] leading-relaxed text-blue-600/80 dark:text-blue-300/70">
                                        Resident names are hidden in this interface to protect PII. Use unit numbers for
                                        precise targeting.
                                    </p>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </div>

                {/* Composer */}
                <div className="lg:col-span-8 space-y-6">
                    <Card className="border-none shadow-lg bg-card/60 backdrop-blur-sm">
                        <CardHeader className="pb-4 border-b">
                            <CardTitle className="text-lg">Message Composer</CardTitle>
                            <CardDescription>Draft your message using the rich text editor.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-6">
                            <div className="space-y-2">
                                <Label htmlFor="subject">Subject Line</Label>
                                <Input
                                    id="subject"
                                    value={formData.subject}
                                    onChange={(e) => setFormData(prev => ( {...prev, subject: e.target.value} ))}
                                    placeholder="e.g., Update regarding upcoming maintenance"
                                    className="font-medium"
                                />
                            </div>

                            <div className="space-y-2">
                                <Label>Content (HTML)</Label>
                                <RichTextEditor
                                    content={formData.content}
                                    onChange={(val) => setFormData(prev => ( {...prev, content: val} ))}
                                    placeholder="Draft your community email here..."
                                />
                            </div>

                            <div className="flex items-center gap-4 pt-4 border-t">
                                <div className="flex items-center gap-2">
                                    <Label htmlFor="priority" className="text-sm">Priority:</Label>
                                    <div className="flex gap-1">
                                        {['low', 'normal', 'high', 'urgent'].map(p => (
                                            <Button
                                                key={p}
                                                variant={formData.priority === p ? 'default' : 'outline'}
                                                size="sm"
                                                className="h-7 text-[10px] capitalize"
                                                onClick={() => setFormData(prev => ( {...prev, priority: p} ))}
                                            >
                                                {p}
                                            </Button>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </div>
            </div>
        </div>
    );
};

export default ManualEmailPage;
