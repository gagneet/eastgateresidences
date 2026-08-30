import React from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import {toast} from 'sonner';

import NotificationsPage from '@/pages/dashboard/NotificationsPage';

const mockApiGet = jest.fn();
const mockApiPost = jest.fn();
const mockApiPut = jest.fn();
const mockHasPermission = jest.fn();

const mockApi = {
    get: mockApiGet,
    post: mockApiPost,
    put: mockApiPut,
    interceptors: {
        request: {use: jest.fn(), eject: jest.fn()},
        response: {use: jest.fn(), eject: jest.fn()},
    },
};

jest.mock('sonner', () => ({
    toast: {
        success: jest.fn(),
        error: jest.fn(),
    },
}));

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: mockApi,
        hasPermission: mockHasPermission,
    }),
}));

jest.mock('@/components/ui/card', () => ({
    Card: ({children}) => <div>{children}</div>,
    CardHeader: ({children}) => <div>{children}</div>,
    CardTitle: ({children}) => <h2>{children}</h2>,
    CardDescription: ({children}) => <p>{children}</p>,
    CardContent: ({children}) => <div>{children}</div>,
}));

jest.mock('@/components/ui/button', () => ({
    Button: ({children, onClick, disabled, type = 'button', variant}) => (
        <button type={type} onClick={onClick} disabled={disabled} data-variant={variant}>
            {children}
        </button>
    ),
}));

jest.mock('@/components/ui/input', () => ({
    Input: (props) => <input {...props} />,
}));

jest.mock('@/components/ui/badge', () => ({
    Badge: ({children}) => <span>{children}</span>,
}));

jest.mock('@/components/ui/label', () => ({
    Label: ({children}) => <label>{children}</label>,
}));

jest.mock('@/components/ui/textarea', () => ({
    Textarea: (props) => <textarea {...props} />,
}));

jest.mock('@/components/ui/dialog', () => ({
    Dialog: ({children}) => <div>{children}</div>,
    DialogContent: ({children}) => <div>{children}</div>,
    DialogDescription: ({children}) => <div>{children}</div>,
    DialogHeader: ({children}) => <div>{children}</div>,
    DialogTitle: ({children}) => <div>{children}</div>,
    DialogTrigger: ({children}) => <div>{children}</div>,
}));

jest.mock('@/components/ui/select', () => ({
    Select: ({children}) => <div>{children}</div>,
    SelectContent: ({children}) => <div>{children}</div>,
    SelectItem: ({children}) => <div>{children}</div>,
    SelectTrigger: ({children}) => <div>{children}</div>,
    SelectValue: () => <div />,
}));

jest.mock('lucide-react', () => {
    const Icon = () => <span />;
    return {
        Bell: Icon,
        CheckCircle2: Icon,
        Clock: Icon,
        DollarSign: Icon,
        Loader2: Icon,
        Mail: Icon,
        MessageSquare: Icon,
        Send: Icon,
        XCircle: Icon,
    };
});

jest.mock('@/lib/utils', () => ({
    formatDateTime: (value) => `formatted:${value}`,
}));

describe('NotificationsPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockHasPermission.mockImplementation((key) => key === 'can_send_notifications');
        mockApiGet.mockImplementation((url) => {
            if (url === '/notifications') {
                return Promise.resolve({
                    data: [{
                        id: 'n1',
                        title: 'Reminder sent',
                        message: 'Quarterly reminder',
                        status: 'sent',
                        notification_type: 'levy_reminder',
                        sent_count: 4,
                        failed_count: 0,
                        channels: ['email'],
                        created_at: '2026-05-12T10:00:00Z',
                    }],
                });
            }
            if (url === '/levy-reminder-settings') {
                return Promise.resolve({
                    data: {
                        enabled: true,
                        reminder_days: [14, 7],
                    },
                });
            }
            if (url === '/levy-reminder-log') {
                return Promise.resolve({
                    data: [{
                        due_date: '2026-06-01',
                        days_before: 14,
                        sent_count: 12,
                    }],
                });
            }
            return Promise.resolve({data: {}});
        });
        mockApiPost.mockResolvedValue({data: {message: 'Sent 3 levy reminders'}});
        mockApiPut.mockResolvedValue({data: {message: 'ok'}});
    });

    it('loads notifications and levy reminder settings for permitted users', async () => {
        render(<NotificationsPage />);

        await waitFor(() => expect(mockApiGet).toHaveBeenCalledWith('/notifications'));
        await waitFor(() => expect(mockApiGet).toHaveBeenCalledWith('/levy-reminder-settings'));
        await waitFor(() => expect(mockApiGet).toHaveBeenCalledWith('/levy-reminder-log'));

        expect(screen.getByText('Automated Levy Reminders')).toBeInTheDocument();
        expect(screen.getByText(/Selected: 14, 7 days before due date/i)).toBeInTheDocument();
        expect(screen.getByText(/Due: 2026-06-01 \(14 days before\)/i)).toBeInTheDocument();
    });

    it('saves updated legacy levy reminder settings payload', async () => {
        render(<NotificationsPage />);

        await waitFor(() => expect(screen.getByText(/Selected: 14, 7 days before due date/i)).toBeInTheDocument());

        fireEvent.click(screen.getByRole('button', {name: /21 days/i}));
        fireEvent.click(screen.getByRole('button', {name: /Save Settings/i}));

        await waitFor(() => expect(mockApiPut).toHaveBeenCalledWith(
            '/levy-reminder-settings',
            expect.objectContaining({
                enabled: true,
                reminder_days: [21, 14, 7],
            }),
        ));
        expect(toast.success).toHaveBeenCalledWith('Automated reminder settings saved');
    });

    it('triggers manual levy reminders from the notifications page', async () => {
        render(<NotificationsPage />);

        await waitFor(() => expect(screen.getByRole('button', {name: /Send Levy Reminder/i})).toBeInTheDocument());
        fireEvent.click(screen.getByRole('button', {name: /Send Levy Reminder/i}));

        await waitFor(() => expect(mockApiPost).toHaveBeenCalledWith('/notifications/levy-reminder'));
        expect(toast.success).toHaveBeenCalledWith('Sent 3 levy reminders');
    });

    it('does not load reminder settings when the user lacks notification permissions', async () => {
        mockHasPermission.mockReturnValue(false);

        render(<NotificationsPage />);

        await waitFor(() => expect(mockApiGet).toHaveBeenCalledWith('/notifications'));
        expect(mockApiGet).not.toHaveBeenCalledWith('/levy-reminder-settings');
        expect(mockApiGet).not.toHaveBeenCalledWith('/levy-reminder-log');
        expect(screen.queryByText('Automated Levy Reminders')).not.toBeInTheDocument();
    });
});
