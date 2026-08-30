import {type ClassValue, clsx} from "clsx";
import {twMerge} from "tailwind-merge";
/**
 * @generated FunctionHeader
 * Function: cn
 * Path: frontend/src/lib/utils.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs));
}
// Format date for display
/**
 * @generated FunctionHeader
 * Function: formatDate
 * Path: frontend/src/lib/utils.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function formatDate(dateString: string | Date | null | undefined): string {
    if (!dateString) return '';
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return '';
    return date.toLocaleDateString('en-AU', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
    });
}
// Format date with time (24h)
/**
 * @generated FunctionHeader
 * Function: formatDateTime
 * Path: frontend/src/lib/utils.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function formatDateTime(dateString: string | Date | null | undefined): string {
    if (!dateString) return '';
    const date = new Date(dateString);
    if (isNaN(date.getTime())) return '';
    return date.toLocaleDateString('en-AU', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        hour12: false
    });
}
// Format currency
/**
 * @generated FunctionHeader
 * Function: formatCurrency
 * Path: frontend/src/lib/utils.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function formatCurrency(amount: number | string | null | undefined): string {
    const num = typeof amount === 'string' ? parseFloat(amount) : (amount || 0);
    return new Intl.NumberFormat('en-AU', {
        style: 'currency',
        currency: 'AUD',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    }).format(num);
}
// Truncate text
/**
 * @generated FunctionHeader
 * Function: truncateText
 * Path: frontend/src/lib/utils.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function truncateText(text: string | null | undefined, maxLength: number = 100): string {
    if (!text || text.length <= maxLength) return text || '';
    return text.substring(0, maxLength) + '...';
}
/**
 * Intelligent truncation that breaks at word boundaries and adds ellipsis.
 * Useful for SEO descriptions.
 */
export function truncateDescription(text: string | null | undefined, maxLength: number = 160): string {
    if (!text || text.length <= maxLength) return text || '';

    // Find the last space before the limit
    const truncated = text.substring(0, maxLength);
    const lastSpace = truncated.lastIndexOf(' ');

    if (lastSpace > 0) {
        return truncated.substring(0, lastSpace) + '...';
    }

    return truncated + '...';
}
// Get initials from name
/**
 * @generated FunctionHeader
 * Function: getInitials
 * Path: frontend/src/lib/utils.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function getInitials(name: string | null | undefined): string {
    if (!name) return '';
    return name
        .split(' ')
        .filter(Boolean)
        .map(n => n[0])
        .join('')
        .toUpperCase()
        .substring(0, 2);
}

// Role display names
export const roleDisplayNames: Record<string, string> = {
    super_admin: 'Super Admin',
    strata_admin: 'Strata Admin',
    ec_member: 'EC Member',
    strata_manager: 'Strata Manager',
    admin_staff: 'Admin Staff',
    owner: 'Owner',
    tenant: 'Tenant',
    real_estate_agent: 'Real Estate Agent',
    service_provider: 'Service Provider',
    guest: 'Guest'
};

// EC position display labels shown under role name in sidebar.
// Per migration 0025: chairman is an EC position (not a role); the
// CHAIRMAN slot is rendered as "Chairman" — the legacy label "Building
// Admin" was carried over from the building_admin → strata_admin rename
// and conflated the role with the position.
export const ecPositionDisplayNames: Record<string, string> = {
    CHAIRMAN: 'Chairman',
    TREASURER: 'Treasurer',
    SECRETARY: 'Secretary',
    MEMBER: '',
};

// Listing type display names
export const listingTypeNames: Record<string, string> = {
    for_sale: 'For Sale',
    for_rent: 'For Rent',
    service: 'Service',
    item_sale: 'Item for Sale'
};

// Priority display
export interface PriorityStyle {
    label: string;
    class: string;
}

export const priorityConfig: Record<string, PriorityStyle> = {
    low: {label: 'Low', class: 'bg-gray-100 text-gray-800'},
    normal: {label: 'Normal', class: 'bg-blue-100 text-blue-800'},
    high: {label: 'High', class: 'bg-orange-100 text-orange-800'},
    urgent: {label: 'Urgent', class: 'bg-red-100 text-red-800'}
};

// Document categories
export const documentCategories = [
    {value: 'ec_documents', label: 'EC Documents'},
    {value: 'public_documents', label: 'Public Documents'},
    {value: 'meeting_minutes', label: 'Meeting Minutes'},
    {value: 'financial_reports', label: 'Financial Reports'},
    {value: 'bylaws', label: 'Bylaws'},
    {value: 'notices', label: 'Notices'}
];

// Emergency service categories
export const emergencyCategories: Record<string, string> = {
    fire: 'Fire & Rescue',
    police: 'Police',
    medical: 'Medical',
    utility: 'Utilities',
    building: 'Building Services'
};

// Schedule types
export const scheduleTypes: Record<string, string> = {
    general: 'General',
    cleaning: 'Cleaning',
    maintenance: 'Maintenance',
    inspection: 'Inspection'
};

// Status configurations
export interface StatusStyle {
    label: string;
    class: string;
}

export const statusConfig: Record<string, StatusStyle> = {
    active: {label: 'Active', class: 'bg-green-100 text-green-800'},
    pending: {label: 'Pending', class: 'bg-yellow-100 text-yellow-800'},
    completed: {label: 'Completed', class: 'bg-blue-100 text-blue-800'},
    cancelled: {label: 'Cancelled', class: 'bg-gray-100 text-gray-800'},
    scheduled: {label: 'Scheduled', class: 'bg-purple-100 text-purple-800'},
    in_progress: {label: 'In Progress', class: 'bg-orange-100 text-orange-800'},
    archived: {label: 'Archived', class: 'bg-slate-100 text-slate-600'}
};
// Format a number to at most 4 decimal places (default 2).
// desiredDecimals: preferred precision; maxDecimals: hard upper bound (≤ 4).
/**
 * @generated FunctionHeader
 * Function: formatNumber
 * Path: frontend/src/lib/utils.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function formatNumber(
    value: number | string | null | undefined,
    desiredDecimals: number = 2,
    maxDecimals: number = 4
): string {
    const num = typeof value === 'string' ? parseFloat(value) : (value ?? 0);
    if (isNaN(num)) return '—';
    const capped = Math.min(desiredDecimals, maxDecimals);
    return num.toLocaleString('en-AU', {
        minimumFractionDigits: 0,
        maximumFractionDigits: capped,
    });
}

// ─── GST helpers ────────────────────────────────────────────────────────────
// All levy category amounts in the database are GST-exclusive.
// Owner-facing screens must show GST-inclusive payable totals; treasurer/admin
// screens may show GST-exclusive fund splits, but must label them explicitly.
//
// New finance code should prefer frontend/src/lib/finance/levyDisplay.ts because
// it returns unambiguous field names: adminExGST/sinkingExGST/totalInclGST or
// adminInclGST/sinkingInclGST/totalInclGST. The compatibility helpers below are
// retained for existing imports.

export const GST_RATE = 0.10;
/**
 * @generated FunctionHeader
 * Function: roundMoney
 * Path: frontend/src/lib/utils.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function roundMoney(value: number): number {
    return Math.round((Number(value) || 0) * 100) / 100;
}
/**
 * @generated FunctionHeader
 * Function: withGST
 * Path: frontend/src/lib/utils.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function withGST(
    amount: number | null | undefined,
    gstRate: number = GST_RATE,
    gstRegistered: boolean = true,
): number {
    const effectiveRate = gstRegistered ? gstRate : 0;
    return roundMoney((amount ?? 0) * (1 + effectiveRate));
}
/**
 * @generated FunctionHeader
 * Function: formatLevy
 * Path: frontend/src/lib/utils.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function formatLevy(
    amountExGST: number | null | undefined,
    gstRate: number = GST_RATE,
    gstRegistered: boolean = true,
): string {
    return formatCurrency(withGST(amountExGST, gstRate, gstRegistered));
}

export interface LevyBreakdown {
    /** Admin component before GST. Kept for backward compatibility; prefer adminExGST. */
    admin: number;
    /** Sinking/capital works component before GST. Kept for backward compatibility; prefer sinkingExGST. */
    sinking: number;
    /** Admin component before GST, explicit name. */
    adminExGST: number;
    /** Sinking/capital works component before GST, explicit name. */
    sinkingExGST: number;
    /** GST component. */
    gst: number;
    /** Payable total including GST. */
    total: number;
    /** Payable total including GST, explicit name. */
    totalInclGST: number;
}
/**
 * Compatibility wrapper for existing code.
 * It returns GST-exclusive admin/sinking fund split lines plus GST and the GST-inclusive total.
 * Prefer levyBreakdownExGST() from lib/finance/levyDisplay for new work.
 */
export function levyBreakdown(
    adminExGST: number,
    sinkingExGST: number,
    gstRate: number = GST_RATE,
    gstRegistered: boolean = true,
): LevyBreakdown {
    const admin = roundMoney(adminExGST);
    const sinking = roundMoney(sinkingExGST);
    const effectiveRate = gstRegistered ? gstRate : 0;
    const gst = roundMoney((admin + sinking) * effectiveRate);
    const total = roundMoney(admin + sinking + gst);
    return {
        admin,
        sinking,
        adminExGST: admin,
        sinkingExGST: sinking,
        gst,
        total,
        totalInclGST: total,
    };
}
// ────────────────────────────────────────────────────────────────────────────

// Format as AUD currency with exactly 2dp. Returns decimal-formatted string without
/**
 * Return a consistent Financial Year label string.
 *
 * @param year           - The starting year of the financial year (e.g. 2026)
 * @param startMonth     - Month the FY begins (1=Jan … 12=Dec; default 7 = July, Australian convention)
 *
 * Examples:
 *   getFYLabel(2026, 7)  → "FY2026-27"
 *   getFYLabel(2026, 1)  → "FY2026"
 *   getFYLabel(2025)     → "FY2025-26"  (default July start)
 */
export function getFYLabel(year: number, startMonth: number = 7): string {
    if (startMonth === 1) {
        // Calendar-year FY — just show the year
        return `FY${year}`;
    }
    const endYear = (year + 1).toString().slice(-2);
    return `FY${year}-${endYear}`;
}
/**
 * Derive the current financial year start year given a start month.
 * e.g. if startMonth=7 and today is April 2026, the current FY started in July 2025 → returns 2025.
 */
export function currentFYYear(startMonth: number = 7): number {
    const now = new Date();
    return now.getMonth() + 1 >= startMonth ? now.getFullYear() : now.getFullYear() - 1;
}
/**
 * Download a file from blob data
 *
 * Consolidates the duplicated download pattern used for PDFs, CSVs, and other files.
 * Eliminates code duplication across MaintenancePage, FinancePage, and other components.
 *
 * @param {Blob} blob - The blob data to download
 * @param {string} filename - Name for the downloaded file
 *
 * @example
 * const response = await api.get('/finance/export', { responseType: 'blob' });
 * downloadFile(response.data, 'finance-report.csv');
 */
export function downloadFile(blob: Blob, filename: string): void {
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
}
