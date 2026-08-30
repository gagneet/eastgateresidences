/**
 * Type definition validation tests.
 * These tests verify that the TypeScript interfaces defined during the
 * TS core migration (branch: ts-core-migration) are correctly structured
 * and match the expected shapes from the backend API.
 */

import type {AuthNotification, RegisterRequest,} from '@/types/auth';
import type {
    AnnouncementResponse,
    CommunicationHistoryEntry,
    NoticeAttachment,
    NoticeResponse,
} from '@/types/communication';
import type {MaintenanceApprovalEntry, MaintenanceNote,} from '@/types/maintenance';
import type {FeatureAccessSummary,} from '@/types/feature_toggle';

// ---------------------------------------------------------------------------
// AuthNotification shape
// ---------------------------------------------------------------------------
describe('AuthNotification type', () => {
    it('accepts a valid notification object', () => {
        const notification: AuthNotification = {
            id: 'notif-001',
            title: 'Test notification',
            message: 'This is a test',
            is_read: false,
            created_at: '2026-01-01T00:00:00Z',
        };
        expect(notification.id).toBe('notif-001');
        expect(notification.link).toBeUndefined();
    });

    it('accepts a notification with an optional link', () => {
        const notification: AuthNotification = {
            id: 'notif-002',
            title: 'With link',
            message: 'Click here',
            is_read: true,
            created_at: '2026-01-02T00:00:00Z',
            link: '/financials/overview',
        };
        expect(notification.link).toBe('/financials/overview');
    });
});

// ---------------------------------------------------------------------------
// RegisterRequest shape
// ---------------------------------------------------------------------------
describe('RegisterRequest type', () => {
    it('requires email, password, and full_name', () => {
        const req: RegisterRequest = {
            email: 'test@example.com',
            password: 'secret123',
            full_name: 'Test User',
        };
        expect(req.email).toBeDefined();
        expect(req.password).toBeDefined();
        expect(req.full_name).toBeDefined();
    });

    it('accepts optional fields', () => {
        const req: RegisterRequest = {
            email: 'owner@example.com',
            password: 'secure!',
            full_name: 'Owner Name',
            unit_number: 'UA001',
            role: 'owner',
            phone: '0400000000',
        };
        expect(req.unit_number).toBe('UA001');
    });
});

// ---------------------------------------------------------------------------
// CommunicationHistoryEntry shape
// ---------------------------------------------------------------------------
describe('CommunicationHistoryEntry type', () => {
    it('requires action, performed_by, and timestamp', () => {
        const entry: CommunicationHistoryEntry = {
            action: 'created',
            performed_by: 'user-001',
            timestamp: '2026-01-01T00:00:00Z',
        };
        expect(entry.performed_by_name).toBeUndefined();
        expect(entry.details).toBeUndefined();
    });

    it('accepts optional fields', () => {
        const entry: CommunicationHistoryEntry = {
            action: 'approved',
            performed_by: 'admin-001',
            performed_by_name: 'Admin User',
            timestamp: '2026-02-01T00:00:00Z',
            details: 'Approved at board meeting',
        };
        expect(entry.performed_by_name).toBe('Admin User');
    });
});

// ---------------------------------------------------------------------------
// NoticeAttachment shape
// ---------------------------------------------------------------------------
describe('NoticeAttachment type', () => {
    it('requires filename and file_url', () => {
        const attachment: NoticeAttachment = {
            filename: 'document.pdf',
            file_url: 'https://storage.example.com/document.pdf',
        };
        expect(attachment.file_size).toBeUndefined();
        expect(attachment.content_type).toBeUndefined();
    });

    it('accepts all optional fields', () => {
        const attachment: NoticeAttachment = {
            filename: 'bylaws.pdf',
            file_url: 'https://storage.example.com/bylaws.pdf',
            file_size: 102400,
            content_type: 'application/pdf',
        };
        expect(attachment.file_size).toBe(102400);
    });
});

// ---------------------------------------------------------------------------
// AnnouncementResponse shape
// ---------------------------------------------------------------------------
describe('AnnouncementResponse type', () => {
    it('accepts a valid announcement with typed history', () => {
        const history: CommunicationHistoryEntry[] = [
            {action: 'created', performed_by: 'user-1', timestamp: '2026-01-01T00:00:00Z'},
        ];
        const announcement: AnnouncementResponse = {
            id: 'ann-001',
            title: 'AGM Notice',
            content: 'The AGM will be held...',
            priority: 'high',
            is_public: true,
            created_by: 'user-1',
            created_by_name: 'Chairman',
            history,
            created_at: '2026-01-01T00:00:00Z',
        };
        expect(announcement.history[0].action).toBe('created');
        expect(announcement.history[0].performed_by).toBe('user-1');
    });
});

// ---------------------------------------------------------------------------
// NoticeResponse shape
// ---------------------------------------------------------------------------
describe('NoticeResponse type', () => {
    it('accepts a valid notice with typed attachments', () => {
        const notice: NoticeResponse = {
            id: 'notice-001',
            title: 'By-laws Update',
            content: 'Updated by-laws...',
            category: 'legal',
            priority: 'normal',
            is_pinned: false,
            requires_acknowledgment: true,
            attachments: [
                {filename: 'bylaws.pdf', file_url: 'https://example.com/bylaws.pdf'},
            ],
            created_by: 'admin-1',
            created_by_name: 'Administrator',
            history: [],
            created_at: '2026-01-01T00:00:00Z',
            updated_at: '2026-01-02T00:00:00Z',
            acknowledgment_count: 5,
            comment_count: 2,
        };
        expect(notice.attachments?.[0].filename).toBe('bylaws.pdf');
    });
});

// ---------------------------------------------------------------------------
// MaintenanceApprovalEntry shape
// ---------------------------------------------------------------------------
describe('MaintenanceApprovalEntry type', () => {
    it('accepts valid approval actions', () => {
        const entry: MaintenanceApprovalEntry = {
            action: 'approved',
            performed_by: 'ec-member-1',
            timestamp: '2026-01-05T10:00:00Z',
        };
        expect(entry.action).toBe('approved');
        expect(entry.comment).toBeUndefined();
    });

    it('accepts all maintenance action types', () => {
        const actions: MaintenanceApprovalEntry['action'][] = [
            'approved', 'rejected', 'submitted', 'assigned', 'completed', 'under_review'
        ];
        actions.forEach(action => {
            const entry: MaintenanceApprovalEntry = {
                action,
                performed_by: 'user-1',
                timestamp: '2026-01-01T00:00:00Z',
            };
            expect(entry.action).toBe(action);
        });
    });
});

// ---------------------------------------------------------------------------
// MaintenanceNote shape
// ---------------------------------------------------------------------------
describe('MaintenanceNote type', () => {
    it('requires id, content, created_by, and created_at', () => {
        const note: MaintenanceNote = {
            id: 'note-001',
            content: 'Contractor arrived on site',
            created_by: 'contractor-1',
            created_at: '2026-01-10T14:00:00Z',
        };
        expect(note.created_by_name).toBeUndefined();
    });
});

// ---------------------------------------------------------------------------
// FeatureAccessSummary shape
// ---------------------------------------------------------------------------
describe('FeatureAccessSummary type', () => {
    it('maps feature keys to effective_access booleans', () => {
        const summary: FeatureAccessSummary = {
            feature_key: 'maintenance',
            feature_name: 'Maintenance',
            site_wide_enabled: true,
            effective_access: true,
        };
        const accessMap: Record<string, boolean> = {};
        accessMap[summary.feature_key] = summary.effective_access;
        expect(accessMap['maintenance']).toBe(true);
    });

    it('accepts optional user_override and category', () => {
        const summary: FeatureAccessSummary = {
            feature_key: 'admin_tools',
            feature_name: 'Admin Tools',
            site_wide_enabled: false,
            user_override: true,
            effective_access: true,
            category: 'admin',
        };
        expect(summary.user_override).toBe(true);
        expect(summary.category).toBe('admin');
    });
});
