import React from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import StaffRegisterPage from '@/pages/auth/StaffRegisterPage';

const mockPush = jest.fn();
const mockPost = jest.fn();
const mockGet = jest.fn();

jest.mock('next/navigation', () => ({
    useRouter: () => ({push: mockPush}),
}));

jest.mock('axios', () => ({
    __esModule: true,
    default: {
        get: (...args: unknown[]) => mockGet(...args),
        post: (...args: unknown[]) => mockPost(...args),
    },
}));

jest.mock('sonner', () => ({
    toast: {
        error: jest.fn(),
        success: jest.fn(),
    },
}));

describe('StaffRegisterPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockGet.mockResolvedValue({
            data: [{id: '13195', name: 'East Gate Residences'}],
        });
        mockPost.mockResolvedValue({data: {status: 'pending_approval'}});
    });

    it('submits the backend staff registration contract', async () => {
        render(<StaffRegisterPage/>);

        await waitFor(() => expect(mockGet).toHaveBeenCalledTimes(1));
        fireEvent.click(screen.getByRole('button', {name: /strata manager/i}));
        fireEvent.change(screen.getByLabelText(/full name/i), {target: {value: 'Jane Manager'}});
        fireEvent.change(screen.getByLabelText(/^email/i), {target: {value: 'jane.manager@example.com'}});
        fireEvent.change(screen.getByLabelText(/^phone/i), {target: {value: '0400000000'}});
        fireEvent.change(screen.getByPlaceholderText(/min\. 8 characters/i), {target: {value: 'jest-fixture-password-not-a-credential'}});
        fireEvent.change(screen.getByPlaceholderText(/repeat your password/i), {target: {value: 'jest-fixture-password-not-a-credential'}});
        fireEvent.change(screen.getByLabelText(/company \/ agency name/i), {target: {value: 'Example Strata Pty Ltd'}});
        fireEvent.change(screen.getByLabelText(/strata license #/i), {target: {value: 'LIC-12345'}});
        fireEvent.click(screen.getByRole('checkbox', {name: /i accept the/i}));
        fireEvent.click(screen.getByRole('button', {name: /submit application/i}));

        await waitFor(() => expect(mockPost).toHaveBeenCalledTimes(1));

        const [url, payload] = mockPost.mock.calls[0];
        expect(url).toMatch(/\/api\/auth\/register\/staff$/);
        expect(payload).toEqual(expect.objectContaining({
            full_name: 'Jane Manager',
            email: 'jane.manager@example.com',
            phone: '0400000000',
            password: 'jest-fixture-password-not-a-credential',
            organisation: 'Example Strata Pty Ltd',
            professional_licence: 'LIC-12345',
            role: 'strata_manager',
            building_id: '13195',
            terms_accepted: true,
        }));
        expect(payload).not.toHaveProperty('confirmPassword');
        expect(payload).not.toHaveProperty('license_number');
        expect(payload).not.toHaveProperty('description');
    }, 10000);
});
