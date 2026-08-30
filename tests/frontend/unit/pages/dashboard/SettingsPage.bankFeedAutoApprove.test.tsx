/**
 * Tests: SettingsPage — Bank Feed Transaction Matching card.
 *
 * Scoped to just this card — SettingsPage is a large, pre-existing, otherwise
 * untested component; this file does not attempt broader coverage.
 *
 * Covers:
 *   - The switch reflects settings.bank_feed_auto_approve from GET /settings.
 *     Missing/undefined means "on", matching the backend's fail-open/default-on
 *     sync policy; explicit false means every match requires manual approval.
 *   - Toggling the switch and saving includes bank_feed_auto_approve in the
 *     PUT /settings payload
 */
import React from 'react';
import {render, screen, fireEvent, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import SettingsPage from '@/pages/dashboard/SettingsPage';

jest.mock('sonner', () => ({toast: {error: jest.fn(), success: jest.fn()}}));

const mockApi = {
    get: jest.fn(),
    put: jest.fn(),
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: mockApi,
        user: {role: 'super_admin', email: 'admin@test.com'},
    }),
}));

const BASE_SETTINGS = {
    building_name: 'Test Building',
    bank_feed_auto_approve: true,
};

describe('SettingsPage — Bank Feed Transaction Matching', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockApi.get.mockImplementation((url: string) => {
            if (url === '/settings') return Promise.resolve({data: BASE_SETTINGS});
            if (url === '/settings/ocr-provider') return Promise.resolve({
                data: {ocr_provider: 'auto', monthly_scans: 5, global_default: null, available_providers: []},
            });
            if (url === '/settings/unit-display') return Promise.resolve({data: {rules: []}});
            return Promise.resolve({data: {}});
        });
        mockApi.put.mockResolvedValue({data: {}});
    });

    async function openPaymentTab() {
        render(<SettingsPage/>);
        await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith('/settings'));
        const paymentTab = await screen.findByRole('tab', {name: /payment & bank/i});
        fireEvent.mouseDown(paymentTab, {button: 0, ctrlKey: false});
        await screen.findByText('Bank Feed Transaction Matching');
    }

    it('shows the switch ON when GET /settings returns bank_feed_auto_approve: true', async () => {
        await openPaymentTab();
        const toggle = screen.getByTestId('bank-feed-auto-approve-switch');
        expect(toggle).toHaveAttribute('data-state', 'checked');
    });

    it('shows the switch ON when GET /settings omits bank_feed_auto_approve', async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url === '/settings') {
                return Promise.resolve({data: {building_name: 'Test Building'}});
            }
            if (url === '/settings/ocr-provider') return Promise.resolve({
                data: {ocr_provider: 'auto', monthly_scans: 5, global_default: null, available_providers: []},
            });
            return Promise.resolve({data: {}});
        });

        await openPaymentTab();
        const toggle = screen.getByTestId('bank-feed-auto-approve-switch');
        expect(toggle).toHaveAttribute('data-state', 'checked');
    });

    it('shows the switch OFF when GET /settings returns bank_feed_auto_approve: false', async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url === '/settings') return Promise.resolve({data: {...BASE_SETTINGS, bank_feed_auto_approve: false}});
            if (url === '/settings/ocr-provider') return Promise.resolve({
                data: {ocr_provider: 'auto', monthly_scans: 5, global_default: null, available_providers: []},
            });
            return Promise.resolve({data: {}});
        });
        await openPaymentTab();
        const toggle = screen.getByTestId('bank-feed-auto-approve-switch');
        expect(toggle).toHaveAttribute('data-state', 'unchecked');
    });

    it('includes bank_feed_auto_approve in the PUT payload after toggling it off', async () => {
        await openPaymentTab();
        const toggle = screen.getByTestId('bank-feed-auto-approve-switch');
        fireEvent.click(toggle);

        const saveButton = screen.getByRole('button', {name: /save/i});
        fireEvent.click(saveButton);

        await waitFor(() => {
            expect(mockApi.put).toHaveBeenCalledWith(
                '/settings',
                expect.objectContaining({bank_feed_auto_approve: false}),
            );
        });
    });
});
