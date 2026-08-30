// @featuretrace:error-recovery-framework — Named recovery states for feature, permission, server, and network failures.
// Layer: frontend
// Data flow: page guard/component crash/API error -> named recovery state -> RecoveryPanel (global).
// Related: frontend/src/components/shared/RecoveryPanel.tsx
//          frontend/src/pages/powerhouse/PowerhouseConversationCenterPage.tsx

"use client";

import RecoveryPanel from "@/components/shared/RecoveryPanel";

type StateProps = {
  featureKey?: string;
  requestId?: string;
  onRetry?: () => void;
  testId?: string;
};
/**
 * @generated FunctionHeader
 * Function: FeatureDisabled
 * Path: frontend/src/components/shared/RecoveryStates.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function FeatureDisabled({ featureKey, testId = "feature-disabled-recovery" }: StateProps) {
  return (
    <RecoveryPanel
      variant="feature_disabled"
      title="This feature is not available yet"
      message="This area is controlled by a building-level feature setting. It is not available for your building right now."
      category="feature_disabled"
      featureKey={featureKey}
      featureStatus="Disabled for this building"
      testId={testId}
    />
  );
}
/**
 * @generated FunctionHeader
 * Function: UnavailableFeature
 * Path: frontend/src/components/shared/RecoveryStates.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function UnavailableFeature({ featureKey, testId = "unavailable-feature-recovery" }: StateProps) {
  return (
    <RecoveryPanel
      variant="feature_disabled"
      title="This feature is temporarily unavailable"
      message="The feature exists, but the supporting service or data source is not ready for this building."
      category="feature_unavailable"
      featureKey={featureKey}
      featureStatus="Unavailable"
      testId={testId}
    />
  );
}
/**
 * @generated FunctionHeader
 * Function: InternalPreview
 * Path: frontend/src/components/shared/RecoveryStates.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function InternalPreview({ featureKey, testId = "internal-preview-recovery" }: StateProps) {
  return (
    <RecoveryPanel
      variant="internal_preview"
      title="Internal preview"
      message="This shell is available for controlled internal review only. It is not ready for normal resident or committee workflows."
      category="internal_preview"
      featureKey={featureKey}
      featureStatus="Shell/internal preview"
      testId={testId}
    />
  );
}
/**
 * @generated FunctionHeader
 * Function: PermissionDenied
 * Path: frontend/src/components/shared/RecoveryStates.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function PermissionDenied({ requestId, testId = "permission-denied-recovery" }: StateProps) {
  return (
    <RecoveryPanel
      variant="permission_denied"
      title="You do not have access to this area"
      message="Your current role or building membership does not allow access to this page."
      category="forbidden"
      requestId={requestId}
      testId={testId}
    />
  );
}
/**
 * @generated FunctionHeader
 * Function: ServerError
 * Path: frontend/src/components/shared/RecoveryStates.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function ServerError({ requestId, onRetry, testId = "server-error-recovery" }: StateProps) {
  return (
    <RecoveryPanel
      variant="server_error"
      title="Something went wrong"
      message="StrataOS could not complete this request. No sensitive technical details are shown here."
      category="server_error"
      requestId={requestId}
      retryable
      onRetry={onRetry}
      testId={testId}
    />
  );
}
/**
 * @generated FunctionHeader
 * Function: NetworkError
 * Path: frontend/src/components/shared/RecoveryStates.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function NetworkError({ onRetry, testId = "network-error-recovery" }: StateProps) {
  return (
    <RecoveryPanel
      variant="network_error"
      title="We could not reach StrataOS"
      message="Check your network connection and retry. If the issue continues, return to your dashboard."
      category="network_error"
      retryable
      onRetry={onRetry}
      testId={testId}
    />
  );
}
