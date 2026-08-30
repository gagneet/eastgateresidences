// @featuretrace:by-law-breach-register — Guards the on-card reasoning for an NA axis.
// Layer: test
// Data flow: health score breakdown -> PulseScoreCard tooltip / detail modal copy (building-scoped).
// Related: frontend/src/components/dashboard/PulseScoreCard.tsx
/**
 * "Disputes: NA" is only defensible if the card says WHY.
 *
 * The axis is unavailable because East Gate's by-law breach register is empty, and an
 * empty register is not a clean one — scoring it 100/100 would award a tenth of the
 * health score for having no evidence. That reasoning lived in a commit message and a
 * ticket; a manager looking at the card could not reach either.
 */
import React from 'react';
import {render, screen} from '@testing-library/react';
import '@testing-library/jest-dom';

import PulseScoreCard, {AXIS_HELP, AXIS_UNAVAILABLE_HELP} from '@/components/dashboard/PulseScoreCard';

const AXES = [
    // 64, not 72: the overall score renders 72 too, and getByText would be ambiguous.
    {k: 'Financial', v: 64, color: '#4F46E5'},
    {k: 'Disputes', v: null, color: '#0EA5E9'},
];

describe('Pulse axis reasoning', () => {
    it('renders an unavailable axis as NA, not as a zero', () => {
        render(<PulseScoreCard score={72} breakdown={AXES as any}/>);
        expect(screen.getByText('NA')).toBeInTheDocument();
    });

    it('explains in the tooltip that an empty register is not a clean one', () => {
        render(<PulseScoreCard score={72} breakdown={AXES as any}/>);
        const na = screen.getByText('NA');
        expect(na.getAttribute('title')).toMatch(/not the same as having no disputes/i);
        expect(na.getAttribute('title')).toMatch(/excluded and its weight redistributed/i);
    });

    it('says a tribunal referral still counts as unresolved', () => {
        // Counting with BreachStatus.OPEN would report a building with five live
        // tribunal cases as having none; the card has to state that it does not.
        expect(AXIS_HELP.Disputes).toMatch(/ACAT or NCAT/);
        expect(AXIS_HELP.Disputes).toMatch(/still counts as unresolved/i);
    });

    it('tells the reader how to start measuring the axis', () => {
        expect(AXIS_UNAVAILABLE_HELP.Disputes).toMatch(/Record a breach to start measuring/i);
        expect(AXIS_UNAVAILABLE_HELP.Disputes).toMatch(/zero unresolved becomes a real 100/i);
    });

    it('warns that scoring an empty register would be unearned', () => {
        expect(AXIS_UNAVAILABLE_HELP.Disputes).toMatch(/tenth of the health score/i);
    });

    it('still shows a measured axis with its score and formula', () => {
        render(<PulseScoreCard score={72} breakdown={AXES as any}/>);
        const measured = screen.getByText('64');
        expect(measured.getAttribute('title')).toMatch(/Financial: 64\/100/);
    });
});
