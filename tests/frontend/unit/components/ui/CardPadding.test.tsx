/**
 * Card padding primitive — regression coverage.
 *
 * `cn()` is tailwind-merge, which resolves conflicts only WITHIN a variant group.
 * The Card primitives' `sm:p-6` / `sm:pt-0` defaults therefore survived a caller's
 * unprefixed padding and won at >=640px, so callers silently rendered padding they
 * had not asked for. Two reported instances:
 *
 *   /select-building        <CardContent className="p-6">   -> padding-top 0
 *   /governance/proposals   <CardContent className="py-12"> -> padding-top 0, so
 *                           the empty-state icon touched the card's top edge.
 *
 * Measured before the fix: 640 of 818 Card* usages rendered padding other than
 * what their author wrote. These tests assert on the RESOLVED class list, which is
 * what the bug was actually about — jsdom does not evaluate media queries, so
 * asserting computed style here would prove nothing.
 */
import React from 'react';
import {render} from '@testing-library/react';
import {Card, CardContent, CardHeader, CardFooter} from '@/components/ui/card';

const classesOf = (el: Element | null) => (el?.className ?? '').split(/\s+/).filter(Boolean);
const smPadding = (cls: string[]) => cls.filter((c) => /^sm:p[trbl]?-/.test(c));

describe('Card padding does not override the caller', () => {
    it('drops sm:pt-0 when the caller sets top padding via p-*  (/select-building)', () => {
        const {container} = render(
            <Card><CardContent className="p-6 flex items-center">x</CardContent></Card>
        );
        const cls = classesOf(container.querySelector('[class*="p-6"]'));
        expect(cls).toContain('p-6');
        expect(cls).not.toContain('sm:pt-0');
        expect(smPadding(cls)).toHaveLength(0);
    });

    it('drops sm:pt-0 and sm:pb-6 when the caller sets py-*  (/governance/proposals)', () => {
        const {container} = render(
            <Card><CardContent className="py-12 text-center">No proposals found.</CardContent></Card>
        );
        const cls = classesOf(container.querySelector('[class*="py-12"]'));
        expect(cls).toContain('py-12');
        expect(cls).not.toContain('sm:pt-0');
        expect(cls).not.toContain('sm:pb-6');
        // Axes the caller did NOT set keep their responsive default.
        expect(cls).toEqual(expect.arrayContaining(['sm:pr-6', 'sm:pl-6']));
    });

    it('honours p-0 at every breakpoint', () => {
        const {container} = render(<Card><CardContent className="p-0">x</CardContent></Card>);
        const cls = classesOf(container.querySelector('[class*="p-0"]'));
        expect(cls).toContain('p-0');
        expect(smPadding(cls)).toHaveLength(0);
    });

    it('keeps the full responsive default when the caller passes no padding', () => {
        const {container} = render(<Card><CardContent className="space-y-4">x</CardContent></Card>);
        const cls = classesOf(container.querySelector('[class*="space-y-4"]'));
        expect(cls).toEqual(
            expect.arrayContaining(['p-4', 'pt-0', 'sm:pt-0', 'sm:pr-6', 'sm:pb-6', 'sm:pl-6'])
        );
    });

    it("leaves a caller's own responsive utility alone", () => {
        const {container} = render(<Card><CardContent className="sm:p-8">x</CardContent></Card>);
        const cls = classesOf(container.querySelector('[class*="sm:p-8"]'));
        expect(cls).toContain('sm:p-8');
    });

    it('applies to CardHeader and CardFooter, not just CardContent', () => {
        const {container} = render(
            <Card>
                <CardHeader className="p-8">h</CardHeader>
                <CardFooter className="py-10">f</CardFooter>
            </Card>
        );
        const header = classesOf(container.querySelector('[class*="p-8"]'));
        expect(header).toContain('p-8');
        expect(smPadding(header)).toHaveLength(0);

        const footer = classesOf(container.querySelector('[class*="py-10"]'));
        expect(footer).toContain('py-10');
        expect(footer).not.toContain('sm:pt-0');
        expect(footer).not.toContain('sm:pb-6');
    });
});
