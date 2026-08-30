import React, {act} from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import MeetingsPage from '@/pages/dashboard/MeetingsPage';

jest.mock('next/navigation', () => ({
    usePathname: () => '/governance/meetings',
    useRouter: () => ({
        push: jest.fn(),
    }),
    useSearchParams: () => ({
        get: jest.fn().mockReturnValue(null),
    }),
}));

const mockApi = {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn(),
    delete: jest.fn(),
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: mockApi,
        hasPermission: (p: string) => true,
        user: {id: 'user-1', role: 'chairman', full_name: 'Chairman'},
    }),
}));

describe('MeetingsPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockApi.get.mockImplementation((url: string) => {
            if (url.includes('/meetings')) {
                return Promise.resolve({
                    data: [
                        {
                            id: 'm1',
                            title: 'EC Meeting Jan',
                            meeting_date: '2026-01-15T18:00:00',
                            location: 'Common Room',
                            status: 'scheduled',
                            agenda: ['Item 1', 'Item 2']
                        }
                    ]
                });
            }
            return Promise.resolve({data: []});
        });
    });

    it('renders meetings list', async () => {
        await act(async () => {
            render(
                    <MeetingsPage/>
            );
        });

        expect(screen.getByTestId('meetings-page')).toBeInTheDocument();

        await waitFor(() => {
            expect(screen.getByText('EC Meeting Jan')).toBeInTheDocument();
            expect(screen.getByText('Common Room')).toBeInTheDocument();
        });
    });

    it('allows scheduling a meeting', async () => {
        await act(async () => {
            render(
                    <MeetingsPage/>
            );
        });

        const scheduleBtn = screen.getByTestId('schedule-meeting-btn');
        fireEvent.click(scheduleBtn);

        // Use a more specific query if multiple elements exist
        expect(screen.getByRole('heading', {name: 'Schedule Meeting'})).toBeInTheDocument();

        fireEvent.change(screen.getByTestId('meeting-title-input'), {target: {value: 'New Test Meeting'}});
        fireEvent.change(screen.getByTestId('meeting-date-input'), {target: {value: '2026-02-20T10:00'}});
        fireEvent.change(screen.getByTestId('meeting-location-input'), {target: {value: 'Zoom'}});

        mockApi.post.mockResolvedValueOnce({data: {id: 'm2'}});

        const submitBtn = screen.getByRole('button', {name: /^Schedule Meeting$/i});
        fireEvent.click(submitBtn);

        await waitFor(() => {
            expect(mockApi.post).toHaveBeenCalledWith('/meetings', expect.objectContaining({
                title: 'New Test Meeting',
                location: 'Zoom'
            }));
        });
    });

    it('allows editing a meeting', async () => {
        await act(async () => {
            render(
                    <MeetingsPage/>
            );
        });

        await waitFor(() => {
            expect(screen.getByText('EC Meeting Jan')).toBeInTheDocument();
        });

        fireEvent.click(screen.getByRole('button', {name: /^Edit$/i}));

        expect(screen.getByRole('heading', {name: 'Edit Meeting'})).toBeInTheDocument();

        fireEvent.change(screen.getByTestId('meeting-title-input'), {target: {value: 'EC Meeting Updated'}});
        fireEvent.change(screen.getByTestId('meeting-location-input'), {target: {value: 'Zoom Room'}});

        mockApi.put.mockResolvedValueOnce({data: {id: 'm1'}});

        fireEvent.click(screen.getByRole('button', {name: /Save Meeting Changes/i}));

        await waitFor(() => {
            expect(mockApi.put).toHaveBeenCalledWith('/meetings/m1', expect.objectContaining({
                title: 'EC Meeting Updated',
                location: 'Zoom Room'
            }));
        });
    });
});
