// @featuretrace:management-hierarchy — Management model selector for building settings/onboarding.
// Layer: frontend
// Data flow: BuildingSettings / OnboardingWizard →
//            POST /api/management-hierarchy/schemes/{id}/ensure (building-scoped)
// Related: backend/routers/management_hierarchy.py
//          backend/services/management_hierarchy_service.py
//          frontend/src/pages/dashboard/admin/BuildingOnboardingForm.jsx
// Toggle: management_hierarchy_enabled
'use client';

import React, { useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { useAuth } from '@/contexts/AuthContext';

export type ManagementMode =
  | 'agency_managed'
  | 'self_managed'
  | 'independent_manager'
  | 'unknown';

interface ManagementModelOption {
  mode: ManagementMode;
  label: string;
  description: string;
  badge: string;
  badgeVariant: 'default' | 'secondary' | 'outline';
}

const MANAGEMENT_OPTIONS: ManagementModelOption[] = [
  {
    mode: 'agency_managed',
    label: 'Agency Managed',
    description:
      'The Owners Corporation has appointed a licensed strata management agency. The agency assigns one or more strata managers.',
    badge: 'Professional',
    badgeVariant: 'default',
  },
  {
    mode: 'self_managed',
    label: 'Self-Managed Owners Corporation',
    description:
      'The Owners Corporation manages itself. EC members may appoint one internal person as strata or building manager.',
    badge: 'Self-Managed',
    badgeVariant: 'secondary',
  },
  {
    mode: 'independent_manager',
    label: 'Independent Strata Manager',
    description:
      'An individual contractor or sole trader manages one or more buildings directly without belonging to an agency.',
    badge: 'Independent',
    badgeVariant: 'outline',
  },
  {
    mode: 'unknown',
    label: 'Configure Later',
    description:
      'Management hierarchy is unknown or transitional. A placeholder record will be created; configure the model when details are available.',
    badge: 'Pending',
    badgeVariant: 'outline',
  },
];

interface ManagementModelSelectorProps {
  schemeId: string;
  buildingId?: string;
  currentMode?: ManagementMode | null;
  onSaved?: (result: ManagementHierarchyResult) => void;
  className?: string;
}

interface ManagementHierarchyResult {
  scheme_id: string;
  created: boolean;
  entity: {
    management_entity_id: string;
    entity_type: string;
    name: string;
    status: string;
    is_system_generated: boolean;
  };
  assignment: {
    assignment_id: string;
    assignment_type: string;
    status: string;
  };
  warnings: string[];
}
/**
 * @generated FunctionHeader
 * Function: ManagementModelSelector
 * Path: frontend/src/components/management/ManagementModelSelector.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function ManagementModelSelector({
  schemeId,
  buildingId,
  currentMode,
  onSaved,
  className,
}: ManagementModelSelectorProps) {
  const { api } = useAuth();
  const [selectedMode, setSelectedMode] = useState<ManagementMode | null>(
    currentMode ?? null
  );
  const [agencyId, setAgencyId] = useState('');
  const [entityNameHint, setEntityNameHint] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<ManagementHierarchyResult | null>(null);
  /**
   * @generated FunctionHeader
   * Function: handleSave
   * Path: frontend/src/components/management/ManagementModelSelector.tsx
   *
   * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
   */
  const handleSave = async () => {
    if (!selectedMode) return;
    setError(null);
    setSaving(true);
    try {
      const body: Record<string, unknown> = {
        mode: selectedMode,
        building_id: buildingId,
        entity_name_hint: entityNameHint || undefined,
      };
      if (selectedMode === 'agency_managed' && agencyId) {
        body.source_agency_id = agencyId;
      }
      const { data } = await api.post(
        `/management-hierarchy/schemes/${schemeId}/ensure`,
        body
      );
      setSaved(data);
      onSaved?.(data);
    } catch (err: any) {
      setError(
        err?.response?.data?.detail ??
          'Failed to save management model. Please try again.'
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className={className} data-testid="management-model-selector">
      <div className="space-y-3">
        {MANAGEMENT_OPTIONS.map((opt) => (
          <button
            key={opt.mode}
            type="button"
            data-testid={`management-option-${opt.mode}`}
            onClick={() => {
              setSelectedMode(opt.mode);
              setSaved(null);
              setError(null);
            }}
            className={[
              'w-full text-left rounded-lg border p-4 transition-all',
              selectedMode === opt.mode
                ? 'border-primary bg-primary/5 ring-1 ring-primary'
                : 'border-border hover:border-primary/40 hover:bg-muted/50',
            ].join(' ')}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="font-medium text-sm">{opt.label}</span>
                  <Badge variant={opt.badgeVariant} className="text-xs">
                    {opt.badge}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground leading-relaxed">
                  {opt.description}
                </p>
              </div>
              <div
                className={[
                  'mt-0.5 h-4 w-4 rounded-full border-2 flex-shrink-0 transition-colors',
                  selectedMode === opt.mode
                    ? 'border-primary bg-primary'
                    : 'border-muted-foreground/30',
                ].join(' ')}
              />
            </div>
          </button>
        ))}
      </div>

      {/* Contextual fields */}
      {selectedMode === 'agency_managed' && (
        <div className="mt-4 space-y-3 p-4 rounded-lg bg-muted/40 border border-border">
          <div>
            <Label htmlFor="agency-id" className="text-sm">
              Agency ID{' '}
              <span className="text-muted-foreground font-normal">(optional if already created)</span>
            </Label>
            <Input
              id="agency-id"
              data-testid="agency-id-input"
              value={agencyId}
              onChange={(e) => setAgencyId(e.target.value)}
              placeholder="UUID of existing agency in core.agencies"
              className="mt-1 text-sm"
            />
          </div>
          <div>
            <Label htmlFor="entity-name" className="text-sm">
              Override entity name{' '}
              <span className="text-muted-foreground font-normal">(optional)</span>
            </Label>
            <Input
              id="entity-name"
              data-testid="entity-name-input"
              value={entityNameHint}
              onChange={(e) => setEntityNameHint(e.target.value)}
              placeholder="Leave blank to use agency name"
              className="mt-1 text-sm"
            />
          </div>
        </div>
      )}

      {selectedMode === 'self_managed' && (
        <div className="mt-4 p-4 rounded-lg bg-muted/40 border border-border">
          <Label htmlFor="entity-name-sm" className="text-sm">
            Self-managed entity name{' '}
            <span className="text-muted-foreground font-normal">(optional)</span>
          </Label>
          <Input
            id="entity-name-sm"
            data-testid="entity-name-sm-input"
            value={entityNameHint}
            onChange={(e) => setEntityNameHint(e.target.value)}
            placeholder={`e.g. ${buildingId ?? 'Building'} Owners Corporation`}
            className="mt-1 text-sm"
          />
          <p className="text-xs text-muted-foreground mt-2">
            A Self-Managed OC entity will be created automatically. You can
            appoint an EC-internal strata manager separately after saving.
          </p>
        </div>
      )}

      {selectedMode === 'independent_manager' && (
        <div className="mt-4 p-4 rounded-lg bg-muted/40 border border-border">
          <Label htmlFor="entity-name-ind" className="text-sm">
            Manager / entity name{' '}
            <span className="text-muted-foreground font-normal">(optional)</span>
          </Label>
          <Input
            id="entity-name-ind"
            data-testid="entity-name-ind-input"
            value={entityNameHint}
            onChange={(e) => setEntityNameHint(e.target.value)}
            placeholder="e.g. Jane Smith Independent Strata Manager"
            className="mt-1 text-sm"
          />
        </div>
      )}

      {error && (
        <Alert variant="destructive" className="mt-4">
          <AlertDescription className="text-sm">{error}</AlertDescription>
        </Alert>
      )}

      {saved && !error && (
        <Alert className="mt-4 border-green-200 bg-green-50">
          <AlertDescription className="text-sm text-green-800">
            Management model saved.{' '}
            {saved.created ? 'New hierarchy created.' : 'Existing hierarchy returned.'}
            {saved.warnings.length > 0 && (
              <ul className="mt-1 list-disc list-inside">
                {saved.warnings.map((w, i) => (
                  <li key={i} className="text-xs text-amber-700">
                    {w}
                  </li>
                ))}
              </ul>
            )}
          </AlertDescription>
        </Alert>
      )}

      <div className="mt-4 flex justify-end">
        <Button
          data-testid="save-management-model-btn"
          onClick={handleSave}
          disabled={!selectedMode || saving}
          className="rounded-full active:scale-95"
        >
          {saving ? 'Saving…' : 'Save Management Model'}
        </Button>
      </div>
    </div>
  );
}

// ── Compact card wrapper for use inside building settings pages ───────────────

interface ManagementModelCardProps extends ManagementModelSelectorProps {
  title?: string;
}
/**
 * @generated FunctionHeader
 * Function: ManagementModelCard
 * Path: frontend/src/components/management/ManagementModelSelector.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function ManagementModelCard({
  title = 'Management Model',
  ...props
}: ManagementModelCardProps) {
  return (
    <Card data-testid="management-model-card">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <ManagementModelSelector {...props} />
      </CardContent>
    </Card>
  );
}
