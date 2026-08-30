"use client";
import React, { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useAuth } from '../../contexts/AuthContext';
import { Badge } from '../../components/ui/badge';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Switch } from '../../components/ui/switch';
import {
    AlertTriangle,
    ArrowRight,
    Building2,
    Car,
    CheckCircle2,
    ChevronDown,
    ChevronUp,
    ClipboardList,
    Droplets,
    Edit,
    ExternalLink,
    FileText,
    Flame,
    GlassWater,
    Info,
    KeyRound,
    Loader2,
    Lock,
    Mail,
    MapPin,
    Paintbrush,
    Phone,
    Plus,
    Search,
    Shield,
    ShieldAlert,
    Thermometer,
    Trash2,
    Users,
    Wrench,
    Zap
} from 'lucide-react';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle
} from '../../components/ui/dialog';
import { toast } from 'sonner';
// ─── Helper: strip non-digits for tel: links ────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: toTelLink
 * Path: frontend/src/pages/public/EmergencyServicesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const toTelLink = (phone) => `tel:${phone.replace(/[^0-9+]/g, '')}`;

// ─── Trade icon + colour mapping ─────────────────────────────────────────────
const TRADE_CONFIG = {
    electrical: {icon: Zap, colour: 'amber', label: 'Electrical'},
    plumbing: {icon: Droplets, colour: 'cyan', label: 'Plumbing'},
    locksmith: {icon: KeyRound, colour: 'indigo', label: 'Locksmith'},
    painting: {icon: Paintbrush, colour: 'orange', label: 'Painting'},
    glass: {icon: GlassWater, colour: 'sky', label: 'Glass'},
    hotwater: {icon: Thermometer, colour: 'rose', label: 'Hot Water'},
    lift: {icon: Building2, colour: 'blue', label: 'Lift'},
    security: {icon: Shield, colour: 'slate', label: 'Security'},
    fire: {icon: Flame, colour: 'red', label: 'Fire'},
    general: {icon: Wrench, colour: 'stone', label: 'General'},
};

const COLOUR_CLASSES = {
    amber: {
        bg: 'bg-amber-50',
        border: 'border-amber-200',
        icon: 'bg-amber-100 text-amber-700',
        btn: 'bg-amber-600 hover:bg-amber-700',
        badge: 'bg-amber-100 text-amber-800'
    },
    cyan: {
        bg: 'bg-cyan-50',
        border: 'border-cyan-200',
        icon: 'bg-cyan-100 text-cyan-700',
        btn: 'bg-cyan-600 hover:bg-cyan-700',
        badge: 'bg-cyan-100 text-cyan-800'
    },
    indigo: {
        bg: 'bg-indigo-50',
        border: 'border-indigo-200',
        icon: 'bg-indigo-100 text-indigo-700',
        btn: 'bg-indigo-600 hover:bg-indigo-700',
        badge: 'bg-indigo-100 text-indigo-800'
    },
    orange: {
        bg: 'bg-orange-50',
        border: 'border-orange-200',
        icon: 'bg-orange-100 text-orange-700',
        btn: 'bg-orange-600 hover:bg-orange-700',
        badge: 'bg-orange-100 text-orange-800'
    },
    sky: {
        bg: 'bg-sky-50',
        border: 'border-sky-200',
        icon: 'bg-sky-100 text-sky-700',
        btn: 'bg-sky-600 hover:bg-sky-700',
        badge: 'bg-sky-100 text-sky-800'
    },
    rose: {
        bg: 'bg-rose-50',
        border: 'border-rose-200',
        icon: 'bg-rose-100 text-rose-700',
        btn: 'bg-rose-600 hover:bg-rose-700',
        badge: 'bg-rose-100 text-rose-800'
    },
    blue: {
        bg: 'bg-blue-50',
        border: 'border-blue-200',
        icon: 'bg-blue-100 text-blue-700',
        btn: 'bg-blue-600 hover:bg-blue-700',
        badge: 'bg-blue-100 text-blue-800'
    },
    slate: {
        bg: 'bg-slate-50',
        border: 'border-slate-200',
        icon: 'bg-slate-100 text-slate-700',
        btn: 'bg-slate-600 hover:bg-slate-700',
        badge: 'bg-slate-100 text-slate-800'
    },
    red: {
        bg: 'bg-red-50',
        border: 'border-red-200',
        icon: 'bg-red-100 text-red-700',
        btn: 'bg-red-600 hover:bg-red-700',
        badge: 'bg-red-100 text-red-800'
    },
    stone: {
        bg: 'bg-stone-50',
        border: 'border-stone-200',
        icon: 'bg-stone-100 text-stone-700',
        btn: 'bg-stone-600 hover:bg-stone-700',
        badge: 'bg-stone-100 text-stone-800'
    },
};
// ─── Classify a service into a trade group ───────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: classifyService
 * Path: frontend/src/pages/public/EmergencyServicesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function classifyService(service) {
    const n = service.name.toLowerCase();
    if (n.includes('electric')) return 'electrical';
    if (n.includes('plumb') || n.includes("jim's")) return 'plumbing';
    if (n.includes('lock')) return 'locksmith';
    if (n.includes('paint')) return 'painting';
    if (n.includes('glass')) return 'glass';
    if (n.includes('hot water') || n.includes('stiebel')) return 'hotwater';
    if (n.includes('lift') || n.includes('electra')) return 'lift';
    if (n.includes('security') || n.includes('cctv') || n.includes('cxi')) return 'security';
    if (service.category === 'fire') return 'fire';
    return 'general';
}

// ─── Smart Emergency Guide scenarios ─────────────────────────────────────────
const SMART_GUIDE = [
    {
        id: 'fire',
        emoji: '🔥',
        problem: 'Fire or gas smell',
        advice: 'Evacuate immediately. Do not use lifts.',
        urgency: 'critical',
        contacts: [{name: 'Triple Zero', phone: '000', label: 'CALL 000 NOW'}],
    },
    {
        id: 'water',
        emoji: '💧',
        problem: 'Water leak or burst pipe',
        advice: 'Turn off the water at the mains if safe to do so.',
        urgency: 'high',
        sectionTarget: 'plumbing',
        contacts: [
            {name: 'Level Plumbing', phone: '6185 0341'},
            {name: "Jim's Plumbing", phone: '13 15 46', badge: '24/7'},
        ],
    },
    {
        id: 'power',
        emoji: '⚡',
        problem: 'Power outage or electrical fault',
        advice: 'Do not touch exposed wires. Check your switchboard first.',
        urgency: 'high',
        sectionTarget: 'electrical',
        contacts: [
            {name: 'GMH Electrical', phone: '0418 623 046'},
            {name: 'Lee Madden', phone: '0400 913 440'},
        ],
    },
    {
        id: 'locked',
        emoji: '🔑',
        problem: 'Locked out of unit',
        advice: 'Both locksmiths provide 24/7 emergency access.',
        urgency: 'medium',
        sectionTarget: 'locksmith',
        contacts: [
            {name: 'Canberra Locksmiths', phone: '6285 3544', badge: '24/7'},
            {name: 'ACT Mobile Locksmith', phone: '1800 167 420', badge: '24/7'},
        ],
    },
    {
        id: 'lift',
        emoji: '🛗',
        problem: 'Trapped in lift',
        advice: 'Stay calm. Use the emergency button inside the lift.',
        urgency: 'high',
        sectionTarget: 'lift',
        contacts: [
            {name: 'Electra Lift Co', phone: '1300 725 290', badge: '24/7'},
            {name: 'Fire Brigade (if trapped)', phone: '000'},
        ],
    },
];
// ─── Main Component ──────────────────────────────────────────────────────────
/**
 * @generated FunctionHeader
 * Function: EmergencyServicesPage
 * Path: frontend/src/pages/public/EmergencyServicesPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const EmergencyServicesPage = () => {
    const {api, isAuthenticated, user, selectedBuilding} = useAuth();
    const [services, setServices] = useState([]);
    const [searchTerm, setSearchTerm] = useState('');
    const [loading, setLoading] = useState(true);
    const [openGuide, setOpenGuide] = useState(null);
    const [buildingSettings, setBuildingSettings] = useState(null);

    useEffect(() => {
        api.get('/settings').then(res => setBuildingSettings(res.data)).catch(() => {});
    }, [api, selectedBuilding?.id]);

    // Admin CRUD
    const [isDialogOpen, setIsDialogOpen] = useState(false);
    const [currentService, setCurrentService] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const [formData, setFormData] = useState({
        name: '', category: 'utility', phone: '', description: '',
        address: '', is_24_7: false, is_private: false, order: 0
    });

    const canManage = user && ['super_admin', 'ec_member'].includes(user.role);

    // Section refs for scroll-to
    const sectionRefs = {
        plumbing: useRef(null),
        electrical: useRef(null),
        locksmith: useRef(null),
        lift: useRef(null),
    };
    /**
     * @generated FunctionHeader
     * Function: scrollToSection
     * Path: frontend/src/pages/public/EmergencyServicesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const scrollToSection = (target) => {
        if (target && sectionRefs[ target ]?.current) {
            sectionRefs[ target ].current.scrollIntoView({behavior: 'smooth', block: 'start'});
        }
    };

    const fetchServices = useCallback(async () => {
        setLoading(true);
        try {
            const buildingId = selectedBuilding?.building_id || selectedBuilding?.id || '';
            const headers = {'X-Building-ID': buildingId};
            const fetcher = isAuthenticated ? api : {

                get: (url, config) => axios.get(`${process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000'}/api${url}`, {
                    ...config,
                    headers
                })
            };

            const response = await api.get('/emergency-services');
            setServices(response.data || []);
        } catch {
            toast.error('Failed to load emergency services');
        } finally {
            setLoading(false);
        }
    }, [api, selectedBuilding, isAuthenticated]);

    useEffect(() => {
        fetchServices();
    }, [fetchServices, selectedBuilding?.id]);

    // ── Filter services ──────────────────────────────────────────────────────
    const filtered = services.filter(s => {
        const t = searchTerm.toLowerCase();
        return s.name.toLowerCase().includes(t) ||
            s.description?.toLowerCase().includes(t) ||
            s.category?.toLowerCase().includes(t) ||
            s.phone?.includes(t);
    });
    /**
     * @generated FunctionHeader
     * Function: byCategory
     * Path: frontend/src/pages/public/EmergencyServicesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const byCategory = (cats) => filtered.filter(s => cats.includes(s.category));
    const managementServices = byCategory(['management']);
    const emergencyServices = filtered.filter(s => ['fire', 'police'].includes(s.category) && s.order < 10);
    const liftServices = filtered.filter(s => classifyService(s) === 'lift');
    const securityServices = filtered.filter(s => classifyService(s) === 'security');
    const electricalServices = filtered.filter(s => classifyService(s) === 'electrical');
    const plumbingServices = filtered.filter(s => classifyService(s) === 'plumbing');
    const locksmithServices = filtered.filter(s => classifyService(s) === 'locksmith');
    const paintingServices = filtered.filter(s => classifyService(s) === 'painting');
    const glassServices = filtered.filter(s => classifyService(s) === 'glass');
    const hotWaterServices = filtered.filter(s => classifyService(s) === 'hotwater');
    const generalServices = filtered.filter(s => s.category === 'contractor' && classifyService(s) === 'general');
    const fireSystemServices = filtered.filter(s => s.category === 'fire' && s.order >= 30);
    // ── CRUD handlers ────────────────────────────────────────────────────────
    /**
     * @generated FunctionHeader
     * Function: handleOpenDialog
     * Path: frontend/src/pages/public/EmergencyServicesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleOpenDialog = (service = null) => {
        if (service) {
            setCurrentService(service);
            setFormData({
                name: service.name, category: service.category, phone: service.phone,
                description: service.description || '', address: service.address || '',
                is_24_7: service.is_24_7, is_private: service.is_private, order: service.order || 0
            });
        } else {
            setCurrentService(null);
            setFormData({
                name: '',
                category: 'utility',
                phone: '',
                description: '',
                address: '',
                is_24_7: false,
                is_private: false,
                order: 0
            });
        }
        setIsDialogOpen(true);
    };
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/public/EmergencyServicesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async (e) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            if (currentService) {
                await api.put(`/emergency-services/${currentService.id}`, formData);
                toast.success('Contact updated');
            } else {
                await api.post('/emergency-services', formData);
                toast.success('Contact added');
            }
            setIsDialogOpen(false);
            fetchServices();
        } catch {
            toast.error('Failed to save contact');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleDelete
     * Path: frontend/src/pages/public/EmergencyServicesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleDelete = async (id) => {
        if (!window.confirm('Delete this contact?')) return;
        try {
            await api.delete(`/emergency-services/${id}`);
            toast.success('Contact deleted');
            fetchServices();
        } catch {
            toast.error('Failed to delete');
        }
    };
    // ── Sub-components ───────────────────────────────────────────────────────

    /* Single contractor card */
    /**
     * @generated FunctionHeader
     * Function: TradeCard
     * Path: frontend/src/pages/public/EmergencyServicesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const TradeCard = ({service, trade = 'general'}) => {
        const config = TRADE_CONFIG[ trade ] || TRADE_CONFIG.general;
        const colours = COLOUR_CLASSES[ config.colour ];
        const Icon = config.icon;
        return (
            <div
                className={`rounded-xl border ${colours.border} ${colours.bg} p-5 flex flex-col gap-3 relative group transition-shadow hover:shadow-md`}>
                {canManage && (
                    <div
                        className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button onClick={() => handleOpenDialog(service)} className="p-1 rounded hover:bg-white/80">
                            <Edit className="h-3 w-3 text-slate-500"/>
                        </button>
                        <button onClick={() => handleDelete(service.id)} className="p-1 rounded hover:bg-white/80">
                            <Trash2 className="h-3 w-3 text-red-400"/>
                        </button>
                    </div>
                )}
                <div className="flex items-start gap-3">
                    <div
                        className={`w-10 h-10 rounded-lg ${colours.icon} flex items-center justify-center flex-shrink-0`}>
                        <Icon className="h-5 w-5"/>
                    </div>
                    <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                            <h3 className="font-semibold text-sm text-slate-900 leading-tight">{service.name}</h3>
                            {service.is_24_7 && (
                                <span
                                    className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-green-100 text-green-700 border border-green-200">24/7</span>
                            )}
                        </div>
                        {service.description && (
                            <p className="text-xs text-slate-500 mt-0.5 leading-snug">{service.description}</p>
                        )}
                    </div>
                </div>
                <a
                    href={toTelLink(service.phone)}
                    className={`flex items-center justify-center gap-2 py-2.5 rounded-lg text-white text-sm font-semibold ${colours.btn} transition-transform active:scale-95`}
                    data-testid={`call-${service.id}`}
                >
                    <Phone className="h-4 w-4"/>
                    {service.phone}
                </a>
                {service.address && (
                    <div className="flex items-start gap-1.5 text-xs text-slate-400 border-t border-slate-100 pt-2">
                        <MapPin className="h-3 w-3 flex-shrink-0 mt-0.5"/>
                        {service.address}
                    </div>
                )}
            </div>
        );
    };
    /* Trade group section */
    /**
     * @generated FunctionHeader
     * Function: TradeSection
     * Path: frontend/src/pages/public/EmergencyServicesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const TradeSection = ({title, trade, services: svcs, sectionRef}) => {
        if (svcs.length === 0) return null;
        const config = TRADE_CONFIG[ trade ] || TRADE_CONFIG.general;
        const colours = COLOUR_CLASSES[ config.colour ];
        const Icon = config.icon;
        return (
            <div ref={sectionRef}>
                <div className="flex items-center gap-2 mb-4">
                    <div className={`w-8 h-8 rounded-lg ${colours.icon} flex items-center justify-center`}>
                        <Icon className="h-4 w-4"/>
                    </div>
                    <h3 className="text-base font-bold text-slate-800">{title}</h3>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
                    {svcs.map(s => <TradeCard key={s.id} service={s} trade={trade}/>)}
                </div>
            </div>
        );
    };
    /* Management contact card */
    /**
     * @generated FunctionHeader
     * Function: ManagementCard
     * Path: frontend/src/pages/public/EmergencyServicesPage.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const ManagementCard = ({service}) => (
        <div
            className="rounded-xl border border-amber-200 bg-amber-50/60 p-5 flex flex-col gap-3 relative group hover:shadow-md transition-shadow">
            {canManage && (
                <div className="absolute top-2 right-2 flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <button onClick={() => handleOpenDialog(service)} className="p-1 rounded hover:bg-white/80"><Edit
                        className="h-3 w-3 text-slate-500"/></button>
                    <button onClick={() => handleDelete(service.id)} className="p-1 rounded hover:bg-white/80"><Trash2
                        className="h-3 w-3 text-red-400"/></button>
                </div>
            )}
            <div className="flex items-center justify-between">
                <h3 className="font-semibold text-slate-900 text-sm pr-8">{service.name}</h3>
                {service.is_24_7 && <span
                    className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-green-100 text-green-700 border border-green-200 flex-shrink-0">24/7</span>}
            </div>
            {service.description && <p className="text-xs text-slate-500 leading-snug">{service.description}</p>}
            <div className="flex flex-col gap-2">
                <a href={toTelLink(service.phone)}
                   className="flex items-center justify-center gap-2 py-2.5 rounded-lg text-white text-sm font-semibold bg-[#2F4F4F] hover:bg-[#1e3333] transition-transform active:scale-95">
                    <Phone className="h-4 w-4"/> {service.phone}
                </a>
                {service.email && (
                    <a href={`mailto:${service.email}`}
                       className="flex items-center justify-center gap-2 py-2 rounded-lg border border-amber-300 bg-white text-amber-800 text-xs font-medium hover:bg-amber-50 transition-colors">
                        <Mail className="h-3.5 w-3.5"/> {service.email}
                    </a>
                )}
            </div>
        </div>
    );

    // ── Render ───────────────────────────────────────────────────────────────
    return (
        <div className="min-h-screen bg-slate-50 pb-24" data-testid="emergency-services-page">

            {/* ── Sticky Emergency Bar ─────────────────────────────────────────── */}
            <div className="sticky top-0 z-50 bg-red-600 shadow-lg">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-3">
                    <div className="flex flex-col sm:flex-row items-center justify-center gap-3 sm:gap-8">
                        <div className="flex items-center gap-2">
                            <ShieldAlert className="h-5 w-5 text-white/80 flex-shrink-0"/>
                            <span className="text-white/90 font-semibold text-sm">LIFE THREATENING EMERGENCY</span>
                        </div>
                        <div className="flex items-center gap-3 flex-wrap justify-center">
                            <a href="tel:000"
                               className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-white text-red-700 text-base font-black hover:scale-105 transition-transform shadow-sm">
                                <Phone className="h-4 w-4"/> 000
                            </a>
                            <a href="tel:131444"
                               className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white/20 border border-white/30 text-white text-sm font-bold hover:bg-white/30 transition-colors">
                                <Shield className="h-3.5 w-3.5"/> Police 131 444
                            </a>
                            {buildingSettings?.contact_phone && (
                                <a href={`tel:${buildingSettings.contact_phone.replace(/\s/g, '')}`}
                                   className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white/20 border border-white/30 text-white text-sm font-bold hover:bg-white/30 transition-colors">
                                    <Building2 className="h-3.5 w-3.5"/> Strata {buildingSettings.contact_phone}
                                </a>
                            )}
                        </div>
                    </div>
                </div>
            </div>

            {/* ── Hero ─────────────────────────────────────────────────────────── */}
            <section className="relative bg-slate-900 text-white py-14 overflow-hidden">
                {/* decorative gradient */}
                <div
                    className="absolute inset-0 bg-gradient-to-br from-slate-900 via-slate-800 to-[#2F4F4F] opacity-80"/>
                <div
                    className="absolute top-0 right-0 hidden h-72 w-72 -translate-y-1/2 translate-x-1/4 rounded-full bg-red-600/10 blur-3xl sm:block"/>
                <div
                    className="absolute bottom-0 left-0 hidden h-48 w-96 -translate-x-1/4 translate-y-1/2 rounded-full bg-[#2F4F4F]/20 blur-3xl sm:block"/>

                <div className="relative max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                    <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-8">
                        <div>
                            <div className="flex items-center gap-3 mb-3">
                                <div
                                    className="w-12 h-12 rounded-xl bg-red-600/20 border border-red-500/30 flex items-center justify-center">
                                    <AlertTriangle className="h-6 w-6 text-red-400"/>
                                </div>
                                <div>
                                    <h1 className="text-3xl sm:text-4xl font-black tracking-tight">Resident Toolkit</h1>
                                    <p className="text-slate-400 text-sm">Emergency & Essential Services</p>
                                </div>
                            </div>
                            <div className="flex items-center gap-3 text-slate-400 text-sm mt-4">
                                <MapPin className="h-4 w-4 text-slate-500 flex-shrink-0"/>
                                <span>{selectedBuilding?.address || 'Community Location'}</span>
                                {selectedBuilding?.building_id && (
                                    <span
                                        className="px-2 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-400 text-xs">Units Plan {selectedBuilding.building_id}</span>
                                )}
                            </div>
                        </div>
                        <div className="flex items-center gap-3 flex-wrap">
                            {canManage && (
                                <Button onClick={() => handleOpenDialog()}
                                        className="bg-white/10 border border-white/20 text-white hover:bg-white/20 gap-2">
                                    <Plus className="h-4 w-4"/> Add Contact
                                </Button>
                            )}
                            <Link href="/maintenance"
                               className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-[#2F4F4F]/60 border border-white/10 text-white/80 text-sm hover:bg-[#2F4F4F] transition-colors">
                                <ClipboardList className="h-4 w-4"/> Report Maintenance
                            </Link>
                        </div>
                    </div>

                    {/* Search */}
                    <div className="mt-8 max-w-xl">
                        <div className="relative">
                            <Search className="absolute left-3.5 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-400"/>
                            <input
                                type="text"
                                placeholder="Search contacts, services or trades…"
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                                className="w-full pl-10 pr-4 py-3 rounded-xl bg-white/10 border border-white/20 text-white placeholder-slate-400 text-sm focus:outline-none focus:ring-2 focus:ring-white/30 focus:bg-white/15"
                            />
                        </div>
                    </div>
                </div>
            </section>

            <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-10 space-y-14">

                {/* ── Smart Emergency Guide ──────────────────────────────────────── */}
                <section>
                    <div className="flex items-center gap-2 mb-6">
                        <div className="w-8 h-8 rounded-lg bg-red-100 text-red-700 flex items-center justify-center">
                            <AlertTriangle className="h-4 w-4"/>
                        </div>
                        <h2 className="text-xl font-bold text-slate-900">Smart Emergency Guide</h2>
                        <span className="text-xs text-slate-400 ml-1">— tap your situation</span>
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">
                        {SMART_GUIDE.map(guide => {
                            const urgencyColour = guide.urgency === 'critical' ? 'border-red-300 bg-red-50' :
                                guide.urgency === 'high' ? 'border-orange-200 bg-orange-50' :
                                    'border-amber-200 bg-amber-50';
                            const isOpen = openGuide === guide.id;
                            return (
                                <div key={guide.id}
                                     className={`rounded-xl border-2 ${urgencyColour} overflow-hidden transition-all`}>
                                    <button
                                        className="w-full p-4 text-left flex items-center gap-3"
                                        onClick={() => setOpenGuide(isOpen ? null : guide.id)}>
                                        <span className="text-2xl">{guide.emoji}</span>
                                        <span
                                            className="font-semibold text-slate-800 text-sm flex-1 leading-tight">{guide.problem}</span>
                                        {isOpen ? <ChevronUp className="h-4 w-4 text-slate-400 flex-shrink-0"/>
                                            : <ChevronDown className="h-4 w-4 text-slate-400 flex-shrink-0"/>}
                                    </button>
                                    {isOpen && (
                                        <div className="px-4 pb-4 space-y-3">
                                            <p className="text-xs text-slate-600 bg-white/70 rounded-lg px-3 py-2 leading-snug">
                                                <Info className="h-3 w-3 inline mr-1 text-blue-500"/>
                                                {guide.advice}
                                            </p>
                                            {guide.contacts.map((c, i) => (
                                                <div key={i} className="flex items-center gap-2">
                                                    <a href={toTelLink(c.phone)}
                                                       className={`flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg text-white text-sm font-bold transition-transform active:scale-95 ${guide.urgency === 'critical' ? 'bg-red-600 hover:bg-red-700' : 'bg-slate-700 hover:bg-slate-800'}`}>
                                                        <Phone className="h-3.5 w-3.5"/>
                                                        {c.label || `Call ${c.name}`}
                                                    </a>
                                                    {c.badge && (
                                                        <span
                                                            className="text-[10px] font-bold px-1.5 py-0.5 rounded bg-green-100 text-green-700 border border-green-200 flex-shrink-0">{c.badge}</span>
                                                    )}
                                                </div>
                                            ))}
                                            {guide.sectionTarget && (
                                                <button onClick={() => {
                                                    setOpenGuide(null);
                                                    scrollToSection(guide.sectionTarget);
                                                }}
                                                        className="w-full text-xs text-slate-500 hover:text-slate-700 flex items-center justify-center gap-1 pt-1">
                                                    View all contacts <ArrowRight className="h-3 w-3"/>
                                                </button>
                                            )}
                                        </div>
                                    )}
                                </div>
                            );
                        })}
                    </div>
                </section>

                {/* ── Strata & Management (private) ─────────────────────────────── */}
                <section>
                    <div className="flex items-center gap-3 mb-6">
                        <div
                            className="w-8 h-8 rounded-lg bg-[#2F4F4F]/10 text-[#2F4F4F] flex items-center justify-center">
                            <Building2 className="h-4 w-4"/>
                        </div>
                        <h2 className="text-xl font-bold text-slate-900">Strata & Management</h2>
                        <Badge variant="outline" className="bg-amber-50 border-amber-200 text-amber-700 gap-1">
                            <Lock className="h-3 w-3"/> Residents Only
                        </Badge>
                    </div>

                    {isAuthenticated ? (
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
                            {managementServices.map(s => (
                                <ManagementCard key={s.id} service={s}/>
                            ))}
                        </div>
                    ) : (
                        <div className="rounded-xl border-2 border-dashed border-amber-200 bg-amber-50 p-8 text-center">
                            <Lock className="h-10 w-10 text-amber-400 mx-auto mb-3"/>
                            <h3 className="font-bold text-amber-900 mb-1">Strata Management Details</h3>
                            <p className="text-amber-700 text-sm max-w-sm mx-auto mb-4">
                                Strata and Owners Corporation contacts are only visible to registered residents.
                            </p>
                            <a href="/login"
                               className="inline-flex items-center gap-2 px-5 py-2 rounded-lg bg-[#2F4F4F] text-white text-sm font-semibold hover:bg-[#1e3333] transition-colors">
                                Login to View <ArrowRight className="h-4 w-4"/>
                            </a>
                        </div>
                    )}
                </section>

                {/* ── Contractor Directory ───────────────────────────────────────── */}
                {loading ? (
                    <div className="flex items-center justify-center py-20">
                        <Loader2 className="h-10 w-10 animate-spin text-slate-300"/>
                    </div>
                ) : (
                    <section>
                        <div className="flex items-center gap-2 mb-8">
                            <div
                                className="w-8 h-8 rounded-lg bg-slate-100 text-slate-600 flex items-center justify-center">
                                <Wrench className="h-4 w-4"/>
                            </div>
                            <h2 className="text-xl font-bold text-slate-900">Contractor Directory</h2>
                            {searchTerm && (
                                <span className="text-sm text-slate-500">— results for "{searchTerm}"</span>
                            )}
                        </div>

                        <div className="space-y-10">
                            <TradeSection title="Electrical" trade="electrical" services={electricalServices}
                                          sectionRef={sectionRefs.electrical}/>
                            <TradeSection title="Plumbing" trade="plumbing" services={plumbingServices}
                                          sectionRef={sectionRefs.plumbing}/>
                            <TradeSection title="Locksmith" trade="locksmith" services={locksmithServices}
                                          sectionRef={sectionRefs.locksmith}/>
                            <TradeSection title="Lift Services" trade="lift" services={liftServices}
                                          sectionRef={sectionRefs.lift}/>
                            <TradeSection title="Security & CCTV" trade="security" services={securityServices}/>
                            <TradeSection title="Painting" trade="painting" services={paintingServices}/>
                            <TradeSection title="Glass Repair" trade="glass" services={glassServices}/>
                            <TradeSection title="Hot Water" trade="hotwater" services={hotWaterServices}/>
                            <TradeSection title="Fire Systems" trade="fire" services={fireSystemServices}/>
                            <TradeSection title="General Services" trade="general" services={generalServices}/>
                        </div>

                        {electricalServices.length === 0 && plumbingServices.length === 0 &&
                            locksmithServices.length === 0 && searchTerm && (
                                <div className="text-center py-12 text-slate-400">
                                    <Search className="h-8 w-8 mx-auto mb-2 opacity-40"/>
                                    <p>No contacts matched "<strong>{searchTerm}</strong>"</p>
                                    <button onClick={() => setSearchTerm('')}
                                            className="mt-2 text-sm text-blue-500 hover:underline">Clear search
                                    </button>
                                </div>
                            )}
                    </section>
                )}

                {/* ── Building Resources ─────────────────────────────────────────── */}
                <section>
                    <div className="flex items-center gap-2 mb-6">
                        <div className="w-8 h-8 rounded-lg bg-blue-100 text-blue-700 flex items-center justify-center">
                            <FileText className="h-4 w-4"/>
                        </div>
                        <h2 className="text-xl font-bold text-slate-900">Building Resources</h2>
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                        {[
                            {
                                icon: ClipboardList,
                                title: 'Report Maintenance Issue',
                                desc: 'Submit a non-urgent repair or maintenance request through your dashboard',
                                href: '/maintenance',
                                colour: 'blue',
                                authRequired: true
                            },
                            {
                                icon: FileText,
                                title: 'Building By-laws',
                                desc: `Rules and regulations applicable to all ${selectedBuilding?.name || 'community'} residents`,
                                href: '/documents',
                                colour: 'amber',
                                authRequired: true
                            },
                            {
                                icon: ShieldAlert,
                                title: 'Building Fire & Evacuation Plan',
                                desc: 'Fire evacuation routes, assembly points, and warden information',
                                href: '/documents',
                                colour: 'red',
                                authRequired: true
                            },
                            {
                                icon: Users,
                                title: 'Approved Contractor List',
                                desc: 'Vetted contractors for in-unit maintenance and repairs',
                                href: '/maintenance?tab=contractors',
                                colour: 'slate',
                                authRequired: true
                            },
                        ].map(({icon: Icon, title, desc, href, colour, authRequired}) => {
                            const colours = COLOUR_CLASSES[ colour ] || COLOUR_CLASSES.slate;
                            const isLocked = authRequired && !isAuthenticated;
                            return isLocked ? (
                                <div key={title}
                                     className={`rounded-xl border ${colours.border} ${colours.bg} p-5 flex flex-col gap-3 opacity-70`}>
                                    <div
                                        className={`w-10 h-10 rounded-lg ${colours.icon} flex items-center justify-center`}>
                                        <Lock className="h-5 w-5"/>
                                    </div>
                                    <div>
                                        <h3 className="font-semibold text-slate-900 text-sm">{title}</h3>
                                        <p className="text-xs text-slate-500 mt-0.5">Login to access this resource.</p>
                                    </div>
                                    <a href="/login"
                                       className="flex items-center gap-1 text-xs font-medium text-blue-500 hover:underline mt-auto">
                                        Login <ArrowRight className="h-3 w-3"/>
                                    </a>
                                </div>
                            ) : (
                                <a key={title} href={href}
                                   className={`rounded-xl border ${colours.border} ${colours.bg} p-5 flex flex-col gap-3 hover:shadow-md transition-all group`}>
                                    <div
                                        className={`w-10 h-10 rounded-lg ${colours.icon} flex items-center justify-center`}>
                                        <Icon className="h-5 w-5"/>
                                    </div>
                                    <div>
                                        <h3 className="font-semibold text-slate-900 text-sm group-hover:underline">{title}</h3>
                                        <p className="text-xs text-slate-500 mt-0.5">{desc}</p>
                                    </div>
                                    <div
                                        className="flex items-center gap-1 text-xs font-medium text-slate-400 group-hover:text-slate-600 transition-colors mt-auto">
                                        Open <ArrowRight className="h-3 w-3"/>
                                    </div>
                                </a>
                            );
                        })}
                    </div>
                </section>

                {/* ── Visitor Parking Notice ─────────────────────────────────────── */}
                <section>
                    <div
                        className="rounded-xl border-2 border-amber-300 bg-gradient-to-r from-amber-50 to-orange-50 p-6 flex items-start gap-4">
                        <div
                            className="w-12 h-12 rounded-xl bg-amber-100 border border-amber-200 flex items-center justify-center flex-shrink-0">
                            <Car className="h-6 w-6 text-amber-700"/>
                        </div>
                        <div>
                            <h3 className="font-bold text-amber-900 text-base mb-1">Visitor Parking Notice</h3>
                            <p className="text-amber-800 text-sm leading-relaxed">
                                <strong>Reminder:</strong> Visitor car parks are for <strong>visitors
                                only.</strong>{' '}
                                Residents must use their allocated parking spaces. Unauthorised vehicles may be reported
                                to building management.
                            </p>
                        </div>
                    </div>
                </section>

                {/* ── Building Address (for sharing with contractors/visitors) ───── */}
                <section>
                    <div
                        className="rounded-xl border border-slate-200 bg-white shadow-sm p-5 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                        <div className="flex items-start gap-3">
                            <div
                                className="w-10 h-10 rounded-lg bg-slate-100 text-slate-600 flex items-center justify-center flex-shrink-0">
                                <MapPin className="h-5 w-5"/>
                            </div>
                            <div>
                                <p className="font-semibold text-slate-900 text-sm">{selectedBuilding?.name || 'Our Residences'}</p>
                                <p className="text-slate-600 text-sm">{selectedBuilding?.address || ''}</p>
                                <p className="text-slate-400 text-xs mt-0.5">
                                    {selectedBuilding?.building_id ? `Units Plan ${selectedBuilding.building_id} — ` : ''}
                                    share this address with contractors or visitors
                                </p>
                            </div>
                        </div>
                        {(buildingSettings?.building_address || selectedBuilding?.address) && (
                            <a href={`https://maps.google.com/?q=${encodeURIComponent(buildingSettings?.building_address || selectedBuilding?.address || '')}`}
                               target="_blank" rel="noopener noreferrer"
                               className="flex-shrink-0 inline-flex items-center gap-2 px-4 py-2 rounded-lg border border-slate-200 bg-slate-50 text-slate-700 text-sm font-medium hover:bg-slate-100 transition-colors">
                                <ExternalLink className="h-4 w-4"/> Get Directions
                            </a>
                        )}
                    </div>
                </section>

                {/* ── Tips & Notes ──────────────────────────────────────────────── */}
                <section className="rounded-xl border border-blue-100 bg-blue-50 p-6">
                    <div className="flex items-start gap-3">
                        <Info className="h-5 w-5 text-blue-500 flex-shrink-0 mt-0.5"/>
                        <div>
                            <h3 className="font-bold text-blue-900 mb-2">Maintenance Request Process</h3>
                            <ul className="space-y-1.5 text-blue-800 text-sm">
                                <li className="flex items-start gap-2"><CheckCircle2
                                    className="h-4 w-4 text-blue-400 flex-shrink-0 mt-0.5"/> For non-urgent repairs,
                                    submit a request via the <Link href="/maintenance"
                                                                className="font-bold underline hover:text-blue-900">Maintenance
                                        Tracker</Link> in your dashboard.
                                </li>
                                <li className="flex items-start gap-2"><CheckCircle2
                                    className="h-4 w-4 text-blue-400 flex-shrink-0 mt-0.5"/> For urgent issues during
                                    business hours, {buildingSettings?.contact_phone ? `call your strata manager on ${buildingSettings.contact_phone}.` : 'contact your strata manager.'}
                                </li>
                                <li className="flex items-start gap-2"><CheckCircle2
                                    className="h-4 w-4 text-blue-400 flex-shrink-0 mt-0.5"/> For after-hours building
                                    emergencies, {buildingSettings?.contact_phone ? `call ${buildingSettings.contact_phone} (additional charges may apply).` : 'contact your strata manager\'s after-hours line.'}
                                </li>
                                <li className="flex items-start gap-2"><CheckCircle2
                                    className="h-4 w-4 text-blue-400 flex-shrink-0 mt-0.5"/> All in-unit repairs are the
                                    owner's or tenant's responsibility unless they affect common property.
                                </li>
                            </ul>
                        </div>
                    </div>
                </section>
            </div>

            {/* ── Footer ────────────────────────────────────────────────────────── */}
            <footer className="bg-slate-100 border-t border-slate-200 py-8">
                <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 text-center">
                    <p className="text-slate-500 text-xs">
                        {selectedBuilding?.name || 'Our Residences'}
                        {selectedBuilding?.building_id ? ` • Units Plan ${selectedBuilding.building_id}` : ''}
                        {selectedBuilding?.address ? ` • ${selectedBuilding.address}` : ''}
                    </p>
                    <p className="text-slate-400 text-xs mt-1">
                        {isAuthenticated ? 'Contact your Super Admin to update these details.' : 'Some details are restricted to verified residents.'}
                    </p>
                </div>
            </footer>

            {/* ── Mobile floating emergency button (always visible when scrolled down) */}
            <a href="tel:000"
               className="fixed bottom-6 right-6 z-50 md:hidden flex items-center justify-center w-14 h-14 rounded-full bg-red-600 text-white shadow-2xl hover:bg-red-700 active:scale-95 transition-transform"
               aria-label="Call 000 emergency"
               data-testid="mobile-emergency-btn">
                <Phone className="h-6 w-6"/>
            </a>

            {/* ── Admin Add/Edit Dialog ──────────────────────────────────────────── */}
            <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
                <DialogContent className="max-w-md">
                    <DialogHeader>
                        <DialogTitle>{currentService ? 'Edit Contact' : 'Add New Contact'}</DialogTitle>
                        <DialogDescription>Manage emergency and service contact details.</DialogDescription>
                    </DialogHeader>
                    <form onSubmit={handleSubmit} className="space-y-4 py-4">
                        <div className="space-y-2">
                            <Label htmlFor="name">Name / Entity</Label>
                            <Input id="name" value={formData.name}
                                   onChange={(e) => setFormData({...formData, name: e.target.value})}
                                   placeholder="e.g., Electrician – Lee Madden" required/>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="category">Category</Label>
                                <select id="category"
                                        className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                        value={formData.category}
                                        onChange={(e) => setFormData({...formData, category: e.target.value})}>
                                    <option value="fire">Fire</option>
                                    <option value="police">Police / Security</option>
                                    <option value="medical">Medical</option>
                                    <option value="utility">Utility / Trade</option>
                                    <option value="building">Building</option>
                                    <option value="management">Management (Private)</option>
                                    <option value="contractor">Contractor</option>
                                </select>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="phone">Phone Number</Label>
                                <Input id="phone" value={formData.phone}
                                       onChange={(e) => setFormData({...formData, phone: e.target.value})}
                                       placeholder="000 or 1300…" required/>
                            </div>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="description">Description (Optional)</Label>
                            <Input id="description" value={formData.description}
                                   onChange={(e) => setFormData({...formData, description: e.target.value})}/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="address">Address (Optional)</Label>
                            <Input id="address" value={formData.address}
                                   onChange={(e) => setFormData({...formData, address: e.target.value})}/>
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="order">Sort Order</Label>
                            <Input id="order" type="number" value={formData.order}
                                   onChange={(e) => setFormData({...formData, order: parseInt(e.target.value) || 0})}/>
                        </div>
                        <div className="flex items-center justify-between p-3 border rounded-lg">
                            <Label>Available 24/7</Label>
                            <Switch checked={formData.is_24_7}
                                    onCheckedChange={(v) => setFormData({...formData, is_24_7: v})}/>
                        </div>
                        <div className="flex items-center justify-between p-3 border rounded-lg bg-amber-50">
                            <div>
                                <Label>Private Contact</Label>
                                <p className="text-[10px] text-amber-700 mt-0.5">Only visible to logged-in
                                    residents.</p>
                            </div>
                            <Switch checked={formData.is_private}
                                    onCheckedChange={(v) => setFormData({...formData, is_private: v})}/>
                        </div>
                        <DialogFooter>
                            <Button type="button" variant="outline"
                                    onClick={() => setIsDialogOpen(false)}>Cancel</Button>
                            <Button type="submit" disabled={submitting}>
                                {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin"/>}
                                {currentService ? 'Update' : 'Add Contact'}
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>
        </div>
    );
};

export default EmergencyServicesPage;
