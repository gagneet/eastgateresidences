/**
 * Tests for MaintenanceRequestTab
 *
 * Covers:
 *  - Component renders without crashing
 *  - Internal navigation uses Next.js <Link> (not bare <a> href)
 *    so the client-side router is not bypassed
 *  - Both /maintenance links use Link
 */
import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom';

// Minimal mocks — the component has no auth or API dependencies
jest.mock('next/link', () => {
    const MockLink = ({ href, children, className }) => (
        <a href={href} className={className} data-testid="next-link">
            {children}
        </a>
    );
    MockLink.displayName = 'MockLink';
    return MockLink;
});

import MaintenanceRequestTab from '@/pages/dashboard/requests/MaintenanceRequestTab';

describe('MaintenanceRequestTab', () => {
    it('renders without crashing', () => {
        render(<MaintenanceRequestTab />);
        expect(screen.getByText(/Maintenance requests are managed/i)).toBeInTheDocument();
    });

    it('shows a link to the Maintenance page in the alert', () => {
        render(<MaintenanceRequestTab />);
        const links = screen.getAllByTestId('next-link');
        const hrefs = links.map((l) => l.getAttribute('href'));
        expect(hrefs).toContain('/maintenance');
    });

    it('shows a call-to-action button that links to the Maintenance page', () => {
        render(<MaintenanceRequestTab />);
        expect(screen.getByText('Go to Maintenance Page')).toBeInTheDocument();
    });

    it('uses Next Link (not bare <a>) for internal navigation', () => {
        const { container } = render(<MaintenanceRequestTab />);
        // All anchors must have data-testid="next-link" (injected by mock)
        // A bare <a> element would NOT have that testid.
        const allAnchors = container.querySelectorAll('a');
        const nextLinks = container.querySelectorAll('[data-testid="next-link"]');
        expect(allAnchors.length).toBeGreaterThan(0);
        expect(allAnchors.length).toBe(nextLinks.length);
    });

    it('all maintenance links point to /maintenance', () => {
        render(<MaintenanceRequestTab />);
        const links = screen.getAllByTestId('next-link');
        links.forEach((link) => {
            expect(link.getAttribute('href')).toBe('/maintenance');
        });
    });

    it('describes the four-tab interface on the Maintenance page', () => {
        render(<MaintenanceRequestTab />);
        // Use getAllByText because "Requests" can appear in multiple places
        expect(screen.getAllByText(/Requests/i).length).toBeGreaterThan(0);
        expect(screen.getByText(/Contractors/i)).toBeInTheDocument();
        expect(screen.getByText(/Purchase Orders/i)).toBeInTheDocument();
        // "Invoices" also appears in the component
        expect(screen.getAllByText(/Invoices/i).length).toBeGreaterThan(0);
    });
});
