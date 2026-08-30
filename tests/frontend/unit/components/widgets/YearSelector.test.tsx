import React from 'react';
import {fireEvent, render, screen} from '@testing-library/react';
import '@testing-library/jest-dom';
import YearSelector from '@/components/widgets/YearSelector';

const setSelectedYear = jest.fn();

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: () => ({
        selectedYear: '2026',
        setSelectedYear,
        availableYears: ['2026', '2025'],
        financialYearStartMonth: 7,
    }),
}));

describe('YearSelector', () => {
    beforeEach(() => {
        setSelectedYear.mockClear();
    });

    it('keeps Levy Year as the external label and shows only year values in the dropdown', () => {
        render(<YearSelector/>);

        expect(screen.getByText('Levy Year:')).toBeInTheDocument();
        fireEvent.click(screen.getByRole('combobox'));

        expect(screen.getByRole('option', {name: '2026-27'})).toBeInTheDocument();
        expect(screen.getByRole('option', {name: '2025-26'})).toBeInTheDocument();
        expect(screen.queryByRole('option', {name: /Levy Year/})).not.toBeInTheDocument();
    });
});
