// @featuretrace:ppm — Maintenance page including the PPM compliance-health summary.
// Layer: frontend
// Data flow: GET /ppm/dashboard -> ppmDashboard.health_score (nullable)
//            -> "Compliance Health" card; null renders as "no items tracked", never green.
// Related: backend/routers/ppm.py
//          frontend/src/pages/dashboard/ManagerDashboard.jsx
// Tests: tests/frontend/unit/pages/dashboard/MaintenancePage.test.tsx

"use client";

import React, {useCallback, useEffect, useState} from 'react';
import {useRouter, useSearchParams} from 'next/navigation';
import {useAuth} from '../../contexts/AuthContext';
import {Card, CardContent, CardHeader, CardTitle} from '../../components/ui/card';
import {Button} from '../../components/ui/button';
import {Input} from '../../components/ui/input';
import {Badge} from '../../components/ui/badge';
import {Label} from '../../components/ui/label';
import {Textarea} from '../../components/ui/textarea';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '../../components/ui/dialog';
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue,} from '../../components/ui/select';
import {Tabs, TabsContent, TabsList, TabsTrigger,} from '../../components/ui/tabs';
import {Tooltip, TooltipContent, TooltipTrigger,} from '../../components/ui/tooltip';
import {Table, TableBody, TableCell, TableHead, TableHeader, TableRow,} from '../../components/ui/table';
import {
    AlertTriangle,
    Calendar,
    CheckCircle2,
    ChevronRight,
    Clock,
    Download,
    Filter,
    FileText,
    Loader2,
    Plus,
    Receipt,
    Shield,
    Users,
    Wrench,
} from 'lucide-react';
import Link from 'next/link';
import {formatCurrency, formatDate} from '../../lib/utils';
import {toast} from 'sonner';

const statusConfig: Record<string, any> = {
    submitted: {label: 'Submitted', color: 'bg-blue-100 text-blue-800'},
    under_review: {label: 'Under Review', color: 'bg-yellow-100 text-yellow-800'},
    approved: {label: 'Approved', color: 'bg-green-100 text-green-800'},
    in_progress: {label: 'In Progress', color: 'bg-orange-100 text-orange-800'},
    completed: {label: 'Completed', color: 'bg-gray-100 text-gray-800'},
    rejected: {label: 'Rejected', color: 'bg-red-100 text-red-800'}
};

// Status config for smart/workflow requests (different status vocabulary)
const workflowStatusConfig: Record<string, any> = {
    open: {label: 'Open', color: 'bg-blue-100 text-blue-800'},
    in_review: {label: 'In Review', color: 'bg-yellow-100 text-yellow-800'},
    in_progress: {label: 'In Progress', color: 'bg-orange-100 text-orange-800'},
    resolved: {label: 'Resolved', color: 'bg-green-100 text-green-800'},
    auto_resolved: {label: 'Auto-Resolved', color: 'bg-teal-100 text-teal-800'},
    closed: {label: 'Closed', color: 'bg-gray-100 text-gray-800'},
};

const priorityConfig: Record<string, any> = {
    low: {label: 'Low', color: 'bg-gray-100 text-gray-800'},
    medium: {label: 'Medium', color: 'bg-yellow-100 text-yellow-800'},
    high: {label: 'High', color: 'bg-orange-100 text-orange-800'},
    urgent: {label: 'Urgent', color: 'bg-red-100 text-red-800'}
};

type StatusOption = {value: string; label: string};
type PpmSummaryKind = 'all' | 'overdue' | 'due_soon' | 'compliance';

const STATUS_FILTER_TABS = ['requests', 'work-orders', 'po', 'invoices'];
const MAINTENANCE_TABS = [...STATUS_FILTER_TABS, 'contractors', 'ppm'];
/**
 * @generated FunctionHeader
 * Function: titleizeStatus
 * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const titleizeStatus = (value: string) => value.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
/**
 * @generated FunctionHeader
 * Function: buildStatusOptions
 * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const buildStatusOptions = (records: any[], configs: Array<Record<string, any>> = []): StatusOption[] => {
    const labels = new Map<string, string>();
    configs.forEach(config => Object.entries(config).forEach(([value, meta]) => {
        labels.set(value, meta?.label || titleizeStatus(value));
    }));
    records.forEach(record => {
        if (record?.status && !labels.has(record.status)) labels.set(record.status, titleizeStatus(record.status));
    });
    return [{value: 'all', label: 'All statuses'}, ...Array.from(labels.entries()).map(([value, label]) => ({value, label}))];
};
/**
 * @generated FunctionHeader
 * Function: countStatuses
 * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const countStatuses = (records: any[], options: StatusOption[]) => {
    const counts: Record<string, number> = Object.fromEntries(options.map(option => [option.value, 0]));
    records.forEach(record => {
        counts.all = (counts.all || 0) + 1;
        if (record?.status && counts[record.status] != null) counts[record.status] += 1;
    });
    return counts;
};
/**
 * @generated FunctionHeader
 * Function: filterStatus
 * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const filterStatus = (records: any[], status: string) => status === 'all' ? records : records.filter(record => record?.status === status);
/**
 * @generated FunctionHeader
 * Function: normalizeMaintenanceTab
 * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const normalizeMaintenanceTab = (tab: string | null) => MAINTENANCE_TABS.includes(tab || '') ? tab || 'requests' : 'requests';
/**
 * @generated FunctionHeader
 * Function: normalizeStatusFilter
 * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const normalizeStatusFilter = (status: string | null, options: StatusOption[]) => (
    options.some(option => option.value === status) ? status as string : 'all'
);

const ppmSummaryCopy: Record<PpmSummaryKind, {title: string; description: string; action: string}> = {
    all: {
        title: 'All scheduled PPM items',
        description: 'Showing every planned preventative maintenance schedule in the current register.',
        action: 'Show all items',
    },
    overdue: {
        title: 'Overdue PPM items',
        description: 'Showing scheduled maintenance items that are past their next due date.',
        action: 'Show overdue items',
    },
    due_soon: {
        title: 'PPM due this month',
        description: 'Showing maintenance items marked due soon by the schedule status.',
        action: 'Show due items',
    },
    compliance: {
        title: 'Compliance-linked PPM items',
        description: 'Showing planned maintenance items flagged as statutory or compliance-related.',
        action: 'Show compliance items',
    },
};
/**
 * @generated FunctionHeader
 * Function: MaintenancePage
 * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
// `initialTab` lets a nested App Router page (e.g. /maintenance/purchase-orders)
// preselect a tab without a ?tab= query string. It takes precedence over ?tab=;
// the legacy ?tab= path still works for back-compat.
const MaintenancePage: React.FC<{initialTab?: string}> = ({initialTab}) => {
    const {api, hasPermission, user, selectedBuilding} = useAuth();
    const router = useRouter();
    const searchParams = useSearchParams();
    const searchParamString = searchParams?.toString?.() || '';
    const [requests, setRequests] = useState<any[]>([]);
    const [smartRequests, setSmartRequests] = useState<any[]>([]);
    const [contractors, setContractors] = useState<any[]>([]);
    const [purchaseOrders, setPurchaseOrders] = useState<any[]>([]);
    const [workOrders, setWorkOrders] = useState<any[]>([]);
    const [invoices, setInvoices] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [activeTab, setActiveTab] = useState(() => normalizeMaintenanceTab(initialTab ?? new URLSearchParams(searchParamString).get('tab')));
    const [statusFilter, setStatusFilter] = useState(() => new URLSearchParams(searchParamString).get('status') || 'all');
    const [dialogOpen, setDialogOpen] = useState(false);
    const [contractorDialogOpen, setContractorDialogOpen] = useState(false);
    const [poDialogOpen, setPODialogOpen] = useState(false);
    const [invoiceDialogOpen, setInvoiceDialogOpen] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [abnLookup, setAbnLookup] = useState<any>(null);
    const [abnLoading, setAbnLoading] = useState(false);
    const [selectedRequest, setSelectedRequest] = useState<any>(null);
    const [assignDialogOpen, setAssignDialogOpen] = useState(false);
    const [selectedContractor, setSelectedContractor] = useState('');

    // PPM state
    const [ppmItems, setPpmItems] = useState<any[]>([]);
    const [ppmSections, setPpmSections] = useState<any[]>([]);
    const [ppmDashboard, setPpmDashboard] = useState<any>(null);
    const [ppmLoading, setPpmLoading] = useState(false);
    const [ppmSectionFilter, setPpmSectionFilter] = useState('all');
    const [ppmStatusFilter, setPpmStatusFilter] = useState('all');
    const [ppmComplianceOnly, setPpmComplianceOnly] = useState(false);
    const [ppmExpandedRow, setPpmExpandedRow] = useState<string | null>(null);
    const [ppmSummaryKind, setPpmSummaryKind] = useState<PpmSummaryKind | null>(null);
    const [ppmCompleteDialogOpen, setPpmCompleteDialogOpen] = useState(false);
    const [ppmSelectedItem, setPpmSelectedItem] = useState<any>(null);
    const [ppmCompleteForm, setPpmCompleteForm] = useState({
        completed_by: '', completion_date: '', notes: '', certificate_ref: ''
    });
    const [ppmSubmitting, setPpmSubmitting] = useState(false);
    const [selectedMaintenanceReq, setSelectedMaintenanceReq] = useState<any>(null);
    const [maintenanceDetailOpen, setMaintenanceDetailOpen] = useState(false);

    const canManage = hasPermission('can_manage_meetings');

    const [requestForm, setRequestForm] = useState({
        title: '', description: '', location: '', category: 'general', priority: 'medium'
    });

    const [contractorForm, setContractorForm] = useState({
        name: '', abn: '', email: '', phone: '', address: '', specialty: '', notes: ''
    });

    const buildingId = selectedBuilding?.building_id || '';
    const [woForm, setWOForm] = useState({
        maintenance_request_id: '',
        title: '',
        description: '',
        supplier_type: 'general',
        priority: 'normal',
        lot_number: '',
        building_id: buildingId
    });

    const [poForm, setPOForm] = useState({
        maintenance_request_id: '', contractor_id: '', description: '', amount: '', due_date: ''
    });

    const [invoiceForm, setInvoiceForm] = useState({
        purchase_order_id: '', amount: '', gst_amount: '', notes: ''
    });

    const categories = ['general', 'plumbing', 'electrical', 'hvac', 'structural', 'cleaning', 'landscaping', 'security', 'lift', 'fire_safety'];

    const fetchData = useCallback(async () => {
        try {
            setLoading(true);
            const [reqRes, contRes, smartRes] = await Promise.all([
                api.get('/maintenance'),
                canManage ? api.get('/contractors') : Promise.resolve({data: []}),
                api.get('/workflow-requests?request_type=maintenance_request').catch(() => ({data: []})),
            ]);
            setRequests(reqRes.data || []);
            setContractors(contRes.data || []);
            setSmartRequests(smartRes.data || []);

            if (canManage) {
                const [poRes, invRes, woRes] = await Promise.all([
                    api.get('/purchase-orders'),
                    api.get('/invoices'),
                    api.get('/work-orders')
                ]);
                setPurchaseOrders(poRes.data || []);
                setInvoices(invRes.data || []);
                setWorkOrders(woRes.data || []);
            }
        } catch (error) {
            console.error('Failed to fetch data:', error);
        } finally {
            setLoading(false);
        }
    }, [api, canManage]); // eslint-disable-line react-hooks/exhaustive-deps

    const fetchPpmData = useCallback(async () => {
        try {
            setPpmLoading(true);
            const [itemsRes, sectionsRes, dashRes] = await Promise.allSettled([
                api.get('/ppm?limit=500'),
                api.get('/ppm/sections'),
                api.get('/ppm/dashboard'),
            ]);
            if (itemsRes.status === 'fulfilled') setPpmItems(itemsRes.value.data || []);
            if (sectionsRes.status === 'fulfilled') setPpmSections(sectionsRes.value.data || []);
            if (dashRes.status === 'fulfilled') setPpmDashboard(dashRes.value.data || null);
        } catch (error) {
            console.error('Failed to fetch PPM data:', error);
        } finally {
            setPpmLoading(false);
        }
    }, [api]); // eslint-disable-line react-hooks/exhaustive-deps

    useEffect(() => {
        const params = new URLSearchParams(searchParamString);
        setActiveTab(normalizeMaintenanceTab(params.get('tab')));
        setStatusFilter(params.get('status') || 'all');

        // ?new=true is how the owner dashboards deep-link "New request" into the
        // create dialog; without this the link silently lands on the plain list.
        //
        // The param is CONSUMED (stripped from the URL) as soon as it is honoured.
        // It must not survive in the URL: updateUrlFilters() rebuilds the query
        // from searchParamString and only deletes `tab`/`status`, so a lingering
        // `new=true` would be carried into every subsequent router.replace(),
        // re-firing this effect and re-opening the dialog each time the user
        // changed a tab or filter after closing it.
        if (params.get('new') === 'true') {
            setDialogOpen(true);
            params.delete('new');
            const rest = params.toString();
            router.replace(rest ? `/maintenance?${rest}` : '/maintenance', {scroll: false});
        }
        // `router` is deliberately NOT a dependency. useRouter() is not guaranteed
        // to return a referentially stable object — under the test harness it
        // returns a fresh literal on every render — so listing it here re-runs this
        // effect on each render, and because the effect calls setActiveTab/
        // setStatusFilter that re-render loops. searchParamString is the only real
        // input; the router is just the transport.
    }, [searchParamString]); // eslint-disable-line react-hooks/exhaustive-deps
    /**
     * @generated FunctionHeader
     * Function: updateUrlFilters
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const updateUrlFilters = (tab: string, status: string) => {
        const params = new URLSearchParams(searchParamString);
        if (tab === 'requests') params.delete('tab');
        else params.set('tab', tab);
        if (status === 'all') params.delete('status');
        else params.set('status', status);
        const query = params.toString();
        router.replace(query ? `/maintenance?${query}` : '/maintenance', {scroll: false});
    };
    /**
     * @generated FunctionHeader
     * Function: handleTabChange
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleTabChange = (tab: string) => {
        setActiveTab(tab);
        setStatusFilter('all');
        updateUrlFilters(tab, 'all');
    };
    /**
     * @generated FunctionHeader
     * Function: handleStatusFilterChange
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleStatusFilterChange = (status: string) => {
        setStatusFilter(status);
        updateUrlFilters(activeTab, status);
    };

    useEffect(() => {
        fetchData();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        if (activeTab === 'ppm') {
            fetchPpmData();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [activeTab]);
    /**
     * @generated FunctionHeader
     * Function: handleRequestSubmit
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleRequestSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await api.post('/maintenance', requestForm);
            toast.success('Maintenance request submitted');
            setDialogOpen(false);
            setRequestForm({title: '', description: '', location: '', category: 'general', priority: 'medium'});
            // Switch to requests tab so the user can see their new request immediately
            handleTabChange('requests');
            await fetchData();
        } catch (error) {
            toast.error('Failed to submit request');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: validateABN
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const validateABN = async (abn: string) => {
        if (abn.replace(/\s/g, '').length !== 11) {
            setAbnLookup(null);
            return;
        }
        setAbnLoading(true);
        try {
            const response = await api.get(`/abn/validate/${abn}`);
            setAbnLookup(response.data);
            if (response.data.valid && response.data.entity_name) {
                setContractorForm(prev => ({...prev, name: response.data.entity_name || prev.name}));
            }
        } catch (error) {
            console.error('ABN validation failed:', error);
        } finally {
            setAbnLoading(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleContractorSubmit
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleContractorSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await api.post('/contractors', contractorForm);
            toast.success('Contractor added');
            setContractorDialogOpen(false);
            setContractorForm({name: '', abn: '', email: '', phone: '', address: '', specialty: '', notes: ''});
            setAbnLookup(null);
            fetchData();
        } catch (error) {
            toast.error('Failed to add contractor');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleWOSubmit
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleWOSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await api.post('/work-orders', woForm);
            toast.success('Work Order created');
            setWOForm({
                maintenance_request_id: '',
                title: '',
                description: '',
                supplier_type: 'general',
                priority: 'normal',
                lot_number: '',
                building_id: buildingId
            });
            fetchData();
        } catch (error) {
            toast.error('Failed to create work order');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handlePOSubmit
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePOSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await api.post('/purchase-orders', {
                ...poForm,
                amount: parseFloat(poForm.amount)
            });
            toast.success('Purchase Order created');
            setPODialogOpen(false);
            setPOForm({maintenance_request_id: '', contractor_id: '', description: '', amount: '', due_date: ''});
            fetchData();
        } catch (error) {
            toast.error('Failed to create PO');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleInvoiceSubmit
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleInvoiceSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setSubmitting(true);
        try {
            await api.post('/invoices', {
                ...invoiceForm,
                amount: parseFloat(invoiceForm.amount),
                gst_amount: parseFloat(invoiceForm.gst_amount || '0')
            });
            toast.success('Invoice created');
            setInvoiceDialogOpen(false);
            setInvoiceForm({purchase_order_id: '', amount: '', gst_amount: '', notes: ''});
            fetchData();
        } catch (error) {
            toast.error('Failed to create invoice');
        } finally {
            setSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: handleStatusUpdate
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handleStatusUpdate = async (requestId: string, status: string) => {
        try {
            await api.put(`/maintenance/${requestId}/status?status=${status}`);
            toast.success('Status updated');
            fetchData();
        } catch (error) {
            toast.error('Failed to update status');
        }
    };
    /**
     * @generated FunctionHeader
     * Function: downloadPO
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const downloadPO = async (poId: string) => {
        try {
            const response = await api.get(`/purchase-orders/${poId}/pdf`, {responseType: 'blob'});
            const blob = new Blob([response.data], {type: 'application/pdf'});
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = `PO-${poId}.pdf`;
            a.click();
            window.URL.revokeObjectURL(url);
        } catch (error) {
            toast.error('Failed to download PO');
        }
    };

    const stats = {
        pending: requests.filter(r => ['submitted', 'under_review'].includes(r.status)).length,
        inProgress: requests.filter(r => ['approved', 'in_progress'].includes(r.status)).length,
        completed: requests.filter(r => r.status === 'completed').length,
        urgent: requests.filter(r => r.priority === 'urgent' && r.status !== 'completed').length
    };

    const maintenanceRequestRecords = [...requests, ...smartRequests];
    const requestStatusOptions = buildStatusOptions(maintenanceRequestRecords, [statusConfig, workflowStatusConfig]);
    const requestStatusCounts = countStatuses(maintenanceRequestRecords, requestStatusOptions);
    const requestStatusFilter = normalizeStatusFilter(statusFilter, requestStatusOptions);
    const filteredRequests = filterStatus(requests, requestStatusFilter);
    const filteredSmartRequests = filterStatus(smartRequests, requestStatusFilter);

    const workOrderStatusOptions = buildStatusOptions(workOrders);
    const workOrderStatusCounts = countStatuses(workOrders, workOrderStatusOptions);
    const workOrderStatusFilter = normalizeStatusFilter(statusFilter, workOrderStatusOptions);
    const filteredWorkOrders = filterStatus(workOrders, workOrderStatusFilter);

    const purchaseOrderStatusOptions = buildStatusOptions(purchaseOrders);
    const purchaseOrderStatusCounts = countStatuses(purchaseOrders, purchaseOrderStatusOptions);
    const purchaseOrderStatusFilter = normalizeStatusFilter(statusFilter, purchaseOrderStatusOptions);
    const filteredPurchaseOrders = filterStatus(purchaseOrders, purchaseOrderStatusFilter);

    const invoiceStatusOptions = buildStatusOptions(invoices);
    const invoiceStatusCounts = countStatuses(invoices, invoiceStatusOptions);
    const invoiceStatusFilter = normalizeStatusFilter(statusFilter, invoiceStatusOptions);
    const filteredInvoices = filterStatus(invoices, invoiceStatusFilter);

    const statusFilterByTab: Record<string, {options: StatusOption[]; counts: Record<string, number>}> = {
        requests: {options: requestStatusOptions, counts: requestStatusCounts},
        'work-orders': {options: workOrderStatusOptions, counts: workOrderStatusCounts},
        po: {options: purchaseOrderStatusOptions, counts: purchaseOrderStatusCounts},
        invoices: {options: invoiceStatusOptions, counts: invoiceStatusCounts},
    };
    /**
     * @generated FunctionHeader
     * Function: renderStatusFilter
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const renderStatusFilter = (tab: string, label: string) => {
        const filter = statusFilterByTab[tab];
        if (!filter) return null;
        const value = normalizeStatusFilter(statusFilter, filter.options);
        return (
            <div className="flex items-center gap-2">
                <Filter className="h-4 w-4 text-muted-foreground" aria-hidden="true"/>
                <Select value={value} onValueChange={handleStatusFilterChange}>
                    <SelectTrigger className="w-full sm:w-56" aria-label={label}>
                        <SelectValue placeholder="Filter status"/>
                    </SelectTrigger>
                    <SelectContent>
                        {filter.options.map(option => (
                            <SelectItem key={option.value} value={option.value}>
                                {option.label} ({filter.counts[option.value] || 0})
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>
        );
    };

    // PPM filtered items
    const filteredPpmItems = ppmItems.filter(item => {
        if (ppmSectionFilter !== 'all' && item.section !== ppmSectionFilter) return false;
        if (ppmStatusFilter !== 'all' && item.status !== ppmStatusFilter) return false;
        if (ppmComplianceOnly && !item.is_compliance) return false;
        return true;
    });
    const ppmSummaryItems = {
        all: ppmItems,
        overdue: ppmItems.filter(item => item.status === 'overdue'),
        due_soon: ppmItems.filter(item => item.status === 'due_soon'),
        compliance: ppmItems.filter(item => item.is_compliance),
    };
    const ppmSummary = ppmSummaryKind ? ppmSummaryCopy[ppmSummaryKind] : null;
    const ppmSummaryRows = ppmSummaryKind ? ppmSummaryItems[ppmSummaryKind] : [];
    /**
     * @generated FunctionHeader
     * Function: applyPpmSummaryFilter
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const applyPpmSummaryFilter = (kind: PpmSummaryKind) => {
        setPpmSectionFilter('all');
        if (kind === 'all') {
            setPpmStatusFilter('all');
            setPpmComplianceOnly(false);
        } else if (kind === 'overdue' || kind === 'due_soon') {
            setPpmStatusFilter(kind);
            setPpmComplianceOnly(false);
        } else {
            setPpmStatusFilter('all');
            setPpmComplianceOnly(true);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: openPpmSummary
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const openPpmSummary = (kind: PpmSummaryKind) => {
        applyPpmSummaryFilter(kind);
        setPpmSummaryKind(kind);
    };
    /**
     * @generated FunctionHeader
     * Function: closePpmSummaryAndShowList
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const closePpmSummaryAndShowList = () => {
        setPpmSummaryKind(null);
        document.getElementById('ppm-items-list')?.scrollIntoView({behavior: 'smooth', block: 'start'});
    };
    /**
     * @generated FunctionHeader
     * Function: renderPpmSummaryCard
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const renderPpmSummaryCard = (
        kind: PpmSummaryKind,
        icon: React.ReactNode,
        value: React.ReactNode,
        label: string,
        testId?: string,
    ) => (
        <button
            type="button"
            className="group text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-lg"
            onClick={() => openPpmSummary(kind)}
            aria-label={`${ppmSummaryCopy[kind].action}. ${label}`}
        >
            <Card className="card-dashboard h-full transition-colors group-hover:border-primary/50 group-hover:bg-muted/30">
                <CardContent className="p-4 flex items-center gap-3">
                    {icon}
                    <div>
                        <div className="text-2xl font-bold" data-testid={testId}>{value}</div>
                        <p className="text-xs text-muted-foreground">{label}</p>
                    </div>
                    <ChevronRight className="ml-auto h-4 w-4 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100"/>
                </CardContent>
            </Card>
        </button>
    );
    /**
     * @generated FunctionHeader
     * Function: handlePpmComplete
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const handlePpmComplete = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!ppmSelectedItem) return;
        setPpmSubmitting(true);
        try {
            await api.post(`/ppm/${ppmSelectedItem.schedule_id}/complete`, ppmCompleteForm);
            toast.success('Completion logged successfully');
            setPpmCompleteDialogOpen(false);
            setPpmCompleteForm({completed_by: '', completion_date: '', notes: '', certificate_ref: ''});
            fetchPpmData();
        } catch (error) {
            toast.error('Failed to log completion');
        } finally {
            setPpmSubmitting(false);
        }
    };
    /**
     * @generated FunctionHeader
     * Function: ppmStatusBadge
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const ppmStatusBadge = (status: string) => {
        switch (status) {
            case 'overdue':
                return <Badge className="bg-red-100 text-red-800 border-red-200">Overdue</Badge>;
            case 'due_soon':
                return <Badge className="bg-amber-100 text-amber-800 border-amber-200">Due Soon</Badge>;
            case 'scheduled':
                return <Badge className="bg-green-100 text-green-800 border-green-200">Scheduled</Badge>;
            default:
                return <Badge variant="outline">{status}</Badge>;
        }
    };
    /**
     * @generated FunctionHeader
     * Function: ppmHealthColor
     * Path: frontend/src/pages/dashboard/MaintenancePage.tsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const ppmHealthColor = (score: number | null | undefined) => {
        // No compliance items tracked -> no score. Neutral grey, never green:
        // an untracked building must not read as a healthy one.
        if (score == null) return 'text-slate-400';
        if (score >= 80) return 'text-green-600';
        if (score >= 60) return 'text-amber-600';
        return 'text-red-600';
    };

    return (
        <div className="space-y-6" data-testid="maintenance-page">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold">Maintenance</h1>
                    <p className="text-muted-foreground">Submit and track maintenance requests</p>
                </div>
                <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
                    <DialogTrigger asChild>
                        <Button data-testid="submit-request-btn"><Plus className="mr-2 h-4 w-4"/>Submit Request</Button>
                    </DialogTrigger>
                    <DialogContent>
                        <DialogHeader>
                            <DialogTitle>Submit Maintenance Request</DialogTitle>
                            <DialogDescription>Report an issue that needs attention</DialogDescription>
                        </DialogHeader>
                        <form onSubmit={handleRequestSubmit} className="space-y-4">
                            <div className="space-y-2">
                                <Label>Title</Label>
                                <Input value={requestForm.title}
                                       onChange={(e) => setRequestForm(prev => ({...prev, title: e.target.value}))}
                                       placeholder="Brief description of the issue" required/>
                            </div>
                            <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label>Category</Label>
                                    <Select value={requestForm.category}
                                            onValueChange={(v) => setRequestForm(prev => ({...prev, category: v}))}>
                                        <SelectTrigger><SelectValue/></SelectTrigger>
                                        <SelectContent>
                                            {categories.map(c => <SelectItem key={c} value={c}
                                                                             className="capitalize">{c.replace('_', ' ')}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-2">
                                    <Label>Priority</Label>
                                    <Select value={requestForm.priority}
                                            onValueChange={(v) => setRequestForm(prev => ({...prev, priority: v}))}>
                                        <SelectTrigger><SelectValue/></SelectTrigger>
                                        <SelectContent>
                                            {Object.entries(priorityConfig).map(([k, v]) => <SelectItem key={k}
                                                                                                        value={k}>{v.label}</SelectItem>)}
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>
                            <div className="space-y-2">
                                <Label>Location</Label>
                                <Input value={requestForm.location}
                                       onChange={(e) => setRequestForm(prev => ({...prev, location: e.target.value}))}
                                       placeholder="e.g., Level 2 corridor, Unit 15 balcony" required/>
                            </div>
                            <div className="space-y-2">
                                <Label>Description</Label>
                                <Textarea value={requestForm.description} onChange={(e) => setRequestForm(prev => ({
                                    ...prev,
                                    description: e.target.value
                                }))} placeholder="Detailed description of the issue..." rows={4} required/>
                            </div>
                            <Button type="submit" className="w-full active:scale-95 transition-transform"
                                    disabled={submitting}>
                                {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : null}Submit Request
                            </Button>
                        </form>
                    </DialogContent>
                </Dialog>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <Card className="card-dashboard">
                    <CardContent className="p-4 flex items-center gap-3">
                        <Clock className="h-8 w-8 text-blue-500"/>
                        <div>
                            <p className="text-2xl font-bold">{stats.pending}</p>
                            <p className="text-xs text-muted-foreground">Pending</p>
                        </div>
                    </CardContent>
                </Card>
                <Card className="card-dashboard">
                    <CardContent className="p-4 flex items-center gap-3">
                        <Wrench className="h-8 w-8 text-orange-500"/>
                        <div>
                            <p className="text-2xl font-bold">{stats.inProgress}</p>
                            <p className="text-xs text-muted-foreground">In Progress</p>
                        </div>
                    </CardContent>
                </Card>
                <Card className="card-dashboard">
                    <CardContent className="p-4 flex items-center gap-3">
                        <CheckCircle2 className="h-8 w-8 text-green-500"/>
                        <div>
                            <p className="text-2xl font-bold">{stats.completed}</p>
                            <p className="text-xs text-muted-foreground">Completed</p>
                        </div>
                    </CardContent>
                </Card>
                <Card className="card-dashboard">
                    <CardContent className="p-4 flex items-center gap-3">
                        <AlertTriangle className="h-8 w-8 text-red-500"/>
                        <div>
                            <p className="text-2xl font-bold">{stats.urgent}</p>
                            <p className="text-xs text-muted-foreground">Urgent</p>
                        </div>
                    </CardContent>
                </Card>
            </div>

            <Tabs value={activeTab} onValueChange={handleTabChange}>
                <TabsList>
                    <TabsTrigger value="requests"><Wrench className="mr-2 h-4 w-4"/>Requests</TabsTrigger>
                    {canManage &&
                        <TabsTrigger value="work-orders"><FileText className="mr-2 h-4 w-4"/>Work Orders</TabsTrigger>}
                    {canManage &&
                        <TabsTrigger value="contractors"><Users className="mr-2 h-4 w-4"/>Contractors</TabsTrigger>}
                    {canManage &&
                        <TabsTrigger value="po"><FileText className="mr-2 h-4 w-4"/>Purchase Orders</TabsTrigger>}
                    {canManage &&
                        <TabsTrigger value="invoices"><Receipt className="mr-2 h-4 w-4"/>Invoices</TabsTrigger>}
                    <TabsTrigger value="ppm" data-testid="ppm-tab-trigger"><Calendar className="mr-2 h-4 w-4"/>PPM
                        Schedule</TabsTrigger>
                </TabsList>

                <TabsContent value="requests" className="space-y-4">
                    <div className="flex justify-end">
                        {renderStatusFilter('requests', 'Filter maintenance requests by status')}
                    </div>
                    {loading ? (
                        <div className="space-y-4">{[1, 2, 3].map(i => <div key={i}
                                                                            className="skeleton h-24 w-full"/>)}</div>
                    ) : filteredRequests.length === 0 && filteredSmartRequests.length === 0 ? (
                        <Card className="card-dashboard"><CardContent className="py-16 text-center">
                            <Wrench className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                            <h3 className="text-lg font-medium mb-2">{requestStatusFilter === 'all' ? 'No Maintenance Requests' : 'No Maintenance Requests for this status'}</h3>
                            <p className="text-muted-foreground">{requestStatusFilter === 'all' ? 'Submit a request when something needs attention' : 'Switch to All statuses to view the full request list'}</p>
                        </CardContent></Card>
                    ) : (
                        <>
                            {/* Standard maintenance requests */}
                            {filteredRequests.map(req => (
                                <Card key={req.id} className="card-dashboard overflow-hidden">
                                    {/* Clickable body — opens detail popup */}
                                    <CardContent
                                        role="button"
                                        tabIndex={0}
                                        data-testid={`maintenance-request-body-${req.id}`}
                                        aria-label={`View details for ${req.title}`}
                                        className="p-4 pb-3 cursor-pointer hover:bg-slate-50/50 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/50"
                                        onClick={() => {
                                            setSelectedMaintenanceReq(req);
                                            setMaintenanceDetailOpen(true);
                                        }}
                                        onKeyDown={e => {
                                            if (e.key === 'Enter' || e.key === ' ') {
                                                e.preventDefault();
                                                setSelectedMaintenanceReq(req);
                                                setMaintenanceDetailOpen(true);
                                            }
                                        }}>
                                        <div className="flex items-start gap-4">
                                            <div className="flex-1">
                                                <div className="flex items-center gap-2 mb-2 flex-wrap">
                                                    <h3 className="font-semibold">{req.title}</h3>
                                                    <Badge
                                                        className={statusConfig[req.status]?.color}>{statusConfig[req.status]?.label}</Badge>
                                                    <Badge
                                                        className={priorityConfig[req.priority]?.color}>{priorityConfig[req.priority]?.label}</Badge>
                                                    <Badge variant="outline"
                                                           className="capitalize">{req.category?.replace('_', ' ')}</Badge>
                                                </div>
                                                <p className="text-sm text-muted-foreground mb-2">{req.description}</p>
                                                <p className="text-xs text-muted-foreground">
                                                    📍 {req.location} • Reported
                                                    by <strong>{req.submitted_by_name}</strong> on {formatDate(req.created_at)}
                                                </p>
                                            </div>
                                            <ChevronRight aria-hidden="true"
                                                          className="h-4 w-4 text-muted-foreground/40 mt-1 shrink-0"/>
                                        </div>
                                    </CardContent>
                                    {/* Action footer — isolated from card click; buttons are full-size pill */}
                                    {canManage && req.status !== 'completed' && (
                                        <div
                                            className="border-t bg-muted/20 px-4 py-3 flex items-center gap-3"
                                            onClick={e => e.stopPropagation()}>
                                            {req.status === 'submitted' && (
                                                <Button
                                                    data-testid={`btn-review-${req.id}`}
                                                    className="rounded-full active:scale-95 transition-transform"
                                                    aria-label={`Mark "${req.title}" under review`}
                                                    onClick={() => handleStatusUpdate(req.id, 'under_review')}
                                                >
                                                    Review
                                                </Button>
                                            )}
                                            {req.status === 'under_review' && (
                                                <Button
                                                    data-testid={`btn-approve-${req.id}`}
                                                    className="rounded-full active:scale-95 transition-transform bg-green-600 hover:bg-green-700 text-white"
                                                    aria-label={`Approve "${req.title}"`}
                                                    onClick={() => handleStatusUpdate(req.id, 'approved')}
                                                >
                                                    <CheckCircle2 aria-hidden="true" className="mr-2 h-4 w-4"/>Approve
                                                </Button>
                                            )}
                                            {req.status === 'approved' && (
                                                <Button
                                                    data-testid={`btn-create-wo-${req.id}`}
                                                    className="rounded-full active:scale-95 transition-transform"
                                                    aria-label={`Create work order for "${req.title}"`}
                                                    onClick={() => {
                                                        setWOForm(prev => ({
                                                            ...prev,
                                                            maintenance_request_id: req.id,
                                                            title: req.title,
                                                            description: req.description,
                                                            lot_number: req.unit_number || '',
                                                            supplier_type: req.category
                                                        }));
                                                        handleTabChange('work-orders');
                                                    }}
                                                >
                                                    <Wrench aria-hidden="true" className="mr-2 h-4 w-4"/>Create Work
                                                    Order
                                                </Button>
                                            )}
                                            {req.status === 'in_progress' && (
                                                <Button
                                                    data-testid={`btn-complete-${req.id}`}
                                                    className="rounded-full active:scale-95 transition-transform bg-[#E07A5F] hover:bg-[#E07A5F]/90 text-white"
                                                    aria-label={`Mark "${req.title}" complete`}
                                                    onClick={() => handleStatusUpdate(req.id, 'completed')}
                                                >
                                                    <CheckCircle2 aria-hidden="true" className="mr-2 h-4 w-4"/>Complete
                                                </Button>
                                            )}
                                        </div>
                                    )}
                                </Card>
                            ))}

                            {/* Smart Requests (from workflow_requests collection) */}
                            {filteredSmartRequests.length > 0 && (
                                <>
                                    {filteredRequests.length > 0 && (
                                        <div className="flex items-center gap-3 pt-2">
                                            <div className="h-px flex-1 bg-border"/>
                                            <span className="text-xs text-muted-foreground font-medium">Via Smart Request</span>
                                            <div className="h-px flex-1 bg-border"/>
                                        </div>
                                    )}
                                    {filteredSmartRequests.map(req => {
                                        const sc = workflowStatusConfig[req.status] || {
                                            label: req.status,
                                            color: 'bg-gray-100 text-gray-700'
                                        };
                                        return (
                                            <Card key={req.id}
                                                  className="card-dashboard border-l-4 border-l-violet-400 hover:border-primary/40 cursor-pointer transition-colors"
                                                  onClick={() => {
                                                      setSelectedMaintenanceReq({
                                                          ...req,
                                                          title: req.subject || req.title,
                                                          description: req.body || req.description,
                                                          _is_smart: true,
                                                      });
                                                      setMaintenanceDetailOpen(true);
                                                  }}>
                                                <CardContent className="p-4">
                                                    <div className="flex items-start justify-between gap-4">
                                                        <div className="flex-1">
                                                            <div className="flex items-center gap-2 mb-2 flex-wrap">
                                                                <Badge variant="outline"
                                                                       className="text-[10px] text-violet-600 border-violet-300 bg-violet-50">Smart
                                                                    Request</Badge>
                                                                <h3 className="font-semibold">{req.subject || req.title}</h3>
                                                                <Badge className={sc.color}>{sc.label}</Badge>
                                                                {req.priority && (
                                                                    <Badge
                                                                        className={priorityConfig[req.priority]?.color || 'bg-gray-100 text-gray-700'}>
                                                                        {priorityConfig[req.priority]?.label || req.priority}
                                                                    </Badge>
                                                                )}
                                                            </div>
                                                            <p className="text-sm text-muted-foreground mb-2 line-clamp-2">
                                                                {req.body || req.description}
                                                            </p>
                                                            <p className="text-xs text-muted-foreground">
                                                                Ref: <strong>{req.reference_number || req.request_number || req.id?.slice(0, 8)}</strong>
                                                                {' '}· Submitted {formatDate(req.created_at)}
                                                                {req.sla_due_at && ` · SLA due ${formatDate(req.sla_due_at)}`}
                                                            </p>
                                                        </div>
                                                        {req.sla_due_at && new Date(req.sla_due_at) < new Date() && !['resolved', 'closed', 'auto_resolved'].includes(req.status) && (
                                                            <div className="shrink-0">
                                                                <Badge className="bg-red-100 text-red-700">SLA
                                                                    Overdue</Badge>
                                                            </div>
                                                        )}
                                                    </div>
                                                </CardContent>
                                            </Card>
                                        );
                                    })}
                                </>
                            )}
                        </>
                    )}
                </TabsContent>

                {canManage && (
                    <TabsContent value="work-orders" className="space-y-4">
                        <div className="flex justify-end">
                            {renderStatusFilter('work-orders', 'Filter work orders by status')}
                        </div>
                        <Card className="card-dashboard">
                            <CardHeader><CardTitle>Create New Work Order</CardTitle></CardHeader>
                            <CardContent>
                                <form onSubmit={handleWOSubmit} className="space-y-4">
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                        <div className="space-y-2">
                                            <Label>Title</Label>
                                            <Input value={woForm.title} onChange={(e) => setWOForm(prev => ({
                                                ...prev,
                                                title: e.target.value
                                            }))} required/>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Supplier Type</Label>
                                            <Select value={woForm.supplier_type}
                                                    onValueChange={(v) => setWOForm(prev => ({
                                                        ...prev,
                                                        supplier_type: v
                                                    }))}>
                                                <SelectTrigger><SelectValue/></SelectTrigger>
                                                <SelectContent>
                                                    {categories.map(c => <SelectItem key={c} value={c}
                                                                                     className="capitalize">{c.replace('_', ' ')}</SelectItem>)}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Description</Label>
                                        <Textarea value={woForm.description} onChange={(e) => setWOForm(prev => ({
                                            ...prev,
                                            description: e.target.value
                                        }))} rows={2} required/>
                                    </div>
                                    <Button type="submit" className="active:scale-95 transition-transform"
                                            disabled={submitting}>
                                        {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : null}
                                        Create Work Order
                                    </Button>
                                </form>
                            </CardContent>
                        </Card>

                        <div className="grid gap-4">
                            {filteredWorkOrders.length === 0 ? (
                                <Card className="card-dashboard"><CardContent className="py-10 text-center text-muted-foreground">No work orders found for this status</CardContent></Card>
                            ) : filteredWorkOrders.map(wo => (
                                <Link key={wo.id} href={`/maintenance/work-order/${wo.id}`}>
                                    <Card className="hover:border-primary transition-colors cursor-pointer group">
                                        <CardContent className="p-4 flex items-center justify-between">
                                            <div>
                                                <div className="flex items-center gap-2 mb-1">
                                                    <h3 className="font-semibold">{wo.title}</h3>
                                                    <Badge variant="outline">{wo.status}</Badge>
                                                </div>
                                                <p className="text-sm text-muted-foreground">Vendor: {wo.vendor_name || 'Unassigned'}</p>
                                            </div>
                                            <ChevronRight className="h-5 w-5 text-muted-foreground"/>
                                        </CardContent>
                                    </Card>
                                </Link>
                            ))}
                        </div>
                    </TabsContent>
                )}

                {canManage && (
                    <TabsContent value="contractors" className="space-y-4">
                        <div className="flex justify-end">
                            <Dialog open={contractorDialogOpen} onOpenChange={setContractorDialogOpen}>
                                <DialogTrigger asChild><Button><Plus className="mr-2 h-4 w-4"/>Add
                                    Contractor</Button></DialogTrigger>
                                <DialogContent>
                                    <DialogHeader><DialogTitle>Add Contractor</DialogTitle></DialogHeader>
                                    <form onSubmit={handleContractorSubmit} className="space-y-4">
                                        <div className="space-y-2">
                                            <Label>ABN</Label>
                                            <Input value={contractorForm.abn} onChange={e => {
                                                setContractorForm(prev => ({...prev, abn: e.target.value}));
                                                validateABN(e.target.value);
                                            }}/>
                                            {abnLookup &&
                                                <p className="text-xs text-green-600">{abnLookup.entity_name}</p>}
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Business Name</Label>
                                            <Input value={contractorForm.name}
                                                   onChange={e => setContractorForm(prev => ({
                                                       ...prev,
                                                       name: e.target.value
                                                   }))} required/>
                                        </div>
                                        <Button type="submit" className="w-full active:scale-95 transition-transform"
                                                disabled={submitting}>
                                            {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : null}
                                            Add Contractor
                                        </Button>
                                    </form>
                                </DialogContent>
                            </Dialog>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            {contractors.map(c => (
                                <Card key={c.id}>
                                    <CardContent className="p-4">
                                        <h3 className="font-semibold">{c.name}</h3>
                                        <Badge variant="outline" className="mt-2">{c.specialty}</Badge>
                                    </CardContent>
                                </Card>
                            ))}
                        </div>
                    </TabsContent>
                )}

                {canManage && (
                    <TabsContent value="po" className="space-y-4">
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                            {renderStatusFilter('po', 'Filter purchase orders by status')}
                            <Dialog open={poDialogOpen} onOpenChange={setPODialogOpen}>
                                <DialogTrigger asChild><Button><Plus className="mr-2 h-4 w-4"/>Create
                                    PO</Button></DialogTrigger>
                                <DialogContent>
                                    <DialogHeader><DialogTitle>Create Purchase Order</DialogTitle></DialogHeader>
                                    <form onSubmit={handlePOSubmit} className="space-y-4">
                                        <div className="space-y-2">
                                            <Label>Contractor</Label>
                                            <Select value={poForm.contractor_id}
                                                    onValueChange={v => setPOForm(prev => ({
                                                        ...prev,
                                                        contractor_id: v
                                                    }))}>
                                                <SelectTrigger><SelectValue
                                                    placeholder="Select contractor"/></SelectTrigger>
                                                <SelectContent>
                                                    {contractors.map(c => <SelectItem key={c.id}
                                                                                      value={c.id}>{c.name}</SelectItem>)}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Amount</Label>
                                            <Input type="number" value={poForm.amount}
                                                   onChange={e => setPOForm(prev => ({
                                                       ...prev,
                                                       amount: e.target.value
                                                   }))} required/>
                                        </div>
                                        <Button type="submit" className="w-full active:scale-95 transition-transform"
                                                disabled={submitting}>
                                            {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : null}
                                            Create PO
                                        </Button>
                                    </form>
                                </DialogContent>
                            </Dialog>
                        </div>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>PO #</TableHead>
                                    <TableHead>Contractor</TableHead>
                                    <TableHead>Amount</TableHead>
                                    <TableHead>Status</TableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filteredPurchaseOrders.map(po => (
                                    <TableRow key={po.id}>
                                        <TableCell>{po.po_number}</TableCell>
                                        <TableCell>{po.contractor_name}</TableCell>
                                        <TableCell>{formatCurrency(po.amount)}</TableCell>
                                        <TableCell><Badge variant="outline">{po.status}</Badge></TableCell>
                                        <TableCell className="text-right">
                                            <Tooltip>
                                                <TooltipTrigger asChild>
                                                    <Button
                                                        variant="ghost"
                                                        size="sm"
                                                        onClick={() => downloadPO(po.id)}
                                                        aria-label="Download Purchase Order"
                                                        className="active:scale-95 transition-transform"
                                                    >
                                                        <Download className="h-4 w-4"/>
                                                    </Button>
                                                </TooltipTrigger>
                                                <TooltipContent>Download Purchase Order</TooltipContent>
                                            </Tooltip>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                        {filteredPurchaseOrders.length === 0 && (
                            <Card className="card-dashboard"><CardContent className="py-10 text-center text-muted-foreground">No purchase orders found for this status</CardContent></Card>
                        )}
                    </TabsContent>
                )}

                {canManage && (
                    <TabsContent value="invoices" className="space-y-4">
                        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                            {renderStatusFilter('invoices', 'Filter invoices by status')}
                            <Dialog open={invoiceDialogOpen} onOpenChange={setInvoiceDialogOpen}>
                                <DialogTrigger asChild><Button><Plus className="mr-2 h-4 w-4"/>Create
                                    Invoice</Button></DialogTrigger>
                                <DialogContent>
                                    <DialogHeader><DialogTitle>Create Invoice</DialogTitle></DialogHeader>
                                    <form onSubmit={handleInvoiceSubmit} className="space-y-4">
                                        <div className="space-y-2">
                                            <Label>PO #</Label>
                                            <Select value={invoiceForm.purchase_order_id}
                                                    onValueChange={v => setInvoiceForm(prev => ({
                                                        ...prev,
                                                        purchase_order_id: v
                                                    }))}>
                                                <SelectTrigger><SelectValue placeholder="Select PO"/></SelectTrigger>
                                                <SelectContent>
                                                    {purchaseOrders.map(po => <SelectItem key={po.id}
                                                                                          value={po.id}>{po.po_number}</SelectItem>)}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                        <div className="space-y-2">
                                            <Label>Amount</Label>
                                            <Input type="number" value={invoiceForm.amount}
                                                   onChange={e => setInvoiceForm(prev => ({
                                                       ...prev,
                                                       amount: e.target.value
                                                   }))} required/>
                                        </div>
                                        <Button type="submit" className="w-full active:scale-95 transition-transform"
                                                disabled={submitting}>
                                            {submitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> : null}
                                            Create Invoice
                                        </Button>
                                    </form>
                                </DialogContent>
                            </Dialog>
                        </div>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Invoice #</TableHead>
                                    <TableHead>PO #</TableHead>
                                    <TableHead>Amount</TableHead>
                                    <TableHead>Status</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filteredInvoices.map(inv => (
                                    <TableRow key={inv.id}>
                                        <TableCell>{inv.invoice_number}</TableCell>
                                        <TableCell>{inv.po_number}</TableCell>
                                        <TableCell>{formatCurrency(inv.total_amount)}</TableCell>
                                        <TableCell><Badge variant="outline">{inv.status}</Badge></TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                        {filteredInvoices.length === 0 && (
                            <Card className="card-dashboard"><CardContent className="py-10 text-center text-muted-foreground">No invoices found for this status</CardContent></Card>
                        )}
                    </TabsContent>
                )}

                {/* ── PPM Schedule Tab ─────────────────────────────────────────── */}
                <TabsContent value="ppm" className="space-y-4">
                    {/* Stats bar */}
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                        {renderPpmSummaryCard(
                            'all',
                            <Calendar className="h-8 w-8 text-blue-500"/>,
                            ppmDashboard?.total_items ?? '—',
                            'Total Items',
                            'ppm-total-items',
                        )}
                        {renderPpmSummaryCard(
                            'overdue',
                            <AlertTriangle className="h-8 w-8 text-red-500"/>,
                            <>
                                {ppmDashboard?.overdue_count ?? '—'}
                                {ppmDashboard?.overdue_count > 0 && (
                                    <Badge className="ml-2 bg-red-100 text-red-800 text-xs">{ppmDashboard.overdue_count}</Badge>
                                )}
                            </>,
                            'Overdue',
                            'ppm-overdue-count',
                        )}
                        {renderPpmSummaryCard(
                            'due_soon',
                            <Clock className="h-8 w-8 text-amber-500"/>,
                            ppmDashboard?.due_soon_count ?? '—',
                            'Due This Month',
                        )}
                        {renderPpmSummaryCard(
                            'compliance',
                            <Shield className="h-8 w-8 text-green-500"/>,
                            <span className={ppmHealthColor(ppmDashboard?.health_score)}>
                                {ppmDashboard?.health_score != null ? `${ppmDashboard.health_score}%` : '—'}
                            </span>,
                            ppmDashboard && ppmDashboard.health_score == null
                                ? 'Compliance Health · no items tracked'
                                : 'Compliance Health',
                            'ppm-health-score',
                        )}
                    </div>

                    {/* Filters */}
                    <div className="flex flex-wrap gap-3 items-center">
                        <Select value={ppmSectionFilter} onValueChange={setPpmSectionFilter}>
                            <SelectTrigger className="w-64">
                                <SelectValue placeholder="All Sections"/>
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All Sections</SelectItem>
                                {ppmSections.map(s => (
                                    <SelectItem key={s.section} value={s.section}>
                                        {s.section} ({s.item_count})
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>

                        <Select value={ppmStatusFilter} onValueChange={setPpmStatusFilter}>
                            <SelectTrigger className="w-40">
                                <SelectValue placeholder="All Statuses"/>
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all">All Statuses</SelectItem>
                                <SelectItem value="overdue">Overdue</SelectItem>
                                <SelectItem value="due_soon">Due Soon</SelectItem>
                                <SelectItem value="scheduled">Scheduled</SelectItem>
                            </SelectContent>
                        </Select>

                        <Button
                            variant={ppmComplianceOnly ? 'default' : 'outline'}
                            onClick={() => setPpmComplianceOnly(v => !v)}
                            className="flex items-center gap-2"
                        >
                            <Shield className="h-4 w-4"/>
                            Compliance Only
                        </Button>

                        <Button variant="outline" onClick={fetchPpmData} disabled={ppmLoading}>
                            {ppmLoading ? <Loader2 className="h-4 w-4 animate-spin mr-2"/> : null}
                            Refresh
                        </Button>
                    </div>

                    {/* Table */}
                    {ppmLoading ? (
                        <div className="space-y-4">{[1, 2, 3].map(i => <div key={i}
                                                                            className="h-12 bg-muted animate-pulse rounded"/>)}</div>
                    ) : filteredPpmItems.length === 0 ? (
                        <Card className="card-dashboard">
                            <CardContent className="py-16 text-center">
                                <Calendar className="h-16 w-16 text-muted-foreground/50 mx-auto mb-4"/>
                                <h3 className="text-lg font-medium mb-2">No PPM Items Found</h3>
                                <p className="text-muted-foreground">Try adjusting your filters or run the seed script
                                    to populate data.</p>
                            </CardContent>
                        </Card>
                    ) : (
                        <div id="ppm-items-list" className="rounded-md border">
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Section</TableHead>
                                        <TableHead>Description</TableHead>
                                        <TableHead>Frequency</TableHead>
                                        <TableHead className="hidden md:table-cell">Vendor</TableHead>
                                        <TableHead className="hidden md:table-cell">Last Completed</TableHead>
                                        <TableHead>Next Due</TableHead>
                                        <TableHead>Status</TableHead>
                                        {canManage && <TableHead className="text-right">Action</TableHead>}
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {filteredPpmItems.map(item => (
                                        <React.Fragment key={item.schedule_id || item.id}>
                                            <TableRow
                                                className="cursor-pointer hover:bg-muted/50"
                                                onClick={() => setPpmExpandedRow(
                                                    ppmExpandedRow === (item.schedule_id || item.id)
                                                        ? null
                                                        : (item.schedule_id || item.id)
                                                )}
                                            >
                                                <TableCell className="text-xs">{item.section}</TableCell>
                                                <TableCell className="max-w-xs">
                                                    <div className="flex items-center gap-2">
                                                        {item.is_compliance && (
                                                            <Tooltip>
                                                                <TooltipTrigger>
                                                                    <Shield
                                                                        className="h-4 w-4 text-red-500 flex-shrink-0"/>
                                                                </TooltipTrigger>
                                                                <TooltipContent>
                                                                    <p>Compliance — {item.standard || 'AS Standard'}</p>
                                                                </TooltipContent>
                                                            </Tooltip>
                                                        )}
                                                        <span className="text-sm truncate">{item.description}</span>
                                                    </div>
                                                </TableCell>
                                                <TableCell className="text-xs capitalize">{item.frequency}</TableCell>
                                                <TableCell
                                                    className="hidden md:table-cell text-xs text-muted-foreground">{item.vendor_name || '—'}</TableCell>
                                                <TableCell
                                                    className="hidden md:table-cell text-xs text-muted-foreground">
                                                    {item.last_completed_date ? formatDate(item.last_completed_date) : 'Never'}
                                                </TableCell>
                                                <TableCell className="text-xs">
                                                    {item.next_due_date ? formatDate(item.next_due_date) : '—'}
                                                </TableCell>
                                                <TableCell>{ppmStatusBadge(item.status)}</TableCell>
                                                {canManage && (
                                                    <TableCell className="text-right">
                                                        <Button
                                                            size="sm"
                                                            variant="outline"
                                                            onClick={e => {
                                                                e.stopPropagation();
                                                                setPpmSelectedItem(item);
                                                                setPpmCompleteForm(prev => ({
                                                                    ...prev,
                                                                    completed_by: (user as any)?.full_name || '',
                                                                    completion_date: new Date().toISOString().split('T')[0],
                                                                }));
                                                                setPpmCompleteDialogOpen(true);
                                                            }}
                                                        >
                                                            <CheckCircle2 className="h-3 w-3 mr-1"/>
                                                            Log
                                                        </Button>
                                                    </TableCell>
                                                )}
                                            </TableRow>
                                            {ppmExpandedRow === (item.schedule_id || item.id) && (
                                                <TableRow>
                                                    <TableCell colSpan={canManage ? 8 : 7} className="bg-muted/30 p-4">
                                                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm">
                                                            <div>
                                                                <p><span
                                                                    className="font-medium">Inspection Type:</span> {item.inspection_type || '—'}
                                                                </p>
                                                                <p><span
                                                                    className="font-medium">Standard:</span> {item.standard || '—'}
                                                                </p>
                                                                <p><span
                                                                    className="font-medium">Vendor Contact:</span> {item.vendor_contact || '—'}
                                                                </p>
                                                                <p><span
                                                                    className="font-medium">Lifespan:</span> {item.lifespan_years ? `${item.lifespan_years} years` : '—'}
                                                                </p>
                                                                {item.capital_years?.length > 0 && (
                                                                    <p><span
                                                                        className="font-medium">Capital Years:</span> {item.capital_years.join(', ')}
                                                                    </p>
                                                                )}
                                                            </div>
                                                            <div>
                                                                <p className="font-medium mb-1">Completion History:</p>
                                                                {item.completion_log?.length > 0 ? (
                                                                    <ul className="space-y-1">
                                                                        {item.completion_log.slice(-3).map((log: any, idx: number) => (
                                                                            <li key={idx}
                                                                                className="text-xs text-muted-foreground">
                                                                                {formatDate(log.date)} — {log.completed_by}
                                                                                {log.certificate_ref && ` (Cert: ${log.certificate_ref})`}
                                                                                {log.notes && ` — ${log.notes}`}
                                                                            </li>
                                                                        ))}
                                                                    </ul>
                                                                ) : (
                                                                    <p className="text-xs text-muted-foreground">No
                                                                        completion history</p>
                                                                )}
                                                            </div>
                                                        </div>
                                                    </TableCell>
                                                </TableRow>
                                            )}
                                        </React.Fragment>
                                    ))}
                                </TableBody>
                            </Table>
                        </div>
                    )}

                    <Dialog open={Boolean(ppmSummaryKind)} onOpenChange={open => !open && setPpmSummaryKind(null)}>
                        <DialogContent>
                            <DialogHeader>
                                <DialogTitle>{ppmSummary?.title}</DialogTitle>
                                <DialogDescription>{ppmSummary?.description}</DialogDescription>
                            </DialogHeader>
                            <div className="space-y-4">
                                <div className="grid grid-cols-2 gap-3">
                                    <div className="rounded-lg border p-3">
                                        <p className="text-xs text-muted-foreground">Matching items</p>
                                        <p className="text-2xl font-bold">{ppmSummaryRows.length}</p>
                                    </div>
                                    <div className="rounded-lg border p-3">
                                        <p className="text-xs text-muted-foreground">Visible after filters</p>
                                        <p className="text-2xl font-bold">{filteredPpmItems.length}</p>
                                    </div>
                                </div>
                                {ppmSummaryRows.length > 0 ? (
                                    <div className="space-y-2">
                                        <p className="text-sm font-medium">Next items</p>
                                        <div className="divide-y rounded-lg border">
                                            {ppmSummaryRows.slice(0, 4).map(item => (
                                                <div key={item.schedule_id || item.id} className="p-3 text-sm">
                                                    <div className="flex items-center justify-between gap-3">
                                                        <span className="font-medium">{item.description || 'PPM item'}</span>
                                                        {ppmStatusBadge(item.status)}
                                                    </div>
                                                    <p className="mt-1 text-xs text-muted-foreground">
                                                        {[item.section, item.next_due_date ? `Due ${formatDate(item.next_due_date)}` : null]
                                                            .filter(Boolean)
                                                            .join(' · ') || 'No due date recorded'}
                                                    </p>
                                                </div>
                                            ))}
                                        </div>
                                    </div>
                                ) : (
                                    <p className="rounded-lg border p-4 text-sm text-muted-foreground">
                                        No PPM items match this card yet.
                                    </p>
                                )}
                            </div>
                            <DialogFooter>
                                <Button type="button" variant="outline" onClick={() => setPpmSummaryKind(null)}>
                                    Close
                                </Button>
                                <Button type="button" onClick={closePpmSummaryAndShowList}>
                                    {ppmSummary?.action || 'Show items'}
                                </Button>
                            </DialogFooter>
                        </DialogContent>
                    </Dialog>

                    {/* Log Completion Dialog */}
                    {canManage && (
                        <Dialog open={ppmCompleteDialogOpen} onOpenChange={setPpmCompleteDialogOpen}>
                            <DialogContent>
                                <DialogHeader>
                                    <DialogTitle>Log Completion</DialogTitle>
                                    <DialogDescription>
                                        {ppmSelectedItem?.description}
                                    </DialogDescription>
                                </DialogHeader>
                                <form onSubmit={handlePpmComplete} className="space-y-4">
                                    <div className="space-y-2">
                                        <Label>Completed By *</Label>
                                        <Input
                                            value={ppmCompleteForm.completed_by}
                                            onChange={e => setPpmCompleteForm(p => ({
                                                ...p,
                                                completed_by: e.target.value
                                            }))}
                                            required
                                            placeholder="Name or company"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Completion Date *</Label>
                                        <Input
                                            type="date"
                                            value={ppmCompleteForm.completion_date}
                                            onChange={e => setPpmCompleteForm(p => ({
                                                ...p,
                                                completion_date: e.target.value
                                            }))}
                                            required
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Certificate Reference</Label>
                                        <Input
                                            value={ppmCompleteForm.certificate_ref}
                                            onChange={e => setPpmCompleteForm(p => ({
                                                ...p,
                                                certificate_ref: e.target.value
                                            }))}
                                            placeholder="e.g. AS1851 certificate number"
                                        />
                                    </div>
                                    <div className="space-y-2">
                                        <Label>Notes</Label>
                                        <Textarea
                                            value={ppmCompleteForm.notes}
                                            onChange={e => setPpmCompleteForm(p => ({...p, notes: e.target.value}))}
                                            placeholder="Any additional notes"
                                            rows={3}
                                        />
                                    </div>
                                    <Button type="submit" className="w-full" disabled={ppmSubmitting}>
                                        {ppmSubmitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin"/> :
                                            <CheckCircle2 className="mr-2 h-4 w-4"/>}
                                        Log Completion
                                    </Button>
                                </form>
                            </DialogContent>
                        </Dialog>
                    )}
                </TabsContent>
            </Tabs>

            {/* Maintenance Request Detail Dialog */}
            {selectedMaintenanceReq && (
                <Dialog open={maintenanceDetailOpen} onOpenChange={setMaintenanceDetailOpen}>
                    <DialogContent className="max-w-lg">
                        <DialogHeader>
                            <DialogTitle className="flex items-center gap-2">
                                <Wrench className="h-5 w-5 text-orange-500"/>
                                {selectedMaintenanceReq.title}
                            </DialogTitle>
                            <DialogDescription>Maintenance Request Details</DialogDescription>
                        </DialogHeader>
                        <div className="space-y-4">
                            <div className="flex flex-wrap gap-2">
                                <Badge
                                    className={statusConfig[selectedMaintenanceReq.status]?.color}>{statusConfig[selectedMaintenanceReq.status]?.label}</Badge>
                                <Badge
                                    className={priorityConfig[selectedMaintenanceReq.priority]?.color}>{priorityConfig[selectedMaintenanceReq.priority]?.label}</Badge>
                                <Badge variant="outline"
                                       className="capitalize">{selectedMaintenanceReq.category?.replace('_', ' ')}</Badge>
                            </div>
                            <div>
                                <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">Description</p>
                                <p className="text-sm bg-slate-50 rounded-lg p-3">{selectedMaintenanceReq.description}</p>
                            </div>
                            <div className="grid grid-cols-2 gap-3 text-sm">
                                <div>
                                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">Location</p>
                                    <p>{selectedMaintenanceReq.location || '—'}</p>
                                </div>
                                <div>
                                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">Submitted
                                        By</p>
                                    <p>{selectedMaintenanceReq.submitted_by_name || '—'}</p>
                                </div>
                                <div>
                                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">Submitted</p>
                                    <p>{selectedMaintenanceReq.created_at ? formatDate(selectedMaintenanceReq.created_at) : '—'}</p>
                                </div>
                                {selectedMaintenanceReq.assigned_to_name && (
                                    <div>
                                        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">Assigned
                                            To</p>
                                        <p>{selectedMaintenanceReq.assigned_to_name}</p>
                                    </div>
                                )}
                                {selectedMaintenanceReq.scheduled_date && (
                                    <div>
                                        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">Scheduled
                                            Date</p>
                                        <p className="text-emerald-700 font-medium">{formatDate(selectedMaintenanceReq.scheduled_date)}</p>
                                    </div>
                                )}
                                {selectedMaintenanceReq.estimated_completion && (
                                    <div>
                                        <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">Est.
                                            Completion</p>
                                        <p>{formatDate(selectedMaintenanceReq.estimated_completion)}</p>
                                    </div>
                                )}
                            </div>
                            {selectedMaintenanceReq.contractor_name && (
                                <div>
                                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">Contractor</p>
                                    <p className="text-sm font-medium">{selectedMaintenanceReq.contractor_name}</p>
                                </div>
                            )}
                            {selectedMaintenanceReq.admin_notes && (
                                <div>
                                    <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1">Notes</p>
                                    <p className="text-sm bg-slate-50 rounded-lg p-3">{selectedMaintenanceReq.admin_notes}</p>
                                </div>
                            )}
                        </div>
                    </DialogContent>
                </Dialog>
            )}
        </div>
    );
};

export default MaintenancePage;
