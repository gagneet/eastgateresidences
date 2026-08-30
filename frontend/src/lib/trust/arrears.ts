/**
 * ARREARS ESCALATION LOGIC
 *
 * All thresholds and rates come from BuildingArrearsConfig.
 * NEVER read from global env or hardcoded constants.
 */
import {interestAccrued} from './money';

export interface BuildingArrearsConfig {
    grace_period_days: number;
    reminder_days: number;
    formal_notice_days: number;
    interest_charge_days: number;
    legal_flag_days: number;
    annual_interest_rate: number;
}

export type ArrearsStage =
    | 'current'
    | 'reminder'
    | 'formal_notice'
    | 'interest_charge'
    | 'legal';

export interface LevyForArrears {
    status: string;
    due_date: Date | string;
    grace_period_days: number;
    outstanding_cents?: number;
    opening_debt_cents?: number;
    total_confirmed_paid_cents?: number;
    overdue_since?: Date | string | null;
    drb_stage?: string;
}

export interface AuditForArrears {
    action: string;
    entity_id: string;
    metadata?: Record<string, unknown> | null;
    timestamp: Date | string;
}

export interface EscalationAction {
    schedule: LevyForArrears & { _id?: string; unit_number?: string };
    stage: ArrearsStage;
    interestCents: number;
    totalOwingCents: number;
    shouldSendEmail: boolean;
    shouldGeneratePdf: boolean;
    shouldPostInterestTransaction: boolean;
    shouldTransitionToDrb: boolean;
    shouldFlagLegal: boolean;
    previousStage: ArrearsStage | null;
}
/**
 * Get days since a date.
 */
function daysSince(date: Date | string, asAt: Date): number {
    const d = typeof date === 'string' ? new Date(date) : date;
    const diffMs = asAt.getTime() - d.getTime();
    return diffMs / (1000 * 60 * 60 * 24);
}

/**
 * Mirrors backend/domain/finance/formulas/arrears.py::recoverable_arrears().
 * This is prior-year carry-forward debt only; do not use it for quarter-scoped
 * current levy arrears.
 */
export function recoverableArrearsCents(
    openingDebtCents: number | null | undefined,
    totalConfirmedPaidCents: number | null | undefined,
): number {
    return Math.max(0, Math.round((openingDebtCents ?? 0) - (totalConfirmedPaidCents ?? 0)));
}

function arrearsBaseCents(levy: Pick<LevyForArrears, 'outstanding_cents' | 'opening_debt_cents' | 'total_confirmed_paid_cents'>): number {
    // Only use the arrears-board formula when opening debt is present. A caller
    // may carry payment metadata without an opening-debt source; in that case
    // the legacy outstanding amount remains the honest base.
    if (levy.opening_debt_cents != null) {
        return recoverableArrearsCents(levy.opening_debt_cents, levy.total_confirmed_paid_cents);
    }
    return Math.max(0, Math.round(levy.outstanding_cents ?? 0));
}
/**
 * Determine what stage an overdue levy is at.
 */
export function getArrearsStage(
    levy: Pick<LevyForArrears, 'status' | 'due_date' | 'grace_period_days'>,
    config: BuildingArrearsConfig,
    asAt?: Date,
): ArrearsStage {
    const now = asAt ?? new Date();

    // Paid or waived levies are not in arrears
    if (levy.status === 'paid' || levy.status === 'waived') {
        return 'current';
    }

    const dueDate = typeof levy.due_date === 'string' ? new Date(levy.due_date) : levy.due_date;
    const daysOverdue = daysSince(dueDate, now) - levy.grace_period_days;

    if (daysOverdue <= 0) {
        return 'current';
    }

    if (daysOverdue >= config.legal_flag_days) {
        return 'legal';
    }
    if (daysOverdue >= config.interest_charge_days) {
        return 'interest_charge';
    }
    if (daysOverdue >= config.formal_notice_days) {
        return 'formal_notice';
    }
    if (daysOverdue >= config.reminder_days) {
        return 'reminder';
    }

    // Past grace period but before reminder_days threshold.
    // This occurs when daysOverdue is positive but less than reminder_days.
    // Any positive daysOverdue (after grace) is treated as the earliest overdue state ('reminder').
    return 'reminder';
}
/**
 * Calculate interest accrued on a levy using that building's configured rate.
 */
export function calculateLevyInterest(
    levy: Pick<LevyForArrears, 'outstanding_cents' | 'opening_debt_cents' | 'total_confirmed_paid_cents' | 'overdue_since' | 'status'>,
    config: BuildingArrearsConfig,
    asAt?: Date,
): number {
    if (levy.status === 'paid' || levy.status === 'waived') return 0;
    if (!levy.overdue_since) return 0;
    const baseCents = arrearsBaseCents(levy);
    if (!baseCents) return 0;

    const overdueSince = typeof levy.overdue_since === 'string'
        ? new Date(levy.overdue_since)
        : levy.overdue_since;

    return interestAccrued(baseCents, config.annual_interest_rate, overdueSince, asAt);
}
/**
 * Determine what escalation actions are required for a list of levy schedules.
 * Respects idempotency — already-notified levies at the same stage are excluded.
 */
export function getLeviesRequiringEscalation(
    schedules: (LevyForArrears & { _id?: string; unit_number?: string })[],
    auditHistory: AuditForArrears[],
    config: BuildingArrearsConfig,
    asAt?: Date,
): EscalationAction[] {
    const results: EscalationAction[] = [];

    // Build a map of last escalated stage per schedule
    const lastEscalated = new Map<string, ArrearsStage>();
    for (const audit of auditHistory) {
        if (audit.action === 'arrears_escalated' && audit.entity_id) {
            const stage = (audit.metadata?.stage as ArrearsStage) ?? null;
            if (stage) {
                lastEscalated.set(audit.entity_id, stage);
            }
        }
    }

    for (const schedule of schedules) {
        if (schedule.status === 'paid' || schedule.status === 'waived') continue;

        const stage = getArrearsStage(schedule, config, asAt);
        if (stage === 'current') continue;

        const scheduleId = schedule._id ?? '';
        const previousStage = lastEscalated.get(scheduleId) ?? null;

        // Skip if already at this stage (idempotency)
        if (previousStage === stage) continue;

        const interestCents = calculateLevyInterest(schedule, config, asAt);
        const outstandingCents = arrearsBaseCents(schedule);
        const totalOwingCents = outstandingCents + interestCents;

        results.push({
            schedule,
            stage,
            interestCents,
            totalOwingCents,
            shouldSendEmail: stage === 'reminder' || stage === 'formal_notice',
            shouldGeneratePdf: stage === 'formal_notice',
            shouldPostInterestTransaction: stage === 'interest_charge' || stage === 'legal',
            shouldTransitionToDrb: stage === 'formal_notice',
            shouldFlagLegal: stage === 'legal',
            previousStage,
        });
    }

    return results;
}
