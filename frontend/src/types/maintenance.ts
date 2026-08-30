export enum MaintenanceStatus {
    SUBMITTED = "submitted",
    UNDER_REVIEW = "under_review",
    APPROVED = "approved",
    IN_PROGRESS = "in_progress",
    COMPLETED = "completed",
    REJECTED = "rejected",
}

export interface MaintenanceApprovalEntry {
    action: "approved" | "rejected" | "submitted" | "assigned" | "completed" | "under_review";
    performed_by: string;
    performed_by_name?: string;
    timestamp: string;
    comment?: string;
}

export interface MaintenanceNote {
    id: string;
    content: string;
    created_by: string;
    created_by_name?: string;
    created_at: string;
}

export interface MaintenanceRequestResponse {
    id: string;
    title: string;
    description: string;
    category: string;
    location: string;
    priority: "low" | "normal" | "high" | "urgent";
    status: MaintenanceStatus;
    images: string[];
    submitted_by: string;
    submitted_by_name: string;
    assigned_contractor?: string | null;
    contractor_name?: string | null;
    estimated_cost?: number | null;
    actual_cost?: number | null;
    purchase_order_id?: string | null;
    invoice_id?: string | null;
    work_order_ids: string[];
    approval_history: MaintenanceApprovalEntry[];
    notes: MaintenanceNote[];
    created_at: string;
    updated_at: string;
    completed_at?: string | null;
}

export interface ContractorResponse {
    id: string;
    name: string;
    abn?: string | null;
    email?: string | null;
    phone?: string | null;
    address?: string | null;
    services: string[];
    notes?: string | null;
    insurance_expiry?: string | null;
    public_liability_expiry?: string | null;
    workers_comp_expiry?: string | null;
    rating?: number | null;
    total_jobs: number;
    total_spent: number;
    created_at: string;
}

export interface PurchaseOrderResponse {
    id: string;
    po_number: string;
    maintenance_request_id: string;
    contractor_id: string;
    contractor_name: string;
    description: string;
    amount: number;
    status: "draft" | "sent" | "accepted" | "completed" | "cancelled";
    due_date?: string | null;
    created_by: string;
    created_at: string;
}

export interface InvoiceResponse {
    id: string;
    invoice_number: string;
    purchase_order_id: string;
    po_number: string;
    contractor_id: string;
    contractor_name: string;
    amount: number;
    gst_amount: number;
    total_amount: number;
    status: "pending" | "approved" | "paid";
    notes?: string | null;
    approved_by?: string | null;
    approved_at?: string | null;
    paid_at?: string | null;
    created_at: string;
}
