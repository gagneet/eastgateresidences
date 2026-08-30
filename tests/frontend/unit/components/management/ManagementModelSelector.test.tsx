/**
 * Tests for ManagementModelSelector component.
 *
 * Part H — Frontend RTL tests (management hierarchy hardening sprint)
 *
 * Coverage:
 *  1.  Renders all four management mode options
 *  2.  agency_managed shows Agency ID input and entity name override input
 *  3.  self_managed shows optional entity name input
 *  4.  independent_manager shows optional entity name input
 *  5.  unknown (Configure Later) hides all extra fields
 *  6.  Save button disabled when no mode selected
 *  7.  Save button enabled after selecting a mode
 *  8.  Successful save calls POST /management-hierarchy/schemes/{schemeId}/ensure
 *  9.  Success state shown after save (created=true)
 *  10. Existing-hierarchy state shown (created=false)
 *  11. API error is displayed to the user
 *  12. Loading/disabled state during save
 *  13. agency_managed send source_agency_id when Agency ID filled
 *  14. onSaved callback invoked with result
 *  15. ManagementModelCard wraps selector with title
 */

import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import '@testing-library/jest-dom';

// ── UI mocks ──────────────────────────────────────────────────────────────────
jest.mock('@/components/ui/card', () => ({
  Card: ({ children, ...props }: any) => <div data-testid="card" {...props}>{children}</div>,
  CardContent: ({ children }: any) => <div>{children}</div>,
  CardHeader: ({ children }: any) => <div>{children}</div>,
  CardTitle: ({ children }: any) => <h2>{children}</h2>,
}));
jest.mock('@/components/ui/badge', () => ({
  Badge: ({ children, variant }: any) => <span data-variant={variant}>{children}</span>,
}));
jest.mock('@/components/ui/button', () => ({
  Button: ({ children, disabled, onClick, ...props }: any) => (
    <button disabled={disabled} onClick={onClick} {...props}>{children}</button>
  ),
}));
jest.mock('@/components/ui/input', () => ({
  Input: ({ value, onChange, placeholder, id, ...props }: any) => (
    <input id={id} value={value} onChange={onChange} placeholder={placeholder} {...props} />
  ),
}));
jest.mock('@/components/ui/label', () => ({
  Label: ({ children, htmlFor }: any) => <label htmlFor={htmlFor}>{children}</label>,
}));
jest.mock('@/components/ui/alert', () => ({
  Alert: ({ children, variant }: any) => (
    <div data-testid="alert" data-variant={variant}>{children}</div>
  ),
  AlertDescription: ({ children }: any) => <div data-testid="alert-desc">{children}</div>,
}));

// ── AuthContext mock ──────────────────────────────────────────────────────────
const mockPost = jest.fn();
jest.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    api: { post: mockPost },
  }),
}));

// ── Import under test ─────────────────────────────────────────────────────────
import {
  ManagementModelSelector,
  ManagementModelCard,
} from '@/components/management/ManagementModelSelector';

const SCHEME_ID = 'aaaaaaaa-0000-0000-0000-000000000001';
const BUILDING_ID = '13195';

const MOCK_RESULT = {
  scheme_id: SCHEME_ID,
  created: true,
  entity: {
    management_entity_id: 'bbbbbbbb-0000-0000-0000-000000000002',
    entity_type: 'self_managed_oc',
    name: 'Test OC',
    status: 'active',
    is_system_generated: true,
  },
  assignment: {
    assignment_id: 'cccccccc-0000-0000-0000-000000000003',
    assignment_type: 'self_managed',
    status: 'active',
  },
  warnings: [],
};

function renderSelector(props: Partial<React.ComponentProps<typeof ManagementModelSelector>> = {}) {
  return render(
    <ManagementModelSelector
      schemeId={SCHEME_ID}
      buildingId={BUILDING_ID}
      {...props}
    />
  );
}

beforeEach(() => {
  mockPost.mockReset();
});

// ── Test 1: Renders all four modes ────────────────────────────────────────────
describe('ManagementModelSelector — option rendering', () => {
  it('renders all four management mode options', () => {
    renderSelector();
    expect(screen.getByTestId('management-option-agency_managed')).toBeInTheDocument();
    expect(screen.getByTestId('management-option-self_managed')).toBeInTheDocument();
    expect(screen.getByTestId('management-option-independent_manager')).toBeInTheDocument();
    expect(screen.getByTestId('management-option-unknown')).toBeInTheDocument();
  });

  it('renders the selector container with test id', () => {
    renderSelector();
    expect(screen.getByTestId('management-model-selector')).toBeInTheDocument();
  });
});

// ── Test 2: agency_managed contextual fields ──────────────────────────────────
describe('ManagementModelSelector — agency_managed fields', () => {
  it('shows Agency ID input after selecting agency_managed', () => {
    renderSelector();
    fireEvent.click(screen.getByTestId('management-option-agency_managed'));
    expect(screen.getByTestId('agency-id-input')).toBeInTheDocument();
  });

  it('shows entity name override input for agency_managed', () => {
    renderSelector();
    fireEvent.click(screen.getByTestId('management-option-agency_managed'));
    expect(screen.getByTestId('entity-name-input')).toBeInTheDocument();
  });

  it('does not show agency fields when unknown is selected', () => {
    renderSelector();
    fireEvent.click(screen.getByTestId('management-option-unknown'));
    expect(screen.queryByTestId('agency-id-input')).not.toBeInTheDocument();
  });
});

// ── Test 3: self_managed contextual fields ────────────────────────────────────
describe('ManagementModelSelector — self_managed fields', () => {
  it('shows optional entity name input for self_managed', () => {
    renderSelector();
    fireEvent.click(screen.getByTestId('management-option-self_managed'));
    expect(screen.getByTestId('entity-name-sm-input')).toBeInTheDocument();
  });

  it('does not show self_managed fields when agency_managed is selected', () => {
    renderSelector();
    fireEvent.click(screen.getByTestId('management-option-agency_managed'));
    expect(screen.queryByTestId('entity-name-sm-input')).not.toBeInTheDocument();
  });
});

// ── Test 4: independent_manager contextual fields ─────────────────────────────
describe('ManagementModelSelector — independent_manager fields', () => {
  it('shows optional entity name input for independent_manager', () => {
    renderSelector();
    fireEvent.click(screen.getByTestId('management-option-independent_manager'));
    expect(screen.getByTestId('entity-name-ind-input')).toBeInTheDocument();
  });
});

// ── Test 5: unknown hides extra fields ────────────────────────────────────────
describe('ManagementModelSelector — unknown mode', () => {
  it('hides all extra fields for unknown mode', () => {
    renderSelector();
    fireEvent.click(screen.getByTestId('management-option-unknown'));
    expect(screen.queryByTestId('agency-id-input')).not.toBeInTheDocument();
    expect(screen.queryByTestId('entity-name-sm-input')).not.toBeInTheDocument();
    expect(screen.queryByTestId('entity-name-ind-input')).not.toBeInTheDocument();
  });
});

// ── Tests 6–7: Save button state ──────────────────────────────────────────────
describe('ManagementModelSelector — save button state', () => {
  it('save button is disabled when no mode is selected', () => {
    renderSelector();
    expect(screen.getByTestId('save-management-model-btn')).toBeDisabled();
  });

  it('save button is enabled after selecting a mode', () => {
    renderSelector();
    fireEvent.click(screen.getByTestId('management-option-self_managed'));
    expect(screen.getByTestId('save-management-model-btn')).not.toBeDisabled();
  });
});

// ── Test 8: Successful save calls correct API endpoint ────────────────────────
describe('ManagementModelSelector — save API call', () => {
  it('calls POST /management-hierarchy/schemes/{schemeId}/ensure on save', async () => {
    mockPost.mockResolvedValueOnce({ data: MOCK_RESULT });
    renderSelector();

    fireEvent.click(screen.getByTestId('management-option-self_managed'));
    fireEvent.click(screen.getByTestId('save-management-model-btn'));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        `/management-hierarchy/schemes/${SCHEME_ID}/ensure`,
        expect.objectContaining({ mode: 'self_managed', building_id: BUILDING_ID })
      );
    });
  });

  it('includes source_agency_id when agency_managed with agency ID filled', async () => {
    mockPost.mockResolvedValueOnce({
      data: { ...MOCK_RESULT, entity: { ...MOCK_RESULT.entity, entity_type: 'agency' } },
    });
    renderSelector();

    fireEvent.click(screen.getByTestId('management-option-agency_managed'));
    fireEvent.change(screen.getByTestId('agency-id-input'), {
      target: { value: 'dddddddd-0000-0000-0000-000000000004' },
    });
    fireEvent.click(screen.getByTestId('save-management-model-btn'));

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalledWith(
        expect.any(String),
        expect.objectContaining({
          mode: 'agency_managed',
          source_agency_id: 'dddddddd-0000-0000-0000-000000000004',
        })
      );
    });
  });
});

// ── Test 9–10: Success state ──────────────────────────────────────────────────
describe('ManagementModelSelector — success states', () => {
  it('shows "New hierarchy created" when created=true', async () => {
    mockPost.mockResolvedValueOnce({ data: { ...MOCK_RESULT, created: true } });
    renderSelector();
    fireEvent.click(screen.getByTestId('management-option-self_managed'));
    fireEvent.click(screen.getByTestId('save-management-model-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('alert-desc')).toHaveTextContent('New hierarchy created');
    });
  });

  it('shows "Existing hierarchy returned" when created=false', async () => {
    mockPost.mockResolvedValueOnce({ data: { ...MOCK_RESULT, created: false, warnings: [] } });
    renderSelector();
    fireEvent.click(screen.getByTestId('management-option-self_managed'));
    fireEvent.click(screen.getByTestId('save-management-model-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('alert-desc')).toHaveTextContent('Existing hierarchy returned');
    });
  });

  it('displays warnings returned by the API', async () => {
    mockPost.mockResolvedValueOnce({
      data: { ...MOCK_RESULT, warnings: ['Auto-created management entity'] },
    });
    renderSelector();
    fireEvent.click(screen.getByTestId('management-option-unknown'));
    fireEvent.click(screen.getByTestId('save-management-model-btn'));

    await waitFor(() => {
      expect(screen.getByText('Auto-created management entity')).toBeInTheDocument();
    });
  });
});

// ── Test 11: API error display ────────────────────────────────────────────────
describe('ManagementModelSelector — API error state', () => {
  it('displays the API error detail when save fails', async () => {
    mockPost.mockRejectedValueOnce({
      response: { data: { detail: 'strata_admin or super_admin required' } },
    });
    renderSelector();
    fireEvent.click(screen.getByTestId('management-option-self_managed'));
    fireEvent.click(screen.getByTestId('save-management-model-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('alert')).toBeInTheDocument();
      expect(screen.getByTestId('alert-desc')).toHaveTextContent(
        'strata_admin or super_admin required'
      );
    });
  });

  it('displays fallback message when API error has no detail', async () => {
    mockPost.mockRejectedValueOnce(new Error('Network error'));
    renderSelector();
    fireEvent.click(screen.getByTestId('management-option-self_managed'));
    fireEvent.click(screen.getByTestId('save-management-model-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('alert-desc')).toHaveTextContent('Failed to save management model');
    });
  });
});

// ── Test 12: Loading / disabled state ────────────────────────────────────────
describe('ManagementModelSelector — loading state', () => {
  it('shows Saving… text and disables button during save', async () => {
    let resolvePost!: (value: any) => void;
    const pendingPromise = new Promise((res) => { resolvePost = res; });
    mockPost.mockReturnValueOnce(pendingPromise);

    renderSelector();
    fireEvent.click(screen.getByTestId('management-option-self_managed'));

    act(() => {
      fireEvent.click(screen.getByTestId('save-management-model-btn'));
    });

    await waitFor(() => {
      expect(screen.getByTestId('save-management-model-btn')).toBeDisabled();
      expect(screen.getByTestId('save-management-model-btn')).toHaveTextContent('Saving…');
    });

    act(() => {
      resolvePost({ data: MOCK_RESULT });
    });

    await waitFor(() => {
      expect(screen.getByTestId('save-management-model-btn')).toHaveTextContent(
        'Save Management Model'
      );
    });
  });
});

// ── Test 14: onSaved callback ─────────────────────────────────────────────────
describe('ManagementModelSelector — onSaved callback', () => {
  it('calls onSaved with the result when save succeeds', async () => {
    mockPost.mockResolvedValueOnce({ data: MOCK_RESULT });
    const onSaved = jest.fn();

    renderSelector({ onSaved });
    fireEvent.click(screen.getByTestId('management-option-self_managed'));
    fireEvent.click(screen.getByTestId('save-management-model-btn'));

    await waitFor(() => {
      expect(onSaved).toHaveBeenCalledWith(MOCK_RESULT);
    });
  });

  it('does not call onSaved when save fails', async () => {
    mockPost.mockRejectedValueOnce({ response: { data: { detail: 'Forbidden' } } });
    const onSaved = jest.fn();

    renderSelector({ onSaved });
    fireEvent.click(screen.getByTestId('management-option-self_managed'));
    fireEvent.click(screen.getByTestId('save-management-model-btn'));

    await waitFor(() => {
      expect(screen.getByTestId('alert')).toBeInTheDocument();
    });
    expect(onSaved).not.toHaveBeenCalled();
  });
});

// ── Test 15: ManagementModelCard wrapper ──────────────────────────────────────
describe('ManagementModelCard', () => {
  it('renders the card wrapper with default title', () => {
    render(
      <ManagementModelCard schemeId={SCHEME_ID} buildingId={BUILDING_ID} />
    );
    expect(screen.getByTestId('management-model-card')).toBeInTheDocument();
    expect(screen.getByText('Management Model')).toBeInTheDocument();
  });

  it('renders the card with a custom title', () => {
    render(
      <ManagementModelCard
        schemeId={SCHEME_ID}
        buildingId={BUILDING_ID}
        title="Select Hierarchy Type"
      />
    );
    expect(screen.getByText('Select Hierarchy Type')).toBeInTheDocument();
  });

  it('renders the selector inside the card', () => {
    render(
      <ManagementModelCard schemeId={SCHEME_ID} buildingId={BUILDING_ID} />
    );
    expect(screen.getByTestId('management-model-selector')).toBeInTheDocument();
  });
});
