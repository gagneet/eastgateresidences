import React, { useEffect, useMemo, useState } from 'react';
import { useAuth } from '../../contexts/AuthContext';
import { Card, CardContent, CardHeader, CardTitle } from '../../components/ui/card';
import { Badge } from '../../components/ui/badge';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '../../components/ui/tabs';
import { Alert, AlertDescription } from '../../components/ui/alert';
import {
    AlertTriangle,
    Building2,
    CheckCircle2,
    FileText,
    Flame,
    Info,
    Loader2,
    Phone,
    PhoneCall,
    Shield,
    XCircle,
} from 'lucide-react';

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';

// ─── Emergency Steps ──────────────────────────────────────────────────────────
const FIRE_STEPS = [
    {
        num: 1,
        text: 'Stay calm. Do NOT use the lifts.',
    },
    {
        num: 2,
        text: 'Check if your door is hot before opening. If hot, stay in your unit, seal the gap under the door with towels, and call 000.',
    },
    {
        num: 3,
        text: 'If safe to leave, close (do not lock) your door behind you and proceed to the nearest stairwell exit.',
    },
    {
        num: 4,
        text: 'Follow exit signs to the designated Assembly Area — the car park entrance on Hoolihan St.',
    },
    {
        num: 5,
        text: 'Do NOT re-enter the building until the Fire Brigade gives the all-clear.',
    },
    {
        num: 6,
        text: 'If you require mobility assistance, wait in the pressurised stairwell refuge area and alert the Fire Brigade warden.',
    },
];

// ─── Fire Detection Zone Schedule ────────────────────────────────────────────
const FIRE_ZONES = [
    {num: 1, label: 'Ground Floor South'},
    {num: 2, label: 'Ground Floor North'},
    {num: 3, label: 'Level One South'},
    {num: 4, label: 'Level One North'},
    {num: 5, label: 'Building A Level 2 South'},
    {num: 6, label: 'Building A Level 2 North'},
    {num: 7, label: 'Building B Level 2'},
    {num: 8, label: 'Building A Level 3 South'},
    {num: 9, label: 'Building A Level 3 North'},
    {num: 10, label: 'Building B Level 3 South'},
    {num: 11, label: 'Building B Level 3 North'},
    {num: 12, label: 'Building A Level 4 South'},
    {num: 13, label: 'Building A Level 4 North'},
    {num: 14, label: 'Building B Level 4 South'},
    {num: 15, label: 'Building B Level 4 North'},
    {num: 16, label: 'Building A Level 5 South'},
    {num: 17, label: 'Building A Level 5 North'},
    {num: 18, label: 'Building B Level 5 South'},
    {num: 19, label: 'Building B Level 5 North'},
    {num: 20, label: 'Building A Level 6 North'},
    {num: 21, label: 'Building B Level 6 South'},
    {num: 22, label: 'Building B Level 6 North'},
    {num: 23, label: 'Building B Level 7 North'},
    {num: 30, label: 'Building A South Lift Shaft'},
    {num: 31, label: 'Building A North Lift Shaft'},
    {num: 32, label: 'Building B South Lift Shaft'},
    {num: 33, label: 'Building B North Lift Shaft'},
];

// ─── Building Contacts ────────────────────────────────────────────────────────
const CONTACTS = [
    {
        category: 'emergency',
        title: 'Emergency Services',
        color: 'border-red-500 bg-red-50',
        titleColor: 'text-red-700',
        icon: Flame,
        iconColor: 'text-red-600',
        entries: [
            {
                label: 'Fire / Police / Ambulance',
                number: '000',
                note: 'All life-threatening emergencies — call first',
                urgent: true
            },
            {label: 'Non-Emergency Police', number: '131 444', note: 'Police assistance (non-urgent)'},
            {label: 'SES (storm / flood)', number: '132 500', note: 'State Emergency Service'},
            {label: 'Poison Information Centre', number: '13 11 26', note: '24/7 poisons advice'},
        ],
    },
    {
        category: 'building',
        title: 'Building Systems',
        color: 'border-orange-400 bg-orange-50',
        titleColor: 'text-orange-700',
        icon: Building2,
        iconColor: 'text-orange-600',
        entries: [
            {
                label: 'Lift Emergency — Electra Lift Co',
                number: '1300 725 290',
                note: 'If trapped in lift (24/7) — also call 000 if no response',
                urgent: true
            },
            {
                label: 'Fire System Monitoring — 360 Degree Fire',
                number: '02 6299 0006',
                note: 'Fire alarm faults (non-emergency); testing every 2nd Thursday 9 AM'
            },
            {
                label: 'Fire System Service — O\'Neill & Brown Fire',
                number: '(02) 6297 2022',
                note: 'Fire system maintenance and service'
            },
        ],
    },
    {
        category: 'strata',
        title: 'Strata & EC',
        color: 'border-blue-400 bg-blue-50',
        titleColor: 'text-blue-700',
        icon: Shield,
        iconColor: 'text-blue-600',
        entries: [
            {
                label: 'Strata Manager — Civium (Jessica Minichiello)',
                number: '1300 724 256',
                note: 'Building issues, by-law queries, after-hours emergencies',
                email: 'UP13195@civium.com.au'
            },
            {label: 'Direct Strata Line', number: '(02) 6285 0325', note: 'Business hours'},
            {
                label: 'EC Chairperson — Anthony McDonald (UA063)',
                number: '0412 017 126',
                note: 'Owners Corporation matters',
                email: 'eastgatedenman13195@gmail.com'
            },
            {
                label: 'EC General Enquiries',
                number: '',
                note: 'For non-urgent matters',
                email: 'ec@eastgateresidences.com.au'
            },
        ],
    },
];
// ─── Sub-components ───────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: ContactCard
 * Path: frontend/src/pages/dashboard/BuildingInfoPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const ContactCard = ({group}) => {
    const Icon = group.icon;
    return (
        <Card className={`border-l-4 ${group.color}`}>
            <CardHeader className="pb-3">
                <CardTitle className={`text-base flex items-center gap-2 ${group.titleColor}`}>
                    <Icon className={`h-4 w-4 ${group.iconColor}`}/>
                    {group.title}
                </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
                {group.entries.map((entry, idx) => (
                    <div key={idx} className="flex flex-col gap-0.5">
                        <div className="flex items-start justify-between gap-2">
                            <span className="text-sm font-medium text-foreground">{entry.label}</span>
                            {entry.urgent && (
                                <Badge className="bg-red-600 text-white text-xs shrink-0">Urgent</Badge>
                            )}
                        </div>
                        {entry.number && (
                            <a
                                href={`tel:${entry.number.replace(/\s/g, '')}`}
                                className="flex items-center gap-1 text-base font-bold text-blue-700 hover:underline w-fit"
                            >
                                <PhoneCall className="h-3.5 w-3.5"/>
                                {entry.number}
                            </a>
                        )}
                        {entry.email && (
                            <a
                                href={`mailto:${entry.email}`}
                                className="text-xs text-blue-600 hover:underline"
                            >
                                {entry.email}
                            </a>
                        )}
                        {entry.note && (
                            <p className="text-xs text-muted-foreground">{entry.note}</p>
                        )}
                    </div>
                ))}
            </CardContent>
        </Card>
    );
};
// ─── Main Page ────────────────────────────────────────────────────────────────

/**
 * @generated FunctionHeader
 * Function: BuildingInfoPage
 * Path: frontend/src/pages/dashboard/BuildingInfoPage.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const BuildingInfoPage = () => {
    const {api, selectedBuilding} = useAuth();

    const [bylaws, setBylaws] = useState(null);
    const [bylawsLoading, setBylawsLoading] = useState(false);
    const [bylawsError, setBylawsError] = useState(false);
    const [activeTab, setActiveTab] = useState('emergency');
    const [buildingSettings, setBuildingSettings] = useState(null);

    useEffect(() => {
        api.get('/settings').then(res => setBuildingSettings(res.data)).catch(() => {});
    }, [api, selectedBuilding?.id]);

    // Build strata/EC contacts dynamically from settings; fall back to the static CONTACTS list
    const dynamicContacts = useMemo(() => CONTACTS.map(group => {
        if (group.category !== 'strata') return group;
        const entries = [];
        if (buildingSettings?.contact_phone) {
            entries.push({label: 'Strata Manager', number: buildingSettings.contact_phone, note: 'Building issues, by-law queries, after-hours emergencies'});
        }
        if (buildingSettings?.contact_email) {
            entries.push({label: 'General Enquiries', number: '', note: 'For non-urgent matters', email: buildingSettings.contact_email});
        }
        return entries.length > 0 ? {...group, entries} : group;
    }), [buildingSettings]);

    useEffect(() => {
        if (activeTab === 'rules' && bylaws === null && !bylawsLoading) {
            setBylawsLoading(true);
            setBylawsError(false);
            api.get('/by-laws/current')
                .then(r => setBylaws(r.data))
                .catch(() => setBylawsError(true))
                .finally(() => setBylawsLoading(false));
        }
    }, [activeTab, api, bylaws, bylawsLoading]);

    return (
        <div className="p-6 max-w-5xl mx-auto space-y-6">
            {/* Page Header */}
            <div className="flex items-start gap-4">
                <div className="p-3 bg-red-100 rounded-xl">
                    <Building2 className="h-7 w-7 text-red-700"/>
                </div>
                <div>
                    <h1 className="text-2xl font-bold text-foreground">Building Info &amp; Safety</h1>
                    <p className="text-muted-foreground text-sm mt-1">
                        {selectedBuilding?.name || buildingSettings?.building_name || 'Your Building'}{buildingSettings?.building_address ? ` — ${buildingSettings.building_address}` : ''}
                    </p>
                </div>
            </div>

            {/* Critical warning banner */}
            <Alert className="border-red-400 bg-red-50">
                <Flame className="h-4 w-4 text-red-600"/>
                <AlertDescription className="text-red-800 font-semibold">
                    IN THE EVENT OF A FIRE — RING <strong>000</strong> IMMEDIATELY TO ENSURE FIRE SERVICES RESPONSE
                </AlertDescription>
            </Alert>

            {/* Tabs */}
            <Tabs value={activeTab} onValueChange={setActiveTab}>
                <TabsList className="grid w-full grid-cols-3">
                    <TabsTrigger value="emergency" className="flex items-center gap-1.5">
                        <Flame className="h-4 w-4"/> Emergency &amp; Fire
                    </TabsTrigger>
                    <TabsTrigger value="rules" className="flex items-center gap-1.5">
                        <FileText className="h-4 w-4"/> Building Rules
                    </TabsTrigger>
                    <TabsTrigger value="contacts" className="flex items-center gap-1.5">
                        <Phone className="h-4 w-4"/> Key Contacts
                    </TabsTrigger>
                </TabsList>

                {/* ── Tab 1: Emergency & Fire Escape ────────────────────────────── */}
                <TabsContent value="emergency" className="space-y-6 mt-4">
                    {/* DO NOT use lifts warning */}
                    <Card className="border-red-500 border-2 bg-red-50">
                        <CardContent className="pt-5 flex items-center gap-3">
                            <XCircle className="h-8 w-8 text-red-600 shrink-0"/>
                            <div>
                                <p className="font-bold text-red-800 text-lg">Do NOT use lifts during evacuation</p>
                                <p className="text-red-700 text-sm">Always use the stairwell marked EXIT. Lifts may
                                    become inoperable during a fire.</p>
                            </div>
                        </CardContent>
                    </Card>

                    {/* 6-step procedure */}
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-base">
                                <AlertTriangle className="h-5 w-5 text-orange-500"/>
                                On Hearing the Fire Alarm — 6-Step Procedure
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-3">
                            {FIRE_STEPS.map(step => (
                                <div key={step.num} className="flex items-start gap-3">
                                    <div
                                        className="shrink-0 w-8 h-8 rounded-full bg-orange-500 text-white flex items-center justify-center font-bold text-sm">
                                        {step.num}
                                    </div>
                                    <p className="text-sm text-foreground pt-1">{step.text}</p>
                                </div>
                            ))}
                        </CardContent>
                    </Card>

                    {/* Assembly area */}
                    <Card className="border-green-400 bg-green-50">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-base flex items-center gap-2 text-green-800">
                                <CheckCircle2 className="h-5 w-5 text-green-600"/>
                                Assembly Area
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <p className="text-sm text-green-900">
                                The designated assembly area is the <strong>car park entrance on Hoolihan St</strong>.
                                Proceed there after evacuating and wait for the Fire Brigade all-clear before
                                re-entering.
                            </p>
                        </CardContent>
                    </Card>

                    {/* Fire Detection Block Plan */}
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2 text-base">
                                <Shield className="h-5 w-5 text-blue-500"/>
                                Fire Detection Block Plan — Zone Schedule
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <p className="text-sm text-muted-foreground">
                                {selectedBuilding?.name || buildingSettings?.building_name || 'This building'}{buildingSettings?.building_address ? ` — ${buildingSettings.building_address}` : ''}. The automatic fire detection
                                system is installed in accordance with BCA (2016), AS1670.1 (2015) and Fire Engineering
                                Report CA170085 R1.2 by Warringtonfire (August 2020).
                            </p>
                            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                                {FIRE_ZONES.map(zone => (
                                    <div
                                        key={zone.num}
                                        className="flex items-center gap-2 p-2 rounded-md bg-slate-50 border border-slate-200 text-sm"
                                    >
                                        <Badge variant="outline"
                                               className="text-xs font-mono shrink-0 min-w-[52px] justify-center">
                                            Zone {zone.num}
                                        </Badge>
                                        <span className="text-muted-foreground text-xs">{zone.label}</span>
                                    </div>
                                ))}
                            </div>
                            <Alert className="border-blue-300 bg-blue-50">
                                <Info className="h-4 w-4 text-blue-600"/>
                                <AlertDescription className="text-blue-800 text-sm">
                                    <strong>Performance Solution Notice:</strong> This building incorporates performance
                                    solution
                                    designs for National Construction Code compliance. Refer to the visual block plans
                                    located
                                    near fire indicator panels on each floor for exact exit routes.
                                    <br/>
                                    <strong>Fire System Service Provider:</strong> O'Neill &amp; Brown Fire Services —
                                    (02) 6297 2022
                                </AlertDescription>
                            </Alert>
                        </CardContent>
                    </Card>

                    {/* Quick contact summary */}
                    <Card className="border-slate-300">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-base">Emergency Contacts at a Glance</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-sm">
                                {[
                                    {label: 'Fire / Police / Ambulance', number: '000'},
                                    {label: 'Non-Emergency Police', number: '131 444'},
                                    {label: 'Civium Strata (24/7)', number: '1300 724 256'},
                                    {label: 'Lift Emergency — Electra Lift Co', number: '1300 725 290'},
                                    {label: 'SES (storm / flood)', number: '132 500'},
                                    {label: 'Poison Information Centre', number: '13 11 26'},
                                ].map(c => (
                                    <div key={c.label}
                                         className="flex justify-between items-center p-2 rounded bg-slate-50">
                                        <span className="text-muted-foreground">{c.label}</span>
                                        <a
                                            href={`tel:${c.number.replace(/\s/g, '')}`}
                                            className="font-bold text-foreground hover:text-blue-700"
                                        >
                                            {c.number}
                                        </a>
                                    </div>
                                ))}
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                {/* ── Tab 2: Building Rules & By-Laws ─────────────────────────────── */}
                <TabsContent value="rules" className="mt-4">
                    {bylawsLoading && (
                        <div className="flex items-center justify-center py-20 gap-3 text-muted-foreground">
                            <Loader2 className="h-6 w-6 animate-spin"/>
                            <span>Loading building rules…</span>
                        </div>
                    )}

                    {bylawsError && !bylawsLoading && (
                        <Alert className="border-amber-400 bg-amber-50">
                            <AlertTriangle className="h-4 w-4 text-amber-600"/>
                            <AlertDescription className="text-amber-800">
                                Building by-laws could not be loaded at this time. Please contact the Strata Manager
                                {buildingSettings?.contact_phone && <>{' '}at{' '}<strong>{buildingSettings.contact_phone}</strong></>}
                                {buildingSettings?.contact_email && <>{' '}or{' '}<a href={`mailto:${buildingSettings.contact_email}`} className="underline">{buildingSettings.contact_email}</a></>}
                                {!buildingSettings?.contact_phone && !buildingSettings?.contact_email && ' for assistance'}.
                            </AlertDescription>
                        </Alert>
                    )}

                    {bylaws && !bylawsLoading && (
                        <div className="space-y-4">
                            <div className="flex items-center justify-between flex-wrap gap-3">
                                <div>
                                    <h2 className="text-lg font-semibold">Building By-Laws &amp; Rules</h2>
                                    <div className="flex items-center gap-3 mt-1 text-sm text-muted-foreground">
                                        {bylaws.version && (
                                            <Badge variant="outline">Version {bylaws.version}</Badge>
                                        )}
                                        {bylaws.effective_date && (
                                            <span>
                        Effective:{' '}
                                                {new Date(bylaws.effective_date).toLocaleDateString('en-AU', {
                                                    day: 'numeric',
                                                    month: 'long',
                                                    year: 'numeric',
                                                })}
                      </span>
                                        )}
                                    </div>
                                </div>
                            </div>

                            <Card>
                                <CardContent className="pt-5">
                                    <div
                                        className="prose prose-sm max-w-none max-h-[60vh] overflow-y-auto pr-2"
                                        style={{whiteSpace: 'pre-wrap', lineHeight: '1.7'}}
                                    >
                                        {bylaws.content || (
                                            <p className="text-muted-foreground italic">No content available.</p>
                                        )}
                                    </div>
                                </CardContent>
                            </Card>

                            <p className="text-xs text-muted-foreground">
                                For the official registered by-laws document, contact the Strata Manager or visit the
                                ACT
                                Access Canberra strata portal.
                            </p>
                        </div>
                    )}

                    {!bylaws && !bylawsLoading && !bylawsError && (
                        <div className="flex items-center justify-center py-20 gap-3 text-muted-foreground">
                            <Loader2 className="h-6 w-6 animate-spin"/>
                            <span>Loading…</span>
                        </div>
                    )}
                </TabsContent>

                {/* ── Tab 3: Building Contacts ─────────────────────────────────────── */}
                <TabsContent value="contacts" className="mt-4 space-y-4">
                    <p className="text-sm text-muted-foreground">
                        Save these numbers. In a life-threatening emergency always call <strong>000</strong> first.
                    </p>
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                        {dynamicContacts.map(group => (
                            <ContactCard key={group.category} group={group}/>
                        ))}
                    </div>

                    {/* After-hours note */}
                    <Card className="border-slate-300 bg-slate-50">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm flex items-center gap-2 text-slate-700">
                                <Info className="h-4 w-4"/>
                                After-Hours Procedure
                            </CardTitle>
                        </CardHeader>
                        <CardContent className="text-sm text-muted-foreground space-y-1">
                            <p>
                                For after-hours building emergencies (e.g., burst pipe, lift failure, security breach),
                                {buildingSettings?.contact_phone
                                    ? <> call <strong>{buildingSettings.contact_phone}</strong> — check with your strata manager for after-hours availability.</>
                                    : ' contact your strata manager — check the Contacts tab for the after-hours number.'}
                            </p>
                            {buildingSettings?.contact_email && (
                                <p>
                                    For non-urgent issues outside business hours, email{' '}
                                    <a href={`mailto:${buildingSettings.contact_email}`} className="text-blue-600 hover:underline">
                                        {buildingSettings.contact_email}
                                    </a>.
                                </p>
                            )}
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
};

export default BuildingInfoPage;
