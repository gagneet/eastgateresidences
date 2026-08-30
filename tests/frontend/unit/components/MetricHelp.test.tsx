import React from 'react';
import {render} from '@testing-library/react';
import '@testing-library/jest-dom';
import {MetricHelp} from '@/components/shared/MetricHelp';
import {TooltipProvider} from '@/components/ui/tooltip';

describe('MetricHelp', () => {
    it('renders with correct accessibility attributes', () => {
        const {getByRole} = render(
            <TooltipProvider>
                <MetricHelp text="Test help text"/>
            </TooltipProvider>
        );

        const trigger = getByRole('button');
        expect(trigger).toHaveAttribute('tabIndex', '0');
        expect(trigger).toHaveAttribute('aria-label', 'Help information');
        expect(trigger).toHaveClass('focus-visible:ring-2');
    });
});
