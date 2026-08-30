
/**
 * @generated FunctionHeader
 * Function: parseMoneyValue
 * Path: frontend/src/lib/moneyNormalization.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const parseMoneyValue = (value: unknown): number | null => {
    if (value === null || value === undefined) return null;
    const raw = typeof value === "string"
        ? value.split(String.fromCharCode(36)).join("").split(",").join("")
        : value;
    const n = Number(raw);
    return Number.isFinite(n) ? n : null;
};
/**
 * @generated FunctionHeader
 * Function: firstMoneyValue
 * Path: frontend/src/lib/moneyNormalization.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const firstMoneyValue = (...values: unknown[]): number | null => {
    for (const value of values) {
        const parsed = parseMoneyValue(value);
        if (parsed !== null) return parsed;
    }
    return null;
};
/**
 * @generated FunctionHeader
 * Function: fundDisplayBalance
 * Path: frontend/src/lib/moneyNormalization.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
const fundDisplayBalance = (fund: Record<string, unknown> | null | undefined): number => {
    if (!fund) return 0;
    const dollarValue = firstMoneyValue(
        fund.current_balance,
        fund.balance,
        fund.live_balance,
        fund.last_reconciled_balance,
        fund.closing_balance_actual,
        fund.closing_balance,
        fund.total_paid,
    );
    if (dollarValue !== null) return dollarValue;

    const centsValue = firstMoneyValue(
        fund.current_balance_cents,
        fund.balance_cents,
        fund.live_balance_cents,
        fund.last_reconciled_balance_cents,
        fund.closing_balance_actual_cents,
        fund.closing_balance_cents,
        fund.total_paid_cents,
    );
    return centsValue !== null ? centsValue / 100 : 0;
};

export {parseMoneyValue, firstMoneyValue, fundDisplayBalance};
