export interface CommunicationHistoryEntry {
    action: string;
    performed_by: string;
    performed_by_name?: string;
    timestamp: string;
    details?: string;
}

export interface NoticeAttachment {
    filename: string;
    file_url: string;
    file_size?: number;
    content_type?: string;
}

export interface AnnouncementResponse {
    id: string;
    title: string;
    content: string;
    priority: "low" | "normal" | "high" | "urgent";
    is_public: boolean;
    created_by: string;
    created_by_name: string;
    expires_at?: string | null;
    target_roles?: string[] | null;
    target_users?: string[] | null;
    history: CommunicationHistoryEntry[];
    created_at: string;
}

export interface NoticeResponse {
    id: string;
    title: string;
    content: string;
    category: "general" | "maintenance" | "financial" | "legal" | "meeting";
    priority: "low" | "normal" | "high" | "urgent";
    is_pinned: boolean;
    requires_acknowledgment: boolean;
    target_roles?: string[] | null;
    attachments?: NoticeAttachment[] | null;
    created_by: string;
    created_by_name: string;
    expires_at?: string | null;
    history: CommunicationHistoryEntry[];
    created_at: string;
    updated_at: string;
    acknowledgment_count: number;
    comment_count: number;
}
