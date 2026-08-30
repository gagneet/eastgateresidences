/**
 * BuildingStrengthCard's handling of UNKNOWN signals.
 *
 * The card previously had only two states per signal (tick / warning) and a non-nullable
 * score. That forced every "we have no data" case into a confident verdict, and it always
 * picked one:
 *   - a null score fell through the grade ladder to "D"
 *   - the grade badge was hardcoded emerald, so that "D" was presented in green
 *   - an unknown signal rendered as a green tick or an amber alarm, never as unknown
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import "@testing-library/jest-dom";
import BuildingStrengthCard from "@/components/dashboard/BuildingStrengthCard";

const items = [
    {k: "Funds healthy", ok: true, detail: "Reserves stay positive"},
    {k: "Compliance", ok: null, detail: "No compliance summary available yet"},
    {k: "Maintenance SLA", ok: false, detail: "2 SLA breaches active"},
];

describe("BuildingStrengthCard", () => {
    it('renders an unknown score as "—" rather than grading it D', () => {
        render(<BuildingStrengthCard score={null} items={items} />);
        expect(screen.getByText(/Grade —/)).toBeInTheDocument();
        expect(screen.queryByText(/Grade D/)).not.toBeInTheDocument();
    });

    it('labels the gauge "No data" when the score is unknown', () => {
        render(<BuildingStrengthCard score={null} items={items} />);
        expect(screen.getByText(/No data/)).toBeInTheDocument();
        expect(screen.getByLabelText(/Building health score unavailable/i)).toBeInTheDocument();
    });

    it("grades a real score normally", () => {
        render(<BuildingStrengthCard score={88} items={items} />);
        expect(screen.getByText(/Grade A/)).toBeInTheDocument();
        expect(screen.getByLabelText(/Building health score 88 out of 100/i)).toBeInTheDocument();
    });

    it("does not present a failing grade in the passing colour", () => {
        const {container} = render(<BuildingStrengthCard score={20} items={items} />);
        const badge = screen.getByText(/Grade D/).closest("span");
        expect(badge?.className).toMatch(/rose/);
        expect(badge?.className).not.toMatch(/emerald/);
        expect(container).toBeTruthy();
    });

    it("keeps a passing grade in the passing colour", () => {
        render(<BuildingStrengthCard score={90} items={items} />);
        expect(screen.getByText(/Grade A/).closest("span")?.className).toMatch(/emerald/);
    });

    it("renders all three signal states distinctly", () => {
        render(<BuildingStrengthCard score={70} items={items} />);
        // Each signal keeps its own caption; the unknown one is not silently dropped.
        expect(screen.getByText("Funds healthy")).toBeInTheDocument();
        expect(screen.getByText("No compliance summary available yet")).toBeInTheDocument();
        expect(screen.getByText("2 SLA breaches active")).toBeInTheDocument();
    });

    it("gives an unknown signal a neutral icon rather than a tick or an alarm", () => {
        render(<BuildingStrengthCard score={70} items={items} />);
        const unknownRow = screen.getByText("Compliance").closest("li");
        const marker = unknownRow?.querySelector("span");
        // Asserts the neutral TOKEN, not a raw palette shade: the design-token ratchet
        // (GAP-UI-001) forbids raw neutrals, so pinning "slate" here would have made the
        // test fail the moment the component was converted correctly.
        expect(marker?.className).toMatch(/bg-muted/);
        expect(marker?.className).not.toMatch(/emerald|amber|rose/);
    });

    it("still renders an empty item list without inventing signals", () => {
        render(<BuildingStrengthCard score={null} items={[]} />);
        expect(screen.getByText(/No signals available yet/i)).toBeInTheDocument();
    });
});
