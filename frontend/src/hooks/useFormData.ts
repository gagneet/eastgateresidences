"use client";
/**
 * Custom React Hook for Form Data Management
 */

import {useCallback, useState} from 'react';

interface FormUpdates {
    [key: string]: any;
}
/**
 * Hook for managing form state with built-in handlers
 */
export function useFormData<T extends Record<string, any>>(initialState: T) {
    const [formData, setFormData] = useState<T>(initialState);
    const [isSubmitting, setIsSubmitting] = useState(false);

    /**
     * Handle input change for controlled form inputs
     * Supports both direct values and event objects
     */
    const handleChange = useCallback((nameOrEvent: any, value?: any) => {
        if (typeof nameOrEvent === 'string') {
            // Direct usage: handleChange('fieldName', value)
            setFormData(prev => ({...prev, [nameOrEvent]: value}));
        } else {
            // Event usage: handleChange(e)
            const event = nameOrEvent;
            const target = event.target;
            const name = target.name;
            const newValue = target.type === 'checkbox' ? target.checked : target.value;

            setFormData(prev => ({...prev, [name]: newValue}));
        }
    }, []);

    /**
     * Handle multiple field updates at once
     * Useful for setting default values or bulk updates
     */
    const updateFields = useCallback((updates: Partial<T>) => {
        setFormData(prev => ({...prev, ...updates}));
    }, []);

    /**
     * Reset form to initial state
     */
    const resetForm = useCallback(() => {
        setFormData(initialState);
        setIsSubmitting(false);
    }, [initialState]);

    /**
     * Create a submit handler with automatic loading state
     */
    const handleSubmit = useCallback((onSubmit: (data: T) => Promise<void>) => {
        return async (e?: React.FormEvent) => {
            if (e && e.preventDefault) {
                e.preventDefault();
            }

            setIsSubmitting(true);
            try {
                await onSubmit(formData);
            } catch (error) {
                throw error;
            } finally {
                setIsSubmitting(false);
            }
        };
    }, [formData]);

    return {
        formData,
        setFormData,
        handleChange,
        updateFields,
        resetForm,
        handleSubmit,
        isSubmitting,
    };
}

export default useFormData;
