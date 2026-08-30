import React, {useMemo, useState, useEffect} from 'react';
import {useRouter, useSearchParams} from 'next/navigation';
import {Filter} from 'lucide-react';
import {Select, SelectContent, SelectItem, SelectTrigger, SelectValue} from '@/components/ui/select';

export const ALL_STATUS_VALUE = 'all';
/**
 * @generated FunctionHeader
 * Function: titleize
 * Path: frontend/src/components/requests/statusFilter.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const titleize = (value) => String(value || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
/**
 * @generated FunctionHeader
 * Function: statusOptionsFromConfig
 * Path: frontend/src/components/requests/statusFilter.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const statusOptionsFromConfig = (statusConfig, records = []) => {
    const labels = new Map();
    Object.entries(statusConfig || {}).forEach(([value, config]) => {
        labels.set(value, config?.label || titleize(value));
    });
    (records || []).forEach(record => {
        if (record?.status && !labels.has(record.status)) {
            labels.set(record.status, titleize(record.status));
        }
    });
    return [
        {value: ALL_STATUS_VALUE, label: 'All statuses'},
        ...Array.from(labels.entries()).map(([value, label]) => ({value, label})),
    ];
};
/**
 * @generated FunctionHeader
 * Function: countByStatus
 * Path: frontend/src/components/requests/statusFilter.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const countByStatus = (records, options) => {
    const counts = Object.fromEntries(options.map(option => [option.value, 0]));
    (records || []).forEach(record => {
        counts[ALL_STATUS_VALUE] = (counts[ALL_STATUS_VALUE] || 0) + 1;
        if (record?.status && counts[record.status] != null) {
            counts[record.status] += 1;
        }
    });
    return counts;
};
/**
 * @generated FunctionHeader
 * Function: filterByStatus
 * Path: frontend/src/components/requests/statusFilter.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export const filterByStatus = (records, status) => {
    if (!status || status === ALL_STATUS_VALUE) return records || [];
    return (records || []).filter(record => record?.status === status);
};
/**
 * @generated FunctionHeader
 * Function: useUrlStatusFilter
 * Path: frontend/src/components/requests/statusFilter.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function useUrlStatusFilter({records, statusConfig, tab, basePath = '/requests'}) {
    const router = useRouter();
    const searchParams = useSearchParams();
    const searchParamString = searchParams?.toString?.() || '';
    const options = useMemo(() => statusOptionsFromConfig(statusConfig, records), [statusConfig, records]);
    const validValues = useMemo(() => new Set(options.map(option => option.value)), [options]);
    const [statusFilter, setStatusFilter] = useState(() => {
        const value = new URLSearchParams(searchParamString).get('status') || ALL_STATUS_VALUE;
        return validValues.has(value) ? value : ALL_STATUS_VALUE;
    });

    useEffect(() => {
        const value = new URLSearchParams(searchParamString).get('status') || ALL_STATUS_VALUE;
        setStatusFilter(validValues.has(value) ? value : ALL_STATUS_VALUE);
    }, [searchParamString, validValues]);

    const counts = useMemo(() => countByStatus(records, options), [records, options]);
    const filteredRecords = useMemo(() => filterByStatus(records, statusFilter), [records, statusFilter]);
    /**
     * @generated FunctionHeader
     * Function: setUrlStatusFilter
     * Path: frontend/src/components/requests/statusFilter.jsx
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const setUrlStatusFilter = (value) => {
        const next = validValues.has(value) ? value : ALL_STATUS_VALUE;
        setStatusFilter(next);
        const params = new URLSearchParams(searchParamString);
        // Persist the owning tab so copied URLs reopen the same request sub-list.
        if (tab) params.set('tab', tab);
        if (next === ALL_STATUS_VALUE) params.delete('status');
        else params.set('status', next);
        const query = params.toString();
        router.replace(query ? `${basePath}?${query}` : basePath, {scroll: false});
    };

    return {options, counts, filteredRecords, statusFilter, setStatusFilter: setUrlStatusFilter};
}
/**
 * @generated FunctionHeader
 * Function: StatusFilterSelect
 * Path: frontend/src/components/requests/statusFilter.jsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function StatusFilterSelect({options, counts, value, onChange, label = 'Filter by status'}) {
    return (
        <div className="flex items-center gap-2">
            <Filter className="h-4 w-4 text-muted-foreground" aria-hidden="true"/>
            <Select value={value} onValueChange={onChange}>
                <SelectTrigger className="w-full sm:w-56" aria-label={label}>
                    <SelectValue placeholder="Filter status"/>
                </SelectTrigger>
                <SelectContent>
                    {options.map(option => (
                        <SelectItem key={option.value} value={option.value}>
                            {option.label} ({counts[option.value] || 0})
                        </SelectItem>
                    ))}
                </SelectContent>
            </Select>
        </div>
    );
}
