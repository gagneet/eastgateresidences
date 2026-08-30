/**
 * LettersPage test suite
 *
 * Coverage:
 *  1. Loading state while templates fetch
 *  2. Template selector populates with loaded templates
 *  3. Selecting a template shows its dynamic fields
 *  4. Template description updates on selection change
 *  5. Preview button calls POST /letters/generate and opens preview dialog
 *  6. Send button calls POST /letters/send with unit_numbers
 *  7. Send shows success toast with recipient count
 *  8. Send rejects empty unit numbers with error toast
 *  9. Rate-limit 429 shows specific error message
 * 10. History tab fetches and displays sent letters
 * 11. History tab shows empty state when no letters sent
 * 12. Field values reset when template changes
 */

import React from "react";
import {render, screen, waitFor, fireEvent} from "@testing-library/react";
import "@testing-library/jest-dom";

// ── Mocks ──────────────────────────────────────────────────────────────────────
const mockApi = {get: jest.fn(), post: jest.fn()};

jest.mock("@/contexts/AuthContext", () => ({
    useAuth: () => ({api: mockApi, isManager: () => true, loading: false}),
}));

jest.mock("sonner", () => ({toast: {success: jest.fn(), error: jest.fn()}}));

jest.mock("date-fns", () => ({
    format: (_date: Date, _fmt: string) => "26 Apr 2026 10:00",
}));

// Tabs — wire onValueChange so tab switching works in tests
jest.mock("@/components/ui/tabs", () => {
    const React = require("react") as typeof import("react");
    const Ctx = React.createContext<{ active?: string; change: (value: string) => void }>({
        change: () => undefined,
    });
    return {
        Tabs: ({children, defaultValue, onValueChange}: any) => {
            const [active, setActive] = React.useState(defaultValue);
            const change = (v: string) => {
                setActive(v);
                onValueChange?.(v);
            };
            return <Ctx.Provider value={{active, change}}>
                <div>{children}</div>
            </Ctx.Provider>;
        },
        TabsList: ({children, "data-testid": dt}: any) => <div data-testid={dt}>{children}</div>,
        TabsTrigger: ({children, value, "data-testid": dt}: any) => {
            const {change} = React.useContext(Ctx);
            return <button data-testid={dt || `tab-${value}`} onClick={() => change(value)}>{children}</button>;
        },
        TabsContent: ({children, value}: any) => {
            const {active} = React.useContext(Ctx);
            return active === value ? <div data-testid={`tab-content-${value}`}>{children}</div> : null;
        },
    };
});

// Select — Select itself renders as a <select> so options are proper DOM children.
// SelectTrigger provides the data-testid; SelectContent provides the options.
jest.mock("@/components/ui/select", () => {
    const React = require("react");
    return {
        Select: ({children, value, onValueChange}: any) => {
            // Extract data-testid from SelectTrigger child and options from SelectContent
            let testId = "select";
            const options: React.ReactNode[] = [];
            React.Children.forEach(children, (child: any) => {
                if (!child) return;
                if (child.props?.["data-testid"]) testId = child.props["data-testid"];
                // SelectContent children are SelectItems → collect as options
                if (child.props?.children && !child.props?.["data-testid"]) {
                    React.Children.forEach(child.props.children, (opt: any) => options.push(opt));
                }
            });
            return (
                <select
                    data-testid={testId}
                    value={value ?? ""}
                    onChange={(e: any) => onValueChange?.(e.target.value)}
                >
                    <option value="">Select…</option>
                    {options}
                </select>
            );
        },
        SelectTrigger: () => null,
        SelectValue: () => null,
        SelectContent: ({children}: any) => <>{children}</>,
        SelectItem: ({children, value, "data-testid": dt}: any) => (
            <option data-testid={dt} value={value}>{children}</option>
        ),
    };
});

import LettersPage from "@/pages/dashboard/admin/LettersPage";
import {toast} from "sonner";

// ── Fixtures ───────────────────────────────────────────────────────────────────

const TEMPLATES = [
    {
        name: "levy_reminder",
        label: "Levy Reminder",
        description: "Overdue levy notice",
        fields: [
            {name: "owner_name", type: "string", required: true, label: "Owner Name"},
            {name: "unit_number", type: "string", required: true, label: "Unit Number"},
            {name: "amount_due", type: "number", required: true, label: "Amount Due (AUD)"},
            {name: "due_date", type: "string", required: true, label: "Due Date"},
            {name: "body", type: "textarea", required: false, label: "Extra Notes"},
        ],
    },
    {
        name: "general_notice",
        label: "General Notice",
        description: "Freeform owner notice",
        fields: [
            {name: "owner_name", type: "string", required: true, label: "Owner Name"},
            {name: "subject", type: "string", required: true, label: "Subject"},
            {name: "body", type: "textarea", required: true, label: "Letter Body"},
        ],
    },
];

const HISTORY = [
    {
        id: "log-1",
        template: "levy_reminder",
        unit_numbers: ["42"],
        sent_by_name: "Alice Manager",
        sent_at: "2026-04-26T10:00:00Z",
        recipient_count: 1,
        subject: "Levy Reminder Q2",
    },
];

const FAKE_PDF_BLOB = new Blob(["%PDF-fake"], {type: "application/pdf"});

function mockSuccess() {
    mockApi.get.mockImplementation((url: string) => {
        if (url === "/letters/templates") return Promise.resolve({data: TEMPLATES});
        if (url === "/letters/history") return Promise.resolve({data: HISTORY});
        return Promise.reject(new Error(`Unexpected GET: ${url}`));
    });
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe("LettersPage", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        // Mock URL.createObjectURL / revokeObjectURL
        global.URL.createObjectURL = jest.fn(() => "blob:fake-url");
        global.URL.revokeObjectURL = jest.fn();
    });

    // ── 1. Loading ──────────────────────────────────────────────────────────────
    it("shows loading spinner while templates fetch", () => {
        mockApi.get.mockReturnValue(new Promise(() => {
        }));
        render(<LettersPage/>);
        expect(screen.getByTestId("letters-loading")).toBeInTheDocument();
    });

    // ── 2. Template selector ───────────────────────────────────────────────────
    it("populates template selector after load", async () => {
        mockSuccess();
        render(<LettersPage/>);
        await waitFor(() =>
            expect(screen.getByTestId("template-select")).toBeInTheDocument()
        );
        const options = screen.getAllByRole("option");
        const names = options.map((o: HTMLElement) => (o as HTMLOptionElement).value).filter(Boolean);
        expect(names).toContain("levy_reminder");
        expect(names).toContain("general_notice");
    });

    // ── 3. Dynamic fields ──────────────────────────────────────────────────────
    it("renders dynamic fields for selected template", async () => {
        mockSuccess();
        render(<LettersPage/>);
        await waitFor(() => screen.getByTestId("template-fields"));
        // levy_reminder fields should appear
        expect(screen.getByTestId("field-owner_name")).toBeInTheDocument();
        expect(screen.getByTestId("field-unit_number")).toBeInTheDocument();
        expect(screen.getByTestId("field-amount_due")).toBeInTheDocument();
        expect(screen.getByTestId("field-body")).toBeInTheDocument(); // textarea
    });

    // ── 4. Template description ────────────────────────────────────────────────
    it("shows template description for selected template", async () => {
        mockSuccess();
        render(<LettersPage/>);
        await waitFor(() =>
            expect(screen.getByTestId("template-description")).toHaveTextContent("Overdue levy notice")
        );
    });

    it("updates fields when template changes", async () => {
        mockSuccess();
        render(<LettersPage/>);
        await waitFor(() => screen.getByTestId("template-select"));

        fireEvent.change(screen.getByTestId("template-select"), {
            target: {value: "general_notice"},
        });

        await waitFor(() =>
            expect(screen.getByTestId("field-subject")).toBeInTheDocument()
        );
        // levy_reminder-specific field should be gone
        expect(screen.queryByTestId("field-unit_number")).not.toBeInTheDocument();
    });

    // ── 5. Preview ─────────────────────────────────────────────────────────────
    it("preview button calls POST /letters/generate and opens dialog", async () => {
        mockSuccess();
        mockApi.post.mockResolvedValue({data: FAKE_PDF_BLOB});
        render(<LettersPage/>);
        await waitFor(() => screen.getByTestId("preview-btn"));

        fireEvent.click(screen.getByTestId("preview-btn"));

        await waitFor(() =>
            expect(mockApi.post).toHaveBeenCalledWith(
                "/letters/generate",
                expect.objectContaining({template: "levy_reminder"}),
                expect.objectContaining({responseType: "blob"})
            )
        );
        await waitFor(() =>
            expect(screen.getByTestId("letter-preview-iframe")).toBeInTheDocument()
        );
    });

    // ── 6 & 7. Send ────────────────────────────────────────────────────────────
    it("send button calls POST /letters/send with unit_numbers", async () => {
        mockSuccess();
        mockApi.post.mockResolvedValue({data: {sent: 2, queued: 2}});
        render(<LettersPage/>);
        await waitFor(() => screen.getByTestId("unit-numbers-input"));

        fireEvent.change(screen.getByTestId("unit-numbers-input"), {
            target: {value: "1, 2"},
        });
        fireEvent.click(screen.getByTestId("send-btn"));

        await waitFor(() =>
            expect(mockApi.post).toHaveBeenCalledWith(
                "/letters/send",
                expect.objectContaining({
                    template: "levy_reminder",
                    unit_numbers: ["1", "2"],
                })
            )
        );
        expect(toast.success).toHaveBeenCalledWith("Letter sent to 2 recipient(s)");
    });

    // ── 8. Empty unit numbers ──────────────────────────────────────────────────
    it("shows error toast when unit numbers field is empty on send", async () => {
        mockSuccess();
        render(<LettersPage/>);
        await waitFor(() => screen.getByTestId("send-btn"));

        fireEvent.click(screen.getByTestId("send-btn"));

        expect(toast.error).toHaveBeenCalledWith("Enter at least one unit number");
        expect(mockApi.post).not.toHaveBeenCalled();
    });

    // ── 9. Rate limit 429 ─────────────────────────────────────────────────────
    it("shows rate-limit message on 429 response", async () => {
        mockSuccess();
        const err: any = new Error("429");
        err.response = {status: 429};
        mockApi.post.mockRejectedValue(err);
        render(<LettersPage/>);
        await waitFor(() => screen.getByTestId("unit-numbers-input"));

        fireEvent.change(screen.getByTestId("unit-numbers-input"), {
            target: {value: "42"},
        });
        fireEvent.click(screen.getByTestId("send-btn"));

        await waitFor(() =>
            expect(toast.error).toHaveBeenCalledWith(
                expect.stringMatching(/rate limit/i)
            )
        );
    });

    // ── 10 & 11. History tab ───────────────────────────────────────────────────
    it("history tab shows sent letters", async () => {
        mockSuccess();
        render(<LettersPage/>);
        await waitFor(() => screen.getByTestId("tab-history"));

        fireEvent.click(screen.getByTestId("tab-history"));

        await waitFor(() =>
            expect(screen.getByTestId("history-list")).toBeInTheDocument()
        );
        expect(screen.getByTestId("history-item-log-1")).toBeInTheDocument();
        expect(screen.getByText("Levy Reminder Q2")).toBeInTheDocument();
    });

    it("history tab shows empty state when no letters", async () => {
        mockApi.get.mockImplementation((url: string) => {
            if (url === "/letters/templates") return Promise.resolve({data: TEMPLATES});
            if (url === "/letters/history") return Promise.resolve({data: []});
            return Promise.reject(new Error(`Unexpected: ${url}`));
        });
        render(<LettersPage/>);
        await waitFor(() => screen.getByTestId("tab-history"));

        fireEvent.click(screen.getByTestId("tab-history"));

        await waitFor(() =>
            expect(screen.getByTestId("history-empty")).toBeInTheDocument()
        );
    });

    // ── 12. Field reset on template change ────────────────────────────────────
    it("clears field values when template changes", async () => {
        mockSuccess();
        render(<LettersPage/>);
        await waitFor(() => screen.getByTestId("field-owner_name"));

        // Type into a field
        fireEvent.change(screen.getByTestId("field-owner_name"), {
            target: {value: "John Smith"},
        });
        expect(screen.getByTestId("field-owner_name")).toHaveValue("John Smith");

        // Switch template
        fireEvent.change(screen.getByTestId("template-select"), {
            target: {value: "general_notice"},
        });

        await waitFor(() => screen.getByTestId("field-owner_name"));
        expect(screen.getByTestId("field-owner_name")).toHaveValue("");
    });
});
