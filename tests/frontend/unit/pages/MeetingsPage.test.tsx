import React from 'react';
import {render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import MeetingsPage from '@/pages/dashboard/MeetingsPage';

// Mock dependencies
jest.mock('@/contexts/AuthContext', () => ({
    useAuth: jest.fn()
}));

const mockUseAuth = require('@/contexts/AuthContext').useAuth;

describe('MeetingsPage', () => {
    beforeEach(() => {
        mockUseAuth.mockReturnValue({
            user: {role: 'owner', unit_number: '101'},
            token: 'fake-token',
            api: {
                get: (url: string) => {
                    if (url.includes('/meetings')) {
                        return Promise.resolve({
                            data: [
                                {
                                    id: 'meet-1',
                                    title: 'Annual General Meeting 2024',
                                    type: 'agm',
                                    date: '2024-11-15T10:00:00Z',
                                    time: '10:00 AM',
                                    location_type: 'online',
                                    attendees_count: 45
                                }
                            ]
                        });
                    }
                    return Promise.resolve({data: {}});
                },
            },
            hasPermission: () => true,
            isAdmin: () => false,
            isManager: () => false,
            isECMember: () => false,
        });
    });

    it('renders correctly and displays meetings data', async () => {
        render(
                <MeetingsPage/>
        );

        // Wait for data to load
        await waitFor(() => {
            expect(screen.getByRole('heading', {name: /^Meetings$/i})).toBeInTheDocument();
        }, {timeout: 3000});

        // Check meeting card
        await waitFor(() => {
            expect(screen.getByText('Annual General Meeting 2024')).toBeInTheDocument();
        });
    });
});
