import {
    BuildingArrearsConfig,
    calculateLevyInterest,
    getArrearsStage,
    getLeviesRequiringEscalation,
    recoverableArrearsCents,
} from '@/lib/trust/arrears';

const CONFIG_A: BuildingArrearsConfig = {
    grace_period_days: 14,
    reminder_days: 14,
    formal_notice_days: 21,
    interest_charge_days: 30,
    legal_flag_days: 60,
    annual_interest_rate: 0.10,
};

const CONFIG_B: BuildingArrearsConfig = {
    grace_period_days: 14,
    reminder_days: 14,
    formal_notice_days: 21,
    interest_charge_days: 30,
    legal_flag_days: 60,
    annual_interest_rate: 0.08,
};

function daysAgo(days: number): Date {
    const d = new Date('2026-03-19');
    d.setDate(d.getDate() - days);
    return d;
}

const TODAY = new Date('2026-03-19');

describe('getArrearsStage', () => {
    it('returns current for paid levy', () => {
        const levy = {status: 'paid', due_date: daysAgo(30), grace_period_days: 14};
        expect(getArrearsStage(levy, CONFIG_A, TODAY)).toBe('current');
    });

    it('returns current for levy within grace period', () => {
        const levy = {status: 'pending', due_date: daysAgo(7), grace_period_days: 14};
        expect(getArrearsStage(levy, CONFIG_A, TODAY)).toBe('current');
    });

    it('returns reminder at grace_period_days + 1', () => {
        const levy = {status: 'overdue', due_date: daysAgo(15), grace_period_days: 14};
        expect(getArrearsStage(levy, CONFIG_A, TODAY)).toBe('reminder');
    });

    it('returns formal_notice at formal_notice_days', () => {
        const levy = {status: 'overdue', due_date: daysAgo(14 + 21), grace_period_days: 14};
        expect(getArrearsStage(levy, CONFIG_A, TODAY)).toBe('formal_notice');
    });

    it('returns interest_charge at interest_charge_days', () => {
        const levy = {status: 'overdue', due_date: daysAgo(14 + 30), grace_period_days: 14};
        expect(getArrearsStage(levy, CONFIG_A, TODAY)).toBe('interest_charge');
    });

    it('returns legal at legal_flag_days', () => {
        const levy = {status: 'overdue', due_date: daysAgo(14 + 60), grace_period_days: 14};
        expect(getArrearsStage(levy, CONFIG_A, TODAY)).toBe('legal');
    });
});

describe('calculateLevyInterest', () => {
    it('Building A (10%): 30 days on $1000 → 822 cents', () => {
        const overdueSince = new Date('2026-01-01');
        const asAt = new Date('2026-01-31');
        const levy = {status: 'overdue', outstanding_cents: 100000, overdue_since: overdueSince};
        expect(calculateLevyInterest(levy, CONFIG_A, asAt)).toBe(822);
    });

    it('Building B (8%): 30 days on $1000 → 658 cents', () => {
        const overdueSince = new Date('2026-01-01');
        const asAt = new Date('2026-01-31');
        const levy = {status: 'overdue', outstanding_cents: 100000, overdue_since: overdueSince};
        expect(calculateLevyInterest(levy, CONFIG_B, asAt)).toBe(658);
    });

    it('Same outstanding, same days, different rate → different result', () => {
        const overdueSince = new Date('2026-01-01');
        const asAt = new Date('2026-01-31');
        const levy = {status: 'overdue', outstanding_cents: 100000, overdue_since: overdueSince};
        const interestA = calculateLevyInterest(levy, CONFIG_A, asAt);
        const interestB = calculateLevyInterest(levy, CONFIG_B, asAt);
        expect(interestA).not.toBe(interestB);
    });

    it('returns 0 for paid levy', () => {
        const levy = {status: 'paid', outstanding_cents: 0, overdue_since: new Date()};
        expect(calculateLevyInterest(levy, CONFIG_A)).toBe(0);
    });

    it('uses recoverable opening-debt arrears when the schedule carries backend arrears-board fields', () => {
        const overdueSince = new Date('2026-01-01');
        const asAt = new Date('2026-01-31');
        const levy = {
            status: 'overdue',
            outstanding_cents: 100000,
            opening_debt_cents: 100000,
            total_confirmed_paid_cents: 20000,
            overdue_since: overdueSince,
        };

        // Interest base is recoverable arrears: 100000 - 20000 = 80000 cents.
        expect(calculateLevyInterest(levy, CONFIG_A, asAt)).toBe(658);
    });

    it('does not switch to recoverable arrears without an opening-debt source', () => {
        const overdueSince = new Date('2026-01-01');
        const asAt = new Date('2026-01-31');
        const levy = {
            status: 'overdue',
            outstanding_cents: 100000,
            total_confirmed_paid_cents: 20000,
            overdue_since: overdueSince,
        };

        expect(calculateLevyInterest(levy, CONFIG_A, asAt)).toBe(822);
    });
});

describe('recoverableArrearsCents', () => {
    it('matches the backend recoverable_arrears shape', () => {
        expect(recoverableArrearsCents(100000, 20000)).toBe(80000);
    });

    it('clamps overpaid carry-forward arrears to zero', () => {
        expect(recoverableArrearsCents(100000, 120000)).toBe(0);
    });
});

describe('getLeviesRequiringEscalation', () => {
    it('returns empty for all-paid building', () => {
        const schedules = [
            {_id: '1', status: 'paid', due_date: daysAgo(30), grace_period_days: 14},
        ];
        expect(getLeviesRequiringEscalation(schedules, [], CONFIG_A, TODAY)).toHaveLength(0);
    });

    it('sets shouldTransitionToDrb = true at formal_notice', () => {
        const schedules = [{
            _id: '1',
            status: 'overdue',
            due_date: daysAgo(14 + 25), // formal_notice stage
            grace_period_days: 14,
            outstanding_cents: 100000,
            overdue_since: daysAgo(25),
        }];
        const actions = getLeviesRequiringEscalation(schedules, [], CONFIG_A, TODAY);
        expect(actions.length).toBeGreaterThan(0);
        expect(actions[0].shouldTransitionToDrb).toBe(true);
    });

    it('already-notified levy at same stage is NOT returned', () => {
        const schedules = [{
            _id: '1',
            status: 'overdue',
            due_date: daysAgo(14 + 15), // reminder stage
            grace_period_days: 14,
            outstanding_cents: 100000,
        }];
        const auditHistory = [{
            action: 'arrears_escalated',
            entity_id: '1',
            metadata: {stage: 'reminder' as const},
            timestamp: new Date(),
        }];
        const actions = getLeviesRequiringEscalation(schedules, auditHistory, CONFIG_A, TODAY);
        expect(actions).toHaveLength(0);
    });

    it('asAt parameter works with synthetic dates', () => {
        const syntheticToday = new Date('2026-03-19');
        const schedules = [{
            _id: '1',
            status: 'overdue',
            due_date: new Date('2026-01-01'), // far in the past
            grace_period_days: 14,
            outstanding_cents: 100000,
            overdue_since: new Date('2026-01-15'),
        }];
        const actions = getLeviesRequiringEscalation(schedules, [], CONFIG_A, syntheticToday);
        expect(actions.length).toBeGreaterThan(0);
    });

    it('uses recoverable opening debt for escalation total owing when supplied', () => {
        const schedules = [{
            _id: '1',
            status: 'overdue',
            due_date: new Date('2026-01-01'),
            grace_period_days: 14,
            outstanding_cents: 100000,
            opening_debt_cents: 100000,
            total_confirmed_paid_cents: 20000,
            overdue_since: new Date('2026-01-15'),
        }];

        const actions = getLeviesRequiringEscalation(schedules, [], CONFIG_A, new Date('2026-01-31'));
        expect(actions[0].totalOwingCents).toBe(actions[0].interestCents + 80000);
    });
});
