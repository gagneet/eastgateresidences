import * as React from "react"

import {cn} from "@/lib/utils"

// ─── Responsive default padding ──────────────────────────────────────────────
//
// These primitives default to smaller padding on narrow screens and larger from
// `sm` up. Writing that as `p-4 sm:p-6` looks harmless and is not.
//
// `cn()` is tailwind-merge, and tailwind-merge resolves conflicts only WITHIN a
// variant group. A caller's unprefixed `p-8` therefore does not displace this
// component's own `sm:p-6`: both classes survive, and from 640px up the media
// query wins, so the caller silently gets 1.5rem instead of the 2rem they asked
// for. `CardContent`/`CardFooter` additionally carried `sm:pt-0` — there so
// content sits flush under a `CardHeader` — which survived a caller's `p-6` or
// `py-12` the same way and stripped their top padding entirely at desktop widths.
//
// That was live and app-wide. Two reported instances:
//   /select-building        <CardContent className="p-6">   -> padding-top 0
//   /governance/proposals   <CardContent className="py-12"> -> padding-top 0,
//                           so the empty-state icon touched the card's top edge.
// Measured across the app before this fix: 640 of 818
// CardContent/CardHeader/CardFooter usages rendered padding other than what their
// author wrote.
//
// The fix emits the `sm:` half of the default PER EDGE, and only for edges the
// caller has not set. An unprefixed caller utility then wins at every breakpoint
// (tailwind-merge already handles the unprefixed half correctly), while a
// component that passes no padding keeps exactly the spacing it had before.
//
// Callers' own responsive utilities (`sm:p-8`, `lg:px-2`) are left untouched —
// tailwind-merge resolves those correctly already, because they share a variant
// group with the default.
const PADDING_EDGES = ["t", "r", "b", "l"] as const;

type PaddingEdge = (typeof PADDING_EDGES)[number];

// Unprefixed utilities that control each edge. `p-` and the axis utilities count:
// `py-12` sets top and bottom, so it must suppress `sm:pt-0` and `sm:pb-6`.
const EDGE_PATTERNS: Record<PaddingEdge, RegExp> = {
    t: /^(?:p|py|pt)-[^\s]+$/,
    r: /^(?:p|px|pr)-[^\s]+$/,
    b: /^(?:p|py|pb)-[^\s]+$/,
    l: /^(?:p|px|pl)-[^\s]+$/,
};

/**
 * The `sm:` padding classes this component should still emit.
 *
 * Any edge the caller sets with an unprefixed utility is dropped so the caller's
 * value applies at every breakpoint instead of being silently overridden from
 * `sm` up.
 */
function responsivePadding(
    className: string | undefined,
    smValues: Record<PaddingEdge, string>,
): string {
    if (!className) return PADDING_EDGES.map((edge) => smValues[edge]).join(" ");
    const tokens = className.split(/\s+/).filter(Boolean);
    return PADDING_EDGES
        .filter((edge) => !tokens.some((token) => EDGE_PATTERNS[edge].test(token)))
        .map((edge) => smValues[edge])
        .join(" ");
}

// `sm:p-6` with a flush top — the `sm` half of CardContent and CardFooter.
// Written as literal class strings so Tailwind's scanner still generates them.
const SM_PADDING_FLUSH_TOP: Record<PaddingEdge, string> = {
    t: "sm:pt-0",
    r: "sm:pr-6",
    b: "sm:pb-6",
    l: "sm:pl-6",
};

// `sm:p-6` on every edge — the `sm` half of CardHeader.
const SM_PADDING_ALL: Record<PaddingEdge, string> = {
    t: "sm:pt-6",
    r: "sm:pr-6",
    b: "sm:pb-6",
    l: "sm:pl-6",
};

const Card = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({className, ...props}, ref) => (
    <div
        ref={ref}
        className={cn(
            "min-w-0 overflow-hidden rounded-xl border bg-card text-card-foreground shadow",
            className
        )}
        {...props}
    />
))
Card.displayName = "Card"

const CardHeader = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({className, ...props}, ref) => (
    <div
        ref={ref}
        className={cn(
            "flex min-w-0 flex-col space-y-1.5 p-4",
            responsivePadding(className, SM_PADDING_ALL),
            className,
        )}
        {...props}
    />
))
CardHeader.displayName = "CardHeader"

const CardTitle = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({className, ...props}, ref) => (
    <div
        ref={ref}
        className={cn("min-w-0 break-words font-semibold leading-tight tracking-tight", className)}
        {...props}
    />
))
CardTitle.displayName = "CardTitle"

const CardDescription = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({className, ...props}, ref) => (
    <div
        ref={ref}
        className={cn("min-w-0 break-words text-sm text-muted-foreground", className)}
        {...props}
    />
))
CardDescription.displayName = "CardDescription"

const CardContent = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({className, ...props}, ref) => (
    <div
        ref={ref}
        className={cn(
            "min-w-0 p-4 pt-0",
            responsivePadding(className, SM_PADDING_FLUSH_TOP),
            className,
        )}
        {...props}
    />
))
CardContent.displayName = "CardContent"

const CardFooter = React.forwardRef<
    HTMLDivElement,
    React.HTMLAttributes<HTMLDivElement>
>(({className, ...props}, ref) => (
    <div
        ref={ref}
        className={cn(
            "flex min-w-0 flex-wrap items-center gap-2 p-4 pt-0",
            responsivePadding(className, SM_PADDING_FLUSH_TOP),
            className,
        )}
        {...props}
    />
))
CardFooter.displayName = "CardFooter"

export {Card, CardHeader, CardFooter, CardTitle, CardDescription, CardContent}
