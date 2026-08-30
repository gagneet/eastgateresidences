import React from 'react';
import {fireEvent, render, screen, waitFor} from '@testing-library/react';
import '@testing-library/jest-dom';
import LevyKpiDialog from '@/components/finance/LevyKpiDialog';
import {useAuth} from '@/contexts/AuthContext';

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: jest.fn(),
}));

jest.mock('next/navigation', () => ({
    useRouter: () => ({
        push: jest.fn(),
        back: jest.fn(),
    }),
}));

jest.mock('recharts', () => ({
    Cell: () => <div />,
    Pie: () => <div />,
    PieChart: ({children}: any) => <div>{children}</div>,
    ResponsiveContainer: ({children}: any) => <div>{children}</div>,
    Tooltip: () => <div />,
    Bar: () => <div />,
    BarChart: ({children}: any) => <div>{children}</div>,
    XAxis: () => <div />,
    YAxis: () => <div />,
    CartesianGrid: () => <div />,
}));

jest.mock('@/components/ui/dialog', () => ({
    Dialog: ({children}: any) => <div>{children}</div>,
    DialogContent: ({children}: any) => <div>{children}</div>,
    DialogHeader: ({children}: any) => <div>{children}</div>,
    DialogTitle: ({children}: any) => <div>{children}</div>,
    DialogDescription: ({children}: any) => <div>{children}</div>,
}));

jest.mock('@/components/ui/button', () => ({
    Button: ({children, onClick}: any) => <button onClick={onClick}>{children}</button>,
}));

jest.mock('@/components/ui/badge', () => ({
    Badge: ({children}: any) => <span>{children}</span>,
}));

jest.mock('@/components/ui/table', () => ({
    Table: ({children}: any) => <table>{children}</table>,
    TableBody: ({children}: any) => <tbody>{children}</tbody>,
    TableCell: ({children}: any) => <td>{children}</td>,
    TableHead: ({children}: any) => <th>{children}</th>,
    TableHeader: ({children}: any) => <thead>{children}</thead>,
    TableRow: ({children}: any) => <tr>{children}</tr>,
}));

// TooltipContent is a <span>, not a <div>, on purpose.
//
// The real component wraps its content in TooltipPrimitive.Portal, so it renders at
// the document root and can legally hold block content. A mock that drops it inline
// as a <div> puts a div inside StatTile's <p> label and logs
// "<p> cannot contain a nested <div>" on every render — a warning about the MOCK
// that reads exactly like a warning about the component. A span keeps the mock
// legal and keeps the tip text assertable.
jest.mock('@/components/ui/tooltip', () => ({
    Tooltip: ({children}: any) => <>{children}</>,
    TooltipTrigger: ({children}: any) => <>{children}</>,
    TooltipContent: ({children}: any) => <span>{children}</span>,
}));

jest.mock('@/components/widgets/YearSelector', () => () => <div data-testid="year-selector" />);

const mockUseAuth = useAuth as jest.Mock;

describe('LevyKpiDialog', () => {
    const apiGet = jest.fn();

    beforeEach(() => {
        jest.clearAllMocks();
        mockUseAuth.mockReturnValue({
            api: {get: apiGet},
        });
        apiGet.mockResolvedValue({
            data: {
                collection_rate: 0.88,
                lot_compliance_rate: 0.75,
                admin_fund_health: 1.1,
                admin_fund_health_incl_receivables: 1.2,
                sinking_cash_coverage: 1.3,
                overall_liquidity_cash_only: 1.4,
                overall_liquidity_incl_receivables: 1.5,
                compliant_lot_count: 1,
                non_compliant_lot_count: 1,
                quarter_billed_total_display: 1000,
                quarter_billed_total_lot_sum: 1000,
                current_quarter_collected_total: 800,
                current_quarter_unpaid_total: 200,
                true_arrears_total: 50,
                credit_total: 20,
                total_lot_count: 2,
                prior_period_arrears_rate: 0.12,
                top_true_arrears: [],
                admin_fund_balance: 1000,
                admin_quarter_budget: 900,
                admin_annual_gross: 3000,
                admin_opening_balance: 100,
                admin_live_balance: 1100,
                admin_ratio: 0.6,
                sinking_fund_balance: 2000,
                sinking_quarter_budget: 1500,
                sinking_annual_gross: 2000,
                sinking_opening_balance: 200,
                sinking_live_balance: 2100,
                sinking_percent_funded: 0.66,
                ytd_admin_paid: 200,
                ytd_admin_expenses: 50,
                strata_mgmt_admin_balance: 1250,
                ytd_sinking_paid: 300,
                ytd_sinking_expenses: 80,
                strata_mgmt_sinking_balance: 2280,
                strata_mgmt_total_balance: 3530,
                total_annual_gross: 5000,
                total_cash_balance: 3000,
                total_live_balance: 3100,
                net_arrears_outstanding: 30,
                can_view_top_true_arrears: false,
            },
        });
    });

    it('loads KPI data for the selected year and renders the dialog title', async () => {
        render(
            <LevyKpiDialog
                open={true}
                onOpenChange={jest.fn()}
                selectedYear="2026"
            />
        );

        await waitFor(() => {
            expect(apiGet).toHaveBeenCalledWith('/finance/levy-kpi?year=2026');
        });

        expect(await screen.findByText('Levy KPI Dashboard')).toBeInTheDocument();
        expect(screen.getByText('Collection Rate')).toBeInTheDocument();

        fireEvent.click(screen.getByRole('button', {name: /Fund Health/i}));
        await waitFor(() => {
            expect(screen.getByText('Admin Fund Health')).toBeInTheDocument();
        });
    });

    // The seven KPI figures moved from hand-rolled divs onto the shared StatTile,
    // which puts the VALUE and the LABEL in different elements. The test above only
    // ever asserted the labels, so it would have passed just as happily with every
    // figure wired to the wrong field, or to nothing. These pin the values.
    it('renders each collection figure from the API response', async () => {
        render(<LevyKpiDialog open={true} onOpenChange={jest.fn()} selectedYear="2026"/>);

        expect(await screen.findByText('88%')).toBeInTheDocument();           // collection_rate 0.88
        expect(screen.getByText('75%')).toBeInTheDocument();                  // lot_compliance_rate 0.75
        expect(screen.getByText('$50.00')).toBeInTheDocument();               // true_arrears_total
        expect(screen.getByText('$20.00')).toBeInTheDocument();               // credit_total
        expect(screen.getByText(/\$800\.00 of \$1,000\.00/)).toBeInTheDocument();
        expect(screen.getByText(/1 of 2 lots paid or in credit/)).toBeInTheDocument();
    });

    it('renders each fund-health figure and its band label', async () => {
        render(<LevyKpiDialog open={true} onOpenChange={jest.fn()} selectedYear="2026"/>);
        await screen.findByText('Levy KPI Dashboard');
        fireEvent.click(screen.getByRole('button', {name: /Fund Health/i}));

        expect(await screen.findByText('110%')).toBeInTheDocument();          // admin_fund_health 1.1
        expect(screen.getByText('130%')).toBeInTheDocument();                 // sinking_cash_coverage 1.3
        expect(screen.getByText('140%')).toBeInTheDocument();                 // overall_liquidity 1.4
        // Band labels come from the tone helpers. Colour is never the only signal,
        // so the word has to be on screen: admin 110% -> Healthy, sinking 130% ->
        // Adequate (>=100 but <200).
        expect(screen.getByText('Healthy')).toBeInTheDocument();
        expect(screen.getByText('Adequate')).toBeInTheDocument();
    });

    it('shows a reason, not a number, when a rate the API did not send is missing', async () => {
        apiGet.mockResolvedValue({
            data: {
                collection_rate: 0.88,
                lot_compliance_rate: 0.75,
                sinking_cash_coverage: 1.3,
                admin_fund_health: 1.1,
                compliant_lot_count: 1,
                non_compliant_lot_count: 1,
                total_lot_count: 2,
                quarter_billed_total_display: 1000,
                quarter_billed_total_lot_sum: 1000,
                current_quarter_collected_total: 800,
                true_arrears_total: 50,
                credit_total: 20,
                top_true_arrears: [],
                can_view_top_true_arrears: false,
                // prior_period_arrears_rate deliberately absent
            },
        });
        render(<LevyKpiDialog open={true} onOpenChange={jest.fn()} selectedYear="2026"/>);

        // Missing is not zero. Without the guard this rendered "NaN% of quarter billed".
        expect(await screen.findByText(/Share of quarter billed — not available/)).toBeInTheDocument();
        expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
    });
});
