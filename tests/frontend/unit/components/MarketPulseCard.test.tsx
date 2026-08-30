import React from 'react';
import {fireEvent, render} from '@testing-library/react';
import '@testing-library/jest-dom';
import {MarketPulseCard} from '@/components/dashboard/premium/MarketPulseCard';

// Mock SparkAreaChart to avoid rendering issues in JSDOM

describe('MarketPulseCard', () => {
    it('renders correctly without onClick', () => {
        const {getByText, queryByRole} = render(<MarketPulseCard suburb="Test Suburb"/>);
        expect(getByText('Test Suburb')).toBeInTheDocument();
        expect(queryByRole('button')).not.toBeInTheDocument();
    });

    it('is accessible when onClick is provided', () => {
        const handleClick = jest.fn();
        const {getByRole} = render(
            <MarketPulseCard suburb="Test Suburb" onClick={handleClick}/>
        );

        const card = getByRole('button');
        expect(card).toHaveAttribute('tabIndex', '0');
        expect(card).toHaveAttribute('aria-label', 'View market pulse for Test Suburb');
        expect(card).toHaveClass('cursor-pointer');
        expect(card).toHaveClass('focus-visible:ring-2');

        // Test click
        fireEvent.click(card);
        expect(handleClick).toHaveBeenCalledTimes(1);

        // Test Enter key
        fireEvent.keyDown(card, {key: 'Enter'});
        expect(handleClick).toHaveBeenCalledTimes(2);

        // Test Space key
        fireEvent.keyDown(card, {key: ' '});
        expect(handleClick).toHaveBeenCalledTimes(3);
    });
});
