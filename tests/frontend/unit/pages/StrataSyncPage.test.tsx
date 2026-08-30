// @featuretrace:strata_sync — Smoke test for StrataSyncPage: renders without crashing and
// shows its initial (no sync yet) state.
// Layer: test
// Tests: frontend/src/pages/strata-sync/StrataSyncPage.jsx
import React from 'react';
import {render, screen} from '@testing-library/react';
import '@testing-library/jest-dom';

import StrataSyncPage from '@/pages/strata-sync/StrataSyncPage';

const apiGet = jest.fn(() => Promise.resolve({data: {last_job: null, summary: null}}));
const apiPost = jest.fn(() => Promise.resolve({data: {}}));

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: {get: (...args: any[]) => apiGet(...args), post: (...args: any[]) => apiPost(...args)},
    }),
}));

jest.mock('sonner', () => ({toast: {success: jest.fn(), error: jest.fn(), info: jest.fn()}}));

describe('StrataSyncPage', () => {
    beforeEach(() => {
        apiGet.mockClear();
        apiPost.mockClear();
    });

    it('renders without crashing and loads the latest sync on mount', async () => {
        render(<StrataSyncPage />);

        expect(await screen.findByText(/Strata.*Sync/i)).toBeInTheDocument();
        expect(apiGet).toHaveBeenCalledWith('/strata/sync/latest');
    });

    it('shows a start-sync action when no job is in progress', async () => {
        render(<StrataSyncPage />);

        await screen.findByText(/Strata.*Sync/i);
        expect(screen.getByRole('button', {name: /sync/i})).toBeInTheDocument();
    });
});
