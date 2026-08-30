// @featuretrace:ui-table-conventions — Reusable click-to-sort column header + sort hook.
// Layer: frontend
// Data flow: column header click -> useTableSort() -> sorted rows -> table body (global).
// Related: frontend/src/pages/dashboard/admin/SecurityIPLogsPage.jsx
//          docs/architecture/ui_table_and_search_conventions.md

import React, {useCallback, useMemo, useState} from 'react';
import {ArrowDown, ArrowUp, ArrowUpDown} from 'lucide-react';

/**
 * Click-to-sort table header cell.
 *
 * Every data table in this app sorts by clicking a column header. Shipping this
 * as a shared primitive rather than per-page logic is deliberate: hand-rolled
 * sorting drifts — one page sorts case-sensitively, another puts nulls first,
 * a third forgets keyboard access — and users then cannot predict what a click
 * will do.
 *
 * Accessibility is part of the contract, not an extra. The cell carries
 * `aria-sort` so a screen reader announces the current order, and it is a real
 * <button> so it is reachable by keyboard. A <th> with an onClick is not.
 *
 * Documented as a single props object, not as five loose @param entries. TypeScript
 * infers this component's signature from the JSDoc when a .tsx page imports it, and the
 * loose form made it read as `SortableTh(label: string)` — so every call site in a typed
 * file failed with "object is not assignable to type 'string'". Fixed here rather than
 * with a co-located .d.ts, which would break Turbopack's resolution of a first-party
 * .jsx file.
 *
 * @param {object} props
 * @param {string} props.label       Visible column title.
 * @param {string} props.field       Key this column sorts by.
 * @param {object} props.sort        `{field, direction}` from useTableSort.
 * @param {function} props.onSort    `toggle` from useTableSort.
 * @param {string} [props.align]     'left' | 'right' | 'center'.
 * @param {string} [props.className]
 */
export function SortableTh({label, field, sort, onSort, align = 'left', className = ''}) {
    const sortable = Boolean(field && onSort);
    const active = sortable && sort?.field === field;
    const direction = active ? sort.direction : null;

    // aria-sort must be one of these exact tokens; 'none' on an inactive
    // sortable column is what tells assistive tech the column CAN be sorted.
    const ariaSort = !sortable ? undefined : active ? (direction === 'asc' ? 'ascending' : 'descending') : 'none';

    const Icon = !active ? ArrowUpDown : direction === 'asc' ? ArrowUp : ArrowDown;
    const justify = align === 'right' ? 'justify-end' : align === 'center' ? 'justify-center' : 'justify-start';

    return (
        <th
            scope="col"
            aria-sort={ariaSort}
            className={`h-12 px-4 align-middle text-xs font-medium text-muted-foreground ${align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left'} ${className}`}
        >
            {sortable ? (
                <button
                    type="button"
                    onClick={() => onSort(field)}
                    data-testid={`sort-${field}`}
                    className={`group inline-flex items-center gap-1 ${justify} rounded px-1 py-0.5 transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2`}
                    title={`Sort by ${label}`}
                >
                    <span>{label}</span>
                    <Icon
                        size={12}
                        className={active ? 'text-primary' : 'text-muted-foreground/50 group-hover:text-muted-foreground'}
                        aria-hidden="true"
                    />
                </button>
            ) : (
                label
            )}
        </th>
    );
}

/** Read `a.b.c` off an object without throwing on a missing level. */
function valueAt(row, path) {
    if (!row || !path) return undefined;
    return path.split('.').reduce((acc, key) => (acc == null ? undefined : acc[key]), row);
}

/**
 * Sorting state plus the sorted rows.
 *
 * @param {Array}  rows            Rows to sort.
 * @param {object} initial         `{field, direction}` starting state.
 * @param {object} accessors       Optional `{field: (row) => comparableValue}` overrides,
 *                                 for columns whose sort key is not a plain path.
 *                                 MUST be a stable reference — a module-level const, or
 *                                 useMemo'd. A fresh object literal on every render is a
 *                                 new dependency every render, so the table re-sorts on
 *                                 every keystroke in an unrelated input: O(n log n) per
 *                                 render for nothing.
 *
 *                                 This used to be absorbed inside the hook with a ref
 *                                 written during render. That is exactly what
 *                                 `react-hooks/refs` forbids, and it was three lint
 *                                 errors in the one primitive every migrated table is
 *                                 required to route through (GAP-UI-002 §3 rule 4).
 *                                 Pushing the stability requirement out to the caller
 *                                 is both lint-clean and honest about who owns it.
 *
 * Null and undefined always sort LAST regardless of direction. Reversing the
 * order should not drag empty cells to the top — "no value" is not a smaller
 * value, and a user reversing a sort wants the other end of the real data.
 */
export function useTableSort(rows, initial = {field: null, direction: 'desc'}, accessors = {}) {
    const [sort, setSort] = useState(initial);

    const toggle = useCallback((field) => {
        setSort((current) => {
            if (current.field !== field) {
                // First click on a new column: descending, because on an audit
                // log the interesting rows are the newest and the largest.
                return {field, direction: 'desc'};
            }
            return {field, direction: current.direction === 'desc' ? 'asc' : 'desc'};
        });
    }, []);

    const sorted = useMemo(() => {
        if (!Array.isArray(rows)) return [];
        if (!sort.field) return rows;

        const get = accessors[sort.field] || ((row) => valueAt(row, sort.field));
        const factor = sort.direction === 'asc' ? 1 : -1;

        return [...rows].sort((a, b) => {
            const av = get(a);
            const bv = get(b);

            const aEmpty = av == null || av === '';
            const bEmpty = bv == null || bv === '';
            if (aEmpty && bEmpty) return 0;
            if (aEmpty) return 1;   // empties last, both directions
            if (bEmpty) return -1;

            if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * factor;
            if (typeof av === 'boolean' && typeof bv === 'boolean') return ((av ? 1 : 0) - (bv ? 1 : 0)) * factor;

            // localeCompare with numeric:true so "Unit 2" sorts before "Unit 10"
            // — the same lexicographic trap the backend has for unit numbers.
            return String(av).localeCompare(String(bv), 'en-AU', {numeric: true, sensitivity: 'base'}) * factor;
        });
    }, [rows, sort, accessors]);

    return {sort, toggle, sorted};
}

export default SortableTh;
