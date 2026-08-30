import React from 'react';
import {render, screen} from '@testing-library/react';
import '@testing-library/jest-dom';

import PaymentStreakCard from '@/components/dashboard/PaymentStreakCard';

describe('PaymentStreakCard', () => {
    it('does not calculate a rank percentage without an explicit total lot count', () => {
        render(
            <PaymentStreakCard
                data={{streak: 4, on_time_pct: 82}}
                rank={3}
            />
        );

        expect(screen.getByText('82% on-time overall')).toBeInTheDocument();
        expect(screen.queryByText(/top \d+% of owners/i)).not.toBeInTheDocument();
    });

    it('shows a rank percentage when a lot count is provided', () => {
        render(
            <PaymentStreakCard
                data={{streak: 4, on_time_pct: 82}}
                rank={3}
                totalLots={10}
            />
        );

        expect(screen.getByText(/top 30% of owners/i)).toBeInTheDocument();
    });

    it('derives the oldest-quarter label from real data instead of a hardcoded "3.5 yrs ago"', () => {
        // Regression test: this label used to be a static "3.5 yrs ago" string regardless of
        // what data was passed in — wrong for any unit whose real history isn't exactly that
        // long (e.g. a unit owned for under a year, or one with 5+ years of history).
        const recent_quarters = [
            {on_time: true, year: '2026', quarter: '2'},
            {on_time: true, year: '2026', quarter: '1'},
            {on_time: true, year: '2025', quarter: '4'},
        ];
        render(
            <PaymentStreakCard data={{streak: 3, on_time_pct: 100, recent_quarters}} />
        );

        expect(screen.getByText('Q4 2025')).toBeInTheDocument();
        expect(screen.queryByText('3.5 yrs ago')).not.toBeInTheDocument();
    });

    it('shows nothing for the oldest-quarter label when there is no real quarter data', () => {
        render(<PaymentStreakCard data={{streak: 0, on_time_pct: 0}} />);

        expect(screen.queryByText('3.5 yrs ago')).not.toBeInTheDocument();
    });
});
