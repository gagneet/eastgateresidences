// @featuretrace:onboarding — Quick-create building form (7-step). Lighter/alternate entry
// point alongside OnboardingWizard.jsx's full 32-step flow — both create sessions via the
// same POST /admin/onboarding/scheme + /onboarding/scheme/{id}/finalize backend, but this
// form only imports the Strata Roll for real (see tasks/GAP-ONBOARD-001).
// Layer: frontend
// Data flow: this page → POST /admin/onboarding/scheme → POST /onboarding/scheme/{id}/lots
//            (Strata Roll only) → PATCH /onboarding/scheme/{id} → core.onboarding_sessions,
//            core.lots (building-scoped).
// Related: frontend/src/pages/dashboard/admin/OnboardingWizard.jsx
//           backend/routers/onboarding.py
// Tests: tests/frontend/unit/pages/dashboard/admin/BuildingOnboardingForm.test.tsx
/**
 * Building Onboarding Form
 *
 * 7-step gamified wizard for creating a new building tenant.
 * Roles: super_admin, strata_manager, admin_staff
 *
 * Steps:
 *   1 — Building Identity   (Unit Plan → building_id, name, type)
 *   2 — Location & Physical (address, state, lots, year built)
 *   3 — Financial & Legal   (FY end, levy schedule, GST, jurisdiction)
 *   4 — Strata Manager      (assign existing user or invite)
 *   5 — Executive Committee (≥1 CHAIRMAN required)
 *   6 — Import Data         (optional CSV/PDF uploads)
 *   7 — Review & Launch
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useAuth } from '../../../contexts/AuthContext';
import { Alert, AlertDescription } from '../../../components/ui/alert';
import { Badge } from '../../../components/ui/badge';
import { Button } from '../../../components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../../components/ui/card';
import { Checkbox } from '../../../components/ui/checkbox';
import { Input } from '../../../components/ui/input';
import { Label } from '../../../components/ui/label';
import { Progress } from '../../../components/ui/progress';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../../components/ui/select';
import { Separator } from '../../../components/ui/separator';
import { Textarea } from '../../../components/ui/textarea';
import {
    AlertTriangle,
    Award,
    BarChart3,
    Building2,
    Calendar,
    CheckCircle2,
    ChevronLeft,
    ChevronRight,
    DollarSign,
    FileText,
    HelpCircle,
    Info,
    Loader2,
    MapPin,
    Plus,
    Search,
    Star,
    Trash2,
    Trophy,
    Upload,
    UserPlus,
    Users,
    X,
} from 'lucide-react';
import { toast } from 'sonner';

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STEPS = [
    {id: 1, label: 'Identity', icon: Building2, xp: 10, color: 'blue'},
    {id: 2, label: 'Location', icon: MapPin, xp: 15, color: 'green'},
    {id: 3, label: 'Lots', icon: BarChart3, xp: 20, color: 'violet'},
    {id: 4, label: 'Financial', icon: DollarSign, xp: 20, color: 'amber'},
    {id: 5, label: 'Manager', icon: UserPlus, xp: 20, color: 'purple'},
    {id: 6, label: 'Committee', icon: Users, xp: 25, color: 'rose'},
    {id: 7, label: 'Import', icon: Upload, xp: 10, color: 'teal'},
    {id: 8, label: 'Review', icon: CheckCircle2, xp: 0, color: 'gray'},
];

const STATES = ['ACT', 'NSW', 'VIC', 'QLD', 'SA', 'WA', 'TAS', 'NT'];
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const TIMEZONES = ['Australia/Sydney', 'Australia/Melbourne', 'Australia/Brisbane', 'Australia/Perth', 'Australia/Adelaide', 'Australia/Darwin', 'Australia/Hobart'];
const EC_POSITIONS = ['CHAIRMAN', 'TREASURER', 'SECRETARY', 'MEMBER'];
const BUILDING_TYPES = [
    {value: 'apartment', label: 'Apartment Complex'},
    {value: 'townhouse', label: 'Townhouse Complex'},
    {value: 'mixed', label: 'Mixed Use'},
    {value: 'commercial', label: 'Commercial Strata'},
    {value: 'retirement', label: 'Retirement Village'},
];

const LEVY_MONTHS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12];

const USE_TYPES = [
    {value: 'residential', label: 'Residential Only', desc: 'Apartments, townhouses, villas — no commercial lots'},
    {value: 'commercial', label: 'Commercial Only', desc: 'Office, retail, industrial — no residential lots'},
    {value: 'mixed', label: 'Mixed Use', desc: 'Combination of residential and commercial/retail lots'},
];

const LOT_TYPES = [
    {value: 'apartment', label: 'Apartments', bca: '2', icon: '🏢'},
    {value: 'townhouse', label: 'Townhouses', bca: '2', icon: '🏘'},
    {value: 'villa', label: 'Villas / Duplexes', bca: '2', icon: '🏡'},
    {value: 'commercial', label: 'Commercial (Office)', bca: '5', icon: '🏬'},
    {value: 'retail', label: 'Retail', bca: '6', icon: '🛍'},
    {value: 'carpark', label: 'Car Parks', bca: '7', icon: '🚗'},
    {value: 'storage', label: 'Storage / Utility', bca: '10a', icon: '📦'},
];

const BCA_CLASSES = [
    {value: '2', label: 'Class 2 — Residential (multi-unit)'},
    {value: '5', label: 'Class 5 — Office'},
    {value: '6', label: 'Class 6 — Retail / Shop'},
    {value: '7a', label: 'Class 7a — Carpark'},
    {value: '7b', label: 'Class 7b — Warehouse / Storage'},
    {value: '8', label: 'Class 8 — Industrial / Factory'},
    {value: '9a', label: 'Class 9a — Health / Hospital'},
    {value: '9b', label: 'Class 9b — Assembly / Public Hall'},
];
// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

/**
 * @generated FunctionHeader
 * Function: deriveId
 * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function deriveId(raw) {
    const stripped = raw.trim().replace(/^[a-zA-Z]+/, '');
    return stripped.replace(/[^0-9a-z_\-]/gi, '').toLowerCase() || raw.toLowerCase().replace(/[^0-9a-z_\-]/g, '');
}
/**
 * @generated FunctionHeader
 * Function: HelpTip
 * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function HelpTip({text}) {
    const [open, setOpen] = useState(false);
    return (
        <span className="inline-flex items-center ml-1">
            <button
                type="button"
                onClick={() => setOpen(v => !v)}
                className="text-muted-foreground hover:text-primary transition-colors"
            >
                <HelpCircle className="h-3.5 w-3.5"/>
            </button>
            {open && (
                <span
                    className="absolute z-20 ml-5 w-64 rounded-lg border bg-card shadow-lg p-3 text-xs text-muted-foreground leading-relaxed">
                    {text}
                    <button type="button" onClick={() => setOpen(false)}
                            className="ml-2 text-muted-foreground hover:text-foreground">
                        <X className="h-3 w-3 inline"/>
                    </button>
                </span>
            )}
        </span>
    );
}
/**
 * @generated FunctionHeader
 * Function: FieldLabel
 * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function FieldLabel({children, required, optional, help}) {
    return (
        <div className="flex items-center gap-1.5 mb-1.5 relative">
            <Label className="text-sm font-medium">{children}</Label>
            {required && <Badge variant="outline"
                                className="text-[10px] h-4 px-1.5 text-red-600 border-red-200 bg-red-50">Required</Badge>}
            {optional && <Badge variant="outline"
                                className="text-[10px] h-4 px-1.5 text-slate-500 border-slate-200">Optional</Badge>}
            {help && <HelpTip text={help}/>}
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: XPBadge
 * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function XPBadge({xp}) {
    return (
        <div
            className="flex items-center gap-1 text-amber-600 bg-amber-50 border border-amber-200 rounded-full px-2.5 py-1 text-xs font-semibold">
            <Star className="h-3 w-3 fill-amber-500 text-amber-500"/>
            +{xp} XP
        </div>
    );
}
/**
 * @generated FunctionHeader
 * Function: StepHint
 * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function StepHint({icon: Icon, children}) {
    return (
        <div
            className="flex items-start gap-2 rounded-lg bg-blue-50 border border-blue-100 px-3 py-2 text-xs text-blue-700 mb-4">
            <Icon className="h-4 w-4 mt-0.5 flex-shrink-0 text-blue-500"/>
            <span>{children}</span>
        </div>
    );
}
// ---------------------------------------------------------------------------
// Pure helpers — module-scope (not closures over component state) so they're
// both used internally and directly unit-testable without rendering the
// whole 8-step wizard. See tests/frontend/unit/pages/dashboard/admin/
// BuildingOnboardingForm.test.tsx.
// ---------------------------------------------------------------------------

// Backend error responses on this page's endpoints are inconsistently shaped:
// some raise HTTPException(detail="plain string"), others raise
// HTTPException(detail={"detail": "...", "code": "..."}) (see onboarding.py's
// /admin/onboarding/scheme and /lots handlers, which use both forms across
// different validation branches). Rendering the object form directly in JSX
// throws "Objects are not valid as a React child" and crashes the page —
// always route error responses through this before storing/rendering them.
export const extractErrorDetail = (err, fallback) => {
    const detail = err?.response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object' && typeof detail.detail === 'string') return detail.detail;
    if (detail && typeof detail === 'object' && typeof detail.message === 'string') return detail.message;
    return fallback;
};

// Adapts this form's documented Strata Roll CSV columns
// (unit_number, owner_name, owner_email, entitlement, phone) to the
// /onboarding/scheme/{id}/lots endpoint's LotInput shape (lot_number,
// unit_number, entitlement_units, owner_email). lot_number isn't
// collected by this form's simpler template, so unit_number is reused
// as lot_number — the common case where a building has no separate
// legal lot numbering. owner_name/phone aren't part of LotInput and are
// not sent (owner invites are driven by owner_email alone).
export const parseStrataRollCsv = (text) => {
    const lines = text.split(/\r?\n/).filter(l => l.trim());
    if (lines.length < 2) return [];
    const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, '').toLowerCase());
    return lines.slice(1)
        .map(line => {
            const vals = line.split(',').map(v => v.trim().replace(/^"|"$/g, ''));
            const row = {};
            headers.forEach((h, i) => { row[h] = vals[i] ?? ''; });
            const unitNumber = row.unit_number || '';
            if (!unitNumber) return null;
            return {
                lot_number: unitNumber,
                unit_number: unitNumber,
                entitlement_units: row.entitlement ? (parseFloat(row.entitlement) || null) : null,
                owner_email: row.owner_email || null,
            };
        })
        .filter(Boolean);
};

const defaultLevyDueDayForMonth = (month) => {
    if ([4, 6, 9, 11].includes(month)) return '30';
    if (month === 2) return '28';
    return '31';
};

export const normaliseLevyDueCustomDates = (months, customDates = {}) => {
    return Object.fromEntries(
        months
            .map(month => {
                const raw = customDates[ String(month) ];
                const day = parseInt(raw, 10);
                if (!Number.isFinite(day) || day < 1 || day > 31) return null;
                return [String(month), day];
            })
            .filter(Boolean)
    );
};

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

/**
 * @generated FunctionHeader
 * Function: BuildingOnboardingForm
 * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function BuildingOnboardingForm() {
    const {api, isAdmin, user} = useAuth();
    const router = useRouter();

    // Gate access
    const effectiveRole = user?.effective_role || user?.role;
    const canAccess = effectiveRole === 'super_admin'
        || effectiveRole === 'strata_admin'
        || effectiveRole === 'strata_manager';
    const isSuperAdmin = isAdmin();

    // Wizard state
    const [step, setStep] = useState(1);
    const [xpTotal, setXpTotal] = useState(0);
    const [completedSteps, setCompletedSteps] = useState(new Set());
    const [error, setError] = useState(null);
    const [submitting, setSubmitting] = useState(false);
    const [result, setResult] = useState(null);
    const [organisations, setOrganisations] = useState([]);
    const [orgLoading, setOrgLoading] = useState(false);
    const [selectedTenantId, setSelectedTenantId] = useState('');

    // ---- Step 1: Identity ----
    const [identity, setIdentity] = useState({
        unit_plan_number: '',
        name: '',
        building_type: 'apartment',
        description: '',
    });

    // ---- Step 2: Location ----
    const [location, setLocation] = useState({
        address: '',
        suburb: '',
        state: 'ACT',
        postcode: '',
        year_built: '',
        contact_email: '',
        contact_phone: '',
        website: '',
    });

    // ---- Step 3: Financial ----
    const [financial, setFinancial] = useState({
        financial_year_end_month: '6',
        state_jurisdiction: 'ACT',
        gst_registered: false,
        aggregate_unit_entitlement: '',
        levy_due_months: [3, 6, 9, 12],
        levy_due_day_type: 'last',
        levy_due_custom_dates: {3: '31', 6: '30', 9: '30', 12: '31'},
        timezone: 'Australia/Sydney',
        lot_count: '',
    });

    // ---- Step 4: Strata Manager ----
    const [sm, setSm] = useState({
        mode: 'invite',       // 'search' | 'invite' | 'skip'
        user_id: '',
        selected_user: null,
        invite_name: '',
        invite_email: '',
        company_name: '',
        phone: '',
        license_number: '',
    });
    const [smQuery, setSmQuery] = useState('');
    const [smResults, setSmResults] = useState([]);
    const [smSearching, setSmSearching] = useState(false);
    const smDebounce = useRef(null);

    // ---- Step 5: EC Members ----
    const [ecMembers, setEcMembers] = useState([
        {
            _key: 1,
            mode: 'invite',
            user_id: '',
            selected_user: null,
            invite_name: '',
            invite_email: '',
            unit_number: '',
            position: 'CHAIRMAN'
        },
    ]);
    const [ecQuery, setEcQuery] = useState({});   // { _key: queryStr }
    const [ecResults, setEcResults] = useState({});   // { _key: [...] }
    const [ecSearching, setEcSearching] = useState({});
    const ecDebounce = useRef({});

    // ---- Step 3: Unit Configuration ----
    const [unitConfig, setUnitConfig] = useState({
        use_type: 'residential',
        lot_types_present: ['apartment'],
        has_class_a_b: false,           // ACT Class A/B demarcation
        act_class_a_count: '',
        act_class_b_count: '',
        lot_type_ranges: [],            // [{ _key, lot_type, range_mode, start, end, numbers, count, act_class }]
    });

    // ---- Step 7: Uploads ----
    const [uploads, setUploads] = useState({
        strata_roll: null,
        financials: null,
        levies: null,
    });

    useEffect(() => {
        if (!isSuperAdmin) {
            setSelectedTenantId(user?.tenant_id || '');
            return;
        }

        let active = true;
        /**
         * @generated FunctionHeader
         * Function: fetchOrganisations
         * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
         *
         * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
         */
        const fetchOrganisations = async () => {
            setOrgLoading(true);
            try {
                const res = await api.get('/admin/sm-organisations', {
                    params: {include_self_managed: true, status: 'active'},
                });
                if (!active) return;
                setOrganisations(res.data?.items || []);
            } catch {
                if (!active) return;
                setOrganisations([]);
            } finally {
                if (active) {
                    setOrgLoading(false);
                }
            }
        };

        fetchOrganisations();
        return () => {
            active = false;
        };
    }, [api, isSuperAdmin, user?.tenant_id]);

    // -------------------------------------------------------------------------
    // Derived values
    // -------------------------------------------------------------------------
    const derivedId = identity.unit_plan_number ? deriveId(identity.unit_plan_number) : '';
    const effectiveTenantId = selectedTenantId || ( isSuperAdmin && organisations.length === 1 ? organisations[ 0 ].tenant_id : '' );

    // -------------------------------------------------------------------------
    // User search — strata manager
    // -------------------------------------------------------------------------
    const searchSM = useCallback(async (q) => {
        if (!q || q.length < 2) {
            setSmResults([]);
            return;
        }
        setSmSearching(true);
        try {
            const res = await api.get('/portfolio/users/search', {params: {role: 'strata_manager', query: q}});
            setSmResults(res.data || []);
        } catch {
            setSmResults([]);
        } finally {
            setSmSearching(false);
        }
    }, [api]);

    useEffect(() => {
        clearTimeout(smDebounce.current);
        smDebounce.current = setTimeout(() => searchSM(smQuery), 350);
    }, [smQuery, searchSM]);

    // -------------------------------------------------------------------------
    // User search — EC members
    // -------------------------------------------------------------------------
    const searchEC = useCallback(async (key, q) => {
        if (!q || q.length < 2) {
            setEcResults(prev => ( {...prev, [ key ]: []} ));
            return;
        }
        setEcSearching(prev => ( {...prev, [ key ]: true} ));
        try {
            const res = await api.get('/portfolio/users/search', {params: {query: q}});
            setEcResults(prev => ( {...prev, [ key ]: res.data || []} ));
        } catch {
            setEcResults(prev => ( {...prev, [ key ]: []} ));
        } finally {
            setEcSearching(prev => ( {...prev, [ key ]: false} ));
        }
    }, [api]);
    // -------------------------------------------------------------------------
    // Validation per step
    // -------------------------------------------------------------------------
    /**
     * @generated FunctionHeader
     * Function: validateStep
     * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const validateStep = (s) => {
        if (s === 1) return !!( identity.unit_plan_number.trim() && identity.name.trim() && derivedId && effectiveTenantId );
        if (s === 2) return !!( location.address.trim() && location.state );
        if (s === 3) return unitConfig.lot_types_present.length > 0;
        if (s === 4) return !!( financial.lot_count && parseInt(financial.lot_count) > 0 );
        if (s === 5) return true; // strata manager recommended but not blocking
        if (s === 6) return ecMembers.some(m => m.position === 'CHAIRMAN' && ( m.user_id || ( m.invite_name.trim() && m.invite_email.trim() ) ));
        if (s === 7) return true;
        return true;
    };
    /**
     * @generated FunctionHeader
     * Function: advanceStep
     * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const advanceStep = () => {
        setError(null);
        if (!validateStep(step)) {
            setError(getStepError(step));
            return;
        }
        const stepDef = STEPS[ step - 1 ];
        if (!completedSteps.has(step)) {
            setCompletedSteps(prev => new Set([...prev, step]));
            setXpTotal(prev => prev + stepDef.xp);
        }
        setStep(s => s + 1);
        window.scrollTo({top: 0, behavior: 'smooth'});
    };
    /**
     * @generated FunctionHeader
     * Function: getStepError
     * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const getStepError = (s) => {
        if (s === 1) return isSuperAdmin
            ? 'Please choose an organisation, then enter a valid Unit Plan number and Building name.'
            : 'Please enter a valid Unit Plan number and Building name.';
        if (s === 2) return 'Street address and state are required.';
        if (s === 3) return 'Select at least one lot type present in this scheme.';
        if (s === 4) return 'Total lots/units is required and must be a positive number.';
        if (s === 6) return 'At least one EC member with the CHAIRMAN position is required (name + email or existing user).';
        return null;
    };
    // -------------------------------------------------------------------------
    // EC helpers
    // -------------------------------------------------------------------------
    /**
     * @generated FunctionHeader
     * Function: addECMember
     * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const addECMember = () => {
        const usedPositions = ecMembers.map(m => m.position);
        const next = EC_POSITIONS.find(p => !usedPositions.includes(p)) || 'MEMBER';
        setEcMembers(prev => [...prev, {
            _key: Date.now(),
            mode: 'invite',
            user_id: '',
            selected_user: null,
            invite_name: '',
            invite_email: '',
            unit_number: '',
            position: next
        }]);
    };
    /**
     * @generated FunctionHeader
     * Function: removeECMember
     * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const removeECMember = (key) => {
        setEcMembers(prev => prev.filter(m => m._key !== key));
    };
    /**
     * @generated FunctionHeader
     * Function: updateECMember
     * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateECMember = (key, field, value) => {
        setEcMembers(prev => prev.map(m => m._key === key ? {...m, [ field ]: value} : m));
    };
    /**
     * @generated FunctionHeader
     * Function: toggleLevy
     * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleLevy = (month) => {
        setFinancial(prev => ( {
            ...prev,
            levy_due_months: prev.levy_due_months.includes(month)
                ? prev.levy_due_months.filter(m => m !== month)
                : [...prev.levy_due_months, month].sort((a, b) => a - b),
            levy_due_custom_dates: prev.levy_due_months.includes(month)
                ? Object.fromEntries(Object.entries(prev.levy_due_custom_dates).filter(([ key ]) => key !== String(month)))
                : {
                    ...prev.levy_due_custom_dates,
                    [ month ]: prev.levy_due_custom_dates[ month ] || defaultLevyDueDayForMonth(month),
                },
        } ));
    };

    const updateCustomLevyDueDay = (month, value) => {
        const cleaned = value.replace(/[^\d]/g, '').slice(0, 2);
        setFinancial(prev => ( {
            ...prev,
            levy_due_custom_dates: {...prev.levy_due_custom_dates, [ month ]: cleaned},
        } ));
    };
    /**
     * @generated FunctionHeader
     * Function: buildOnboardingDraftStepData
     * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const buildOnboardingDraftStepData = () => ({
        legacy_building_setup_draft: {
            identity: {
                unit_plan_number: identity.unit_plan_number.trim(),
                derived_scheme_number: derivedId,
                name: identity.name.trim(),
                building_type: identity.building_type,
                description: identity.description.trim() || null,
            },
            location: {
                address: location.address.trim(),
                suburb: location.suburb.trim() || null,
                state: location.state,
                postcode: location.postcode.trim() || null,
                year_built: location.year_built ? parseInt(location.year_built, 10) : null,
                contact_email: location.contact_email.trim() || null,
                contact_phone: location.contact_phone.trim() || null,
                website: location.website.trim() || null,
            },
            financial: {
                lot_count: financial.lot_count ? parseInt(financial.lot_count, 10) : null,
                financial_year_end_month: parseInt(financial.financial_year_end_month, 10),
                jurisdiction: ( financial.state_jurisdiction || location.state || 'ACT' ).trim(),
                gst_registered: financial.gst_registered,
                aggregate_unit_entitlement: financial.aggregate_unit_entitlement
                    ? parseInt(financial.aggregate_unit_entitlement, 10)
                    : null,
                levy_due_months: financial.levy_due_months,
                levy_due_day_type: financial.levy_due_day_type,
                levy_due_custom_dates: financial.levy_due_day_type === 'custom'
                    ? normaliseLevyDueCustomDates(financial.levy_due_months, financial.levy_due_custom_dates)
                    : {},
                timezone: financial.timezone,
            },
            strata_manager: sm.mode === 'skip' ? null : {
                mode: sm.mode,
                user_id: sm.mode === 'search' ? sm.user_id : null,
                invite_name: sm.mode === 'invite' ? sm.invite_name.trim() || null : null,
                invite_email: sm.mode === 'invite' ? sm.invite_email.trim() || null : null,
                company_name: sm.company_name.trim() || null,
                phone: sm.phone.trim() || null,
                license_number: sm.license_number.trim() || null,
            },
            unit_configuration: {
                use_type: unitConfig.use_type,
                lot_types_present: unitConfig.lot_types_present,
                has_class_a_b_demarcation: unitConfig.has_class_a_b,
                act_class_a_count: unitConfig.act_class_a_count ? parseInt(unitConfig.act_class_a_count, 10) : null,
                act_class_b_count: unitConfig.act_class_b_count ? parseInt(unitConfig.act_class_b_count, 10) : null,
                lot_type_ranges: unitConfig.lot_type_ranges.map((r) => ( {
                    lot_type: r.lot_type,
                    range_mode: r.range_mode,
                    lot_number_start: r.range_mode === 'range' ? ( parseInt(r.start, 10) || null ) : null,
                    lot_number_end: r.range_mode === 'range' ? ( parseInt(r.end, 10) || null ) : null,
                    lot_numbers: r.range_mode === 'nonlinear'
                        ? ( r.numbers || '' ).split(',').map(n => parseInt(n.trim(), 10)).filter(Boolean)
                        : [],
                    count: r.count ? parseInt(r.count, 10) : null,
                    act_class: r.act_class || null,
                } )),
            },
            ec_members: ecMembers.map((m) => ( {
                mode: m.mode,
                user_id: m.mode === 'search' ? m.user_id : null,
                invite_name: m.mode === 'invite' ? m.invite_name.trim() || null : null,
                invite_email: m.mode === 'invite' ? m.invite_email.trim() || null : null,
                unit_number: m.unit_number.trim() || null,
                position: m.position,
            } )),
            uploads: Object.fromEntries(
                Object.entries(uploads).map(([ key, file ]) => [
                    key,
                    file ? {filename: file.name, size: file.size, type: file.type || null} : null,
                ])
            ),
        },
    });
    // -------------------------------------------------------------------------
    // Submit
    // -------------------------------------------------------------------------
    /**
     * @generated FunctionHeader
     * Function: handleSubmit
     * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleSubmit = async () => {
        setError(null);
        setSubmitting(true);
        try {
            const payload = {
                tenant_id: effectiveTenantId,
                scheme_number: derivedId,
                scheme_name: identity.name.trim(),
                jurisdiction: ( financial.state_jurisdiction || location.state || 'ACT' ).trim(),
                legal_name: identity.name.trim() || undefined,
            };

            const res = await api.post('/admin/onboarding/scheme', payload);
            const sessionId = res.data.session_id;
            let persistenceWarning = null;
            try {
                await api.patch(`/onboarding/scheme/${sessionId}`, {
                    current_step: 8,
                    step_data: buildOnboardingDraftStepData(),
                });
            } catch (patchError) {
                persistenceWarning = patchError.response?.data?.detail
                    || 'Scheme created, but we could not save the draft onboarding data to the new session.';
                toast.error(persistenceWarning);
            }

            // Strata Roll is the only one of the three "Import Existing Data"
            // uploads whose columns map onto an existing bulk-import
            // endpoint (see tasks/GAP-ONBOARD-001) — parse and import it for
            // real. "Previous Financial Statements" and "Previous Year Levy
            // Records" use a stricter multi-file CSV contract only the full
            // Onboarding Wizard implements; those are surfaced honestly on
            // the success screen below instead of silently discarded.
            let lotsImported = null;
            if (uploads.strata_roll) {
                try {
                    const text = await uploads.strata_roll.text();
                    const lots = parseStrataRollCsv(text);
                    if (lots.length === 0) {
                        lotsImported = {success: false, error: 'No valid rows found — check the CSV has a unit_number column.'};
                    } else {
                        await api.post(`/onboarding/scheme/${sessionId}/lots`, {lots});
                        lotsImported = {success: true, count: lots.length};
                        toast.success(`Imported ${lots.length} unit(s) from Strata Roll.`);
                    }
                } catch (uploadError) {
                    const detailMsg = extractErrorDetail(uploadError, 'Strata Roll import failed.');
                    lotsImported = {success: false, error: detailMsg};
                    toast.error(detailMsg);
                }
            }
            const unprocessedUploads = ['financials', 'levies'].filter(key => uploads[key]);

            setResult({...res.data, persistenceWarning, lotsImported, unprocessedUploads});
            toast.success(`Scheme "${identity.name}" created successfully.`);
        } catch (e) {
            // Pre-existing bug, fixed here: /admin/onboarding/scheme's error responses use
            // the same {detail, code} object shape as /lots (see extractErrorDetail above) —
            // the previous `e.response?.data?.detail || ...` stored that object directly and
            // crashed the Alert render below on most real validation failures (invalid
            // jurisdiction, duplicate scheme number, tenant not found, etc).
            const msg = extractErrorDetail(e, 'Failed to create building. Please try again.');
            setError(msg);
            toast.error(msg);
        } finally {
            setSubmitting(false);
        }
    };

    // -------------------------------------------------------------------------
    // Success screen
    // -------------------------------------------------------------------------
    if (result) {
        return (
            <div className="max-w-2xl mx-auto p-6 space-y-6">
                <div className="text-center py-8 space-y-4">
                    <div className="flex justify-center">
                        <div className="h-20 w-20 rounded-full bg-green-100 flex items-center justify-center">
                            <Trophy className="h-10 w-10 text-green-600"/>
                        </div>
                    </div>
                    <h1 className="text-2xl font-bold font-[Manrope]">Scheme Created!</h1>
                    <p className="text-muted-foreground">
                        <span className="font-semibold text-foreground">{identity.name}</span> (Scheme ID: {result.scheme_id})
                        has been created and its onboarding session is ready.
                    </p>
                    <div
                        className="flex items-center justify-center gap-2 text-amber-600 bg-amber-50 border border-amber-200 rounded-full px-4 py-2 w-fit mx-auto">
                        <Award className="h-5 w-5"/>
                        <span className="font-semibold">{xpTotal + 50} XP earned — Building Creator achievement unlocked!</span>
                    </div>
                </div>

                {result.persistenceWarning && (
                    <Alert variant="destructive">
                        <AlertTriangle className="h-4 w-4"/>
                        <AlertDescription>{result.persistenceWarning}</AlertDescription>
                    </Alert>
                )}

                {result.lotsImported && (
                    result.lotsImported.success ? (
                        <Alert className="border-green-200 bg-green-50">
                            <CheckCircle2 className="h-4 w-4 text-green-600"/>
                            <AlertDescription className="text-green-800">
                                Imported {result.lotsImported.count} unit(s) from your Strata Roll upload.
                            </AlertDescription>
                        </Alert>
                    ) : (
                        <Alert variant="destructive">
                            <AlertTriangle className="h-4 w-4"/>
                            <AlertDescription>Strata Roll import failed: {result.lotsImported.error}</AlertDescription>
                        </Alert>
                    )
                )}

                {result.unprocessedUploads?.length > 0 && (
                    <Alert className="border-amber-200 bg-amber-50">
                        <Info className="h-4 w-4 text-amber-600"/>
                        <AlertDescription className="text-amber-800">
                            {result.unprocessedUploads.includes('financials') && result.unprocessedUploads.includes('levies')
                                ? 'Your Previous Financial Statements and Previous Year Levy Records files were '
                                : result.unprocessedUploads.includes('financials')
                                    ? 'Your Previous Financial Statements file was '
                                    : 'Your Previous Year Levy Records file was '}
                            not imported — this quick-create form only imports the Strata Roll. Use the{' '}
                            <a href={`/admin/portfolio/onboard?session_id=${result.session_id}&track=B&step=levy_history`} className="underline font-medium">
                                Full Onboarding Wizard
                            </a>{' '}
                            (resumes this same scheme at the Historical Records step) to complete historical financial
                            and levy imports for this scheme, or generate historical
                            levy transactions from the proposed budget any time later from{' '}
                            <a href="/admin/financial-onboarding" className="underline font-medium">
                                Financial Onboarding
                            </a>.
                        </AlertDescription>
                    </Alert>
                )}

                <Card>
                    <CardHeader className="pb-3">
                        <CardTitle className="text-base">What's Next</CardTitle>
                        <CardDescription>Complete these onboarding steps to go live</CardDescription>
                    </CardHeader>
                    <CardContent className="space-y-2 text-sm">
                        {[
                            'Import the full strata roll (owners + unit entitlements)',
                            'Configure levy amounts and due dates',
                            'Set up trust account details',
                            'Upload document folder structure',
                            'Invite residents and owners to the portal',
                        ].map((task, i) => (
                            <div key={i} className="flex items-center gap-2 text-muted-foreground">
                                <div
                                    className="h-5 w-5 rounded-full border-2 border-muted flex items-center justify-center text-[10px] font-bold text-muted-foreground flex-shrink-0">{i + 1}</div>
                                {task}
                            </div>
                        ))}
                    </CardContent>
                </Card>

                <div className="flex gap-3">
                    <Button
                        onClick={() => router.push(effectiveTenantId
                            ? `/admin/sm-organisations/${effectiveTenantId}`
                            : '/admin/sm-organisations')}
                        className="flex-1"
                    >
                        View Organisation
                    </Button>
                    <Button variant="outline" onClick={() => router.push('/admin/buildings')}>
                        Back to Buildings
                    </Button>
                </div>
            </div>
        );
    }

    // -------------------------------------------------------------------------
    // Access gate
    // -------------------------------------------------------------------------
    if (!canAccess) {
        return (
            <div className="flex items-center justify-center min-h-[400px]">
                <Alert variant="destructive" className="max-w-md">
                    <AlertTriangle className="h-4 w-4"/>
                    <AlertDescription>Access restricted to Super Admins, Strata Admins, and Strata
                        Managers.</AlertDescription>
                </Alert>
            </div>
        );
    }

    // -------------------------------------------------------------------------
    // Progress header
    // -------------------------------------------------------------------------
    const progressPct = ( ( step - 1 ) / ( STEPS.length - 1 ) ) * 100;

    const header = (
        <div className="mb-6 space-y-3">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight font-[Manrope] flex items-center gap-2">
                        <Building2 className="h-6 w-6 text-primary"/>
                        New Building Setup
                    </h1>
                    {identity.name && (
                        <p className="text-sm text-muted-foreground mt-0.5">
                            Building: <span className="font-medium text-foreground">{identity.name}</span>
                            {derivedId && <span
                                className="ml-2 font-mono text-xs bg-muted rounded px-1.5 py-0.5">ID: {derivedId}</span>}
                        </p>
                    )}
                </div>
                <div className="flex items-center gap-2">
                    <div
                        className="flex items-center gap-1.5 bg-amber-50 border border-amber-200 rounded-full px-3 py-1.5">
                        <Star className="h-4 w-4 fill-amber-400 text-amber-400"/>
                        <span className="text-sm font-bold text-amber-700">{xpTotal} XP</span>
                    </div>
                </div>
            </div>

            {/* Step dots */}
            <div className="flex items-center gap-1">
                {STEPS.map((s, i) => {
                    const done = completedSteps.has(s.id);
                    const current = s.id === step;
                    return (
                        <React.Fragment key={s.id}>
                            <div className="flex flex-col items-center gap-1">
                                <div
                                    className={`h-8 w-8 rounded-full flex items-center justify-center text-xs font-bold transition-all ${
                                        done ? 'bg-green-500 text-white shadow-sm' :
                                            current ? 'bg-primary text-primary-foreground shadow-md ring-4 ring-primary/20' :
                                                'bg-muted text-muted-foreground'
                                    }`}>
                                    {done ? '✓' : s.id}
                                </div>
                                <span
                                    className={`text-[10px] font-medium ${current ? 'text-primary' : 'text-muted-foreground'}`}>{s.label}</span>
                            </div>
                            {i < STEPS.length - 1 && (
                                <div
                                    className={`flex-1 h-0.5 mb-4 ${completedSteps.has(s.id) ? 'bg-green-400' : 'bg-muted'}`}/>
                            )}
                        </React.Fragment>
                    );
                })}
            </div>

            <Progress value={progressPct} className="h-1.5"/>
        </div>
    );

    const nav = (
        <div className="flex items-center justify-between pt-4 mt-2">
            <Button
                variant="outline"
                onClick={() => {
                    setError(null);
                    setStep(s => s - 1);
                    window.scrollTo({top: 0, behavior: 'smooth'});
                }}
                disabled={step === 1}
                className="gap-1"
            >
                <ChevronLeft className="h-4 w-4"/> Back
            </Button>

            <div className="flex items-center gap-3">
                {STEPS[ step - 1 ]?.xp > 0 && !completedSteps.has(step) && (
                    <XPBadge xp={STEPS[ step - 1 ].xp}/>
                )}
                {step < STEPS.length ? (
                    <Button onClick={advanceStep} className="gap-1">
                        Continue <ChevronRight className="h-4 w-4"/>
                    </Button>
                ) : (
                    <Button onClick={handleSubmit} disabled={submitting} className="gap-2 min-w-[160px]">
                        {submitting ? <Loader2 className="h-4 w-4 animate-spin"/> : <Building2 className="h-4 w-4"/>}
                        {submitting ? 'Creating…' : 'Create Building'}
                    </Button>
                )}
            </div>
        </div>
    );

    // =========================================================================
    // STEP 1 — Building Identity
    // =========================================================================
    const step1 = (
        <Card>
            <CardHeader>
                <div className="flex items-start justify-between">
                    <div>
                        <CardTitle className="flex items-center gap-2 font-[Manrope]">
                            <Building2 className="h-5 w-5 text-blue-500"/>
                            Building Identity
                        </CardTitle>
                        <CardDescription className="mt-1">The legal identity of your strata scheme</CardDescription>
                    </div>
                    <XPBadge xp={10}/>
                </div>
            </CardHeader>
            <CardContent className="space-y-5">
                <StepHint icon={Info}>
                    The Unit Plan number is issued by the Land Titles Office and appears on all legal documents. It
                    becomes the building's unique ID in this platform.
                </StepHint>

                <div className="space-y-2">
                    <FieldLabel required
                                help="The management organisation tenant that will own this new scheme and control its managers, members, and building portfolio.">
                        Management Organisation
                    </FieldLabel>
                    {isSuperAdmin ? (
                        <>
                            <Select value={effectiveTenantId} onValueChange={setSelectedTenantId}>
                                <SelectTrigger data-testid="onboarding-tenant-select" disabled={orgLoading}>
                                    <SelectValue placeholder={orgLoading ? 'Loading organisations…' : 'Select organisation'}/>
                                </SelectTrigger>
                                <SelectContent>
                                    {organisations.map(org => (
                                        <SelectItem key={org.tenant_id} value={org.tenant_id}>
                                            {org.tenant_name}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>
                            <p className="text-xs text-muted-foreground">
                                Choose the management organisation tenant that will own this scheme in the platform.
                            </p>
                        </>
                    ) : (
                        <div className="rounded-lg border bg-muted/40 px-3 py-2 text-sm text-muted-foreground">
                            This scheme will be created under your current management organisation tenant.
                        </div>
                    )}
                </div>

                <div className="space-y-2">
                    <FieldLabel required
                                help="Format: UP12345 or simply 12345. The numeric portion becomes your building's system ID.">
                        Unit Plan Number
                    </FieldLabel>
                    <div className="flex gap-2 items-center">
                        <Input
                            placeholder="e.g. UP13195 or 13195"
                            value={identity.unit_plan_number}
                            onChange={e => setIdentity(p => ( {...p, unit_plan_number: e.target.value} ))}
                            data-testid="unit-plan-number"
                            className="font-mono"
                        />
                        {derivedId && (
                            <div
                                className="flex-shrink-0 flex items-center gap-1.5 bg-green-50 border border-green-200 rounded-lg px-3 py-2 text-xs">
                                <CheckCircle2 className="h-3.5 w-3.5 text-green-600"/>
                                <span className="text-green-700 font-mono font-semibold">{derivedId}</span>
                            </div>
                        )}
                    </div>
                    {derivedId && (
                        <p className="text-xs text-muted-foreground">Building ID will be: <code
                            className="bg-muted px-1 rounded">{derivedId}</code></p>
                    )}
                </div>

                <div className="space-y-2">
                    <FieldLabel required
                                help="The official registered name of the strata scheme, e.g. 'East Gate Residences'.">
                        Complex / Scheme Name
                    </FieldLabel>
                    <Input
                        placeholder="e.g. East Gate Residences"
                        value={identity.name}
                        onChange={e => setIdentity(p => ( {...p, name: e.target.value} ))}
                        data-testid="building-name"
                    />
                </div>

                <div className="space-y-2">
                    <FieldLabel optional
                                help="Helps categorise the building and may affect how some features are presented.">
                        Building Type
                    </FieldLabel>
                    <Select value={identity.building_type}
                            onValueChange={v => setIdentity(p => ( {...p, building_type: v} ))}>
                        <SelectTrigger data-testid="building-type">
                            <SelectValue/>
                        </SelectTrigger>
                        <SelectContent>
                            {BUILDING_TYPES.map(t => (
                                <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>

                <div className="space-y-2">
                    <FieldLabel optional help="A brief description shown on the building's public profile page.">
                        Description
                    </FieldLabel>
                    <Textarea
                        placeholder="Brief description of the building complex…"
                        value={identity.description}
                        onChange={e => setIdentity(p => ( {...p, description: e.target.value} ))}
                        rows={2}
                        className="resize-none"
                    />
                </div>
            </CardContent>
        </Card>
    );

    // =========================================================================
    // STEP 2 — Location & Physical Details
    // =========================================================================
    const step2 = (
        <Card>
            <CardHeader>
                <div className="flex items-start justify-between">
                    <div>
                        <CardTitle className="flex items-center gap-2 font-[Manrope]">
                            <MapPin className="h-5 w-5 text-green-500"/>
                            Location & Physical Details
                        </CardTitle>
                        <CardDescription className="mt-1">Address and building characteristics</CardDescription>
                    </div>
                    <XPBadge xp={15}/>
                </div>
            </CardHeader>
            <CardContent className="space-y-5">
                <StepHint icon={Info}>
                    The full address appears on all official correspondence, levy notices, and the public building
                    profile. It is also used for compliance reporting to the relevant state authority.
                </StepHint>

                <div className="space-y-2">
                    <FieldLabel required help="Street number and street name only. Suburb is entered separately below.">
                        Street Address
                    </FieldLabel>
                    <Input
                        placeholder="e.g. 14 Hoolihan Street"
                        value={location.address}
                        onChange={e => setLocation(p => ( {...p, address: e.target.value} ))}
                        data-testid="street-address"
                    />
                </div>

                <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <FieldLabel optional>Suburb / City</FieldLabel>
                        <Input
                            placeholder="e.g. Denman Prospect"
                            value={location.suburb}
                            onChange={e => setLocation(p => ( {...p, suburb: e.target.value} ))}
                        />
                    </div>
                    <div className="space-y-2">
                        <FieldLabel optional>Postcode</FieldLabel>
                        <Input
                            placeholder="e.g. 2611"
                            value={location.postcode}
                            onChange={e => setLocation(p => ( {...p, postcode: e.target.value} ))}
                            maxLength={4}
                        />
                    </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <FieldLabel required help="Determines the state legislation that governs this strata scheme.">
                            State / Territory
                        </FieldLabel>
                        <Select value={location.state} onValueChange={v => {
                            setLocation(p => ( {...p, state: v} ));
                            setFinancial(p => ( {...p, state_jurisdiction: v} ));
                        }}>
                            <SelectTrigger data-testid="state-select">
                                <SelectValue/>
                            </SelectTrigger>
                            <SelectContent>
                                {STATES.map(s => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="space-y-2">
                        <FieldLabel optional
                                    help="Used for age-based compliance tracking (e.g. building warranty expiry).">
                            Year Built
                        </FieldLabel>
                        <Input
                            type="number"
                            placeholder="e.g. 2018"
                            value={location.year_built}
                            onChange={e => setLocation(p => ( {...p, year_built: e.target.value} ))}
                            min={1900}
                            max={new Date().getFullYear()}
                        />
                    </div>
                </div>

                <Separator/>

                <div className="space-y-3">
                    <p className="text-sm font-medium text-muted-foreground uppercase tracking-wide text-[11px]">Building
                        Contact (Optional)</p>
                    <div className="grid grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <FieldLabel optional>Contact Email</FieldLabel>
                            <Input
                                type="email"
                                placeholder="ec@example.com.au"
                                value={location.contact_email}
                                onChange={e => setLocation(p => ( {...p, contact_email: e.target.value} ))}
                            />
                        </div>
                        <div className="space-y-2">
                            <FieldLabel optional>Contact Phone</FieldLabel>
                            <Input
                                placeholder="(02) 6285 0325"
                                value={location.contact_phone}
                                onChange={e => setLocation(p => ( {...p, contact_phone: e.target.value} ))}
                            />
                        </div>
                    </div>
                    <div className="space-y-2">
                        <FieldLabel optional>Website</FieldLabel>
                        <Input
                            placeholder="https://example.com.au"
                            value={location.website}
                            onChange={e => setLocation(p => ( {...p, website: e.target.value} ))}
                        />
                    </div>
                </div>
            </CardContent>
        </Card>
    );
    // =========================================================================
    // =========================================================================
    // STEP 3 — Unit & Lot Configuration
    // =========================================================================
    /**
     * @generated FunctionHeader
     * Function: toggleLotType
     * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const toggleLotType = (type) => {
        setUnitConfig(prev => ( {
            ...prev,
            lot_types_present: prev.lot_types_present.includes(type)
                ? prev.lot_types_present.filter(t => t !== type)
                : [...prev.lot_types_present, type],
        } ));
    };
    /**
     * @generated FunctionHeader
     * Function: addLotRange
     * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const addLotRange = (lotType) => {
        setUnitConfig(prev => ( {
            ...prev,
            lot_type_ranges: [...prev.lot_type_ranges, {
                _key: Date.now(), lot_type: lotType, range_mode: 'range',
                start: '', end: '', numbers: '', count: '', act_class: '',
            }],
        } ));
    };
    /**
     * @generated FunctionHeader
     * Function: updateLotRange
     * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateLotRange = (key, field, val) => {
        setUnitConfig(prev => ( {
            ...prev,
            lot_type_ranges: prev.lot_type_ranges.map(r => r._key === key ? {...r, [ field ]: val} : r),
        } ));
    };
    /**
     * @generated FunctionHeader
     * Function: removeLotRange
     * Path: frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const removeLotRange = (key) => {
        setUnitConfig(prev => ( {...prev, lot_type_ranges: prev.lot_type_ranges.filter(r => r._key !== key)} ));
    };

    const isACT = location.state === 'ACT';

    const step3 = (
        <Card>
            <CardHeader>
                <div className="flex items-start justify-between">
                    <div>
                        <CardTitle className="flex items-center gap-2 font-[Manrope]">
                            <BarChart3 className="h-5 w-5 text-violet-500"/>
                            Lot & Unit Configuration
                        </CardTitle>
                        <CardDescription className="mt-1">
                            How lots are classified within this strata scheme
                        </CardDescription>
                    </div>
                    <XPBadge xp={20}/>
                </div>
            </CardHeader>
            <CardContent className="space-y-6">
                <StepHint icon={Info}>
                    Under Australian strata law, lots within a scheme may serve different purposes (residential,
                    commercial, retail). In the ACT, lots are further designated as <strong>Class A</strong> (apartments
                    with floor/ceiling/wall boundaries) or <strong>Class B</strong> (townhouses with ground-level
                    surveyed boundaries). This configuration drives levy entitlements and compliance obligations.
                </StepHint>

                {/* Use type */}
                <div className="space-y-3">
                    <FieldLabel required
                                help="Determines which BCA building classes and levy calculation rules apply to this scheme.">
                        Scheme Use Type
                    </FieldLabel>
                    <div className="grid grid-cols-1 gap-2">
                        {USE_TYPES.map(ut => (
                            <button
                                key={ut.value}
                                type="button"
                                onClick={() => setUnitConfig(p => ( {...p, use_type: ut.value} ))}
                                className={`flex items-start gap-3 rounded-xl border-2 px-4 py-3 text-left transition-all ${
                                    unitConfig.use_type === ut.value
                                        ? 'border-violet-400 bg-violet-50 text-violet-900'
                                        : 'border-border hover:border-violet-200'
                                }`}
                            >
                                <div
                                    className={`mt-0.5 h-4 w-4 rounded-full border-2 flex-shrink-0 ${unitConfig.use_type === ut.value ? 'border-violet-500 bg-violet-500' : 'border-muted-foreground'}`}/>
                                <div>
                                    <p className="text-sm font-semibold">{ut.label}</p>
                                    <p className="text-xs text-muted-foreground">{ut.desc}</p>
                                </div>
                            </button>
                        ))}
                    </div>
                </div>

                <Separator/>

                {/* Lot types present */}
                <div className="space-y-3">
                    <FieldLabel required
                                help="Select every lot type present in this scheme. This is used to configure separate levy schedules and compliance workflows per lot type.">
                        Lot Types Present
                    </FieldLabel>
                    <div className="grid grid-cols-2 gap-2">
                        {LOT_TYPES
                            .filter(lt => unitConfig.use_type === 'commercial'
                                ? !['apartment', 'townhouse', 'villa'].includes(lt.value)
                                : unitConfig.use_type === 'residential'
                                    ? ['apartment', 'townhouse', 'villa', 'carpark', 'storage'].includes(lt.value)
                                    : true
                            )
                            .map(lt => {
                                const selected = unitConfig.lot_types_present.includes(lt.value);
                                return (
                                    <button
                                        key={lt.value}
                                        type="button"
                                        onClick={() => toggleLotType(lt.value)}
                                        className={`flex items-center gap-2.5 rounded-lg border-2 px-3 py-2.5 text-sm text-left transition-all ${
                                            selected ? 'border-violet-400 bg-violet-50' : 'border-border hover:border-violet-200'
                                        }`}
                                    >
                                        <span className="text-lg leading-none">{lt.icon}</span>
                                        <div className="flex-1 min-w-0">
                                            <p className={`font-medium ${selected ? 'text-violet-800' : 'text-foreground'}`}>{lt.label}</p>
                                            <p className="text-[10px] text-muted-foreground">BCA Class {lt.bca}</p>
                                        </div>
                                        {selected && <CheckCircle2 className="h-4 w-4 text-violet-600 flex-shrink-0"/>}
                                    </button>
                                );
                            })}
                    </div>
                </div>

                <Separator/>

                {/* ACT Class A / B */}
                {isACT && (
                    <div className="space-y-4 rounded-xl border-2 border-blue-200 bg-blue-50/40 p-4">
                        <div className="flex items-start gap-3">
                            <Checkbox
                                id="class-ab"
                                checked={unitConfig.has_class_a_b}
                                onCheckedChange={v => setUnitConfig(p => ( {...p, has_class_a_b: !!v} ))}
                                className="mt-0.5"
                            />
                            <div>
                                <label htmlFor="class-ab"
                                       className="text-sm font-semibold cursor-pointer text-blue-800">
                                    ACT Class A / Class B Demarcation
                                </label>
                                <p className="text-xs text-blue-600 mt-0.5">
                                    Under the <em>Unit Titles Act 2001</em> (ACT), lots are designated as <strong>Class
                                    A</strong> (apartments — boundaries defined by floors, walls, ceilings) or <strong>Class
                                    B</strong> (townhouses — ground-level with surveyed boundaries). Tick if this
                                    distinction appears on your Unit Plan.
                                </p>
                            </div>
                        </div>
                        {unitConfig.has_class_a_b && (
                            <div className="grid grid-cols-2 gap-4 ml-7">
                                <div className="space-y-2">
                                    <FieldLabel optional
                                                help="Number of lots formally designated as Class A (multi-storey apartments) on the registered Unit Plan.">
                                        Class A Lot Count
                                    </FieldLabel>
                                    <Input
                                        type="number" min={0}
                                        placeholder="e.g. 64"
                                        value={unitConfig.act_class_a_count}
                                        onChange={e => setUnitConfig(p => ( {
                                            ...p,
                                            act_class_a_count: e.target.value
                                        } ))}
                                        className="h-9"
                                    />
                                    <p className="text-[10px] text-muted-foreground">Apartments / multi-storey units</p>
                                </div>
                                <div className="space-y-2">
                                    <FieldLabel optional
                                                help="Number of lots formally designated as Class B (townhouses, ground-level) on the registered Unit Plan.">
                                        Class B Lot Count
                                    </FieldLabel>
                                    <Input
                                        type="number" min={0}
                                        placeholder="e.g. 23"
                                        value={unitConfig.act_class_b_count}
                                        onChange={e => setUnitConfig(p => ( {
                                            ...p,
                                            act_class_b_count: e.target.value
                                        } ))}
                                        className="h-9"
                                    />
                                    <p className="text-[10px] text-muted-foreground">Townhouses / ground-level units</p>
                                </div>
                            </div>
                        )}
                    </div>
                )}

                {/* Lot number ranges */}
                {( unitConfig.lot_types_present.length > 1 || unitConfig.use_type === 'mixed' ) && (
                    <div className="space-y-3">
                        <div className="flex items-center justify-between">
                            <FieldLabel optional
                                        help="Specify which lot numbers belong to each type. Use ranges (e.g. 1–40 apartments) or a comma-separated list for non-linear sequences (e.g. 1,3,5,7 for odd-numbered townhouses).">
                                Lot Number Breakdown by Type
                            </FieldLabel>
                        </div>

                        {unitConfig.lot_type_ranges.length === 0 && (
                            <div
                                className="rounded-lg border border-dashed border-muted-foreground/30 px-4 py-3 text-center text-xs text-muted-foreground">
                                No ranges defined yet. Add a range below to specify which lot numbers belong to each
                                type.
                                <br/>You can also do this later from Admin → Strata Roll.
                            </div>
                        )}

                        {unitConfig.lot_type_ranges.map(r => (
                            <div key={r._key} className="rounded-lg border bg-muted/20 p-3 space-y-3">
                                <div className="flex items-center justify-between gap-2">
                                    <Select value={r.lot_type}
                                            onValueChange={v => updateLotRange(r._key, 'lot_type', v)}>
                                        <SelectTrigger className="h-8 w-40 text-xs">
                                            <SelectValue/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            {unitConfig.lot_types_present.map(t => (
                                                <SelectItem key={t} value={t}>
                                                    {LOT_TYPES.find(l => l.value === t)?.label || t}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                    <div className="flex gap-1">
                                        {['range', 'nonlinear', 'count'].map(mode => (
                                            <button
                                                key={mode}
                                                type="button"
                                                onClick={() => updateLotRange(r._key, 'range_mode', mode)}
                                                className={`text-[10px] px-2 py-1 rounded border font-medium transition-colors ${r.range_mode === mode ? 'border-primary bg-primary/10 text-primary' : 'border-border text-muted-foreground'}`}
                                            >
                                                {mode === 'range' ? 'Range' : mode === 'nonlinear' ? 'Non-linear' : 'Count only'}
                                            </button>
                                        ))}
                                    </div>
                                    <button type="button" onClick={() => removeLotRange(r._key)}
                                            className="text-muted-foreground hover:text-destructive">
                                        <Trash2 className="h-3.5 w-3.5"/>
                                    </button>
                                </div>

                                {r.range_mode === 'range' && (
                                    <div className="flex items-center gap-2">
                                        <Input className="h-8 text-sm w-24" placeholder="Lot from" value={r.start}
                                               onChange={e => updateLotRange(r._key, 'start', e.target.value)}/>
                                        <span className="text-muted-foreground text-sm">—</span>
                                        <Input className="h-8 text-sm w-24" placeholder="Lot to" value={r.end}
                                               onChange={e => updateLotRange(r._key, 'end', e.target.value)}/>
                                        {isACT && unitConfig.has_class_a_b && (
                                            <Select value={r.act_class}
                                                    onValueChange={v => updateLotRange(r._key, 'act_class', v)}>
                                                <SelectTrigger className="h-8 w-28 text-xs"><SelectValue
                                                    placeholder="ACT class"/></SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value="A">Class A</SelectItem>
                                                    <SelectItem value="B">Class B</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        )}
                                    </div>
                                )}

                                {r.range_mode === 'nonlinear' && (
                                    <div className="space-y-1">
                                        <Input
                                            className="h-8 text-sm"
                                            placeholder="e.g. 1, 3, 5, 7, 12, 15"
                                            value={r.numbers}
                                            onChange={e => updateLotRange(r._key, 'numbers', e.target.value)}
                                        />
                                        <p className="text-[10px] text-muted-foreground">Comma-separated lot numbers for
                                            non-sequential arrangements</p>
                                    </div>
                                )}

                                {r.range_mode === 'count' && (
                                    <Input className="h-8 text-sm w-32" placeholder="Number of lots" type="number"
                                           min={1} value={r.count}
                                           onChange={e => updateLotRange(r._key, 'count', e.target.value)}/>
                                )}
                            </div>
                        ))}

                        <div className="flex flex-wrap gap-2">
                            {unitConfig.lot_types_present.map(t => (
                                <Button key={t} type="button" variant="outline" size="sm" onClick={() => addLotRange(t)}
                                        className="gap-1.5 text-xs border-dashed h-8">
                                    <Plus className="h-3 w-3"/>
                                    Add {LOT_TYPES.find(l => l.value === t)?.label || t} range
                                </Button>
                            ))}
                        </div>
                    </div>
                )}
            </CardContent>
        </Card>
    );

    // STEP 3 — Financial & Legal Configuration
    // =========================================================================
    const step4 = (
        <Card>
            <CardHeader>
                <div className="flex items-start justify-between">
                    <div>
                        <CardTitle className="flex items-center gap-2 font-[Manrope]">
                            <DollarSign className="h-5 w-5 text-amber-500"/>
                            Financial & Legal Configuration
                        </CardTitle>
                        <CardDescription className="mt-1">Levy schedule, financial year, and compliance
                            settings</CardDescription>
                    </div>
                    <XPBadge xp={20}/>
                </div>
            </CardHeader>
            <CardContent className="space-y-5">
                <StepHint icon={Calendar}>
                    These settings determine when levy notices are generated and when payments are due. They can be
                    refined later but getting them right now saves work.
                </StepHint>

                <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <FieldLabel required
                                    help="Total number of lots/units in the strata scheme. Used for levy calculations.">
                            Total Lots / Units
                        </FieldLabel>
                        <Input
                            type="number"
                            placeholder="e.g. 87"
                            min={1}
                            value={financial.lot_count}
                            onChange={e => setFinancial(p => ( {...p, lot_count: e.target.value} ))}
                            data-testid="lot-count"
                        />
                    </div>
                    <div className="space-y-2">
                        <FieldLabel optional
                                    help="Sum of all unit entitlements across all lots. Required for proportional levy calculation.">
                            Total Unit Entitlement
                        </FieldLabel>
                        <Input
                            type="number"
                            placeholder="e.g. 8700"
                            min={1}
                            value={financial.aggregate_unit_entitlement}
                            onChange={e => setFinancial(p => ( {...p, aggregate_unit_entitlement: e.target.value} ))}
                        />
                    </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <FieldLabel required
                                    help="Most Australian strata schemes end their financial year in June (month 6). Check your scheme's constitution.">
                            Financial Year End Month
                        </FieldLabel>
                        <Select value={financial.financial_year_end_month}
                                onValueChange={v => setFinancial(p => ( {...p, financial_year_end_month: v} ))}>
                            <SelectTrigger>
                                <SelectValue/>
                            </SelectTrigger>
                            <SelectContent>
                                {MONTHS.map((m, i) => (
                                    <SelectItem key={i + 1} value={String(i + 1)}>{m}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="space-y-2">
                        <FieldLabel optional
                                    help="The state law that governs this scheme. Defaults to the building's state.">
                            State Jurisdiction
                        </FieldLabel>
                        <Select value={financial.state_jurisdiction}
                                onValueChange={v => setFinancial(p => ( {...p, state_jurisdiction: v} ))}>
                            <SelectTrigger>
                                <SelectValue/>
                            </SelectTrigger>
                            <SelectContent>
                                {STATES.map(s => <SelectItem key={s} value={s}>{s}</SelectItem>)}
                            </SelectContent>
                        </Select>
                    </div>
                </div>

                <div className="space-y-3">
                    <FieldLabel optional
                                help="Select which months levy notices are due. Most schemes use quarterly billing: Mar, Jun, Sep, Dec.">
                        Levy Due Months
                    </FieldLabel>
                    <div className="grid grid-cols-4 gap-2">
                        {LEVY_MONTHS.map(m => (
                            <label key={m}
                                   className={`flex items-center gap-2 rounded-lg border px-3 py-2 cursor-pointer transition-colors text-sm ${
                                       financial.levy_due_months.includes(m) ? 'border-primary bg-primary/5 text-primary font-medium' : 'border-border text-muted-foreground hover:border-primary/40'
                                   }`}>
                                <Checkbox
                                    checked={financial.levy_due_months.includes(m)}
                                    onCheckedChange={() => toggleLevy(m)}
                                    className="h-3.5 w-3.5"
                                />
                                {MONTHS[ m - 1 ].slice(0, 3)}
                            </label>
                        ))}
                    </div>
                </div>

                <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                        <FieldLabel optional
                                    help="'Last' means the last day of the levy month. 'Custom' lets you set exact dates.">
                            Levy Due Day
                        </FieldLabel>
                        <Select value={financial.levy_due_day_type}
                                data-testid="levy-due-day-type"
                                onValueChange={v => setFinancial(p => ( {
                                    ...p,
                                    levy_due_day_type: v,
                                    levy_due_custom_dates: v === 'custom'
                                        ? Object.fromEntries(p.levy_due_months.map(month => [
                                            month,
                                            p.levy_due_custom_dates[ month ] || defaultLevyDueDayForMonth(month),
                                        ]))
                                        : p.levy_due_custom_dates,
                                } ))}>
                            <SelectTrigger>
                                <SelectValue/>
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="first">1st of month</SelectItem>
                                <SelectItem value="middle">15th of month</SelectItem>
                                <SelectItem value="last">Last day of month</SelectItem>
                                <SelectItem value="custom">Custom dates</SelectItem>
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="space-y-2">
                        <FieldLabel optional>Timezone</FieldLabel>
                        <Select value={financial.timezone}
                                onValueChange={v => setFinancial(p => ( {...p, timezone: v} ))}>
                            <SelectTrigger>
                                <SelectValue/>
                            </SelectTrigger>
                            <SelectContent>
                                {TIMEZONES.map(tz => (
                                    <SelectItem key={tz} value={tz}>{tz.replace('Australia/', '')}</SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                </div>

                {financial.levy_due_day_type === 'custom' && (
                    <div className="space-y-3 rounded-lg border bg-muted/20 p-4" data-testid="custom-levy-due-dates">
                        <div>
                            <p className="text-sm font-medium">Custom Levy Due Dates</p>
                            <p className="text-xs text-muted-foreground">
                                Enter the day of month for each selected levy due month. If a month has fewer days,
                                the backend clips the due date to that month's last valid day.
                            </p>
                        </div>
                        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                            {financial.levy_due_months.map(month => (
                                <div key={month} className="space-y-1.5">
                                    <label
                                        htmlFor={`levy-due-day-${month}`}
                                        className="text-xs font-medium text-muted-foreground"
                                    >
                                        {MONTHS[ month - 1 ]}
                                    </label>
                                    <Input
                                        id={`levy-due-day-${month}`}
                                        type="number"
                                        inputMode="numeric"
                                        min={1}
                                        max={31}
                                        value={financial.levy_due_custom_dates[ month ] || ''}
                                        onChange={e => updateCustomLevyDueDay(month, e.target.value)}
                                        data-testid={`custom-levy-due-day-${month}`}
                                    />
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                <div className="flex items-start gap-3 rounded-lg border bg-muted/30 px-4 py-3">
                    <Checkbox
                        id="gst-registered"
                        checked={financial.gst_registered}
                        onCheckedChange={v => setFinancial(p => ( {...p, gst_registered: !!v} ))}
                        className="mt-0.5"
                    />
                    <div>
                        <label htmlFor="gst-registered" className="text-sm font-medium cursor-pointer">
                            GST Registered (ATO)
                        </label>
                        <p className="text-xs text-muted-foreground mt-0.5">
                            Registration is mandatory if the Owners Corporation's annual turnover exceeds $150,000
                            (non-profit threshold). Affects levy notices and financial statements.
                        </p>
                    </div>
                </div>
            </CardContent>
        </Card>
    );

    // =========================================================================
    // STEP 4 — Strata Manager
    // =========================================================================
    const step5 = (
        <Card>
            <CardHeader>
                <div className="flex items-start justify-between">
                    <div>
                        <CardTitle className="flex items-center gap-2 font-[Manrope]">
                            <UserPlus className="h-5 w-5 text-purple-500"/>
                            Assign Strata Manager
                        </CardTitle>
                        <CardDescription className="mt-1">The professional responsible for day-to-day building
                            management</CardDescription>
                    </div>
                    <div className="flex items-center gap-2">
                        <Badge variant="outline" className="text-xs text-slate-500 border-slate-200">Recommended</Badge>
                        <XPBadge xp={20}/>
                    </div>
                </div>
            </CardHeader>
            <CardContent className="space-y-5">
                <StepHint icon={Info}>
                    The Strata Manager can be an existing platform user or someone you invite. They get access to
                    financial, maintenance, and communication tools. You can skip this and assign later.
                </StepHint>

                <div className="grid grid-cols-3 gap-2">
                    {[
                        {value: 'search', label: 'Existing User', icon: Search},
                        {value: 'invite', label: 'Invite by Email', icon: UserPlus},
                        {value: 'skip', label: 'Skip for Now', icon: X},
                    ].map(opt => (
                        <button
                            key={opt.value}
                            type="button"
                            onClick={() => setSm(p => ( {...p, mode: opt.value} ))}
                            className={`flex flex-col items-center gap-2 rounded-xl border-2 px-3 py-4 text-sm font-medium transition-all ${
                                sm.mode === opt.value ? 'border-primary bg-primary/5 text-primary' : 'border-border text-muted-foreground hover:border-primary/30'
                            }`}
                        >
                            <opt.icon className="h-5 w-5"/>
                            {opt.label}
                        </button>
                    ))}
                </div>

                {sm.mode === 'search' && (
                    <div className="space-y-3">
                        <div className="relative">
                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground"/>
                            <Input
                                className="pl-9"
                                placeholder="Search by name or email…"
                                value={smQuery}
                                onChange={e => setSmQuery(e.target.value)}
                            />
                        </div>
                        {smSearching && <div className="flex items-center gap-2 text-xs text-muted-foreground"><Loader2
                            className="h-3 w-3 animate-spin"/> Searching…</div>}
                        {smResults.length > 0 && (
                            <div className="border rounded-lg divide-y overflow-hidden">
                                {smResults.map(u => (
                                    <button
                                        key={u.id}
                                        type="button"
                                        onClick={() => {
                                            setSm(p => ( {...p, user_id: u.id, selected_user: u} ));
                                            setSmResults([]);
                                            setSmQuery('');
                                        }}
                                        className={`w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-muted/50 transition-colors ${sm.user_id === u.id ? 'bg-primary/5' : ''}`}
                                    >
                                        <div
                                            className="h-8 w-8 rounded-full bg-primary/10 flex items-center justify-center text-sm font-bold text-primary flex-shrink-0">
                                            {( u.full_name || u.email || '?' )[ 0 ].toUpperCase()}
                                        </div>
                                        <div>
                                            <p className="text-sm font-medium">{u.full_name || 'Unknown'}</p>
                                            <p className="text-xs text-muted-foreground">{u.email}</p>
                                        </div>
                                        {sm.user_id === u.id &&
                                            <CheckCircle2 className="h-4 w-4 text-green-600 ml-auto"/>}
                                    </button>
                                ))}
                            </div>
                        )}
                        {sm.selected_user && (
                            <div
                                className="flex items-center gap-2 rounded-lg bg-green-50 border border-green-200 px-3 py-2.5">
                                <CheckCircle2 className="h-4 w-4 text-green-600 flex-shrink-0"/>
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium text-green-800">{sm.selected_user.full_name}</p>
                                    <p className="text-xs text-green-600">{sm.selected_user.email}</p>
                                </div>
                                <button type="button"
                                        onClick={() => setSm(p => ( {...p, user_id: '', selected_user: null} ))}
                                        className="text-green-600 hover:text-green-800">
                                    <X className="h-4 w-4"/>
                                </button>
                            </div>
                        )}
                    </div>
                )}

                {sm.mode === 'invite' && (
                    <div className="space-y-4">
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <FieldLabel optional>Full Name</FieldLabel>
                                <Input placeholder="Jane Smith" value={sm.invite_name}
                                       onChange={e => setSm(p => ( {...p, invite_name: e.target.value} ))}/>
                            </div>
                            <div className="space-y-2">
                                <FieldLabel optional>Email Address</FieldLabel>
                                <Input type="email" placeholder="manager@firm.com.au" value={sm.invite_email}
                                       onChange={e => setSm(p => ( {...p, invite_email: e.target.value} ))}/>
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <FieldLabel optional>Company / Firm</FieldLabel>
                                <Input placeholder="Silverfox Property" value={sm.company_name}
                                       onChange={e => setSm(p => ( {...p, company_name: e.target.value} ))}/>
                            </div>
                            <div className="space-y-2">
                                <FieldLabel optional>Phone</FieldLabel>
                                <Input placeholder="(02) 6285 0325" value={sm.phone}
                                       onChange={e => setSm(p => ( {...p, phone: e.target.value} ))}/>
                            </div>
                        </div>
                        <div className="space-y-2">
                            <FieldLabel optional
                                        help="The state-issued strata management licence number for compliance records.">
                                Licence Number
                            </FieldLabel>
                            <Input placeholder="NSW STRATA LIC 12345" value={sm.license_number}
                                   onChange={e => setSm(p => ( {...p, license_number: e.target.value} ))}/>
                        </div>
                    </div>
                )}

                {sm.mode === 'skip' && (
                    <div className="rounded-lg bg-amber-50 border border-amber-200 px-4 py-3 text-sm text-amber-700">
                        <p className="font-medium">Skipping strata manager assignment</p>
                        <p className="text-xs mt-1 text-amber-600">You can assign a strata manager later from Admin →
                            Buildings → Manage.</p>
                    </div>
                )}
            </CardContent>
        </Card>
    );

    // =========================================================================
    // STEP 5 — Executive Committee
    // =========================================================================
    const step6 = (
        <Card>
            <CardHeader>
                <div className="flex items-start justify-between">
                    <div>
                        <CardTitle className="flex items-center gap-2 font-[Manrope]">
                            <Users className="h-5 w-5 text-rose-500"/>
                            Executive Committee
                        </CardTitle>
                        <CardDescription className="mt-1">Governance body that oversees the Owners
                            Corporation</CardDescription>
                    </div>
                    <XPBadge xp={25}/>
                </div>
            </CardHeader>
            <CardContent className="space-y-5">
                <StepHint icon={Info}>
                    The EC is the elected governing body of the Owners Corporation. A <strong>Chairman</strong> is
                    required for the building to go live. Positions: Chairman, Treasurer, Secretary, Member (max 7).
                </StepHint>

                <div className="space-y-4">
                    {ecMembers.map((member, idx) => (
                        <div key={member._key}
                             className={`rounded-xl border-2 p-4 space-y-4 ${member.position === 'CHAIRMAN' ? 'border-rose-200 bg-rose-50/30' : 'border-border'}`}>
                            <div className="flex items-center justify-between">
                                <div className="flex items-center gap-2">
                                    <span className={`text-xs font-bold px-2 py-0.5 rounded-full ${
                                        member.position === 'CHAIRMAN' ? 'bg-rose-100 text-rose-700' :
                                            member.position === 'TREASURER' ? 'bg-blue-100 text-blue-700' :
                                                member.position === 'SECRETARY' ? 'bg-green-100 text-green-700' :
                                                    'bg-slate-100 text-slate-700'
                                    }`}>{member.position}</span>
                                    {member.position === 'CHAIRMAN' && <Badge variant="outline"
                                                                              className="text-[10px] h-4 px-1.5 text-red-600 border-red-200 bg-red-50">Required</Badge>}
                                </div>
                                <div className="flex items-center gap-2">
                                    <Select value={member.position}
                                            onValueChange={v => updateECMember(member._key, 'position', v)}>
                                        <SelectTrigger className="h-8 w-36 text-xs">
                                            <SelectValue/>
                                        </SelectTrigger>
                                        <SelectContent>
                                            {EC_POSITIONS.map(p => <SelectItem key={p} value={p}>{p}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                    {ecMembers.length > 1 && (
                                        <button type="button" onClick={() => removeECMember(member._key)}
                                                className="text-muted-foreground hover:text-destructive transition-colors">
                                            <Trash2 className="h-4 w-4"/>
                                        </button>
                                    )}
                                </div>
                            </div>

                            <div className="grid grid-cols-2 gap-2 mb-3">
                                {[
                                    {value: 'search', label: 'Existing User', icon: Search},
                                    {value: 'invite', label: 'Add Details', icon: UserPlus},
                                ].map(opt => (
                                    <button
                                        key={opt.value}
                                        type="button"
                                        onClick={() => updateECMember(member._key, 'mode', opt.value)}
                                        className={`flex items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium transition-all ${
                                            member.mode === opt.value ? 'border-primary bg-primary/5 text-primary' : 'border-border text-muted-foreground hover:border-primary/30'
                                        }`}
                                    >
                                        <opt.icon className="h-3.5 w-3.5"/>
                                        {opt.label}
                                    </button>
                                ))}
                            </div>

                            {member.mode === 'search' ? (
                                <div className="space-y-2">
                                    <div className="relative">
                                        <Search
                                            className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground"/>
                                        <Input
                                            className="pl-8 h-9 text-sm"
                                            placeholder="Search name or email…"
                                            value={ecQuery[ member._key ] || ''}
                                            onChange={e => {
                                                const q = e.target.value;
                                                setEcQuery(prev => ( {...prev, [ member._key ]: q} ));
                                                clearTimeout(ecDebounce.current[ member._key ]);
                                                ecDebounce.current[ member._key ] = setTimeout(() => searchEC(member._key, q), 350);
                                            }}
                                        />
                                    </div>
                                    {ecSearching[ member._key ] &&
                                        <p className="text-xs text-muted-foreground flex items-center gap-1"><Loader2
                                            className="h-3 w-3 animate-spin"/>Searching…</p>}
                                    {( ecResults[ member._key ] || [] ).length > 0 && (
                                        <div className="border rounded-lg divide-y overflow-hidden text-sm">
                                            {( ecResults[ member._key ] || [] ).map(u => (
                                                <button
                                                    key={u.id}
                                                    type="button"
                                                    onClick={() => {
                                                        updateECMember(member._key, 'user_id', u.id);
                                                        updateECMember(member._key, 'selected_user', u);
                                                        setEcResults(prev => ( {...prev, [ member._key ]: []} ));
                                                        setEcQuery(prev => ( {...prev, [ member._key ]: ''} ));
                                                    }}
                                                    className="w-full flex items-center gap-3 px-3 py-2 hover:bg-muted/50"
                                                >
                                                    <div
                                                        className="h-7 w-7 rounded-full bg-primary/10 flex items-center justify-center text-xs font-bold text-primary flex-shrink-0">
                                                        {( u.full_name || u.email || '?' )[ 0 ].toUpperCase()}
                                                    </div>
                                                    <div className="flex-1 min-w-0 text-left">
                                                        <p className="font-medium truncate">{u.full_name || 'Unknown'}</p>
                                                        <p className="text-xs text-muted-foreground truncate">{u.email} ·
                                                            Unit {u.unit_number || '?'}</p>
                                                    </div>
                                                </button>
                                            ))}
                                        </div>
                                    )}
                                    {member.selected_user && (
                                        <div
                                            className="flex items-center gap-2 rounded-lg bg-green-50 border border-green-200 px-3 py-2">
                                            <CheckCircle2 className="h-4 w-4 text-green-600 flex-shrink-0"/>
                                            <div className="flex-1 min-w-0">
                                                <p className="text-sm font-medium text-green-800">{member.selected_user.full_name}</p>
                                                <p className="text-xs text-green-600">{member.selected_user.email}</p>
                                            </div>
                                            <button type="button" onClick={() => {
                                                updateECMember(member._key, 'user_id', '');
                                                updateECMember(member._key, 'selected_user', null);
                                            }} className="text-green-600 hover:text-green-800">
                                                <X className="h-3.5 w-3.5"/>
                                            </button>
                                        </div>
                                    )}
                                </div>
                            ) : (
                                <div className="space-y-3">
                                    <div className="grid grid-cols-2 gap-3">
                                        <div className="space-y-1.5">
                                            <FieldLabel required={member.position === 'CHAIRMAN'}
                                                        optional={member.position !== 'CHAIRMAN'}>Full Name</FieldLabel>
                                            <Input className="h-9 text-sm" placeholder="Alex Johnson"
                                                   value={member.invite_name}
                                                   onChange={e => updateECMember(member._key, 'invite_name', e.target.value)}
                                                   data-testid={member.position === 'CHAIRMAN' ? 'chairman-invite-name' : undefined}/>
                                        </div>
                                        <div className="space-y-1.5">
                                            <FieldLabel required={member.position === 'CHAIRMAN'}
                                                        optional={member.position !== 'CHAIRMAN'}>Email</FieldLabel>
                                            <Input className="h-9 text-sm" type="email" placeholder="alex@example.com"
                                                   value={member.invite_email}
                                                   onChange={e => updateECMember(member._key, 'invite_email', e.target.value)}
                                                   data-testid={member.position === 'CHAIRMAN' ? 'chairman-invite-email' : undefined}/>
                                        </div>
                                    </div>
                                    <div className="space-y-1.5">
                                        <FieldLabel optional>Unit Number</FieldLabel>
                                        <Input className="h-9 text-sm w-32" placeholder="e.g. 42"
                                               value={member.unit_number}
                                               onChange={e => updateECMember(member._key, 'unit_number', e.target.value)}/>
                                    </div>
                                </div>
                            )}
                        </div>
                    ))}
                </div>

                {ecMembers.length < 7 && (
                    <Button type="button" variant="outline" onClick={addECMember}
                            className="w-full gap-2 border-dashed">
                        <Plus className="h-4 w-4"/>
                        Add EC Member
                    </Button>
                )}
            </CardContent>
        </Card>
    );

    // =========================================================================
    // STEP 6 — Import Data (Optional)
    // =========================================================================
    const step7 = (
        <Card>
            <CardHeader>
                <div className="flex items-start justify-between">
                    <div>
                        <CardTitle className="flex items-center gap-2 font-[Manrope]">
                            <Upload className="h-5 w-5 text-teal-500"/>
                            Import Existing Data
                        </CardTitle>
                        <CardDescription className="mt-1">All uploads are optional and can be done after
                            setup</CardDescription>
                    </div>
                    <div className="flex items-center gap-2">
                        <Badge variant="outline" className="text-xs text-slate-500 border-slate-200">All
                            Optional</Badge>
                        <XPBadge xp={10}/>
                    </div>
                </div>
            </CardHeader>
            <CardContent className="space-y-6">
                <StepHint icon={Info}>
                    Importing existing data now saves time and ensures owners receive their first levy notice correctly.
                    All three uploads are optional — you can do them later from Admin → Strata Roll or Admin → Financial
                    Data.
                </StepHint>

                {[
                    {
                        key: 'strata_roll',
                        title: 'Strata Roll (Owners List)',
                        icon: Users,
                        accept: '.csv',
                        color: 'blue',
                        description: 'CSV with columns: unit_number, owner_name, owner_email, entitlement, phone (optional)',
                        benefit: 'Imported immediately when you click Create Building — populates all unit records and allows levy notices to be sent right away.',
                    },
                    {
                        key: 'financials',
                        title: 'Previous Financial Statements',
                        icon: BarChart3,
                        accept: '.pdf,.xlsx',
                        color: 'green',
                        description: 'PDF or Excel of the most recent annual financial statements.',
                        benefit: 'Not imported by this quick-create form — attach here as a reminder, then import via the full Onboarding Wizard.',
                    },
                    {
                        key: 'levies',
                        title: 'Previous Year Levy Records',
                        icon: FileText,
                        accept: '.csv,.xlsx',
                        color: 'amber',
                        description: 'CSV/Excel with columns: unit_number, year, quarter, amount_cents, status',
                        benefit: 'Not imported by this quick-create form — attach here as a reminder, then import via the full Onboarding Wizard.',
                    },
                ].map(item => {
                    const file = uploads[ item.key ];
                    return (
                        <div key={item.key}
                             className={`rounded-xl border-2 p-4 space-y-3 transition-colors ${file ? 'border-green-200 bg-green-50/30' : 'border-dashed border-border'}`}>
                            <div className="flex items-start gap-3">
                                <div className={`h-9 w-9 rounded-lg flex items-center justify-center flex-shrink-0 ${
                                    item.color === 'blue' ? 'bg-blue-100 text-blue-600' :
                                        item.color === 'green' ? 'bg-green-100 text-green-600' :
                                            'bg-amber-100 text-amber-600'
                                }`}>
                                    <item.icon className="h-4 w-4"/>
                                </div>
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-semibold">{item.title}</p>
                                    <p className="text-xs text-muted-foreground mt-0.5">{item.description}</p>
                                    <p className="text-xs text-green-600 mt-1">✓ {item.benefit}</p>
                                </div>
                            </div>
                            {file ? (
                                <div
                                    className="flex items-center gap-2 rounded-lg bg-green-50 border border-green-200 px-3 py-2">
                                    <CheckCircle2 className="h-4 w-4 text-green-600 flex-shrink-0"/>
                                    <span className="text-sm text-green-800 flex-1 truncate">{file.name}</span>
                                    <span className="text-xs text-green-600">{( file.size / 1024 ).toFixed(0)} KB</span>
                                    <button type="button"
                                            onClick={() => setUploads(p => ( {...p, [ item.key ]: null} ))}
                                            className="text-green-600 hover:text-green-800">
                                        <X className="h-3.5 w-3.5"/>
                                    </button>
                                </div>
                            ) : (
                                <label
                                    className="flex items-center gap-2 rounded-lg border border-dashed border-muted-foreground/30 px-3 py-2.5 cursor-pointer hover:border-primary/50 hover:bg-muted/20 transition-colors">
                                    <Upload className="h-4 w-4 text-muted-foreground flex-shrink-0"/>
                                    <span
                                        className="text-sm text-muted-foreground">Click to upload {item.accept.replace(/\./g, '').toUpperCase()}</span>
                                    <input
                                        type="file"
                                        className="hidden"
                                        accept={item.accept}
                                        data-testid={`upload-${item.key}`}
                                        onChange={e => {
                                            const f = e.target.files?.[ 0 ];
                                            if (f) setUploads(p => ( {...p, [ item.key ]: f} ));
                                        }}
                                    />
                                </label>
                            )}
                        </div>
                    );
                })}

                <div className="rounded-lg bg-muted/30 border px-4 py-3 text-xs text-muted-foreground">
                    <p className="font-medium text-foreground mb-1 flex items-center gap-1.5">
                        <Info className="h-3.5 w-3.5"/>
                        File Upload Note
                    </p>
                    The Strata Roll is imported immediately when you click Create Building. Previous Financial
                    Statements and Previous Year Levy Records use a more structured import that isn't part of this
                    quick-create form — those two files are kept attached only as a reminder; complete their import
                    via the full Onboarding Wizard after the building is created.
                </div>
            </CardContent>
        </Card>
    );

    // =========================================================================
    // STEP 7 — Review & Launch
    // =========================================================================
    const hasChairman = ecMembers.some(m => m.position === 'CHAIRMAN' && ( m.user_id || ( m.invite_name.trim() && m.invite_email.trim() ) ));
    const hasSM = sm.mode === 'skip' ? false : sm.mode === 'search' ? !!sm.user_id : !!( sm.invite_name.trim() || sm.invite_email.trim() );

    const lotTypeSummary = unitConfig.lot_types_present.length > 0
        ? unitConfig.lot_types_present.map(t => LOT_TYPES.find(l => l.value === t)?.label || t).join(', ')
        : '—';
    const useTypeSummary = USE_TYPES.find(u => u.value === unitConfig.use_type)?.label || unitConfig.use_type;

    const reviewChecks = [
        {label: 'Unit Plan Number', ok: !!derivedId, value: derivedId || '—', required: true},
        {label: 'Building Name', ok: !!identity.name.trim(), value: identity.name || '—', required: true},
        {
            label: 'Address',
            ok: !!location.address.trim(),
            value: `${location.address}${location.suburb ? ', ' + location.suburb : ''}`,
            required: true
        },
        {label: 'State', ok: !!location.state, value: location.state, required: true},
        {label: 'Scheme Use Type', ok: !!unitConfig.use_type, value: useTypeSummary, required: false},
        {label: 'Lot Types', ok: unitConfig.lot_types_present.length > 0, value: lotTypeSummary, required: true},
        ...( isACT && unitConfig.has_class_a_b ? [
            {
                label: 'ACT Class A lots',
                ok: !!unitConfig.act_class_a_count,
                value: unitConfig.act_class_a_count || '—',
                required: false
            },
            {
                label: 'ACT Class B lots',
                ok: !!unitConfig.act_class_b_count,
                value: unitConfig.act_class_b_count || '—',
                required: false
            },
        ] : [] ),
        {
            label: 'Total Lots',
            ok: !!( financial.lot_count && parseInt(financial.lot_count) > 0 ),
            value: financial.lot_count || '—',
            required: true
        },
        {
            label: 'Financial Year End',
            ok: true,
            value: MONTHS[ parseInt(financial.financial_year_end_month) - 1 ],
            required: false
        },
        {
            label: 'Levy Schedule',
            ok: financial.levy_due_months.length > 0,
            value: financial.levy_due_months.map(m => MONTHS[ m - 1 ].slice(0, 3)).join(', '),
            required: false
        },
        {
            label: 'Strata Manager',
            ok: hasSM,
            value: sm.mode === 'skip' ? 'To be assigned later' : sm.mode === 'search' ? ( sm.selected_user?.full_name || 'Selected' ) : ( sm.invite_email || 'To be invited' ),
            required: false
        },
        {
            label: 'EC Chairman',
            ok: hasChairman,
            value: hasChairman ? ( ecMembers.find(m => m.position === 'CHAIRMAN')?.invite_name || ecMembers.find(m => m.position === 'CHAIRMAN')?.selected_user?.full_name || 'Assigned' ) : 'MISSING',
            required: true
        },
        {label: 'EC Members', ok: ecMembers.length > 0, value: `${ecMembers.length} member(s)`, required: false},
    ];

    const canSubmit = reviewChecks.filter(c => c.required).every(c => c.ok);

    const step8 = (
        <div className="space-y-4">
            <Card>
                <CardHeader className="pb-3">
                    <div className="flex items-start justify-between">
                        <div>
                            <CardTitle className="flex items-center gap-2 font-[Manrope]">
                                <CheckCircle2 className="h-5 w-5 text-green-500"/>
                                Review & Launch
                            </CardTitle>
                            <CardDescription className="mt-1">Confirm all details before creating the
                                building</CardDescription>
                        </div>
                        <div
                            className="flex items-center gap-1.5 bg-amber-50 border border-amber-200 rounded-full px-3 py-1.5">
                            <Trophy className="h-4 w-4 text-amber-500"/>
                            <span className="text-sm font-bold text-amber-700">{xpTotal} XP</span>
                        </div>
                    </div>
                </CardHeader>
                <CardContent>
                    <div className="divide-y">
                        {reviewChecks.map((check, i) => (
                            <div key={i} className="flex items-center justify-between py-2.5 text-sm">
                                <div className="flex items-center gap-2">
                                    {check.ok
                                        ? <CheckCircle2 className="h-4 w-4 text-green-500 flex-shrink-0"/>
                                        : check.required
                                            ? <AlertTriangle className="h-4 w-4 text-red-500 flex-shrink-0"/>
                                            :
                                            <div className="h-4 w-4 rounded-full border-2 border-muted flex-shrink-0"/>
                                    }
                                    <span
                                        className={check.required && !check.ok ? 'text-red-600 font-medium' : 'text-muted-foreground'}>{check.label}</span>
                                    {check.required && <Badge variant="outline"
                                                              className="text-[10px] h-4 px-1 text-red-600 border-red-200 bg-red-50">Required</Badge>}
                                </div>
                                <span
                                    className={`font-medium text-right max-w-[200px] truncate ${!check.ok && check.required ? 'text-red-600' : 'text-foreground'}`}>{check.value}</span>
                            </div>
                        ))}
                    </div>
                </CardContent>
            </Card>

            {uploads.strata_roll && (
                <div
                    className="flex items-center gap-2 rounded-lg bg-blue-50 border border-blue-200 px-4 py-3 text-sm text-blue-700">
                    <CheckCircle2 className="h-4 w-4 flex-shrink-0"/>
                    Strata roll ({uploads.strata_roll.name}) will be imported after building creation.
                </div>
            )}

            {!canSubmit && (
                <Alert variant="destructive">
                    <AlertTriangle className="h-4 w-4"/>
                    <AlertDescription>
                        Please go back and complete the required fields marked in red before creating the building.
                    </AlertDescription>
                </Alert>
            )}

            {canSubmit && (
                <div
                    className="rounded-xl bg-gradient-to-r from-green-50 to-emerald-50 border border-green-200 px-5 py-4 space-y-1.5">
                    <p className="text-sm font-semibold text-green-800 flex items-center gap-2">
                        <CheckCircle2 className="h-4 w-4"/>
                        Ready to go! All required fields are complete.
                    </p>
                    <p className="text-xs text-green-600">
                        Clicking "Create Building" will register <strong>{identity.name}</strong> (ID: {derivedId}),
                        assign the team, and create a 12-step onboarding checklist to guide you to go-live.
                    </p>
                </div>
            )}
        </div>
    );

    const stepContent = [step1, step2, step3, step4, step5, step6, step7, step8][ step - 1 ];

    return (
        <div className="max-w-2xl mx-auto p-6 pb-16 space-y-0">
            {header}
            {error && (
                <Alert variant="destructive" className="mb-4">
                    <AlertTriangle className="h-4 w-4"/>
                    <AlertDescription>{error}</AlertDescription>
                </Alert>
            )}
            {stepContent}
            {nav}
        </div>
    );
}
