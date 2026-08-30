
/**
 * DEFT CUSTOMER REFERENCE NUMBER (CRN) UTILITIES
 *
 * DEFT is the payment platform used for strata levy collection via BPAY.
 * The check digit uses the Luhn modulo-10 algorithm (BPAY/DEFT standard).
 */

/**
 * Compute Luhn modulo-10 check digit for a string of digits.
 * 1. From right to left, double every second digit; if doubled > 9, subtract 9
 * 2. Sum all digits
 * 3. Check digit = (10 - (sum % 10)) % 10
 */
function luhnCheckDigit(digits: string): number {
    const nums = digits.split('').reverse().map(Number);
    const sum = nums.reduce((acc, digit, idx) => {
        if (idx % 2 === 1) {
            const doubled = digit * 2;
            return acc + (doubled > 9 ? doubled - 9 : doubled);
        }
        return acc + digit;
    }, 0);
    return (10 - (sum % 10)) % 10;
}
/**
 * Generate a DEFT CRN for one unit in one quarter.
 * Format: {billerCode}-{lotPadded4}-{quarterCode}-{checkDigit}
 * Example: "452301-0018-2026Q1-7"
 */
export function generateDeftCrn(
    lotNumber: number,
    quarter: string,        // "2026-Q1" format
    billerCode: string,     // building.trust_config.deft_biller_code_admin
): string {
    const lotPadded = String(lotNumber).padStart(4, '0');
    // Normalize quarter: "2026-Q1" → "2026Q1"
    const quarterCode = quarter.replace('-', '');

    // Build the base string (stripped of dashes for Luhn calculation)
    const baseForLuhn = (billerCode + lotPadded + quarterCode).replace(/[^0-9]/g, '');
    const checkDigit = luhnCheckDigit(baseForLuhn);

    return `${billerCode}-${lotPadded}-${quarterCode}-${checkDigit}`;
}
/**
 * Validate a CRN using Luhn modulo-10 check digit algorithm.
 */
export function validateDeftCrn(crn: string): boolean {
    const parsed = parseDeftCrn(crn);
    if (!parsed) return false;

    const {billerCode, lotNumber, quarter, checkDigit} = parsed;
    const lotPadded = String(lotNumber).padStart(4, '0');
    const quarterCode = quarter.replace('-', '');

    const baseForLuhn = (billerCode + lotPadded + quarterCode).replace(/[^0-9]/g, '');
    const expectedCheckDigit = luhnCheckDigit(baseForLuhn);

    return checkDigit === expectedCheckDigit;
}
/**
 * Parse a CRN string into its components. Returns null if invalid format.
 * Expected format: {billerCode}-{lotPadded4}-{quarterCode}-{checkDigit}
 */
export function parseDeftCrn(crn: string): {
    billerCode: string;
    lotNumber: number;
    quarter: string;
    checkDigit: number;
} | null {
    if (!crn) return null;

    // Split from right: last segment is check digit
    const lastDash = crn.lastIndexOf('-');
    if (lastDash === -1) return null;

    const checkDigitStr = crn.slice(lastDash + 1);
    const checkDigit = parseInt(checkDigitStr, 10);
    if (isNaN(checkDigit)) return null;

    const rest = crn.slice(0, lastDash);

    // Next-to-last segment is quarter code
    const secondLastDash = rest.lastIndexOf('-');
    if (secondLastDash === -1) return null;

    const quarterCode = rest.slice(secondLastDash + 1);
    // Normalize: "2026Q1" → "2026-Q1"
    const quarter = quarterCode.replace(/(\d{4})(Q\d+)/, '$1-$2');

    const rest2 = rest.slice(0, secondLastDash);

    // Next segment is lot number (4 digits)
    const thirdLastDash = rest2.lastIndexOf('-');
    if (thirdLastDash === -1) return null;

    const lotStr = rest2.slice(thirdLastDash + 1);
    const lotNumber = parseInt(lotStr, 10);
    if (isNaN(lotNumber)) return null;

    const billerCode = rest2.slice(0, thirdLastDash);
    if (!billerCode) return null;

    return {billerCode, lotNumber, quarter, checkDigit};
}
/**
 * Extract just the lot number from a CRN.
 */
export function extractLotFromCrn(crn: string): number | null {
    const parsed = parseDeftCrn(crn);
    return parsed ? parsed.lotNumber : null;
}
/**
 * Extract just the quarter code from a CRN.
 */
export function extractQuarterFromCrn(crn: string): string | null {
    const parsed = parseDeftCrn(crn);
    return parsed ? parsed.quarter : null;
}
/**
 * Generate all CRNs for an entire building in one quarter.
 * Returns a Map<lotNumber, crn> for efficient lookup.
 */
export function generateBuildingCrns(
    units: Array<{ lot_number: number }>,
    quarter: string,
    buildingBillerCode: string,
): Map<number, string> {
    const map = new Map<number, string>();
    for (const unit of units) {
        const crn = generateDeftCrn(unit.lot_number, quarter, buildingBillerCode);
        map.set(unit.lot_number, crn);
    }
    return map;
}
