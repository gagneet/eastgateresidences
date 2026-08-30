// @featuretrace:user-management — Regression test: IP-notice integrity check must key off which
//   notice element the active layout actually rendered, not a hardcoded "/dashboard" URL prefix.
// Layer: test
// Data flow: IntegrityProvider (global) → DOM (#dashboard-ip-notice from DashboardLayout,
//            #ip-protection-notice from the public Footer) → full-screen lockout overlay on 2
//            consecutive failed checks.
// Related: frontend/src/contexts/IntegrityContext.tsx
//           frontend/src/components/layout/DashboardLayout.tsx
//           frontend/src/components/layout/Footer.tsx
// Tests: this file

/**
 * Regression test for the 2026-08-07 product-namespace migration false-positive lockout.
 *
 * IntegrityContext decided which branding notice to check using
 * `window.location.pathname.startsWith('/dashboard')`. That was safe only while every
 * authenticated page lived under /dashboard/*. Once ~150 pages moved to /financials, /admin,
 * /intelligence, /powerhouse etc. (while still rendering via DashboardLayout, which emits
 * #dashboard-ip-notice, not the public Footer's #ip-protection-notice), the URL-prefix check
 * went stale: on every moved page it looked for a footer notice that page never renders, and
 * after 2 consecutive failed 60s checks it threw a full-screen "System Integrity Error"
 * lockout for real users on entirely legitimate pages.
 *
 * The fix checks DOM presence directly (prefer #dashboard-ip-notice if present, else
 * #ip-protection-notice) instead of the URL string, so it can't regress the same way again
 * regardless of future route renames.
 */

import React from 'react';
import {act, render, screen} from '@testing-library/react';
import '@testing-library/jest-dom';

const mockGet = jest.fn();

jest.mock('axios', () => ({
    __esModule: true,
    default: {
        get: (...args: unknown[]) => mockGet(...args),
    },
}));

import {IntegrityProvider} from '@/contexts/IntegrityContext';

const SILVERFOX_TEXT =
    'A vision by: Silverfox Technologies, Australia • Contact: gagneet@silverfoxtechnologies.com.au';

function setPathname(pathname: string) {
    window.history.pushState({}, '', pathname);
}

async function settleAndCheckTwice() {
    // isSettled flips after 20s; checkIntegrity runs on a 60s interval and requires 2
    // consecutive failures before rendering the lockout — advance past both.
    await act(async () => {
        jest.advanceTimersByTime(20000);
    });
    await act(async () => {
        jest.advanceTimersByTime(60000);
    });
    await act(async () => {
        jest.advanceTimersByTime(60000);
    });
}

describe('IntegrityContext', () => {
    beforeAll(() => {
        // jsdom has no layout engine and doesn't implement innerText at all (only
        // textContent) — the component under test relies on innerText, so polyfill it
        // for this suite rather than changing production code to use textContent
        // (innerText vs textContent differ on whitespace/hidden text in real browsers,
        // and the component's choice of innerText is intentional, not a bug).
        Object.defineProperty(HTMLElement.prototype, 'innerText', {
            configurable: true,
            get() {
                return this.textContent;
            },
        });
    });

    beforeEach(() => {
        jest.useFakeTimers();
        mockGet.mockResolvedValue({data: {}});
        document.body.innerHTML = '';
        // jsdom never computes real layout — getBoundingClientRect() is always a zero
        // rect unless stubbed, which would make every element look "zero size" and
        // trip the integrity check regardless of what's actually being tested here.
        jest.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
            width: 100, height: 20, top: 0, left: 0, bottom: 20, right: 100, x: 0, y: 0,
            toJSON: () => {},
        });
    });

    afterEach(() => {
        jest.useRealTimers();
        jest.clearAllMocks();
    });

    it('does not lock out a moved product-namespace page whose DashboardLayout notice is intact', async () => {
        // The exact regression scenario: a page moved off /dashboard (e.g. /financials/levy-payments)
        // but still rendered via DashboardLayout, so only #dashboard-ip-notice exists — no footer at all.
        setPathname('/financials/levy-payments');
        document.body.innerHTML = `<p id="dashboard-ip-notice">${SILVERFOX_TEXT}</p>`;

        render(
            <IntegrityProvider>
                <div data-testid="app-content">App content</div>
            </IntegrityProvider>
        );

        await settleAndCheckTwice();

        expect(screen.getByTestId('app-content')).toBeInTheDocument();
        expect(screen.queryByText('System Integrity Error')).not.toBeInTheDocument();
    });

    it('does not lock out a legacy /dashboard page whose DashboardLayout notice is intact', async () => {
        setPathname('/dashboard');
        document.body.innerHTML = `<p id="dashboard-ip-notice">${SILVERFOX_TEXT}</p>`;

        render(
            <IntegrityProvider>
                <div data-testid="app-content">App content</div>
            </IntegrityProvider>
        );

        await settleAndCheckTwice();

        expect(screen.getByTestId('app-content')).toBeInTheDocument();
        expect(screen.queryByText('System Integrity Error')).not.toBeInTheDocument();
    });

    it('does not lock out a public marketing page whose Footer notice is intact', async () => {
        setPathname('/about');
        document.body.innerHTML = `<p id="ip-protection-notice">${SILVERFOX_TEXT}</p>`;

        render(
            <IntegrityProvider>
                <div data-testid="app-content">App content</div>
            </IntegrityProvider>
        );

        await settleAndCheckTwice();

        expect(screen.getByTestId('app-content')).toBeInTheDocument();
        expect(screen.queryByText('System Integrity Error')).not.toBeInTheDocument();
    });

    it('still locks out when the branding notice is genuinely removed from the DOM', async () => {
        setPathname('/financials/levy-payments');
        document.body.innerHTML = ''; // neither notice present — genuine tampering/removal

        render(
            <IntegrityProvider>
                <div data-testid="app-content">App content</div>
            </IntegrityProvider>
        );

        await settleAndCheckTwice();

        expect(screen.getByText('System Integrity Error')).toBeInTheDocument();
        expect(screen.queryByTestId('app-content')).not.toBeInTheDocument();
    });

    it('still locks out when the intact notice element has its text tampered with', async () => {
        setPathname('/admin/cutover-status');
        document.body.innerHTML = `<p id="dashboard-ip-notice">Powered by Someone Else</p>`;

        render(
            <IntegrityProvider>
                <div data-testid="app-content">App content</div>
            </IntegrityProvider>
        );

        await settleAndCheckTwice();

        expect(screen.getByText('System Integrity Error')).toBeInTheDocument();
        expect(screen.getByText(/Dashboard IP Notice content tampered with/)).toBeInTheDocument();
    });
});
