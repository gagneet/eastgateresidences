import React from 'react';
import {render, screen} from '@testing-library/react';
import '@testing-library/jest-dom';
import {ManagementDashboard} from '@/app/(dashboard)/dashboard/ManagementDashboard';

// The mocked context value is built ONCE and returned by reference.
//
// Returning a fresh object literal from useAuth() gave `api` a new identity on
// every render. ManagementDashboard's data-loading effect lists `api` in its
// dependency array and calls setDaysSinceLastVisit(), so the effect re-fired on
// every render, re-rendered, and looped: "Maximum update depth exceeded", then
// the worker hung forever. That single file was enough to stall the WHOLE
// frontend suite — it is why full `yarn test` runs never terminated.
//
// The real AuthContext memoises `api` with an empty dependency array, so this
// only ever bit under the harness. A mock must reproduce the real value's
// referential stability, not just its shape.
const mockAuthValue = {
    user: {id: 'u1', role: 'strata_manager'},
    api: {get: jest.fn(() => new Promise(() => {
    }))},
};

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => mockAuthValue,
}));

jest.mock('next/navigation', () => ({
    useRouter: () => ({push: jest.fn()}),
}));

jest.mock('@/components/dashboard/PulseScoreCard', () => ({
    __esModule: true,
    default: () => <div>Building Pulse · live</div>,
}));
jest.mock('@/components/dashboard/SinceLastVisit', () => ({
    __esModule: true,
    default: () => <div>Since your last visit</div>,
}));
jest.mock('@/components/dashboard/TriageQueue', () => ({
    __esModule: true,
    default: () => <div>Today's Triage</div>,
}));
jest.mock('@/components/dashboard/ReserveRunwayChart', () => ({
    __esModule: true,
    default: () => <div>Reserve forecast</div>,
}));
jest.mock('@/components/dashboard/CompactCalendar', () => ({
    __esModule: true,
    default: () => <div>Calendar</div>,
}));
jest.mock('@/components/dashboard/premium', () => ({
    ActivityFeedPremium: () => <div>Activity Feed</div>,
}));

describe('ManagementDashboard simplified layout', () => {
    it('renders the new v2 dashboard sections without legacy metric-card tooltips', () => {
        render(<ManagementDashboard data={{stats: {}, maintenance: {}, compliance: {}, activities: []}} selectedYear="2026"/>);

        expect(screen.getByText(/Building Pulse/i)).toBeInTheDocument();
        expect(screen.getByText(/Since your last visit/i)).toBeInTheDocument();
        expect(screen.getByText(/Today's Triage/i)).toBeInTheDocument();
        expect(screen.getByText(/Levy Fairness Index/i)).toBeInTheDocument();
        expect(screen.queryByText(/Active Residents/i)).toBeNull();
    });
});
