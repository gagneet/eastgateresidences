export interface FeatureAccessSummary {
    feature_key: string;
    feature_name: string;
    site_wide_enabled: boolean;
    user_override?: boolean | null;
    effective_access: boolean;
    category?: string | null;
}

export interface FeatureToggleResponse {
    id: string;
    feature_key: string;
    feature_name: string;
    description?: string | null;
    is_enabled: boolean;
    category?: string | null;
    icon?: string | null;
    routes: string[];
    created_at: string;
    updated_at: string;
    updated_by?: string | null;
}
