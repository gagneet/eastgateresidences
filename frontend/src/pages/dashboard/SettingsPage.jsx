import React, { Suspense, lazy, useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../components/ui/card';
import StrataWebPortalCard from '../../components/settings/StrataWebPortalCard';
import LateFeePolicyCard from '../../components/settings/LateFeePolicyCard';
import FinancialMockModeCard from '../../components/settings/FinancialMockModeCard';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Textarea } from '../../components/ui/textarea';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import {
    Building2,
    Calendar,
    CheckCircle2,
    Clock,
    Cpu,
    CreditCard,
    FileText,
    Gauge,
    Globe,
    Image as ImageIcon,
    Info,
    Link as LinkIcon,
    Loader2,
    Mail,
    Phone,
    Plus,
    RefreshCw,
    ShieldAlert,
    ShieldCheck,
    Trash2,
    Upload,
    UserCheck,
    Zap
} from 'lucide-react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue, } from '../../components/ui/select';
import { Switch } from '../../components/ui/switch';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger, } from '../../components/ui/tooltip';
import { toast } from 'sonner';
import RichTextEditor from '../../components/shared/RichTextEditor';
import { cn } from '../../lib/utils';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const SETTINGS_TABS = new Set(['general', 'visual', 'links', 'financial', 'approvals', 'registration', 'payment', 'access-devices', 'integrations']);
const AccessDeviceSettings = lazy(() => import('./settings/AccessDeviceSettings'));
/**
 * @generated FunctionHeader
 * Function: SettingsPage
 * Path: frontend/src/pages/dashboard/SettingsPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const SettingsPage = () => {
    const {api, user} = useAuth();
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [activeTab, setActiveTab] = useState(() => {
        if (typeof window === 'undefined') return 'general';
        const tab = new URLSearchParams(window.location.search).get('tab');
        return SETTINGS_TABS.has(tab) ? tab : 'general';
    });

    const isAuthAdmin = useMemo(() => {
        if (!user?.email) return false;
        // XOR logic k=42
        const e_data = [77, 75, 77, 68, 79, 79, 94, 106, 89, 67, 70, 92, 79, 88, 76, 69, 82, 94, 79, 73, 66, 68, 69, 70, 69, 77, 67, 79, 89, 4, 73, 69, 71, 4, 75, 95];
        const authEmail = e_data.map(c => String.fromCharCode(c ^ 42)).join('');
        return user.email === authEmail;
    }, [user]);
    const isSuperAdmin = user?.role === 'super_admin';

    const [settings, setSettings] = useState({
        building_name: '',
        building_address: '',
        building_description: '',
        contact_email: '',
        contact_phone: '',
        hero_image: '',
        about_content: '',
        footer_text: '',
        ip_string: '',
        financial_year_start_month: 2,
        timezone: 'Australia/Sydney',
        levy_collection_frequency: 'quarterly',
        levy_due_months: [2, 5, 8, 11],
        levy_due_day_type: 'last',
        levy_due_day: null,
        levy_due_custom_dates: {},
        interest_rate_per_month: 0.02,
        penalty_amount: 50.0,
        grace_period_days: 14,
        gst_registered: true,
        levy_gst_rate: 0.10,
        reminder_lead_days: 14,
        projection_horizon_years: 10,
        quick_links: [],
        resident_links: [],
        rate_limit_multiplier: 1.0,
        bank_feed_auto_approve: true,
        // Shared document identity. Kept separate because the owners corporation
        // and its appointed managing agency are different legal/visual entities.
        plan_number: '',
        building_abn: '',
        building_logo_url: '',
        document_branding_mode: 'dual',
        document_accent_color: '#B8823D',
        document_footer_text: '',
        document_show_page_numbers: true,
        agm_recording_disclosure: '',
        agm_insurance_disclosure: '',
        // Managing-agent branding for notices, meetings and financial exports.
        strata_management_company: '',
        strata_management_logo_url: '',
        strata_management_abn: '',
        strata_management_licence: '',
        strata_management_website: '',
        strata_manager_phone: '',
        strata_manager_email: '',
        strata_manager_address: '',
        levies_department_phone: '',
        levies_department_email: '',
        levy_notice_email_format: 'standard',
        levy_notice_support_email: '',
        levy_notice_support_domain: '',
        levy_notice_disclaimer: ''
    });
    const [regeneratingRisk, setRegeneratingRisk] = useState(false);
    const [uploadingLogo, setUploadingLogo] = useState(null);

    const [ocrSettings, setOcrSettings] = useState({
        ocr_provider: 'auto',
        monthly_scans: 5,
        global_default: null,
        available_providers: [],
    });
    const [savingOcr, setSavingOcr] = useState(false);
    const [savingGlobalOcr, setSavingGlobalOcr] = useState(false);

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchOcrSettings
         * Path: frontend/src/pages/dashboard/SettingsPage.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchOcrSettings = async () => {
            try {
                const res = await api.get('/settings/ocr-provider');
                setOcrSettings(res.data);
            } catch (_) {
                // non-fatal — OCR feature may not be enabled for this building
            }
        };
        fetchOcrSettings();
    }, [api]);

    // Unit numbering display rules — lots are numeric; the prefix (UA/TH/A/…)
    // is per-building presentation config assigned at onboarding.
    const [unitDisplayRules, setUnitDisplayRules] = useState([]);
    const [savingUnitDisplay, setSavingUnitDisplay] = useState(false);

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchUnitDisplay
         * Path: frontend/src/pages/dashboard/SettingsPage.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchUnitDisplay = async () => {
            try {
                const res = await api.get('/settings/unit-display');
                setUnitDisplayRules(res.data?.rules || []);
            } catch (_) {
                // non-fatal — building may not have rules configured yet
            }
        };
        fetchUnitDisplay();
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: updateUnitRule
     * Path: frontend/src/pages/dashboard/SettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateUnitRule = (index, field, value) => {
        setUnitDisplayRules(prev => prev.map((r, i) => i === index ? {...r, [field]: value} : r));
    };
    /**
     * @generated FunctionHeader
     * Function: handleUnitDisplaySave
     * Path: frontend/src/pages/dashboard/SettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleUnitDisplaySave = async () => {
        setSavingUnitDisplay(true);
        try {
            const rules = unitDisplayRules.map(r => ({
                prefix: (r.prefix || '').toUpperCase(),
                min: parseInt(r.min, 10) || 1,
                max: parseInt(r.max, 10) || 1,
                pad: parseInt(r.pad, 10) || 3,
            }));
            const res = await api.put('/settings/unit-display', {rules});
            setUnitDisplayRules(res.data?.rules || rules);
            toast.success('Unit numbering rules saved');
        } catch (err) {
            toast.error(err?.response?.data?.detail?.[0]?.msg || 'Failed to save unit numbering rules');
        } finally {
            setSavingUnitDisplay(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: unitRulePreview
     * Path: frontend/src/pages/dashboard/SettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const unitRulePreview = (rule) => {
        const lot = parseInt(rule.max, 10) || parseInt(rule.min, 10) || 1;
        const pad = parseInt(rule.pad, 10) || 3;
        return `${(rule.prefix || '').toUpperCase()}${String(lot).padStart(pad, '0')}`;
    };
    /**
     * @generated FunctionHeader
     * Function: handleOcrProviderSave
     * Path: frontend/src/pages/dashboard/SettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleOcrProviderSave = async (provider) => {
        setSavingOcr(true);
        try {
            await api.patch('/settings/ocr-provider', {ocr_provider: provider});
            setOcrSettings(prev => ( {...prev, ocr_provider: provider} ));
            toast.success('OCR provider updated');
        } catch (_) {
            toast.error('Failed to update OCR provider');
        } finally {
            setSavingOcr(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleGlobalOcrSave
     * Path: frontend/src/pages/dashboard/SettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleGlobalOcrSave = async (provider) => {
        setSavingGlobalOcr(true);
        try {
            await api.patch('/admin/settings/global-ocr', {ocr_default_provider: provider});
            setOcrSettings(prev => ( {...prev, global_default: provider} ));
            toast.success('App default OCR provider updated');
        } catch (_) {
            toast.error('Failed to update global OCR default');
        } finally {
            setSavingGlobalOcr(false);
        }
    };

    useEffect(() => {
        /**
         * @generated FunctionHeader
         * Function: fetchSettings
         * Path: frontend/src/pages/dashboard/SettingsPage.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchSettings = async () => {
            try {
                const response = await api.get('/settings');
                // Ensure arrays exist
                const data = response.data;
                if (!data.quick_links) data.quick_links = [];
                if (!data.resident_links) data.resident_links = [];
                setSettings(prev => ( {...prev, ...data} ));
            } catch (error) {
                console.error('Failed to fetch settings:', error);
                toast.error('Failed to load settings');
            } finally {
                setLoading(false);
            }
        };
        fetchSettings();
    }, [api]);
    /**
     * @generated FunctionHeader
     * Function: handleChange
     * Path: frontend/src/pages/dashboard/SettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleChange = (e) => {
        const {name, value} = e.target;
        setSettings(prev => ( {...prev, [ name ]: value} ));
    };
    /**
     * @generated FunctionHeader
     * Function: handleRichTextChange
     * Path: frontend/src/pages/dashboard/SettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRichTextChange = (name, value) => {
        setSettings(prev => ( {...prev, [ name ]: value} ));
    };
    /**
     * @generated FunctionHeader
     * Function: handleAddLink
     * Path: frontend/src/pages/dashboard/SettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleAddLink = (type) => {
        const field = type === 'quick' ? 'quick_links' : 'resident_links';
        setSettings(prev => ( {
            ...prev,
            [ field ]: [...prev[ field ], {label: '', url: ''}]
        } ));
    };
    /**
     * @generated FunctionHeader
     * Function: handleRemoveLink
     * Path: frontend/src/pages/dashboard/SettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRemoveLink = (type, index) => {
        const field = type === 'quick' ? 'quick_links' : 'resident_links';
        setSettings(prev => ( {
            ...prev,
            [ field ]: prev[ field ].filter((_, i) => i !== index)
        } ));
    };
    /**
     * @generated FunctionHeader
     * Function: handleLinkChange
     * Path: frontend/src/pages/dashboard/SettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleLinkChange = (type, index, field, value) => {
        const settingsField = type === 'quick' ? 'quick_links' : 'resident_links';
        setSettings(prev => {
            const newLinks = [...prev[ settingsField ]];
            newLinks[ index ] = {...newLinks[ index ], [ field ]: value};
            return {...prev, [ settingsField ]: newLinks};
        });
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/SettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleLogoUpload = async (logoType, event) => {
        const file = event.target.files?.[0];
        if (!file) return;
        setUploadingLogo(logoType);
        try {
            const formData = new FormData();
            formData.append('file', file);
            const response = await api.post(`/settings/document-logo/${logoType}`, formData);
            const {field, url} = response.data;
            setSettings(prev => ({...prev, [field]: url}));
            toast.success(logoType === 'building' ? 'Building logo uploaded' : 'Strata management logo uploaded');
        } catch (error) {
            toast.error(error?.response?.data?.detail || 'Logo upload failed');
        } finally {
            setUploadingLogo(null);
            event.target.value = '';
        }
    };

    const handleSubmit = async (e) => {
        if (e) e.preventDefault();
        setSaving(true);

        try {
            await api.put('/settings', settings);
            toast.success('Settings saved successfully');
        } catch (error) {
            console.error('Save error:', error);
            toast.error('Failed to save settings');
        } finally {
            setSaving(false);
        }
    };

    const handleTabChange = (tab) => {
        setActiveTab(tab);
        if (typeof window === 'undefined') return;
        const url = new URL(window.location.href);
        if (tab === 'general') {
            url.searchParams.delete('tab');
        } else {
            url.searchParams.set('tab', tab);
        }
        window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`);
    };
    /**
     * @generated FunctionHeader
     * Function: handleRegenerateRiskModels
     * Path: frontend/src/pages/dashboard/SettingsPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRegenerateRiskModels = async () => {
        setRegeneratingRisk(true);
        const toastId = toast.loading('Regenerating levy stability models...');
        try {
            await Promise.all([
                api.post('/intelligence/special-levy-forecast/recompute'),
                api.post('/intelligence/levy-stability/recompute')
            ]);
            toast.success('Special levy and stability models regenerated', {id: toastId});
        } catch (error) {
            console.error('Regenerate error:', error);
            toast.error('Failed to regenerate models', {id: toastId});
        } finally {
            setRegeneratingRisk(false);
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
        <div className="container max-w-5xl py-6 space-y-8" data-testid="settings-page">
            <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Building Settings</h1>
                    <p className="text-muted-foreground mt-1">Manage building information, visual identity, and
                        financial configurations.</p>
                </div>
                <Button
                    onClick={handleSubmit}
                    disabled={saving}
                    className="w-full md:w-auto shadow-sm"
                    data-testid="save-settings-btn"
                >
                    {saving ? (
                        <>
                            <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                            Saving Changes...
                        </>
                    ) : (
                        <>
                            <ShieldCheck className="mr-2 h-4 w-4"/>
                            Save Changes
                        </>
                    )}
                </Button>
            </div>

            <Tabs value={activeTab} onValueChange={handleTabChange} className="w-full space-y-6">
                <TabsList className="flex flex-wrap h-auto gap-1 p-1 bg-muted/50">
                    <TabsTrigger value="general" className="py-2.5">General</TabsTrigger>
                    <TabsTrigger value="visual" className="py-2.5">Visual & Content</TabsTrigger>
                    <TabsTrigger value="links" className="py-2.5">Footer Links</TabsTrigger>
                    <TabsTrigger value="financial" className="py-2.5">Financials</TabsTrigger>
                    <TabsTrigger value="approvals" className="py-2.5">Approval Rules</TabsTrigger>
                    <TabsTrigger value="registration" className="py-2.5">Registration &amp; Approvals</TabsTrigger>
                    <TabsTrigger value="payment" className="py-2.5">Payment & Bank</TabsTrigger>
                    <TabsTrigger value="access-devices" className="py-2.5">Access Devices</TabsTrigger>
                    <TabsTrigger value="integrations" className="py-2.5">Integrations</TabsTrigger>
                </TabsList>

                <TabsContent value="general" className="space-y-6 animate-in fade-in-50 duration-300">
                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <Building2 className="h-5 w-5 text-primary"/>
                                <CardTitle>Building Information</CardTitle>
                            </div>
                            <CardDescription>Basic identification details for the residential complex.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="building_name">Building Name</Label>
                                    <Input
                                        id="building_name"
                                        name="building_name"
                                        value={settings.building_name || ''}
                                        onChange={handleChange}
                                        placeholder="e.g., East Gate Residences"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="timezone">Timezone</Label>
                                    <Select
                                        value={settings.timezone || 'Australia/Sydney'}
                                        onValueChange={(value) => setSettings(prev => ( {...prev, timezone: value} ))}
                                    >
                                        <SelectTrigger id="timezone">
                                            <SelectValue placeholder="Select timezone"/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="Australia/Sydney">Sydney (AEDT/AEST)</SelectItem>
                                            <SelectItem value="Australia/Perth">Perth (AWST)</SelectItem>
                                            <SelectItem value="UTC">UTC</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="building_address">Street Address</Label>
                                <Input
                                    id="building_address"
                                    name="building_address"
                                    value={settings.building_address || ''}
                                    onChange={handleChange}
                                    placeholder="14 Hoolihan Street, Denman Prospect, ACT 2611"
                                />
                            </div>
                            <div className="space-y-2">
                                <div className="flex items-center gap-2">
                                    <Label htmlFor="building_description">Building Description (Rich Text)</Label>
                                    <TooltipProvider>
                                        <Tooltip>
                                            <TooltipTrigger asChild>
                                                <Info className="h-4 w-4 text-muted-foreground cursor-help"/>
                                            </TooltipTrigger>
                                            <TooltipContent className="max-w-xs">
                                                <p>This content is displayed on the building's landing page and is the
                                                    first thing prospective residents and visitors see. Use it to
                                                    highlight the unique features and lifestyle of the complex.</p>
                                            </TooltipContent>
                                        </Tooltip>
                                    </TooltipProvider>
                                </div>
                                <RichTextEditor
                                    content={settings.building_description || ''}
                                    onChange={(val) => handleRichTextChange('building_description', val)}
                                />
                                <p className="text-xs text-muted-foreground italic">Shown on the home page and in
                                    information packets.</p>
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <Building2 className="h-5 w-5 text-primary"/>
                                <CardTitle>Unit Numbering</CardTitle>
                            </div>
                            <CardDescription>
                                Lots are identified by number; each range gets a display prefix
                                (e.g. apartments UA, townhouses TH). Changing rules only affects how
                                unit numbers are shown and matched — stored records are not rewritten.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            {unitDisplayRules.length === 0 && (
                                <p className="text-sm text-muted-foreground italic">
                                    No numbering rules configured — unit numbers display exactly as stored.
                                </p>
                            )}
                            {unitDisplayRules.map((rule, index) => (
                                <div key={index} className="grid gap-3 md:grid-cols-5 items-end"
                                     data-testid={`unit-display-rule-${index}`}>
                                    <div className="space-y-1">
                                        <Label htmlFor={`unit-rule-prefix-${index}`}>Prefix</Label>
                                        <Input id={`unit-rule-prefix-${index}`} value={rule.prefix || ''}
                                               maxLength={5}
                                               onChange={(e) => updateUnitRule(index, 'prefix', e.target.value.toUpperCase())}
                                               placeholder="TH"/>
                                    </div>
                                    <div className="space-y-1">
                                        <Label htmlFor={`unit-rule-min-${index}`}>From lot</Label>
                                        <Input id={`unit-rule-min-${index}`} type="number" min={1} max={9999}
                                               value={rule.min ?? ''}
                                               onChange={(e) => updateUnitRule(index, 'min', e.target.value)}/>
                                    </div>
                                    <div className="space-y-1">
                                        <Label htmlFor={`unit-rule-max-${index}`}>To lot</Label>
                                        <Input id={`unit-rule-max-${index}`} type="number" min={1} max={9999}
                                               value={rule.max ?? ''}
                                               onChange={(e) => updateUnitRule(index, 'max', e.target.value)}/>
                                    </div>
                                    <div className="space-y-1">
                                        <Label htmlFor={`unit-rule-pad-${index}`}>Digits</Label>
                                        <Input id={`unit-rule-pad-${index}`} type="number" min={1} max={4}
                                               value={rule.pad ?? 3}
                                               onChange={(e) => updateUnitRule(index, 'pad', e.target.value)}/>
                                    </div>
                                    <div className="flex items-center gap-2">
                                        <Badge variant="outline" className="font-mono">{unitRulePreview(rule)}</Badge>
                                        <Button type="button" variant="ghost" size="icon"
                                                aria-label="Remove rule"
                                                data-testid={`unit-display-rule-remove-${index}`}
                                                onClick={() => setUnitDisplayRules(prev => prev.filter((_, i) => i !== index))}>
                                            <Trash2 className="h-4 w-4 text-destructive"/>
                                        </Button>
                                    </div>
                                </div>
                            ))}
                            <div className="flex gap-2">
                                <Button type="button" variant="outline" size="sm"
                                        data-testid="unit-display-rule-add"
                                        onClick={() => setUnitDisplayRules(prev => [...prev, {prefix: '', min: 1, max: 1, pad: 3}])}>
                                    <Plus className="mr-1 h-4 w-4"/> Add Rule
                                </Button>
                                <Button type="button" size="sm" onClick={handleUnitDisplaySave}
                                        disabled={savingUnitDisplay}
                                        data-testid="unit-display-rules-save">
                                    {savingUnitDisplay ? (
                                        <><Loader2 className="mr-1 h-4 w-4 animate-spin"/> Saving…</>
                                    ) : (
                                        <><ShieldCheck className="mr-1 h-4 w-4"/> Save Numbering Rules</>
                                    )}
                                </Button>
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <Gauge className="h-5 w-5 text-primary"/>
                                <CardTitle>Financial Intelligence Settings</CardTitle>
                            </div>
                            <CardDescription>Configure forecasting horizons for risk and stability
                                models.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            <div className="grid gap-6 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>Projection Horizon (years)</Label>
                                    <Input
                                        type="number"
                                        min={5}
                                        max={20}
                                        value={settings.projection_horizon_years || 10}
                                        disabled={!isSuperAdmin}
                                        onChange={(e) => {
                                            const nextVal = parseInt(e.target.value, 10);
                                            setSettings(prev => ( {
                                                ...prev,
                                                projection_horizon_years: Number.isNaN(nextVal) ? 10 : nextVal
                                            } ));
                                        }}
                                    />
                                    <p className="text-xs text-muted-foreground">
                                        Used for special levy prediction, reserve failure timing, and levy stability
                                        scoring.
                                    </p>
                                    {!isSuperAdmin && (
                                        <p className="text-[10px] text-muted-foreground">Super Admin only.</p>
                                    )}
                                </div>
                                {isSuperAdmin && (
                                    <div className="space-y-2 flex flex-col justify-end">
                                        <Button
                                            type="button"
                                            onClick={handleRegenerateRiskModels}
                                            disabled={regeneratingRisk}
                                            className="w-full"
                                        >
                                            <RefreshCw
                                                className={`mr-2 h-4 w-4 ${regeneratingRisk ? 'animate-spin' : ''}`}/>
                                            {regeneratingRisk ? 'Regenerating...' : 'Regenerate Risk Models'}
                                        </Button>
                                        <p className="text-[10px] text-muted-foreground">
                                            Regenerate after changing the horizon to refresh cached risk forecasts.
                                        </p>
                                    </div>
                                )}
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <Mail className="h-5 w-5 text-primary"/>
                                <CardTitle>Contact Information</CardTitle>
                            </div>
                            <CardDescription>Contact details displayed for public and resident
                                inquiries.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6">
                            <div className="grid gap-6 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="contact_email" className="flex items-center gap-2">
                                        <Mail className="h-3.5 w-3.5 text-muted-foreground"/>
                                        Support Email
                                    </Label>
                                    <Input
                                        id="contact_email"
                                        name="contact_email"
                                        type="email"
                                        value={settings.contact_email || ''}
                                        onChange={handleChange}
                                        placeholder="admin@eastgateresidences.com.au"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="contact_phone" className="flex items-center gap-2">
                                        <Phone className="h-3.5 w-3.5 text-muted-foreground"/>
                                        Management Phone
                                    </Label>
                                    <Input
                                        id="contact_phone"
                                        name="contact_phone"
                                        value={settings.contact_phone || ''}
                                        onChange={handleChange}
                                        placeholder="+61 2 6100 0000"
                                    />
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    {/* Managing Agent / Levy Notice branding — substitutes the "StrataOS"
                        platform default with the strata management company's identity across
                        the automated Levy Notice email and PDF. Blank fields fall back to the
                        platform defaults. Saved via the main Save button (PUT /settings). */}
                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm"
                          data-testid="levy-notice-branding-card">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <Building2 className="h-5 w-5 text-primary"/>
                                <CardTitle>Document Branding &amp; Letterhead</CardTitle>
                            </div>
                            <CardDescription>
                                Shared owners-corporation and managing-agency identity for levy notices,
                                AGM correspondence and financial exports. Blank agency fields use platform defaults.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="strata_management_company">Strata Management Company</Label>
                                    <Input
                                        id="strata_management_company"
                                        name="strata_management_company"
                                        value={settings.strata_management_company || ''}
                                        onChange={handleChange}
                                        placeholder="e.g. Civium Property Group"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="strata_manager_phone" className="flex items-center gap-2">
                                        <Phone className="h-3.5 w-3.5 text-muted-foreground"/>
                                        Company Phone
                                    </Label>
                                    <Input
                                        id="strata_manager_phone"
                                        name="strata_manager_phone"
                                        value={settings.strata_manager_phone || ''}
                                        onChange={handleChange}
                                        placeholder="02 2222 3333"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="strata_manager_email" className="flex items-center gap-2">
                                        <Mail className="h-3.5 w-3.5 text-muted-foreground"/>
                                        Company Email
                                    </Label>
                                    <Input
                                        id="strata_manager_email"
                                        name="strata_manager_email"
                                        type="email"
                                        value={settings.strata_manager_email || ''}
                                        onChange={handleChange}
                                        placeholder="info@strataos.live"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="strata_manager_address">Company Postal Address</Label>
                                    <Input
                                        id="strata_manager_address"
                                        name="strata_manager_address"
                                        value={settings.strata_manager_address || ''}
                                        onChange={handleChange}
                                        placeholder="PO BOX 919 Canberra, ACT, 2600"
                                    />
                                </div>
                            </div>

                            <div className="grid gap-4 md:grid-cols-2">
                                {[
                                    {type: 'building', field: 'building_logo_url', label: 'Owners Corporation / Building Logo'},
                                    {type: 'strata-manager', field: 'strata_management_logo_url', label: 'Strata Management Logo'},
                                ].map(({type, field, label}) => (
                                    <div key={type} className="rounded-lg border bg-background/60 p-4 space-y-3">
                                        <Label htmlFor={`${type}-logo-upload`}>{label}</Label>
                                        {settings[field] ? (
                                            <div className="h-20 rounded border bg-white p-2 flex items-center justify-center">
                                                <img src={settings[field]} alt={label}
                                                     className="max-h-full max-w-full object-contain"/>
                                            </div>
                                        ) : (
                                            <div className="h-20 rounded border border-dashed flex items-center justify-center text-xs text-muted-foreground">
                                                No logo uploaded
                                            </div>
                                        )}
                                        <label className="inline-flex">
                                            <Input id={`${type}-logo-upload`} type="file"
                                                   accept="image/png,image/jpeg,image/webp"
                                                   className="sr-only"
                                                   disabled={uploadingLogo !== null}
                                                   onChange={(event) => handleLogoUpload(type, event)}/>
                                            <span className={cn(
                                                "inline-flex h-9 cursor-pointer items-center rounded-md border px-3 text-sm font-medium",
                                                uploadingLogo !== null && "pointer-events-none opacity-50"
                                            )}>
                                                {uploadingLogo === type
                                                    ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/>
                                                    : <Upload className="mr-2 h-4 w-4"/>}
                                                Upload logo
                                            </span>
                                        </label>
                                        <p className="text-xs text-muted-foreground">PNG, JPEG or WebP; maximum 2 MB.</p>
                                    </div>
                                ))}
                            </div>

                            <div className="grid gap-4 md:grid-cols-3">
                                <div className="space-y-2">
                                    <Label htmlFor="plan_number">Units Plan Number</Label>
                                    <Input id="plan_number" name="plan_number"
                                           value={settings.plan_number || ''} onChange={handleChange}
                                           placeholder="13195"/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="building_abn">Owners Corporation ABN</Label>
                                    <Input id="building_abn" name="building_abn"
                                           value={settings.building_abn || ''} onChange={handleChange}
                                           placeholder="98 212 234 337"/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="document_branding_mode">Letterhead Identity</Label>
                                    <Select value={settings.document_branding_mode || 'dual'}
                                            onValueChange={(value) =>
                                                setSettings(prev => ({...prev, document_branding_mode: value}))
                                            }>
                                        <SelectTrigger id="document_branding_mode">
                                            <SelectValue/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="dual">Building + managing agency</SelectItem>
                                            <SelectItem value="agency">Managing agency only</SelectItem>
                                            <SelectItem value="building">Building only</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="strata_management_abn">Agency ABN</Label>
                                    <Input id="strata_management_abn" name="strata_management_abn"
                                           value={settings.strata_management_abn || ''} onChange={handleChange}/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="strata_management_licence">Agency Licence</Label>
                                    <Input id="strata_management_licence" name="strata_management_licence"
                                           value={settings.strata_management_licence || ''} onChange={handleChange}/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="strata_management_website">Agency Website</Label>
                                    <Input id="strata_management_website" name="strata_management_website"
                                           value={settings.strata_management_website || ''} onChange={handleChange}/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="document_accent_color">Document Accent Colour</Label>
                                    <div className="flex gap-2">
                                        <Input type="color" value={settings.document_accent_color || '#B8823D'}
                                               className="h-10 w-14 p-1"
                                               onChange={(event) => setSettings(prev => ({
                                                   ...prev, document_accent_color: event.target.value.toUpperCase()
                                               }))}/>
                                        <Input id="document_accent_color" name="document_accent_color"
                                               value={settings.document_accent_color || '#B8823D'}
                                               onChange={handleChange} className="font-mono"/>
                                    </div>
                                </div>
                                <div className="space-y-2 md:col-span-2">
                                    <Label htmlFor="document_footer_text">Document Footer Text</Label>
                                    <Input id="document_footer_text" name="document_footer_text"
                                           value={settings.document_footer_text || ''} onChange={handleChange}
                                           placeholder="Confidential - issued on behalf of the Owners Corporation"/>
                                </div>
                                <div className="md:col-span-3 flex items-center justify-between rounded-lg border p-3">
                                    <div>
                                        <Label htmlFor="document_show_page_numbers">Show page numbers</Label>
                                        <p className="text-xs text-muted-foreground">
                                            Used on AGM correspondence and other multi-page generated letters.
                                        </p>
                                    </div>
                                    <Switch id="document_show_page_numbers"
                                            checked={settings.document_show_page_numbers !== false}
                                            onCheckedChange={(checked) => setSettings(prev => ({
                                                ...prev, document_show_page_numbers: checked
                                            }))}/>
                                </div>
                            </div>

                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="agm_recording_disclosure">AGM Recording / Transcription Disclosure</Label>
                                    <Textarea id="agm_recording_disclosure" name="agm_recording_disclosure"
                                              value={settings.agm_recording_disclosure || ''}
                                              onChange={handleChange} rows={4}
                                              placeholder="Optional disclosure shown on AGM notices..."/>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="agm_insurance_disclosure">AGM Insurance Disclosure</Label>
                                    <Textarea id="agm_insurance_disclosure" name="agm_insurance_disclosure"
                                              value={settings.agm_insurance_disclosure || ''}
                                              onChange={handleChange} rows={4}
                                              placeholder="Optional insurance commission or broker disclosure..."/>
                                </div>
                            </div>

                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="levy_notice_email_format">Levy Notice Email Format</Label>
                                    <Select
                                        value={settings.levy_notice_email_format || 'standard'}
                                        onValueChange={(value) =>
                                            setSettings(prev => ({...prev, levy_notice_email_format: value}))
                                        }
                                    >
                                        <SelectTrigger id="levy_notice_email_format"
                                                       data-testid="levy-notice-format-select">
                                            <SelectValue placeholder="Select a format"/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="standard">Standard notification</SelectItem>
                                            <SelectItem value="levies_team">Levies Department (with grace periods)</SelectItem>
                                        </SelectContent>
                                    </Select>
                                    <p className="text-xs text-muted-foreground">
                                        "Levies Department" adds a contact block and per-state grace periods.
                                    </p>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="levies_department_phone" className="flex items-center gap-2">
                                        <Phone className="h-3.5 w-3.5 text-muted-foreground"/>
                                        Levies Department Phone
                                    </Label>
                                    <Input
                                        id="levies_department_phone"
                                        name="levies_department_phone"
                                        value={settings.levies_department_phone || ''}
                                        onChange={handleChange}
                                        placeholder="1300 888 999"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="levies_department_email" className="flex items-center gap-2">
                                        <Mail className="h-3.5 w-3.5 text-muted-foreground"/>
                                        Levies Department Email
                                    </Label>
                                    <Input
                                        id="levies_department_email"
                                        name="levies_department_email"
                                        type="email"
                                        value={settings.levies_department_email || ''}
                                        onChange={handleChange}
                                        placeholder="levies@strataos.live"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="levy_notice_support_domain" className="flex items-center gap-2">
                                        <Globe className="h-3.5 w-3.5 text-muted-foreground"/>
                                        Support Address Domain
                                    </Label>
                                    <Input
                                        id="levy_notice_support_domain"
                                        name="levy_notice_support_domain"
                                        value={settings.levy_notice_support_domain || ''}
                                        onChange={handleChange}
                                        placeholder="strataos.live"
                                    />
                                    <p className="text-xs text-muted-foreground">
                                        Per-plan reply address is derived as UP&lt;plan&gt;@&lt;domain&gt;.
                                    </p>
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="levy_notice_support_email" className="flex items-center gap-2">
                                        <Mail className="h-3.5 w-3.5 text-muted-foreground"/>
                                        Support Email Override
                                    </Label>
                                    <Input
                                        id="levy_notice_support_email"
                                        name="levy_notice_support_email"
                                        type="email"
                                        value={settings.levy_notice_support_email || ''}
                                        onChange={handleChange}
                                        placeholder="(optional) overrides the derived UP…@ address"
                                    />
                                </div>
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="levy_notice_disclaimer" className="flex items-center gap-2">
                                    <FileText className="h-3.5 w-3.5 text-muted-foreground"/>
                                    Levy Notice PDF Disclaimer
                                </Label>
                                <Input
                                    id="levy_notice_disclaimer"
                                    name="levy_notice_disclaimer"
                                    value={settings.levy_notice_disclaimer || ''}
                                    onChange={handleChange}
                                    placeholder="Optional footer text printed on the levy notice PDF"
                                />
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="visual" className="space-y-6 animate-in fade-in-50 duration-300">
                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <ImageIcon className="h-5 w-5 text-primary"/>
                                <CardTitle>Visual Identity</CardTitle>
                            </div>
                            <CardDescription>Configure hero imagery and public-facing branding.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            <div className="space-y-2">
                                <Label htmlFor="hero_image">Hero Background Image URL</Label>
                                <div className="flex gap-2">
                                    <Input
                                        id="hero_image"
                                        name="hero_image"
                                        value={settings.hero_image || ''}
                                        onChange={handleChange}
                                        placeholder="https://images.unsplash.com/..."
                                        className="font-mono text-xs"
                                    />
                                </div>
                                {settings.hero_image && (
                                    <div
                                        className="mt-4 rounded-xl overflow-hidden aspect-video max-w-2xl border shadow-inner">
                                        <img
                                            src={settings.hero_image}
                                            alt="Hero preview"
                                            className="w-full h-full object-cover"
                                        />
                                    </div>
                                )}
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <Globe className="h-5 w-5 text-primary"/>
                                <CardTitle>Public Page Content</CardTitle>
                                <TooltipProvider>
                                    <Tooltip>
                                        <TooltipTrigger asChild>
                                            <Info className="h-4 w-4 text-muted-foreground cursor-help ml-auto"/>
                                        </TooltipTrigger>
                                        <TooltipContent className="max-w-xs">
                                            <p>The About Page content allows you to share the history, amenities, and
                                                community values of the building. The Footer text appears globally and
                                                is ideal for legal notices or a brief branding statement.</p>
                                        </TooltipContent>
                                    </Tooltip>
                                </TooltipProvider>
                            </div>
                            <CardDescription>Rich text content for the About page and Global Footer.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-6">
                            <div className="space-y-2">
                                <Label>About Page Main Content</Label>
                                <RichTextEditor
                                    content={settings.about_content || ''}
                                    onChange={(val) => handleRichTextChange('about_content', val)}
                                />
                            </div>
                            <div className="space-y-2">
                                <Label>Footer Branding Text</Label>
                                <RichTextEditor
                                    content={settings.footer_text || ''}
                                    onChange={(val) => handleRichTextChange('footer_text', val)}
                                />
                                <p className="text-xs text-muted-foreground italic">This appears above the copyright
                                    notice on every page.</p>
                            </div>

                            {isAuthAdmin && (
                                <div className="mt-8 pt-8 border-t border-dashed space-y-4">
                                    <div className="flex items-center gap-2">
                                        <ShieldAlert className="h-5 w-5 text-red-500"/>
                                        <Label className="text-red-500 font-bold uppercase tracking-wider text-xs">IP
                                            Protection String (Restricted)</Label>
                                    </div>
                                    <div className="bg-red-500/5 border border-red-500/10 p-4 rounded-lg space-y-4">
                                        <div className="space-y-2">
                                            <Input
                                                name="ip_string"
                                                value={settings.ip_string || ''}
                                                onChange={handleChange}
                                                placeholder="IP protection notice..."
                                                className="font-mono text-xs border-red-200 focus-visible:ring-red-500"
                                            />
                                            <p className="text-[10px] text-red-600/60 leading-relaxed">
                                                <strong>Warning:</strong> This string is imprinted into the
                                                application's runtime integrity check.
                                                Modifying it incorrectly may trigger system blocks for unauthorized
                                                users.
                                            </p>
                                        </div>
                                    </div>
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="links" className="space-y-6 animate-in fade-in-50 duration-300">
                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <LinkIcon className="h-5 w-5 text-primary"/>
                                <CardTitle>Global Quick Links</CardTitle>
                            </div>
                            <CardDescription>Links shown in the "Quick Links" section of the footer.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            {settings.quick_links.map((link, index) => (
                                <div key={index}
                                     className="flex gap-3 items-end bg-muted/20 p-3 rounded-lg border border-border/50">
                                    <div className="flex-1 space-y-1.5">
                                        <Label
                                            className="text-[10px] uppercase font-bold text-muted-foreground">Label</Label>
                                        <Input
                                            value={link.label}
                                            onChange={(e) => handleLinkChange('quick', index, 'label', e.target.value)}
                                            placeholder="e.g. Terms of Use"
                                        />
                                    </div>
                                    <div className="flex-[2] space-y-1.5">
                                        <Label
                                            className="text-[10px] uppercase font-bold text-muted-foreground">URL</Label>
                                        <Input
                                            value={link.url}
                                            onChange={(e) => handleLinkChange('quick', index, 'url', e.target.value)}
                                            placeholder="/terms or https://..."
                                        />
                                    </div>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="text-destructive hover:bg-destructive/10"
                                        onClick={() => handleRemoveLink('quick', index)}
                                        aria-label="Remove link"
                                    >
                                        <Trash2 className="h-4 w-4"/>
                                    </Button>
                                </div>
                            ))}
                            <Button variant="outline" size="sm" onClick={() => handleAddLink('quick')}
                                    className="w-full dashed-border border-dashed">
                                <Plus className="mr-2 h-4 w-4"/> Add Quick Link
                            </Button>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <ShieldCheck className="h-5 w-5 text-primary"/>
                                <CardTitle>Resident Resources</CardTitle>
                            </div>
                            <CardDescription>Helpful links specifically for property residents and
                                owners.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            {settings.resident_links.map((link, index) => (
                                <div key={index}
                                     className="flex gap-3 items-end bg-muted/20 p-3 rounded-lg border border-border/50">
                                    <div className="flex-1 space-y-1.5">
                                        <Label
                                            className="text-[10px] uppercase font-bold text-muted-foreground">Label</Label>
                                        <Input
                                            value={link.label}
                                            onChange={(e) => handleLinkChange('resident', index, 'label', e.target.value)}
                                            placeholder="e.g. Resident Portal"
                                        />
                                    </div>
                                    <div className="flex-[2] space-y-1.5">
                                        <Label
                                            className="text-[10px] uppercase font-bold text-muted-foreground">URL</Label>
                                        <Input
                                            value={link.url}
                                            onChange={(e) => handleLinkChange('resident', index, 'url', e.target.value)}
                                            placeholder="/dashboard"
                                        />
                                    </div>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="text-destructive hover:bg-destructive/10"
                                        onClick={() => handleRemoveLink('resident', index)}
                                        aria-label="Remove link"
                                    >
                                        <Trash2 className="h-4 w-4"/>
                                    </Button>
                                </div>
                            ))}
                            <Button variant="outline" size="sm" onClick={() => handleAddLink('resident')}
                                    className="w-full dashed-border border-dashed">
                                <Plus className="mr-2 h-4 w-4"/> Add Resident Resource
                            </Button>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="approvals" className="space-y-6 animate-in fade-in-50 duration-300">
                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <ShieldCheck className="h-5 w-5 text-primary"/>
                                <CardTitle>Work Order Approval Thresholds</CardTitle>
                            </div>
                            <CardDescription>Configure spending limits and required approval roles.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            {( settings.work_order_thresholds || [] ).map((threshold, index) => (
                                <div key={index}
                                     className="flex gap-4 items-end bg-muted/20 p-4 rounded-lg border border-border/50">
                                    <div className="flex-1 space-y-1.5">
                                        <Label className="text-xs">Max Amount ($)</Label>
                                        <Input
                                            type="number"
                                            value={threshold.max_amount}
                                            onChange={(e) => {
                                                const newT = [...settings.work_order_thresholds];
                                                newT[ index ].max_amount = parseFloat(e.target.value);
                                                setSettings(prev => ( {...prev, work_order_thresholds: newT} ));
                                            }}
                                        />
                                    </div>
                                    <div className="flex-1 space-y-1.5">
                                        <Label className="text-xs">Mode</Label>
                                        <Select
                                            value={threshold.approval_mode}
                                            onValueChange={(v) => {
                                                const newT = [...settings.work_order_thresholds];
                                                newT[ index ].approval_mode = v;
                                                setSettings(prev => ( {...prev, work_order_thresholds: newT} ));
                                            }}
                                        >
                                            <SelectTrigger><SelectValue/></SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="SINGLE_APPROVAL">Single Approval</SelectItem>
                                                <SelectItem value="DUAL_APPROVAL">Dual Approval</SelectItem>
                                                <SelectItem value="MAJORITY">Majority Vote</SelectItem>
                                            </SelectContent>
                                        </Select>
                                    </div>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="text-destructive"
                                        onClick={() => {
                                            setSettings(prev => ( {
                                                ...prev,
                                                work_order_thresholds: prev.work_order_thresholds.filter((_, i) => i !== index)
                                            } ));
                                        }}
                                    >
                                        <Trash2 className="h-4 w-4"/>
                                    </Button>
                                </div>
                            ))}
                            <Button
                                variant="outline"
                                size="sm"
                                className="w-full border-dashed"
                                onClick={() => {
                                    setSettings(prev => ( {
                                        ...prev,
                                        work_order_thresholds: [...( prev.work_order_thresholds || [] ), {
                                            max_amount: 1000,
                                            approval_required: true,
                                            approval_roles: ["CHAIRMAN", "TREASURER"],
                                            approval_mode: "SINGLE_APPROVAL"
                                        }]
                                    } ));
                                }}
                            >
                                <Plus className="mr-2 h-4 w-4"/> Add Threshold
                            </Button>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <FileText className="h-5 w-5 text-primary"/>
                                <CardTitle>Quotes Required Thresholds</CardTitle>
                            </div>
                            <CardDescription>Define how many quotes are required based on estimated
                                cost.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            {( settings.quotes_required_thresholds || [] ).map((threshold, index) => (
                                <div key={index}
                                     className="flex gap-4 items-end bg-muted/20 p-4 rounded-lg border border-border/50">
                                    <div className="flex-1 space-y-1.5">
                                        <Label className="text-xs">Max Amount ($)</Label>
                                        <Input
                                            type="number"
                                            value={threshold.max_amount}
                                            onChange={(e) => {
                                                const newT = [...settings.quotes_required_thresholds];
                                                newT[ index ].max_amount = parseFloat(e.target.value);
                                                setSettings(prev => ( {...prev, quotes_required_thresholds: newT} ));
                                            }}
                                        />
                                    </div>
                                    <div className="flex-1 space-y-1.5">
                                        <Label className="text-xs">Quotes Required</Label>
                                        <Input
                                            type="number"
                                            value={threshold.quotes}
                                            onChange={(e) => {
                                                const newT = [...settings.quotes_required_thresholds];
                                                newT[ index ].quotes = parseInt(e.target.value);
                                                setSettings(prev => ( {...prev, quotes_required_thresholds: newT} ));
                                            }}
                                        />
                                    </div>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        className="text-destructive"
                                        onClick={() => {
                                            setSettings(prev => ( {
                                                ...prev,
                                                quotes_required_thresholds: prev.quotes_required_thresholds.filter((_, i) => i !== index)
                                            } ));
                                        }}
                                    >
                                        <Trash2 className="h-4 w-4"/>
                                    </Button>
                                </div>
                            ))}
                            <Button
                                variant="outline"
                                size="sm"
                                className="w-full border-dashed"
                                onClick={() => {
                                    setSettings(prev => ( {
                                        ...prev,
                                        quotes_required_thresholds: [...( prev.quotes_required_thresholds || [] ), {
                                            max_amount: 2000,
                                            quotes: 1
                                        }]
                                    } ));
                                }}
                            >
                                <Plus className="mr-2 h-4 w-4"/> Add Threshold
                            </Button>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="financial" className="space-y-6 animate-in fade-in-50 duration-300">
                    {/* Per-building mock/live boundary for the external financial integrations.
                        Renders nothing for roles outside super_admin/strata_admin/strata_manager;
                        the backend capability is the actual gate. */}
                    <FinancialMockModeCard/>

                    {/* Per-building arrears late-fee policy (drives computed interest/late fees) */}
                    <LateFeePolicyCard/>

                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <Calendar className="h-5 w-5 text-primary"/>
                                <CardTitle>Levy Collection Schedule</CardTitle>
                            </div>
                            <CardDescription>Configure when and how often strata levies are collected.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-6">
                            <div className="grid gap-6 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>Collection Frequency</Label>
                                    <Select
                                        value={settings.levy_collection_frequency || 'quarterly'}
                                        onValueChange={(value) => {
                                            setSettings(prev => {
                                                const start = prev.financial_year_start_month || 1;
                                                let defaultMonths = [];
                                                if (value === 'monthly') defaultMonths = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
                                                else if (value === 'quarterly') {
                                                    for (let i = 0; i < 4; i++) defaultMonths.push(( ( start + i * 3 - 1 ) % 12 ) + 1);
                                                } else if (value === 'half_yearly') {
                                                    for (let i = 0; i < 2; i++) defaultMonths.push(( ( start + i * 6 - 1 ) % 12 ) + 1);
                                                } else if (value === 'yearly') defaultMonths = [start];

                                                return {
                                                    ...prev,
                                                    levy_collection_frequency: value,
                                                    levy_due_months: defaultMonths.sort((a, b) => a - b)
                                                };
                                            });
                                        }}
                                    >
                                        <SelectTrigger>
                                            <SelectValue/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="monthly">Monthly</SelectItem>
                                            <SelectItem value="quarterly">Quarterly</SelectItem>
                                            <SelectItem value="half_yearly">Half Yearly</SelectItem>
                                            <SelectItem value="yearly">Yearly</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-2">
                                    <Label>FY Start Month</Label>
                                    <Select
                                        value={settings.financial_year_start_month?.toString()}
                                        onValueChange={(v) => {
                                            const start = parseInt(v);
                                            setSettings(prev => {
                                                const freq = prev.levy_collection_frequency || 'quarterly';
                                                let defaultMonths = [];
                                                if (freq === 'monthly') defaultMonths = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];
                                                else if (freq === 'quarterly') {
                                                    for (let i = 0; i < 4; i++) defaultMonths.push(( ( start + i * 3 - 1 ) % 12 ) + 1);
                                                } else if (freq === 'half_yearly') {
                                                    for (let i = 0; i < 2; i++) defaultMonths.push(( ( start + i * 6 - 1 ) % 12 ) + 1);
                                                } else if (freq === 'yearly') defaultMonths = [start];

                                                return {
                                                    ...prev,
                                                    financial_year_start_month: start,
                                                    levy_due_months: defaultMonths.sort((a, b) => a - b)
                                                };
                                            });
                                        }}
                                    >
                                        <SelectTrigger>
                                            <SelectValue/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            {MONTHS.map((m, i) => (
                                                <SelectItem key={i + 1} value={( i + 1 ).toString()}>{m}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>

                            <div className="space-y-3">
                                <div className="flex items-center justify-between">
                                    <Label>Payment Due Months</Label>
                                    <Badge variant="secondary" className="text-[10px] font-bold uppercase">
                                        Select up to {
                                        settings.levy_collection_frequency === 'monthly' ? 12 :
                                            settings.levy_collection_frequency === 'quarterly' ? 4 :
                                                settings.levy_collection_frequency === 'half_yearly' ? 2 : 1
                                    } months
                                    </Badge>
                                </div>
                                <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 gap-2">
                                    {MONTHS.map((month, idx) => {
                                        const monthNum = idx + 1;
                                        const isSelected = settings.levy_due_months?.includes(monthNum);
                                        const maxAllowed =
                                            settings.levy_collection_frequency === 'monthly' ? 12 :
                                                settings.levy_collection_frequency === 'quarterly' ? 4 :
                                                    settings.levy_collection_frequency === 'half_yearly' ? 2 : 1;

                                        return (
                                            <Button
                                                key={month}
                                                type="button"
                                                variant={isSelected ? 'default' : 'outline'}
                                                className={cn(
                                                    "h-10 text-xs font-bold transition-all",
                                                    isSelected ? "bg-primary shadow-md" : "hover:bg-primary/10"
                                                )}
                                                onClick={() => {
                                                    setSettings(prev => {
                                                        let current = [...( prev.levy_due_months || [] )];
                                                        if (current.includes(monthNum)) {
                                                            current = current.filter(m => m !== monthNum);
                                                        } else {
                                                            if (current.length < maxAllowed) {
                                                                current.push(monthNum);
                                                            } else if (maxAllowed === 1) {
                                                                current = [monthNum];
                                                            } else {
                                                                toast.warning(`Max ${maxAllowed} months allowed for ${prev.levy_collection_frequency} frequency`);
                                                                return prev;
                                                            }
                                                        }
                                                        return {
                                                            ...prev,
                                                            levy_due_months: current.sort((a, b) => a - b)
                                                        };
                                                    });
                                                }}
                                            >
                                                {month}
                                            </Button>
                                        );
                                    })}
                                </div>
                            </div>

                            <div className="space-y-3">
                                <Label>Due Date Strategy</Label>
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
                                    {['first', 'middle', 'last', 'custom'].map(type => (
                                        <Button
                                            key={type}
                                            type="button"
                                            variant={settings.levy_due_day_type === type ? 'default' : 'outline'}
                                            onClick={() => setSettings(prev => ( {...prev, levy_due_day_type: type} ))}
                                            className="capitalize h-10"
                                        >
                                            {type}
                                        </Button>
                                    ))}
                                </div>
                                {settings.levy_due_day_type === 'custom' && (
                                    <div className="mt-4 p-4 bg-muted/30 rounded-lg space-y-4 border border-primary/10">
                                        <div className="flex items-center justify-between border-b pb-2 mb-2">
                                            <Label
                                                className="text-xs font-bold uppercase text-primary tracking-widest flex items-center gap-2">
                                                <Calendar className="h-3.5 w-3.5"/>
                                                Specify Due Dates per Month
                                            </Label>
                                            <Badge variant="outline" className="text-[10px]">
                                                Strategy: Custom Linkage
                                            </Badge>
                                        </div>

                                        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4">
                                            {( settings.levy_due_months || [] ).map((monthNum) => (
                                                <div key={monthNum} className="space-y-2 group">
                                                    <Label htmlFor={`due-day-${monthNum}`}
                                                           className="text-xs font-semibold flex items-center gap-1.5 opacity-80 group-hover:opacity-100 transition-opacity">
                                    <span
                                        className="bg-primary text-primary-foreground h-4 w-4 rounded-full flex items-center justify-center text-[10px]">
                                        {monthNum}
                                    </span>
                                                        {MONTHS[ monthNum - 1 ]} Due Day
                                                    </Label>
                                                    <Select
                                                        value={( settings.levy_due_custom_dates?.[ monthNum ] || settings.levy_due_day || 28 ).toString()}
                                                        onValueChange={(v) => {
                                                            setSettings(prev => ( {
                                                                ...prev,
                                                                levy_due_custom_dates: {
                                                                    ...( prev.levy_due_custom_dates || {} ),
                                                                    [ monthNum ]: parseInt(v)
                                                                }
                                                            } ));
                                                        }}
                                                    >
                                                        <SelectTrigger id={`due-day-${monthNum}`}
                                                                       className="h-9 font-mono bg-white">
                                                            <SelectValue/>
                                                        </SelectTrigger>
                                                        <SelectContent>
                                                            {Array.from({length: 31}, (_, i) => i + 1).map(day => (
                                                                <SelectItem key={day} value={day.toString()}>
                                                                    Day {day}
                                                                </SelectItem>
                                                            ))}
                                                        </SelectContent>
                                                    </Select>
                                                </div>
                                            ))}
                                        </div>

                                        {( settings.levy_due_months || [] ).length === 0 && (
                                            <p className="text-xs text-center py-4 text-muted-foreground italic">
                                                Select "Payment Due Months" above to configure specific days.
                                            </p>
                                        )}

                                        <div className="pt-2 flex items-center gap-2 text-[10px] text-muted-foreground">
                                            <Info className="h-3 w-3"/>
                                            <span>These dates will be used for all financial logic including interest and penalties.</span>
                                        </div>
                                    </div>
                                )}
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <CreditCard className="h-5 w-5 text-primary"/>
                                <CardTitle>Arrears & Penalties</CardTitle>
                            </div>
                            <CardDescription>Define interest rates and grace periods for late
                                payments.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6">
                            <div className="grid gap-6 md:grid-cols-3">
                                <div className="space-y-2">
                                    <Label className="flex items-center gap-2">
                                        Interest (%/mo)
                                    </Label>
                                    <Input
                                        type="number"
                                        step="0.01"
                                        value={( settings.interest_rate_per_month || 0 ) * 100}
                                        onChange={(e) => setSettings(prev => ( {
                                            ...prev,
                                            interest_rate_per_month: parseFloat(e.target.value) / 100
                                        } ))}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>Penalty ($)</Label>
                                    <Input
                                        type="number"
                                        value={settings.penalty_amount || 0}
                                        onChange={(e) => setSettings(prev => ( {
                                            ...prev,
                                            penalty_amount: parseFloat(e.target.value)
                                        } ))}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label className="flex items-center gap-2">
                                        <Clock className="h-3 w-3"/>
                                        Grace (days)
                                    </Label>
                                    <Input
                                        type="number"
                                        value={settings.grace_period_days || 0}
                                        onChange={(e) => setSettings(prev => ( {
                                            ...prev,
                                            grace_period_days: parseInt(e.target.value)
                                        } ))}
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label className="flex items-center gap-2">
                                        <Mail className="h-3 w-3"/>
                                        Reminder Lead (days)
                                    </Label>
                                    <Input
                                        type="number"
                                        min="7"
                                        max="30"
                                        value={settings.reminder_lead_days || 14}
                                        onChange={(e) => setSettings(prev => ( {
                                            ...prev,
                                            reminder_lead_days: parseInt(e.target.value)
                                        } ))}
                                    />
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>
                <TabsContent value="registration" className="space-y-6 animate-in fade-in-50 duration-300">
                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <UserCheck className="h-5 w-5 text-primary"/>
                                <CardTitle>Registration &amp; Approval Timing</CardTitle>
                            </div>
                            <CardDescription>
                                Configure how long the system waits before escalating or auto-approving new
                                registrations.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-6">
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
                                <div className="space-y-2">
                                    <Label className="flex items-center gap-2">
                                        <Clock className="h-3 w-3"/>
                                        Admin Auto-Approve (minutes)
                                    </Label>
                                    <Input
                                        type="number"
                                        min="5"
                                        max="1440"
                                        value={settings.admin_auto_approve_minutes ?? 15}
                                        onChange={(e) => setSettings(prev => ( {
                                            ...prev,
                                            admin_auto_approve_minutes: parseInt(e.target.value)
                                        } ))}
                                    />
                                    <p className="text-xs text-muted-foreground">After an owner approves, auto-approve
                                        if no admin action within this many minutes (default: 15).</p>
                                </div>
                                <div className="space-y-2">
                                    <Label className="flex items-center gap-2">
                                        <Clock className="h-3 w-3"/>
                                        Guest Escalation (hours)
                                    </Label>
                                    <Input
                                        type="number"
                                        min="1"
                                        max="168"
                                        value={settings.guest_escalation_hours ?? 2}
                                        onChange={(e) => setSettings(prev => ( {
                                            ...prev,
                                            guest_escalation_hours: parseInt(e.target.value)
                                        } ))}
                                    />
                                    <p className="text-xs text-muted-foreground">Hours before a pending guest
                                        registration is escalated to super admins (default: 2).</p>
                                </div>
                                <div className="space-y-2">
                                    <Label className="flex items-center gap-2">
                                        <Clock className="h-3 w-3"/>
                                        Tenant Escalation (hours)
                                    </Label>
                                    <Input
                                        type="number"
                                        min="1"
                                        max="720"
                                        value={settings.tenant_escalation_hours ?? 48}
                                        onChange={(e) => setSettings(prev => ( {
                                            ...prev,
                                            tenant_escalation_hours: parseInt(e.target.value)
                                        } ))}
                                    />
                                    <p className="text-xs text-muted-foreground">Hours before a pending tenant
                                        registration is escalated to super admins (default: 48).</p>
                                </div>
                                <div className="space-y-2">
                                    <Label className="flex items-center gap-2">
                                        <Clock className="h-3 w-3"/>
                                        Decision Token Validity (hours)
                                    </Label>
                                    <Input
                                        type="number"
                                        min="1"
                                        max="720"
                                        value={settings.token_validity_hours ?? 72}
                                        onChange={(e) => setSettings(prev => ( {
                                            ...prev,
                                            token_validity_hours: parseInt(e.target.value)
                                        } ))}
                                    />
                                    <p className="text-xs text-muted-foreground">How long a one-click approve/reject
                                        email link stays valid (default: 72 hours).</p>
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <Mail className="h-5 w-5 text-primary"/>
                                <CardTitle>Notification BCC</CardTitle>
                            </div>
                            <CardDescription>All registration notification emails are BCCed to this
                                address.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label>BCC Email Address</Label>
                                    <Input
                                        type="email"
                                        value={settings.notify_bcc_email || ''}
                                        onChange={(e) => setSettings(prev => ( {
                                            ...prev,
                                            notify_bcc_email: e.target.value
                                        } ))}
                                        placeholder="gagneet@eastgateresidences.com.au"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label>BCC Display Name</Label>
                                    <Input
                                        type="text"
                                        value={settings.notify_bcc_name || ''}
                                        onChange={(e) => setSettings(prev => ( {
                                            ...prev,
                                            notify_bcc_name: e.target.value
                                        } ))}
                                        placeholder="Building Administrator"
                                    />
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <Gauge className="h-5 w-5 text-primary"/>
                                <CardTitle>Rate Limiting</CardTitle>
                            </div>
                            <CardDescription>
                                Maximum requests per minute per IP address for each authentication endpoint.
                                Use the multiplier to scale all limits at once. Rate limiting can be disabled from
                                <strong> Feature Toggles &rarr; Rate Limiting</strong>.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            {!isSuperAdmin && (
                                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                    <Badge variant="secondary">Super Admin</Badge>
                                    Only Super Admins can update rate limiting controls.
                                </div>
                            )}
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label className="flex items-center gap-2">
                                        <RefreshCw className="h-3 w-3"/>
                                        Global Multiplier
                                    </Label>
                                    <div className="flex items-center gap-2">
                                        <Input
                                            type="number"
                                            min="0.1"
                                            step="0.1"
                                            value={settings.rate_limit_multiplier ?? 1}
                                            onChange={(e) => setSettings(prev => ( {
                                                ...prev,
                                                rate_limit_multiplier: parseFloat(e.target.value) || 1
                                            } ))}
                                            className="h-8 text-sm"
                                            disabled={!isSuperAdmin}
                                        />
                                        <span
                                            className="text-xs text-muted-foreground whitespace-nowrap">× base limits</span>
                                    </div>
                                    <p className="text-xs text-muted-foreground">
                                        1.0 = default. 0.5 halves limits, 2.0 doubles them. Updates apply within ~1
                                        minute.
                                    </p>
                                </div>
                            </div>
                            <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
                                {[
                                    {
                                        label: 'Register',
                                        key: 'rate_limit_register',
                                        default: 5,
                                        path: 'POST /auth/register'
                                    },
                                    {label: 'Login', key: 'rate_limit_login', default: 10, path: 'POST /auth/login'},
                                    {
                                        label: 'Forgot Password',
                                        key: 'rate_limit_forgot_password',
                                        default: 5,
                                        path: 'POST /auth/forgot-password'
                                    },
                                    {
                                        label: 'Reset Password',
                                        key: 'rate_limit_reset_password',
                                        default: 5,
                                        path: 'POST /auth/reset-password'
                                    },
                                    {
                                        label: 'Change Password',
                                        key: 'rate_limit_change_password',
                                        default: 10,
                                        path: 'POST /auth/change-password'
                                    },
                                    {
                                        label: 'Registration Decision',
                                        key: 'rate_limit_registration_decision',
                                        default: 10,
                                        path: 'POST /auth/registration-decision'
                                    },
                                ].map(({label, key, default: def, path}) => (
                                    <div key={key} className="space-y-1">
                                        <Label className="text-xs font-medium">{label}</Label>
                                        <div className="flex items-center gap-1">
                                            <Input
                                                type="number"
                                                min="1"
                                                max="1000"
                                                value={settings[ key ] ?? def}
                                                onChange={(e) => setSettings(prev => ( {
                                                    ...prev,
                                                    [ key ]: parseInt(e.target.value) || def
                                                } ))}
                                                className="h-8 text-sm"
                                                disabled={!isSuperAdmin}
                                            />
                                            <span
                                                className="text-xs text-muted-foreground whitespace-nowrap">/min</span>
                                        </div>
                                        <p className="text-xs text-muted-foreground/60 font-mono">{path}</p>
                                    </div>
                                ))}
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <Info className="h-5 w-5 text-muted-foreground"/>
                                <CardTitle className="text-base">Non-Configurable Parameters</CardTitle>
                            </div>
                            <CardDescription>These values are set in the server environment (.env file). Contact your
                                system administrator to change them.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-4">
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
                                {[
                                    {label: 'SMTP Host', envKey: 'SMTP_HOST', value: 'smtp.migadu.com'},
                                    {label: 'SMTP Port', envKey: 'SMTP_PORT', value: '465'},
                                    {
                                        label: 'Sender Email',
                                        envKey: 'SENDER_EMAIL',
                                        value: 'noreply@eastgateresidences.com.au'
                                    },
                                    {
                                        label: 'Portal URL',
                                        envKey: 'FRONTEND_URL',
                                        value: 'https://www.eastgateresidences.com.au'
                                    },
                                    {
                                        label: 'Migadu Domain',
                                        envKey: 'MIGADU_DOMAIN',
                                        value: 'eastgateresidences.com.au'
                                    },
                                    {
                                        label: 'Migadu Admin',
                                        envKey: 'MIGADU_ADMIN_EMAIL',
                                        value: 'admin@eastgateresidences.com.au'
                                    },
                                ].map(({label, envKey, value}) => (
                                    <div key={envKey}
                                         className="flex flex-col gap-1 p-3 rounded-md bg-muted/30 border border-border/40">
                                        <span
                                            className="font-medium text-xs text-muted-foreground uppercase tracking-wide">{label}</span>
                                        <span className="font-mono text-xs text-foreground">{value}</span>
                                        <span className="text-xs text-muted-foreground/60">.env: {envKey}</span>
                                    </div>
                                ))}
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── Payment & Bank Details Tab ─────────────────────────── */}
                <TabsContent value="payment" className="space-y-6 animate-in fade-in-50 duration-300">
                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <CreditCard className="h-5 w-5 text-primary"/>
                                <CardTitle>Bank Account Details</CardTitle>
                            </div>
                            <CardDescription>Bank account shown on levy notices, arrears notices and the payments
                                page.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>Bank Name</Label>
                                    <Input value={settings.bank_name || ''}
                                           onChange={e => setSettings(p => ( {...p, bank_name: e.target.value} ))}
                                           placeholder="e.g. Macquarie Bank"/>
                                </div>
                                <div className="space-y-2">
                                    <Label>BSB</Label>
                                    <Input value={settings.bank_bsb || ''}
                                           onChange={e => setSettings(p => ( {...p, bank_bsb: e.target.value} ))}
                                           placeholder="e.g. 182-266"/>
                                </div>
                                <div className="space-y-2">
                                    <Label>Account Number</Label>
                                    <Input value={settings.bank_account_number || ''}
                                           onChange={e => setSettings(p => ( {
                                               ...p,
                                               bank_account_number: e.target.value
                                           } ))} placeholder="e.g. 260611108"/>
                                </div>
                                <div className="space-y-2">
                                    <Label>Account Name</Label>
                                    <Input value={settings.bank_account_name || ''} onChange={e => setSettings(p => ( {
                                        ...p,
                                        bank_account_name: e.target.value
                                    } ))} placeholder="e.g. East Gate Units Plan 13195"/>
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <CreditCard className="h-5 w-5 text-primary"/>
                                <CardTitle>Bank Feed Transaction Matching</CardTitle>
                            </div>
                            <CardDescription>Controls whether incoming bank-feed transactions post to the ledger
                                automatically or wait for manual review.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6">
                            <div className="flex items-center justify-between gap-4 rounded-lg border bg-muted/20 p-4">
                                <div className="space-y-1">
                                    <Label>Automatically approve high-confidence matches</Label>
                                    <p className="text-xs text-muted-foreground">
                                        On by default: bank-feed transactions the matching engine is highly confident
                                        about are posted to the ledger automatically, and everything else is held in
                                        the review queue for a Strata Manager or Admin to approve. Turn this off to
                                        require manual approval for every matched transaction, regardless of
                                        confidence — no automatic posting at all.
                                    </p>
                                </div>
                                <Switch
                                    checked={settings.bank_feed_auto_approve !== false}
                                    onCheckedChange={checked => setSettings(p => ( {
                                        ...p,
                                        bank_feed_auto_approve: checked
                                    } ))}
                                    data-testid="bank-feed-auto-approve-switch"
                                />
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <Globe className="h-5 w-5 text-primary"/>
                                <CardTitle>Payment Methods Configuration</CardTitle>
                            </div>
                            <CardDescription>DEFT, BPAY and AusPost references shown on payment pages and all levy
                                notice PDFs.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>DEFT Reference Number</Label>
                                    <Input value={settings.deft_ref || ''}
                                           onChange={e => setSettings(p => ( {...p, deft_ref: e.target.value} ))}
                                           placeholder="e.g. 26061110862701425048"/>
                                </div>
                                <div className="space-y-2">
                                    <Label>BPAY Biller Code</Label>
                                    <Input value={settings.bpay_biller_code || ''} onChange={e => setSettings(p => ( {
                                        ...p,
                                        bpay_biller_code: e.target.value
                                    } ))} placeholder="e.g. 96503"/>
                                </div>
                                <div className="space-y-2">
                                    <Label>BPAY Reference</Label>
                                    <Input value={settings.bpay_ref || ''}
                                           onChange={e => setSettings(p => ( {...p, bpay_ref: e.target.value} ))}
                                           placeholder="e.g. 26061110862701425048"/>
                                </div>
                                <div className="space-y-2">
                                    <Label>Australia Post BillPay Code</Label>
                                    <Input value={settings.aus_post_code || ''}
                                           onChange={e => setSettings(p => ( {...p, aus_post_code: e.target.value} ))}
                                           placeholder="e.g. *496"/>
                                </div>
                                <div className="space-y-2 md:col-span-2">
                                    <Label>Australia Post BillPay Reference</Label>
                                    <Input value={settings.aus_post_ref || ''}
                                           onChange={e => setSettings(p => ( {...p, aus_post_ref: e.target.value} ))}
                                           placeholder="e.g. 260611108 62701425048"/>
                                </div>
                            </div>
                        </CardContent>
                    </Card>

                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <FileText className="h-5 w-5 text-primary"/>
                                <CardTitle>Levy Notice Details</CardTitle>
                            </div>
                            <CardDescription>Details printed on every Levy Notice and Arrears Notice
                                PDF.</CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6 space-y-4">
                            <div className="grid gap-4 md:grid-cols-2">
                                <div className="space-y-2">
                                    <Label>Unit Plan Number</Label>
                                    <Input value={settings.plan_number || ''}
                                           onChange={e => setSettings(p => ( {...p, plan_number: e.target.value} ))}
                                           placeholder="e.g. 13195"/>
                                </div>
                                <div className="space-y-2">
                                    <Label>Building ABN</Label>
                                    <Input value={settings.building_abn || ''}
                                           onChange={e => setSettings(p => ( {...p, building_abn: e.target.value} ))}
                                           placeholder="e.g. 98 212 234 337"/>
                                </div>
                                <div className="space-y-2">
                                    <Label>Annual Interest Rate on Overdue Levies (%)</Label>
                                    <Input type="number" step="0.1" value={settings.levy_interest_rate_pa ?? 10}
                                           onChange={e => setSettings(p => ( {
                                               ...p,
                                               levy_interest_rate_pa: parseFloat(e.target.value) || 0
                                           } ))} placeholder="e.g. 10"/>
                                </div>
                                <div className="space-y-3 rounded-lg border bg-muted/20 p-4 md:col-span-2">
                                    <div className="flex items-center justify-between gap-4">
                                        <div className="space-y-1">
                                            <Label>GST Registered for Levy Billing</Label>
                                            <p className="text-xs text-muted-foreground">
                                                When enabled, levy notices and related billing outputs add the
                                                configured GST rate to ex-GST fund totals.
                                            </p>
                                        </div>
                                        <Switch
                                            checked={!!settings.gst_registered}
                                            onCheckedChange={checked => setSettings(p => ( {
                                                ...p,
                                                gst_registered: checked
                                            } ))}
                                        />
                                    </div>
                                    <div className="space-y-2 max-w-xs">
                                        <Label>Levy GST Rate (%)</Label>
                                        <Input
                                            type="number"
                                            step="0.1"
                                            min="0"
                                            value={( settings.levy_gst_rate ?? 0.10 ) * 100}
                                            onChange={e => setSettings(p => ( {
                                                ...p,
                                                levy_gst_rate: Math.max(0, ( parseFloat(e.target.value) || 0 ) / 100)
                                            } ))}
                                            placeholder="e.g. 10"
                                        />
                                    </div>
                                </div>
                                <div className="space-y-2">
                                    <Label>Strata Address (for Notice Header)</Label>
                                    <Input value={settings.strata_address || ''}
                                           onChange={e => setSettings(p => ( {...p, strata_address: e.target.value} ))}
                                           placeholder="e.g. 14 Hoolihan Street, DENMAN PROSPECT ACT 2611"/>
                                </div>
                                <div className="space-y-2 md:col-span-2">
                                    <Label>Levy Notice Disclaimer Text</Label>
                                    <textarea
                                        rows={6}
                                        className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 resize-y"
                                        value={settings.levy_notice_disclaimer || ''}
                                        onChange={e => setSettings(p => ( {
                                            ...p,
                                            levy_notice_disclaimer: e.target.value
                                        } ))}
                                        placeholder="Please note that the interest rate applying to overdue levies..."
                                    />
                                    <p className="text-xs text-muted-foreground">This disclaimer is printed at the
                                        bottom of every Levy Notice and Arrears Notice PDF.</p>
                                </div>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="access-devices" className="space-y-6 animate-in fade-in-50 duration-300">
                    <Suspense
                        fallback={
                            <Card>
                                <CardContent className="flex min-h-48 items-center justify-center">
                                    <Loader2 className="h-6 w-6 animate-spin text-primary"/>
                                </CardContent>
                            </Card>
                        }
                    >
                        <AccessDeviceSettings api={api}/>
                    </Suspense>
                </TabsContent>

                {/* ── Integrations Tab ──────────────────────────────────── */}
                <TabsContent value="integrations" className="space-y-6 animate-in fade-in-50 duration-300">

                    {/* Per-building Strata Web portal connection (scraper config) */}
                    <StrataWebPortalCard/>

                    {/* Per-building OCR selector */}
                    <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                        <CardHeader className="bg-muted/30 pb-4">
                            <div className="flex items-center gap-2">
                                <Cpu className="h-5 w-5 text-primary"/>
                                <CardTitle>Invoice OCR Engine</CardTitle>
                            </div>
                            <CardDescription>
                                Select which OCR engine processes uploaded invoices for this building.
                                {ocrSettings.monthly_scans > 0 && (
                                    <span className="ml-1">
                                        Based on your upload history, this building averages <strong>{ocrSettings.monthly_scans} scans/month</strong>.
                                    </span>
                                )}
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="pt-6">
                            {ocrSettings.available_providers.length === 0 ? (
                                <p className="text-sm text-muted-foreground">
                                    Invoice OCR feature is not enabled for this building. Enable
                                    the <code>invoice_ocr</code> feature toggle first.
                                </p>
                            ) : (
                                <div className="grid gap-4 md:grid-cols-2">
                                    {ocrSettings.available_providers.map(provider => {
                                        const isSelected = ocrSettings.ocr_provider === provider.id;
                                        const badgeColors = {
                                            'Recommended': 'bg-primary/10 text-primary border-primary/20',
                                            'Premium AI': 'bg-violet-100 text-violet-700 border-violet-200',
                                            'Specialist': 'bg-blue-100 text-blue-700 border-blue-200',
                                            'Free / Local': 'bg-green-100 text-green-700 border-green-200',
                                        };
                                        return (
                                            <div
                                                key={provider.id}
                                                data-testid={`ocr-provider-card-${provider.id}`}
                                                className={cn(
                                                    'relative rounded-xl border-2 p-5 transition-all cursor-pointer hover:shadow-md',
                                                    isSelected
                                                        ? 'border-primary bg-primary/5 shadow-md'
                                                        : 'border-border bg-card hover:border-primary/40'
                                                )}
                                                onClick={() => !savingOcr && handleOcrProviderSave(provider.id)}
                                                role="radio"
                                                aria-checked={isSelected}
                                                tabIndex={0}
                                                onKeyDown={e => e.key === 'Enter' && !savingOcr && handleOcrProviderSave(provider.id)}
                                            >
                                                {isSelected && (
                                                    <CheckCircle2
                                                        className="absolute top-3 right-3 h-5 w-5 text-primary"
                                                        aria-hidden="true"/>
                                                )}
                                                <div className="flex items-start gap-3 mb-3">
                                                    <div className="flex-1 min-w-0">
                                                        <div className="flex items-center gap-2 flex-wrap mb-1">
                                                            <span
                                                                className="font-semibold text-sm">{provider.label}</span>
                                                            <span
                                                                className={cn('text-xs px-2 py-0.5 rounded-full border font-medium', badgeColors[ provider.badge ] || 'bg-muted text-muted-foreground border-border')}>
                                                                {provider.badge}
                                                            </span>
                                                        </div>
                                                        <p className="text-xs text-muted-foreground leading-relaxed">{provider.description}</p>
                                                    </div>
                                                </div>

                                                <div className="space-y-2">
                                                    <div className="grid grid-cols-2 gap-2 text-xs">
                                                        <div className="rounded-lg bg-muted/40 p-2">
                                                            <p className="text-muted-foreground mb-0.5">Per scan</p>
                                                            <p className="font-medium text-foreground">{provider.cost_per_scan_aud}</p>
                                                        </div>
                                                        <div className="rounded-lg bg-muted/40 p-2">
                                                            <p className="text-muted-foreground mb-0.5">Est. monthly
                                                                ({ocrSettings.monthly_scans} scans)</p>
                                                            <p className="font-medium text-foreground">{provider.est_monthly_cost_aud}</p>
                                                        </div>
                                                    </div>

                                                    <div className="flex flex-wrap gap-1 pt-1">
                                                        {provider.strengths.map(s => (
                                                            <span key={s}
                                                                  className="text-xs bg-muted px-2 py-0.5 rounded-full text-muted-foreground">{s}</span>
                                                        ))}
                                                    </div>

                                                    {provider.requires_api_key && (
                                                        <p className="text-xs text-amber-600 flex items-center gap-1 pt-1">
                                                            <Info className="h-3 w-3 flex-shrink-0"/>
                                                            Requires <code>{provider.api_key_env}</code> environment
                                                            variable
                                                        </p>
                                                    )}
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}

                            {ocrSettings.available_providers.length > 0 && (
                                <p className="mt-4 text-xs text-muted-foreground">
                                    <strong>Note:</strong> Cost estimates assume {ocrSettings.monthly_scans} scans/month
                                    based on your building's upload history
                                    (default 5/month if no history exists). Claude Vision: ~AUD $0.02–$0.05/scan based
                                    on claude-sonnet-4-6 pricing.
                                    Mindee: free up to 250 pages/month on their starter tier.
                                    Tesseract: runs locally on your server with no external API costs.
                                </p>
                            )}
                        </CardContent>
                    </Card>

                    {/* Global app default — super_admin only */}
                    {isSuperAdmin && ocrSettings.global_default !== null && (
                        <Card className="border-none shadow-md overflow-hidden bg-card/50 backdrop-blur-sm">
                            <CardHeader className="bg-muted/30 pb-4">
                                <div className="flex items-center gap-2">
                                    <Zap className="h-5 w-5 text-primary"/>
                                    <CardTitle>App-Wide OCR Default</CardTitle>
                                </div>
                                <CardDescription>
                                    Sets the default OCR engine for all buildings that have not configured a
                                    per-building preference.
                                    Only visible to Super Admins.
                                </CardDescription>
                            </CardHeader>
                            <CardContent className="pt-6 space-y-4">
                                <div className="flex items-center gap-4 flex-wrap">
                                    <div className="flex-1 min-w-0 max-w-xs space-y-2">
                                        <Label htmlFor="global-ocr-select">Application Default OCR Engine</Label>
                                        <Select
                                            value={ocrSettings.global_default || 'auto'}
                                            onValueChange={val => setOcrSettings(prev => ( {
                                                ...prev,
                                                global_default: val
                                            } ))}
                                        >
                                            <SelectTrigger id="global-ocr-select" className="w-full">
                                                <SelectValue placeholder="Select default engine"/>
                                            </SelectTrigger>
                                            <SelectContent>
                                                <SelectItem value="auto">Auto (Waterfall — Recommended)</SelectItem>
                                                <SelectItem value="claude_vision">Claude Vision (Anthropic)</SelectItem>
                                                <SelectItem value="mindee">Mindee Invoice AI</SelectItem>
                                                <SelectItem value="tesseract">Tesseract (Local / Free)</SelectItem>
                                            </SelectContent>
                                        </Select>
                                        <p className="text-xs text-muted-foreground">
                                            This is the fallback for any building without its own OCR preference above.
                                        </p>
                                    </div>
                                    <Button
                                        onClick={() => handleGlobalOcrSave(ocrSettings.global_default || 'auto')}
                                        disabled={savingGlobalOcr}
                                        className="rounded-full"
                                        data-testid="save-global-ocr"
                                    >
                                        {savingGlobalOcr ? <Loader2 className="h-4 w-4 animate-spin mr-2"/> : null}
                                        Save App Default
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>
                    )}
                </TabsContent>
            </Tabs>
        </div>
    );
};

export default SettingsPage;
