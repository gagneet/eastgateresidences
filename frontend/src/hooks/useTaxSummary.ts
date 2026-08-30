import {useState} from 'react';
import {toast} from 'sonner';
/**
 * Shared hook for downloading the Annual Tax Summary PDF.
 * @param {Object} api - The API instance from useAuth.
 */
export const useTaxSummary = (api: any) => {
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    /**
     * @generated FunctionHeader
     * Function: downloadTaxSummary
     * Path: frontend/src/hooks/useTaxSummary.ts
     *
     * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
     */
    const downloadTaxSummary = async (unitNumber: string | number, financialYear: string | number) => {
        if (!unitNumber || !financialYear) {
            toast.error("Unit number and financial year are required");
            return;
        }

        setLoading(true);
        setError(null);
        const toastId = toast.loading(`Generating Tax Summary for ${unitNumber}...`);

        try {
            const response = await api.get(
                `/analytics/tax-summary/${unitNumber}?financial_year=${financialYear}`,
                {responseType: 'blob'}
            );

            const url = window.URL.createObjectURL(new Blob([response.data]));
            const link = document.createElement('a');
            link.href = url;
            link.setAttribute('download', `Tax_Summary_${unitNumber}_${financialYear}.pdf`);
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);

            toast.success('Tax summary downloaded', {id: toastId});
        } catch (err: any) {
            const msg = err?.response?.data?.detail || "Failed to generate tax summary";
            setError(msg);
            toast.error(msg, {id: toastId});
        } finally {
            setLoading(false);
        }
    };

    return {
        loading,
        error,
        downloadTaxSummary
    };
};
