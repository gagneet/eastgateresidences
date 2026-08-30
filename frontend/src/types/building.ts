export interface BuildingAssetResponse {
    id: string;
    name: string;
    category: string;
    description?: string | null;
    location?: string | null;
    serial_number?: string | null;
    manufacturer?: string | null;
    model_number?: string | null;
    installation_date?: string | null;
    warranty_expiry?: string | null;
    details: Record<string, any>;
    sensitive_data: Record<string, any>;
    tags: string[];
    created_at: string;
    updated_at: string;
    created_by: string;
}
