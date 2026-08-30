import React from 'react';
import {render, screen, waitFor, fireEvent} from '@testing-library/react';
import '@testing-library/jest-dom';
import HistoricalReconstructionPage from '@/pages/dashboard/financial/HistoricalReconstructionPage';

jest.mock('@/contexts/AuthContext', () => ({
    useAuth: jest.fn(),
}));

jest.mock('sonner', () => ({toast: {error: jest.fn(), success: jest.fn()}}));

const mockUseAuth = require('@/contexts/AuthContext').useAuth;
const {toast} = require('sonner');

// jsdom does not implement scrollIntoView; Radix Select's content-positioning effect
// calls it on mount. No other test file in this repo exercises a Radix Select yet, so
// this polyfill is scoped here rather than added to the shared jest setup.
beforeAll(() => {
    if (!Element.prototype.scrollIntoView) {
        Element.prototype.scrollIntoView = jest.fn();
    }
});

const BATCH_LIST_RESPONSE = {
    items: [
        {
            batch_id: 'batch-1234-5678',
            building_id: '13195',
            financial_year_start: 2022,
            financial_year_end: 2022,
            reconstruction_method: 'gst_uoe_largest_remainder_v5',
            status: 'needs_review',
            unit_count: 87,
            generated_credit_cents: 0,
            reconciliation_variance_cents: 0,
            created_by: 'user-1',
            reviewed_by: null,
            approved_by: null,
            created_at: '2026-07-17T00:00:00Z',
            reviewed_at: null,
            approved_at: null,
            is_test_data: false,
        },
    ],
    next_cursor: null,
};

const BATCH_DETAIL_RESPONSE = {
    ...BATCH_LIST_RESPONSE.items[0],
    source_document_ids: [],
    warnings: [],
};

const DATA_STATUS_NONE = {
    building_id: '13195', unit_count: 87,
    has_any_paid_periods: false, paid_period_count: 0, total_period_count: 696,
};

const DATA_STATUS_PARTIAL = {
    building_id: '13195', unit_count: 87,
    has_any_paid_periods: true, paid_period_count: 12, total_period_count: 696,
};

function mockAuth(overrides: Partial<Record<string, any>> = {}) {
    const api = {
        get: jest.fn((url: string) => {
            if (url.endsWith('/manifest')) {
                return Promise.reject({response: {status: 404}});
            }
            if (url.endsWith('/audit-log')) {
                return Promise.resolve({data: {batch_id: 'batch-1234-5678', entries: []}});
            }
            // Must be checked before the generic '/reconstruction-batches/' substring
            // match below — that check would otherwise also match this URL and
            // incorrectly hand back the batch-detail shape instead.
            if (url.endsWith('/reconciliation-summary')) {
                return Promise.resolve({
                    data: {
                        batch_id: 'batch-1234-5678', manifest: null, by_year_fund: [],
                        manual_review: {count: 0, total_variance_cents: 0, percentage_of_batch: null},
                        totals_reconcile: null, warnings: [],
                    },
                });
            }
            if (url.includes('/reconstruction-batches/')) {
                return Promise.resolve({data: BATCH_DETAIL_RESPONSE});
            }
            return Promise.resolve({data: BATCH_LIST_RESPONSE});
        }),
        post: jest.fn().mockResolvedValue({data: {}}),
    };
    mockUseAuth.mockReturnValue({
        api,
        user: {role: 'strata_manager', effective_role: 'strata_manager'},
        loading: false,
        hasFeatureAccess: jest.fn(() => true),
        selectedBuilding: {id: 'scheme-uuid', building_id: '13195'},
        ...overrides,
    });
    return api;
}

describe('HistoricalReconstructionPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it('redirects non-eligible roles away from the page', async () => {
        const {mockReplace} = require('next/navigation');
        mockAuth({user: {role: 'owner', effective_role: 'owner'}});

        const {container} = render(<HistoricalReconstructionPage/>);
        expect(container.textContent).not.toContain('Historical Reconstruction');
        await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('/dashboard'));
    });

    it('allows strata_manager to view the page (operations model — prep roles broader than approval roles)', async () => {
        mockAuth();
        render(<HistoricalReconstructionPage/>);
        await waitFor(() => expect(screen.getByText('Historical Reconstruction')).toBeInTheDocument());
    });

    it('shows a disabled-posting banner when preparation is on but posting is off', async () => {
        mockAuth({
            hasFeatureAccess: jest.fn((key: string) => key === 'historical_financial_reconstruction'),
        });
        render(<HistoricalReconstructionPage/>);
        await waitFor(() =>
            expect(screen.getByText(/Posting and ledger-changing actions/i)).toBeInTheDocument()
        );
    });

    it('does not fetch when the building is not yet resolved', async () => {
        const api = mockAuth({selectedBuilding: null});
        render(<HistoricalReconstructionPage/>);
        await waitFor(() => expect(screen.getByText('Historical Reconstruction')).toBeInTheDocument());
        expect(api.get).not.toHaveBeenCalled();
    });

    it('lists batches fetched from the building-scoped list endpoint', async () => {
        const api = mockAuth();
        render(<HistoricalReconstructionPage/>);

        await waitFor(() =>
            expect(api.get).toHaveBeenCalledWith(
                '/financials/onboarding/building/13195/reconstruction-batches',
                {params: {limit: 100}},
            )
        );
        await waitFor(() => expect(screen.getByText(/2022–2022/)).toBeInTheDocument());
    });

    it('shows a Load More button when the list response carries a next_cursor, and paginates', async () => {
        const api = mockAuth();
        api.get.mockImplementation((url: string, config?: any) => {
            if (url.endsWith('/manifest')) return Promise.reject({response: {status: 404}});
            if (url.endsWith('/audit-log')) return Promise.resolve({data: {entries: []}});
            if (url.includes('/reconstruction-batches/')) return Promise.resolve({data: BATCH_DETAIL_RESPONSE});
            if (config?.params?.cursor) {
                return Promise.resolve({
                    data: {
                        items: [{...BATCH_LIST_RESPONSE.items[0], batch_id: 'batch-page-2', financial_year_start: 2021, financial_year_end: 2021}],
                        next_cursor: null,
                    },
                });
            }
            return Promise.resolve({data: {...BATCH_LIST_RESPONSE, next_cursor: '2026-07-01T00:00:00Z'}});
        });
        render(<HistoricalReconstructionPage/>);

        await waitFor(() => expect(screen.getByText('Load More')).toBeInTheDocument());
        fireEvent.click(screen.getByText('Load More'));

        await waitFor(() => expect(screen.getByText(/2021–2021/)).toBeInTheDocument());
        expect(api.get).toHaveBeenCalledWith(
            '/financials/onboarding/building/13195/reconstruction-batches',
            {params: {limit: 100, cursor: '2026-07-01T00:00:00Z'}},
        );
    });

    it('shows source evidence and audit history in the batch detail view', async () => {
        mockAuth({
            user: {id: 'user-1', role: 'strata_manager', effective_role: 'strata_manager'},
        }).get.mockImplementation((url: string) => {
            if (url.endsWith('/manifest')) return Promise.reject({response: {status: 404}});
            if (url.endsWith('/audit-log')) {
                return Promise.resolve({
                    data: {
                        entries: [
                            {action: 'reconstruction_batch_created', user_name: 'Alice Admin', created_at: '2026-07-17T00:00:00Z'},
                        ],
                    },
                });
            }
            if (url.includes('/reconstruction-batches/')) {
                return Promise.resolve({
                    data: {...BATCH_DETAIL_RESPONSE, source_document_ids: ['doc-agm-2022'], source_organisation: 'Civium'},
                });
            }
            return Promise.resolve({data: BATCH_LIST_RESPONSE});
        });
        render(<HistoricalReconstructionPage/>);

        await waitFor(() => expect(screen.getByText(/2022–2022/)).toBeInTheDocument());
        fireEvent.click(screen.getByText('View'));

        await waitFor(() => expect(screen.getByText('doc-agm-2022')).toBeInTheDocument());
        expect(screen.getByText(/Organisation: Civium/)).toBeInTheDocument();
        expect(screen.getByText('reconstruction batch created')).toBeInTheDocument();
        expect(screen.getByText('Alice Admin')).toBeInTheDocument();
    });

    it('shows the reconciliation summary decision basis: per-year breakdown and manual-review materiality', async () => {
        mockAuth().get.mockImplementation((url: string) => {
            if (url.endsWith('/manifest')) return Promise.reject({response: {status: 404}});
            if (url.endsWith('/audit-log')) return Promise.resolve({data: {entries: []}});
            if (url.endsWith('/reconciliation-summary')) {
                return Promise.resolve({
                    data: {
                        batch_id: 'batch-1234-5678',
                        manifest: {expected_credit_cents: 100000, expected_transaction_count: 4},
                        by_year_fund: [{
                            year: '2022', fund_type: 'admin',
                            annual_levies_proposed_inc_gst_cents: 100000,
                            regenerated_levy_items_total_cents: 100050,
                            demo_bank_payment_total_cents: 99000,
                            existing_levy_items_total_cents: 95000,
                            variance_cents: 50, within_tolerance: true,
                        }],
                        manual_review: {count: 3, total_variance_cents: 5000, percentage_of_batch: 5.0},
                        totals_reconcile: true,
                        warnings: [],
                    },
                });
            }
            if (url.includes('/reconstruction-batches/')) return Promise.resolve({data: BATCH_DETAIL_RESPONSE});
            return Promise.resolve({data: BATCH_LIST_RESPONSE});
        });
        render(<HistoricalReconstructionPage/>);

        await waitFor(() => expect(screen.getByText(/2022–2022/)).toBeInTheDocument());
        fireEvent.click(screen.getByText('View'));

        await waitFor(() => expect(screen.getByText(/Reconciles against annual levies/)).toBeInTheDocument());
        // "3" and "$50.00" each render inside their own <strong>, split from the
        // surrounding text — match on the containing <span>'s combined text content
        // (substring checks, not full-string equality — robust against exact JSX
        // whitespace rendering) rather than a single text node.
        const manualReviewEl = screen.getByText((_, el) =>
            el?.tagName.toLowerCase() === "span" && !!el.textContent?.includes("unit(s) flagged for manual review"));
        expect(manualReviewEl.textContent).toContain("3");
        expect(manualReviewEl.textContent).toContain("$50.00");
        expect(manualReviewEl.textContent).toContain("5% of batch total");
        // Per-year/fund table renders the actual dollar breakdown, not just a lump total.
        expect(screen.getByText('2022')).toBeInTheDocument();
        expect(screen.getByText('admin')).toBeInTheDocument();
        // "Currently Posted" (existing_levy_items_total_cents) must render — it's the one
        // number that tells a reviewer how much this batch's regeneration will actually
        // move existing finance.levy_items figures, and was silently dropped from the
        // table in the first implementation despite already being returned by the API.
        expect(screen.getByText('Currently Posted')).toBeInTheDocument();
        expect(screen.getByText('$950.00')).toBeInTheDocument();
    });

    it('shows an unknown (not a false negative) badge when reconciliation cannot be computed', async () => {
        mockAuth().get.mockImplementation((url: string) => {
            if (url.endsWith('/manifest')) return Promise.reject({response: {status: 404}});
            if (url.endsWith('/audit-log')) return Promise.resolve({data: {entries: []}});
            if (url.endsWith('/reconciliation-summary')) {
                return Promise.resolve({
                    data: {
                        batch_id: 'batch-1234-5678', manifest: null, by_year_fund: [],
                        manual_review: {count: 0, total_variance_cents: 0, percentage_of_batch: null},
                        totals_reconcile: null,
                        warnings: ['reconciliation_unavailable: No units with positive UOE found'],
                    },
                });
            }
            if (url.includes('/reconstruction-batches/')) return Promise.resolve({data: BATCH_DETAIL_RESPONSE});
            return Promise.resolve({data: BATCH_LIST_RESPONSE});
        });
        render(<HistoricalReconstructionPage/>);

        await waitFor(() => expect(screen.getByText(/2022–2022/)).toBeInTheDocument());
        fireEvent.click(screen.getByText('View'));

        await waitFor(() => expect(screen.getByText(/Reconciliation could not be computed/)).toBeInTheDocument());
        expect(screen.getByText(/reconciliation_unavailable/)).toBeInTheDocument();
        // Must not render either the "reconciles" or "does not reconcile" badges —
        // "unknown" is a distinct state, never presented as a pass or a fail.
        expect(screen.queryByText(/Reconciles against annual levies/)).not.toBeInTheDocument();
        expect(screen.queryByText(/Does not reconcile/)).not.toBeInTheDocument();
    });

    it('shows Review (not Approve) for an unreviewed batch, and posts notes with no client-supplied identity', async () => {
        // Dual control step 1 of 2: a needs_review batch with reviewed_by=null
        // must offer "Review", not "Approve" — approving an unreviewed batch
        // would always 409 server-side (see financial_onboarding.py's
        // approve_reconstruction_batch).
        const api = mockAuth({user: {id: 'user-sa', role: 'strata_admin', effective_role: 'strata_admin'}});
        render(<HistoricalReconstructionPage/>);

        await waitFor(() => expect(screen.getByText(/2022–2022/)).toBeInTheDocument());
        fireEvent.click(screen.getByText('View'));

        await waitFor(() => expect(screen.getByText('Review')).toBeInTheDocument());
        expect(screen.queryByText('Approve')).not.toBeInTheDocument();
        fireEvent.click(screen.getByText('Review'));

        await waitFor(() => expect(screen.getByText('Confirm Review')).toBeInTheDocument());
        const notesInput = screen.getByLabelText(/Review notes/i);
        fireEvent.change(notesInput, {target: {value: 'Totals line up with the AGM pack.'}});
        fireEvent.click(screen.getByText('Confirm Review'));

        await waitFor(() =>
            expect(api.post).toHaveBeenCalledWith(
                '/financials/onboarding/building/13195/reconstruction-batches/batch-1234-5678/review',
                {notes: 'Totals line up with the AGM pack.'},
            )
        );
    });

    it('requires typed confirmation before calling approve, and calls it with no client-supplied identity', async () => {
        // Dual control step 2 of 2: batch already reviewed by a DIFFERENT user.
        const api = mockAuth({user: {id: 'user-admin', role: 'strata_admin', effective_role: 'strata_admin'}});
        api.get.mockImplementation((url: string) => {
            if (url.endsWith('/manifest')) return Promise.reject({response: {status: 404}});
            if (url.includes('/reconstruction-batches/')) {
                return Promise.resolve({data: {...BATCH_DETAIL_RESPONSE, reviewed_by: 'user-sa'}});
            }
            return Promise.resolve({data: BATCH_LIST_RESPONSE});
        });
        render(<HistoricalReconstructionPage/>);

        await waitFor(() => expect(screen.getByText(/2022–2022/)).toBeInTheDocument());
        fireEvent.click(screen.getByText('View'));

        await waitFor(() => expect(screen.getByText('Approve')).toBeInTheDocument());
        expect(screen.queryByText('Review')).not.toBeInTheDocument();
        fireEvent.click(screen.getByText('Approve'));

        // Confirmation dialog opens; approve must not fire without typing APPROVE.
        await waitFor(() => expect(screen.getByText('Confirm Approval')).toBeInTheDocument());
        fireEvent.click(screen.getByText('Confirm Approval'));
        expect(api.post).not.toHaveBeenCalledWith(expect.stringContaining('/approve'), expect.anything());

        const input = screen.getByLabelText(/Type APPROVE to confirm/i);
        fireEvent.change(input, {target: {value: 'APPROVE'}});
        fireEvent.click(screen.getByText('Confirm Approval'));

        await waitFor(() =>
            expect(api.post).toHaveBeenCalledWith(
                '/financials/onboarding/building/13195/reconstruction-batches/batch-1234-5678/approve',
                {},
            )
        );
    });

    it('disables Approve (dual control) when the current user is also the reviewer', async () => {
        const api = mockAuth({user: {id: 'user-sa', role: 'strata_admin', effective_role: 'strata_admin'}});
        api.get.mockImplementation((url: string) => {
            if (url.endsWith('/manifest')) return Promise.reject({response: {status: 404}});
            if (url.includes('/reconstruction-batches/')) {
                return Promise.resolve({data: {...BATCH_DETAIL_RESPONSE, reviewed_by: 'user-sa'}});
            }
            return Promise.resolve({data: BATCH_LIST_RESPONSE});
        });
        render(<HistoricalReconstructionPage/>);

        await waitFor(() => expect(screen.getByText(/2022–2022/)).toBeInTheDocument());
        fireEvent.click(screen.getByText('View'));

        await waitFor(() => expect(screen.getByText('Approve')).toBeInTheDocument());
        const approveButton = screen.getByText('Approve').closest('button');
        expect(approveButton).toBeDisabled();
        expect(approveButton).toHaveAttribute('title', expect.stringContaining('dual control'));
    });

    it('disables Apply Regeneration for non-super_admin roles even when posting is enabled and a plan exists', async () => {
        const api = mockAuth({user: {role: 'strata_admin', effective_role: 'strata_admin'}});
        api.post.mockImplementation((url: string) => {
            if (url.endsWith('/regenerate-plan')) {
                return Promise.resolve({
                    data: {
                        building_id: '13195', from_year: 2022, to_year: 2022,
                        by_year_fund: [], lines: [], warnings: [],
                        journal_classification_summary: {}, totals_reconcile: true,
                    },
                });
            }
            return Promise.resolve({data: {}});
        });
        render(<HistoricalReconstructionPage/>);
        await waitFor(() => expect(screen.getByText('Historical Reconstruction')).toBeInTheDocument());

        // Radix TabsTrigger requires mouseDown, not click, to fire its selection handler.
        fireEvent.mouseDown(screen.getByText('Levy Regeneration'));
        await waitFor(() => expect(screen.getByText('Generate Plan')).toBeInTheDocument());
        fireEvent.click(screen.getByText('Generate Plan'));

        await waitFor(() => expect(screen.getByText('Apply Regeneration')).toBeInTheDocument());
        const applyButton = screen.getByText('Apply Regeneration').closest('button');
        expect(applyButton).toBeDisabled();
    });

    describe('generate-from-budget generation source (GAP-FIN-023)', () => {
        function mockAuthWithDataStatus(dataStatus: any, overrides: Partial<Record<string, any>> = {}) {
            const api = mockAuth(overrides);
            api.get.mockImplementation((url: string) => {
                if (url.includes('historical-data-status')) return Promise.resolve({data: dataStatus});
                if (url.endsWith('/manifest')) return Promise.reject({response: {status: 404}});
                if (url.endsWith('/audit-log')) return Promise.resolve({data: {entries: []}});
                if (url.endsWith('/reconciliation-summary')) {
                    return Promise.resolve({
                        data: {batch_id: 'batch-1234-5678', manifest: null, by_year_fund: [],
                            manual_review: {count: 0, total_variance_cents: 0, percentage_of_batch: null},
                            totals_reconcile: null, warnings: []},
                    });
                }
                if (url.includes('/reconstruction-batches/')) return Promise.resolve({data: BATCH_DETAIL_RESPONSE});
                return Promise.resolve({data: BATCH_LIST_RESPONSE});
            });
            return api;
        }

        it('shows a "no financial history" badge when the building has none', async () => {
            mockAuthWithDataStatus(DATA_STATUS_NONE);
            render(<HistoricalReconstructionPage/>);

            expect(await screen.findByTestId('historical-data-status')).toHaveTextContent(
                /No financial history found/i,
            );
        });

        it('shows a partial-history badge with counts when some periods are already paid', async () => {
            mockAuthWithDataStatus(DATA_STATUS_PARTIAL);
            render(<HistoricalReconstructionPage/>);

            const badge = await screen.findByTestId('historical-data-status');
            expect(badge).toHaveTextContent(/Partial financial history found/i);
            expect(badge).toHaveTextContent('12');
            expect(badge).toHaveTextContent('696');
        });

        it('defaults the create-batch dialog to "generate from proposed budget" and hides the legacy fields', async () => {
            mockAuthWithDataStatus(DATA_STATUS_NONE);
            render(<HistoricalReconstructionPage/>);

            fireEvent.click(await screen.findByRole('button', {name: /Create Batch/i}));
            await waitFor(() => expect(screen.getByText('Create Reconstruction Batch')).toBeInTheDocument());

            expect(screen.getByText(/Generate from proposed budget/i)).toBeInTheDocument();
            expect(screen.queryByLabelText(/^Reconstruction method$/i)).not.toBeInTheDocument();
            expect(screen.queryByLabelText(/Source\/evidence document IDs/i)).not.toBeInTheDocument();
        });

        it('reveals the legacy free-text fields when switching to "link existing" mode', async () => {
            mockAuthWithDataStatus(DATA_STATUS_NONE);
            render(<HistoricalReconstructionPage/>);

            fireEvent.click(await screen.findByRole('button', {name: /Create Batch/i}));
            await waitFor(() => expect(screen.getByText('Create Reconstruction Batch')).toBeInTheDocument());

            fireEvent.click(screen.getByRole('combobox'));
            fireEvent.click(await screen.findByText(/Link existing Demo Bank rows/i));

            await waitFor(() => expect(screen.getByLabelText(/^Reconstruction method$/i)).toBeInTheDocument());
            expect(screen.getByLabelText(/Source\/evidence document IDs/i)).toBeInTheDocument();
        });

        it('submits generate_from_budget_v1 with empty source_document_ids by default', async () => {
            const api = mockAuthWithDataStatus(DATA_STATUS_NONE);
            render(<HistoricalReconstructionPage/>);

            fireEvent.click(await screen.findByRole('button', {name: /Create Batch/i}));
            await waitFor(() => expect(screen.getByText('Create Reconstruction Batch')).toBeInTheDocument());

            const dialogButtons = screen.getAllByRole('button', {name: /^Create Batch$/i});
            fireEvent.click(dialogButtons[dialogButtons.length - 1]);

            await waitFor(() =>
                expect(api.post).toHaveBeenCalledWith(
                    '/financials/onboarding/building/13195/reconstruction-batches',
                    expect.objectContaining({
                        reconstruction_method: 'generate_from_budget_v1',
                        source_document_ids: [],
                    }),
                )
            );
        });
    });
});
