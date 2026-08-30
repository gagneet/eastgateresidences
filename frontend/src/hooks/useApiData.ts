"use client";
/**
 * Custom React Hook for API Data Fetching
 *
 * This hook eliminates the duplicated data fetching pattern used across 15+ pages.
 * Handles loading states, error handling, and provides a clean refetch mechanism.
 */

import {useCallback, useEffect, useRef, useState} from 'react';
import {toast} from 'sonner';

interface ApiDataOptions<T> {
    dependencies?: any[];
    fetchOnMount?: boolean;
    enabled?: boolean;
    onError?: (error: any) => void;
    onSuccess?: (data: T) => void;
}
/**
 * Hook for fetching and managing API data
 *
 * @param {Function|Array<Function>} fetchFunctions - Single fetch function or array of fetch functions
 * @param {Object} options - Configuration options
 * @returns {Object} Data state and control functions
 */
export function useApiData<T = any>(
    fetchFunctions: (() => Promise<any>) | (() => Promise<any>)[],
    options: ApiDataOptions<T> = {}
) {
    const {
        dependencies = [],
        fetchOnMount = true,
        enabled = true,
        onError,
        onSuccess,
    } = options;

    const [data, setData] = useState<T>(Array.isArray(fetchFunctions) ? [] as any : null as any);
    const [loading, setLoading] = useState(fetchOnMount && enabled);
    const [error, setError] = useState<string | null>(null);
    const isMounted = useRef(true);

    const fetchData = useCallback(async () => {
        if (!enabled) return;

        setLoading(true);
        setError(null);

        try {
            let result: T;

            if (Array.isArray(fetchFunctions)) {
                // Multiple API calls — allSettled so one failure doesn't blank the whole view
                const promises = fetchFunctions.map(fn => fn());
                const settled = await Promise.allSettled(promises);
                result = settled.map(r => (r.status === 'fulfilled' ? r.value.data : null)) as any;
            } else {
                // Single API call
                const response = await fetchFunctions();
                result = response.data;
            }

            if (isMounted.current) {
                setData(result);
                if (onSuccess) {
                    onSuccess(result);
                }
            }
        } catch (err: any) {
            if (isMounted.current) {
                const errorMessage = err.response?.data?.detail || err.message || 'Failed to fetch data';
                setError(errorMessage);

                if (onError) {
                    onError(err);
                } else {
                    toast.error(errorMessage);
                }
            }
        } finally {
            if (isMounted.current) {
                setLoading(false);
            }
        }
    }, [fetchFunctions, onError, onSuccess, enabled]);

    useEffect(() => {
        isMounted.current = true;
        if (fetchOnMount && enabled) {
            fetchData();
        }

        return () => {
            isMounted.current = false;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [fetchData, fetchOnMount, enabled, ...dependencies]);

    return {
        data,
        loading,
        error,
        refetch: fetchData,
        setData, // Allow manual data updates
    };
}

export default useApiData;
