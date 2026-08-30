import React from 'react';
import {render, screen} from '@testing-library/react';
import '@testing-library/jest-dom';

import FeatureGuard from '@/components/layout/FeatureGuard';

const mockPush = jest.fn();
const authState = {
    hasFeatureAccess: jest.fn(() => false),
    loading: false,
    selectedBuilding: {id: '16244', name: 'Sierra'},
};

jest.mock('next/navigation', () => ({
    useRouter: () => ({push: mockPush}),
}));

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => authState,
}));

jest.mock('@/components/ui/card', () => ({
    Card: ({children}: any) => <div>{children}</div>,
    CardContent: ({children}: any) => <div>{children}</div>,
}));

jest.mock('@/components/ui/button', () => ({
    Button: ({children, ...props}: any) => <button {...props}>{children}</button>,
}));

describe('FeatureGuard', () => {
    beforeEach(() => {
        authState.hasFeatureAccess = jest.fn(() => false);
        authState.loading = false;
        authState.selectedBuilding = {id: '16244', name: 'Sierra'};
        mockPush.mockReset();
    });

    it('shows building-specific unavailable message with Strata Web link', () => {
        render(
            <FeatureGuard featureKey="maintenance">
                <div>Protected content</div>
            </FeatureGuard>
        );

        expect(screen.queryByText('Access Disabled')).not.toBeInTheDocument();
        expect(screen.getByText('Feature not available for Sierra')).toBeInTheDocument();

        const link = screen.getByRole('link', {name: 'Strata Website'});
        expect(link).toHaveAttribute('href', 'https://my.civiumstrata.com.au');
    });

    it('renders protected children when the feature is enabled', () => {
        authState.hasFeatureAccess = jest.fn(() => true);

        render(
            <FeatureGuard featureKey="maintenance">
                <div>Protected content</div>
            </FeatureGuard>
        );

        expect(screen.getByText('Protected content')).toBeInTheDocument();
        expect(screen.queryByText(/Feature not available for/)).not.toBeInTheDocument();
    });

    it('renders a loading spinner while auth state is loading', () => {
        authState.loading = true;

        render(
            <FeatureGuard featureKey="maintenance">
                <div>Protected content</div>
            </FeatureGuard>
        );

        expect(document.querySelector('.animate-spin')).not.toBeNull();
    });

    it('renders the provided fallback instead of the default unavailable card', () => {
        render(
            <FeatureGuard featureKey="maintenance" fallback={<div>Custom fallback</div>}>
                <div>Protected content</div>
            </FeatureGuard>
        );

        expect(screen.getByText('Custom fallback')).toBeInTheDocument();
        expect(screen.queryByText(/Feature not available for/)).not.toBeInTheDocument();
    });
});
