// @featuretrace:email-delivery — Regression test: /my-communications?tab=preferences deep-link.
// Layer: test
// Data flow: CommunicationsWorkspacePage (useSearchParams) → activeTab initial state (global).
// Related: frontend/src/pages/dashboard/CommunicationsWorkspacePage.jsx
//           frontend/src/app/(app)/my-communications/page.tsx
// Tests: this file

/**
 * Regression test for the email-preferences deep-link fix (2026-08-07).
 *
 * Notification digest/portal emails link to /my-communications — before this fix, that
 * always landed on whichever tab happened to be first in the tabs array (Preferences,
 * incidentally, for most users), with no way to guarantee it. Added ?tab= support so the
 * link target is explicit and doesn't depend on tab ordering.
 */

import React from 'react';
import {render, screen} from '@testing-library/react';
import '@testing-library/jest-dom';
import CommunicationsWorkspacePage from '@/pages/dashboard/CommunicationsWorkspacePage';

const mockGet = jest.fn().mockResolvedValue({data: {}});

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        api: {get: mockGet, post: jest.fn().mockResolvedValue({data: {}})},
        hasFeatureAccess: (key: string) => key === 'email_preferences' || key === 'webmail',
        hasPermission: () => true,
    }),
}));

let mockSearchParams = new URLSearchParams();
jest.mock('next/navigation', () => ({
    useSearchParams: () => mockSearchParams,
}));

describe('CommunicationsWorkspacePage tab deep-linking', () => {
    beforeEach(() => {
        mockSearchParams = new URLSearchParams();
        jest.clearAllMocks();
    });

    it('defaults to the Preferences tab with no query param', async () => {
        render(<CommunicationsWorkspacePage/>);
        const preferencesTab = await screen.findByRole('tab', {name: 'Preferences'});
        expect(preferencesTab).toHaveAttribute('data-state', 'active');
    });

    it('honours ?tab=preferences explicitly', async () => {
        mockSearchParams = new URLSearchParams('tab=preferences');
        render(<CommunicationsWorkspacePage/>);
        const preferencesTab = await screen.findByRole('tab', {name: 'Preferences'});
        expect(preferencesTab).toHaveAttribute('data-state', 'active');
    });

    it('honours ?tab=mailbox', async () => {
        mockSearchParams = new URLSearchParams('tab=mailbox');
        render(<CommunicationsWorkspacePage/>);
        const mailboxTab = await screen.findByRole('tab', {name: 'Mailbox'});
        expect(mailboxTab).toHaveAttribute('data-state', 'active');
    });

    it('falls back to the first enabled tab when ?tab= is unrecognised', async () => {
        mockSearchParams = new URLSearchParams('tab=nonexistent');
        render(<CommunicationsWorkspacePage/>);
        const preferencesTab = await screen.findByRole('tab', {name: 'Preferences'});
        expect(preferencesTab).toHaveAttribute('data-state', 'active');
    });
});
