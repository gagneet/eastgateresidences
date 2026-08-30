import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom';
import userEvent from '@testing-library/user-event';

// ── Mocks ────────────────────────────────────────────────────────────────────

const mockApi = {get: jest.fn(), post: jest.fn(), delete: jest.fn()};
const mockRouter = {replace: jest.fn()};

jest.mock('next/navigation', () => ( {useRouter: () => mockRouter} ));
const mockUseAuth = jest.fn(() => ( {
    api: mockApi,
    isManager: () => true,
    loading: false,
    user: {role: 'strata_manager'},
} ));

jest.mock('@/contexts/AuthContext', () => ( {
    useAuth: (...args) => mockUseAuth(...args),
} ));
jest.mock('sonner', () => ( {toast: {success: jest.fn(), error: jest.fn()}} ));
jest.mock('@/components/invoices/InvoiceUploadModal', () =>
    function MockModal({open, onClose}) {
        return open ? <div data-testid="upload-modal">
            <button onClick={onClose}>close</button>
        </div> : null;
    }
);

// ── Fixtures ─────────────────────────────────────────────────────────────────

const PENDING_INVOICE = {
    id: 'inv-001', status: 'pending_review', document_type: 'invoice',
    file_name: 'plumbing.pdf', ocr_source: 'claude_vision', ocr_confidence: 0.92,
    receipt_generated: false,
    parsed_data: {vendor_name: 'ABC Plumbing', invoice_number: 'INV-001', total: 495.00, invoice_date: '2026-04-15'},
    confirmed_data: null,
};

const CONFIRMED_INVOICE = {
    id: 'inv-002', status: 'confirmed', document_type: 'invoice',
    file_name: 'electrician.pdf', ocr_source: 'mindee', ocr_confidence: 0.80,
    receipt_generated: true,
    parsed_data: null,
    confirmed_data: {
        vendor_name: 'Fast Electrics',
        invoice_number: 'INV-002',
        total: 330.00,
        invoice_date: '2026-04-10'
    },
};

// ── Imports ───────────────────────────────────────────────────────────────────

import InvoicesPage from '@/pages/dashboard/InvoicesPage';

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('InvoicesPage', () => {
    beforeEach(() => {
        jest.clearAllMocks();
        mockApi.get.mockResolvedValue({data: {invoices: [PENDING_INVOICE, CONFIRMED_INVOICE], total: 2}});
    });

    it('renders page heading and upload buttons', async () => {
        render(<InvoicesPage/>);
        await waitFor(() => expect(screen.getByText('Invoices & Quotes')).toBeInTheDocument());
        expect(screen.getByTestId('upload-invoice-btn')).toBeInTheDocument();
        expect(screen.getByTestId('upload-quote-btn')).toBeInTheDocument();
    });

    it('shows summary stats correctly', async () => {
        render(<InvoicesPage/>);
        await waitFor(() => expect(screen.getByTestId('invoices-table')).toBeInTheDocument());
        expect(screen.getAllByText('2').length).toBeGreaterThan(0); // total uploaded
        expect(screen.getAllByText('1').length).toBeGreaterThan(0); // pending count
    });

    it('renders both invoices in the table', async () => {
        render(<InvoicesPage/>);
        await waitFor(() => expect(screen.getByTestId('invoices-table')).toBeInTheDocument());
        expect(screen.getByText('ABC Plumbing')).toBeInTheDocument();
        expect(screen.getByText('Fast Electrics')).toBeInTheDocument();
    });

    it('shows status badges correctly', async () => {
        render(<InvoicesPage/>);
        await waitFor(() => screen.getByTestId('invoices-table'));
        // Both labels appear in badge + filter dropdown — assert at least one
        expect(screen.getAllByText('Pending Review').length).toBeGreaterThan(0);
        expect(screen.getAllByText('Confirmed').length).toBeGreaterThan(0);
    });

    it('shows OCR source badges', async () => {
        render(<InvoicesPage/>);
        await waitFor(() => screen.getByTestId('invoices-table'));
        expect(screen.getByText('Claude Vision')).toBeInTheDocument();
        expect(screen.getByText('Mindee')).toBeInTheDocument();
    });

    it('opens upload modal when Upload Invoice is clicked', async () => {
        render(<InvoicesPage/>);
        await waitFor(() => screen.getByTestId('upload-invoice-btn'));
        await userEvent.click(screen.getByTestId('upload-invoice-btn'));
        expect(screen.getByTestId('upload-modal')).toBeInTheDocument();
    });

    it('closes modal on close callback', async () => {
        render(<InvoicesPage/>);
        await waitFor(() => screen.getByTestId('upload-invoice-btn'));
        await userEvent.click(screen.getByTestId('upload-invoice-btn'));
        expect(screen.getByTestId('upload-modal')).toBeInTheDocument();
        await userEvent.click(screen.getByText('close'));
        expect(screen.queryByTestId('upload-modal')).not.toBeInTheDocument();
    });

    it('shows empty state when no invoices', async () => {
        mockApi.get.mockResolvedValue({data: {invoices: [], total: 0}});
        render(<InvoicesPage/>);
        await waitFor(() => expect(screen.getByTestId('empty-state')).toBeInTheDocument());
        expect(screen.getByText('No invoices yet')).toBeInTheDocument();
    });

    it('filters by search text', async () => {
        render(<InvoicesPage/>);
        await waitFor(() => screen.getByTestId('invoices-table'));
        await userEvent.type(screen.getByTestId('search-input'), 'Fast');
        expect(screen.queryByText('ABC Plumbing')).not.toBeInTheDocument();
        expect(screen.getByText('Fast Electrics')).toBeInTheDocument();
    });

    it('calls delete API and removes row for pending invoice', async () => {
        const confirmSpy = jest.spyOn(window, 'confirm').mockReturnValue(true);
        mockApi.delete.mockResolvedValue({});
        render(<InvoicesPage/>);
        await waitFor(() => screen.getByTestId('delete-inv-001'));
        await userEvent.click(screen.getByTestId('delete-inv-001'));
        await waitFor(() => expect(mockApi.delete).toHaveBeenCalledWith('/invoices/inv-001'));
        expect(screen.queryByText('ABC Plumbing')).not.toBeInTheDocument();
        confirmSpy.mockRestore();
    });

    it('returns null and redirects when user is not a manager', async () => {
        mockUseAuth.mockReturnValueOnce({
            api: mockApi,
            isManager: () => false,
            loading: false,
            user: {role: 'tenant'},
        });
        const {container} = render(<InvoicesPage/>);
        await waitFor(() => expect(mockRouter.replace).toHaveBeenCalledWith('/dashboard'));
        expect(container.firstChild).toBeNull();
    });
});
