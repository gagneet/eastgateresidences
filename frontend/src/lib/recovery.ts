// @featuretrace:error-recovery-framework — Role-aware recovery destinations for error pages.
// Layer: frontend
// Data flow: AuthContext user role -> getRoleAwareDashboardPath() -> recovery page actions (global).
// Related: frontend/src/components/shared/RecoveryPanel.tsx
//          frontend/src/app/not-found.tsx
//          docs/architecture/error-recovery-framework.md

export type RecoveryUser = {
  role?: string | null;
  ec_position?: string | null;
} | null | undefined;
/**
 * @generated FunctionHeader
 * Function: getRoleAwareDashboardPath
 * Path: frontend/src/lib/recovery.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function getRoleAwareDashboardPath(_user: RecoveryUser): string {
  // /dashboard handles role-aware routing internally; always land there so
  // the 404 recovery button never sends users to a role-specific sub-path
  // they may not recognise (e.g. /admin/console after a bad link).
  return "/dashboard";
}
/**
 * @generated FunctionHeader
 * Function: supportContact
 * Path: frontend/src/lib/recovery.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function supportContact(): string | null {
  return process.env.NEXT_PUBLIC_SUPPORT_EMAIL || process.env.NEXT_PUBLIC_CONTACT_EMAIL || null;
}
