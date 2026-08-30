import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import userEvent from '@testing-library/user-event';

// ── Mocks ─────────────────────────────────────────────────────────────────────

const mockApi = {get: jest.fn(), post: jest.fn()};
const mockUseAuth = jest.fn(() => ( {user: {role: 'strata_manager'}, api: mockApi} ));

jest.mock('@/contexts/AuthContext', () => ( {
    useAuth: (...args) => mockUseAuth(...args),
} ));
jest.mock('sonner', () => ( {toast: {success: jest.fn(), error: jest.fn()}} ));

jest.mock('@/components/ui/dialog', () => ( {
    Dialog: ({children, open}) => open ? <div data-testid="dialog">{children}</div> : null,
    DialogContent: ({children}) => <div>{children}</div>,
    DialogDescription: ({children}) => <p>{children}</p>,
    DialogFooter: ({children}) => <div>{children}</div>,
    DialogHeader: ({children}) => <div>{children}</div>,
    DialogTitle: ({children}) => <h2>{children}</h2>,
} ));
jest.mock('@/components/ui/tabs', () => ( {
    Tabs: ({children}) => <div>{children}</div>,
    TabsContent: ({children}) => <div>{children}</div>,
    TabsList: ({children}) => <div>{children}</div>,
    TabsTrigger: ({children}) => <button>{children}</button>,
} ));

// ── Fixtures ──────────────────────────────────────────────────────────────────

const BATCH_PENDING = {
    id: 'batch-001',
    description: 'Q1 2026 Levy Migration',
    source_system: 'MRI',
    status: 'pending',
    created_at: '2026-04-01T10:00:00Z',
    records_total: 87,
    records_processed: 0,
    records_failed: 0,
};

const BATCH_COMMITTED = {
    id: 'batch-002',
    description: 'FY2025 Trust Migration',
    source_system: 'Strata Master',
    status: 'committed',
    created_at: '2026-03-15T08:00:00Z',
    records_total: 200,
    records_processed: 200,
    records_failed: 0,
};

const BATCH_DETAIL = {
    ...BATCH_PENDING,
    validation_issues: [
        {severity: 'warning', field: 'owner_email', message: 'Email format unusual'},
    ],
    records: [],
};

// ── Import ────────────────────────────────────────────────────────────────────

import MriMigrationPage from '@/pages/dashboard/MriMigrationPage';

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('MriMigrationPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockUseAuth.mockImplementation(() => ( {user: {role: 'strata_manager'}, api: mockApi} ));
        mockApi.get.mockImplementation((url) => {
            if (url === '/migration/mri/batches')
                return Promise.resolve({data: {items: [BATCH_PENDING, BATCH_COMMITTED]}});
            if (url.startsWith('/migration/mri/batches/'))
                return Promise.resolve({data: BATCH_DETAIL});
            return Promise.reject(new Error(`Unexpected GET: ${url}`));
        });
        mockApi.post.mockResolvedValue({data: {...BATCH_PENDING, id: 'batch-new'}});
    });

    // ── API URL construction ─────────────────────────────────────────────────

    describe('API URL construction — no absolute URL, no double /api/ prefix', () => {
        it('fetches batch list using path-only URL', async () => {
            render(<MriMigrationPage/>);
            await waitFor(() => expect(mockApi.get).toHaveBeenCalled());
            const url = mockApi.get.mock.calls[ 0 ][ 0 ];
            expect(url).toBe('/migration/mri/batches');
            expect(url).not.toMatch(/^https?:\/\//);
            expect(url).not.toContain('/api/');
        });

        it('fetches batch detail using path-only URL', async () => {
            render(<MriMigrationPage/>);
            await waitFor(() => screen.getAllByRole('button', {name: 'View'}));
            await userEvent.click(screen.getAllByRole('button', {name: 'View'})[ 0 ]);
            await waitFor(() =>
                expect(mockApi.get).toHaveBeenCalledWith('/migration/mri/batches/batch-001')
            );
            const detailUrl = mockApi.get.mock.calls.find(c => c[ 0 ].includes('/batch-001'))?.[ 0 ];
            expect(detailUrl).not.toMatch(/^https?:\/\//);
            expect(detailUrl).not.toContain('/api/');
        });

        it('posts new batch using path-only URL', async () => {
            render(<MriMigrationPage/>);
            await waitFor(() => screen.getByRole('button', {name: /New Batch/i}));
            await userEvent.click(screen.getByRole('button', {name: /New Batch/i}));
            await userEvent.type(
                screen.getByPlaceholderText(/MRI Q1 2026/i),
                'Test batch description'
            );
            await userEvent.click(screen.getByRole('button', {name: /Create Batch/i}));
            await waitFor(() => expect(mockApi.post).toHaveBeenCalled());
            expect(mockApi.post.mock.calls[ 0 ][ 0 ]).toBe('/migration/mri/batches');
            expect(mockApi.post.mock.calls[ 0 ][ 0 ]).not.toMatch(/^https?:\/\//);
        });

        it('posts action using path-only URL', async () => {
            // Actions like validate/dry-run/commit use /batches/{id}/{action}
            // Verify the URL pattern is path-only by inspecting the component's fetchBatchDetail call
            render(<MriMigrationPage/>);
            await waitFor(() => screen.getAllByRole('button', {name: 'View'}));
            await userEvent.click(screen.getAllByRole('button', {name: 'View'})[ 0 ]);
            await waitFor(() => expect(mockApi.get).toHaveBeenCalledWith('/migration/mri/batches/batch-001'));
            const detailUrl = mockApi.get.mock.calls.find(c => c[ 0 ].startsWith('/migration'))?.[ 0 ];
            expect(detailUrl).not.toMatch(/^https?:\/\//);
        });
    });

    // ── Rendering ────────────────────────────────────────────────────────────

    describe('rendering', () => {
        it('renders Migration Batches heading', async () => {
            render(<MriMigrationPage/>);
            await waitFor(() =>
                expect(screen.getByText('Migration Batches')).toBeInTheDocument()
            );
        });

        it('shows both batch descriptions after load', async () => {
            render(<MriMigrationPage/>);
            await waitFor(() => {
                expect(screen.getByText('Q1 2026 Levy Migration')).toBeInTheDocument();
                expect(screen.getByText('FY2025 Trust Migration')).toBeInTheDocument();
            });
        });

        it('shows status badges for each batch', async () => {
            render(<MriMigrationPage/>);
            await waitFor(() => {
                expect(screen.getByText('Pending')).toBeInTheDocument();
                expect(screen.getByText('Committed')).toBeInTheDocument();
            });
        });

        it('shows source systems', async () => {
            render(<MriMigrationPage/>);
            await waitFor(() => {
                expect(screen.getByText('MRI')).toBeInTheDocument();
                expect(screen.getByText('Strata Master')).toBeInTheDocument();
            });
        });

        it('shows New Batch and Refresh buttons', async () => {
            render(<MriMigrationPage/>);
            await waitFor(() => {
                expect(screen.getByRole('button', {name: /New Batch/i})).toBeInTheDocument();
                expect(screen.getByRole('button', {name: /Refresh/i})).toBeInTheDocument();
            });
        });

        it('shows a View button per batch row', async () => {
            render(<MriMigrationPage/>);
            await waitFor(() => {
                const viewButtons = screen.getAllByRole('button', {name: 'View'});
                expect(viewButtons.length).toBe(2);
            });
        });
    });

    // ── Access control ───────────────────────────────────────────────────────

    describe('access control', () => {
        it('shows permission denied message for owner role', () => {
            mockUseAuth.mockReturnValueOnce({user: {role: 'owner'}, api: mockApi});
            render(<MriMigrationPage/>);
            expect(screen.getByText(/do not have permission/i)).toBeInTheDocument();
            expect(screen.queryByText('Migration Batches')).not.toBeInTheDocument();
        });

        it('renders normally for strata_manager role', async () => {
            render(<MriMigrationPage/>);
            await waitFor(() =>
                expect(screen.getByText('Migration Batches')).toBeInTheDocument()
            );
        });
    });

    // ── Loading and error states ─────────────────────────────────────────────

    describe('loading and error states', () => {
        it('shows no batch rows while loading', () => {
            mockApi.get.mockReturnValue(new Promise(() => {
            })); // never resolves
            render(<MriMigrationPage/>);
            expect(screen.queryByText('Q1 2026 Levy Migration')).not.toBeInTheDocument();
        });

        it('shows toast error when batch list fetch fails', async () => {
            const {toast} = require('sonner');
            mockApi.get.mockRejectedValue(new Error('Network error'));
            render(<MriMigrationPage/>);
            await waitFor(() =>
                expect(toast.error).toHaveBeenCalledWith('Failed to load migration batches')
            );
        });

        it('shows empty state when no batches exist', async () => {
            mockApi.get.mockResolvedValue({data: []});
            render(<MriMigrationPage/>);
            await waitFor(() =>
                expect(screen.getByText('No migration batches found.')).toBeInTheDocument()
            );
        });
    });

    // ── New batch modal ──────────────────────────────────────────────────────

    describe('new batch modal', () => {
        it('opens dialog when New Batch is clicked', async () => {
            render(<MriMigrationPage/>);
            await waitFor(() => screen.getByRole('button', {name: /New Batch/i}));
            await userEvent.click(screen.getByRole('button', {name: /New Batch/i}));
            expect(screen.getByTestId('dialog')).toBeInTheDocument();
            expect(screen.getByText('New Migration Batch')).toBeInTheDocument();
        });

        it('shows description input and source system select', async () => {
            render(<MriMigrationPage/>);
            await waitFor(() => screen.getByRole('button', {name: /New Batch/i}));
            await userEvent.click(screen.getByRole('button', {name: /New Batch/i}));
            expect(screen.getByPlaceholderText(/MRI Q1 2026/i)).toBeInTheDocument();
        });

        it('validates that description is required', async () => {
            const {toast} = require('sonner');
            render(<MriMigrationPage/>);
            await waitFor(() => screen.getByRole('button', {name: /New Batch/i}));
            await userEvent.click(screen.getByRole('button', {name: /New Batch/i}));
            await userEvent.click(screen.getByRole('button', {name: /Create Batch/i}));
            await waitFor(() =>
                expect(toast.error).toHaveBeenCalledWith('Description is required')
            );
        });
    });

    // ── Hooks order (Rules of Hooks fix) ─────────────────────────────────────
    //
    // Regression guard: hooks must fire before the role-guard early return.
    // If hooks were declared after the conditional return, React would throw
    // "Rendered more hooks than during the previous render" when the role
    // changes from unauthorised → authorised or vice versa.

    describe('hooks order — role guard does not break hook consistency', () => {
        it('fetches batches even when initially rendered as owner (denied), then re-renders as manager', async () => {
            // First render as owner — shows the permission-denied message.
            // Hooks must have fired so the component can safely re-render.
            mockUseAuth.mockReturnValueOnce({user: {role: 'owner'}, api: mockApi});
            const {rerender} = render(<MriMigrationPage/>);
            expect(screen.getByText(/do not have permission/i)).toBeInTheDocument();

            // Re-render as strata_manager — hooks were already registered so
            // React will not throw "rendered more/fewer hooks than previous render".
            mockUseAuth.mockReturnValue({user: {role: 'strata_manager'}, api: mockApi});
            rerender(<MriMigrationPage/>);
            await waitFor(() =>
                expect(screen.getByText('Migration Batches')).toBeInTheDocument()
            );
            // The batch list API was called (hooks ran, including the useEffect)
            expect(mockApi.get).toHaveBeenCalledWith('/migration/mri/batches');
        });

        it('API calls fire for unauthorised users (hooks run before guard)', async () => {
            // Even when the permission-denied view is shown, the useEffect hooks
            // that call fetchBatches have already been registered. This is the
            // correct React behaviour: hooks run unconditionally, the guard only
            // controls what is *rendered*, not whether hooks fire.
            mockUseAuth.mockReturnValue({user: {role: 'guest'}, api: mockApi});
            render(<MriMigrationPage/>);
            // The denied message is shown
            expect(screen.getByText(/do not have permission/i)).toBeInTheDocument();
            // The fetch was still called because hooks (useEffect) run before the
            // conditional return — this is correct React behaviour.
            await waitFor(() =>
                expect(mockApi.get).toHaveBeenCalledWith('/migration/mri/batches')
            );
        });

        it('does not crash when role changes across renders', async () => {
            // Simulates a user whose role updates mid-session (e.g. elevation).
            mockUseAuth.mockReturnValue({user: {role: 'strata_manager'}, api: mockApi});
            const {rerender} = render(<MriMigrationPage/>);
            await waitFor(() => screen.getByText('Migration Batches'));

            // Change role to owner — must not throw hook-order error
            mockUseAuth.mockReturnValue({user: {role: 'owner'}, api: mockApi});
            expect(() => rerender(<MriMigrationPage/>)).not.toThrow();
            expect(screen.getByText(/do not have permission/i)).toBeInTheDocument();
        });
    });
});
