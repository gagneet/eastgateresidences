// @featuretrace:error-recovery-framework — Frontend API error classifier and safe messages.
// Layer: frontend
// Data flow: axios/fetch failure -> classifyApiError() -> APIErrorMessage/RecoveryPanel (global).
// Related: frontend/src/contexts/AuthContext.tsx
//          frontend/src/components/shared/APIErrorMessage.tsx
//          backend/utils/error_response.py

export type ApiErrorCategory =
  | "unauthenticated"
  | "forbidden"
  | "not_found"
  | "validation"
  | "conflict"
  | "rate_limited"
  | "server_error"
  | "service_unavailable"
  | "network_error"
  | "feature_disabled"
  // A 403 the ROLE would have allowed, refused because the signed-in manager's
  // APPOINTMENT does not cover this part of the product. Separated from plain
  // "forbidden" because the recovery is completely different: "forbidden" means
  // go away, this one means ask your agency to widen your appointment - and the
  // backend already names the exact surface, which a generic 403 throws away.
  | "function_scope_denied"
  | "unknown";

export type ClassifiedApiError = {
  category: ApiErrorCategory;
  status?: number;
  message: string;
  technicalCode: string;
  requestId?: string;
  retryable: boolean;
  suggestedAction: string;
  /** Domain fields the backend attached to the error (e.g. unit_number). */
  metadata?: Record<string, any>;
  method?: string;
  path?: string;
};

/**
 * The backend code for "your appointment does not cover this".
 *
 * Emitted by `utils/route_guards.require_manager_surface`. Declared here, once, so
 * no page compares the literal itself - the same reason role checks go through
 * AuthContext helpers instead of `user.role === "ec_member"`.
 */
export const MANAGER_FUNCTION_SCOPE_CODE = "MANAGER_FUNCTION_SCOPE";

/** True when a refusal was about the manager's FUNCTION, not their role. */
export function isFunctionScopeDenied(error: any): boolean {
  const code = error?.response?.data?.error?.code ?? error?.technicalCode;
  return code === MANAGER_FUNCTION_SCOPE_CODE;
}

/** The surface that was refused ("whs", "invoices"), when the backend named one. */
export function deniedSurface(error: any): string | undefined {
  const meta = error?.response?.data?.error?.metadata ?? error?.metadata;
  return typeof meta?.surface === "string" ? meta.surface : undefined;
}

const STATUS_CATEGORY: Record<number, ApiErrorCategory> = {
  400: "validation",
  401: "unauthenticated",
  403: "forbidden",
  404: "not_found",
  409: "conflict",
  422: "validation",
  429: "rate_limited",
  500: "server_error",
  502: "service_unavailable",
  503: "service_unavailable",
};

const DEFAULT_MESSAGES: Record<ApiErrorCategory, string> = {
  unauthenticated: "Please sign in to continue.",
  forbidden: "You do not have access to this area.",
  not_found: "We could not find that page or API route.",
  validation: "Some required information is missing or invalid.",
  conflict: "This request conflicts with the latest record state.",
  rate_limited: "Too many requests. Please wait a moment and try again.",
  server_error: "Something went wrong on our side.",
  service_unavailable: "The service is temporarily unavailable.",
  network_error: "We could not reach StrataOS. Check your connection and try again.",
  feature_disabled: "This feature is not available for your building yet.",
  // Only a fallback. The backend sends a message naming the surface
  // ("...does not cover whs."), and classifyApiError prefers it.
  function_scope_denied: "Your appointment at this agency does not cover this area.",
  unknown: "Something went wrong.",
};

const SUGGESTED_ACTIONS: Record<ApiErrorCategory, string> = {
  unauthenticated: "Sign in again.",
  forbidden: "Go back or return to your dashboard.",
  not_found: "Check the link or return to your dashboard.",
  validation: "Review the highlighted details and try again.",
  conflict: "Refresh the page and try again.",
  rate_limited: "Wait a moment before retrying.",
  server_error: "Retry, or contact support if it keeps happening.",
  service_unavailable: "Retry in a few minutes.",
  network_error: "Check your network connection and retry.",
  feature_disabled: "Return to your dashboard or ask an administrator about access.",
  function_scope_denied:
    "Ask your strata management agency to widen your appointment if you need this.",
  unknown: "Retry, or return to your dashboard.",
};
/**
 * @generated FunctionHeader
 * Function: responseHeader
 * Path: frontend/src/lib/api-error.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
function responseHeader(error: any, name: string): string | undefined {
  const headers = error?.response?.headers || {};
  return headers[name] || headers[name.toLowerCase()] || headers[name.toUpperCase()];
}
/**
 * @generated FunctionHeader
 * Function: classifyApiError
 * Path: frontend/src/lib/api-error.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function classifyApiError(error: any): ClassifiedApiError {
  const status = error?.response?.status;
  const envelope = error?.response?.data?.error;
  const technicalCode = envelope?.code || error?.code || (status ? `HTTP_${status}` : "NETWORK_ERROR");
  // A typed code beats the HTTP status: both of these arrive as 403, and the
  // status alone would collapse them into "forbidden" and discard the reason.
  const category: ApiErrorCategory =
    technicalCode === "FEATURE_DISABLED"
      ? "feature_disabled"
      : technicalCode === MANAGER_FUNCTION_SCOPE_CODE
        ? "function_scope_denied"
        : status
          ? STATUS_CATEGORY[status] || "unknown"
          : "network_error";
  const retryable = typeof envelope?.retryable === "boolean"
    ? envelope.retryable
    : category === "network_error" || ["rate_limited", "server_error", "service_unavailable"].includes(category);

  return {
    category,
    status,
    message: envelope?.message || error?.rateLimitMessage || error?.response?.data?.detail || DEFAULT_MESSAGES[category],
    technicalCode,
    requestId: envelope?.request_id || responseHeader(error, "x-request-id") || responseHeader(error, "x-correlation-id"),
    retryable,
    suggestedAction: SUGGESTED_ACTIONS[category],
    metadata: envelope?.metadata,
    method: error?.config?.method?.toUpperCase(),
    path: error?.config?.url,
  };
}
/**
 * @generated FunctionHeader
 * Function: isClassifiedApiError
 * Path: frontend/src/lib/api-error.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function isClassifiedApiError(error: any): error is ClassifiedApiError {
  return Boolean(error?.category && error?.technicalCode && error?.message);
}
/**
 * @generated FunctionHeader
 * Function: logClientRecoveryEvent
 * Path: frontend/src/lib/api-error.ts
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export function logClientRecoveryEvent(error: ClassifiedApiError, context: Record<string, unknown> = {}) {
  if (typeof window === "undefined") return;
  const event = {
    ...context,
    category: error.category,
    status: error.status,
    technicalCode: error.technicalCode,
    requestId: error.requestId,
    route: window.location.pathname,
    timestamp: new Date().toISOString(),
  };
  window.dispatchEvent(new CustomEvent("strataos:recovery", { detail: event }));
  if (process.env.NODE_ENV !== "production") {
    console.info("StrataOS recovery event", event);
  }
}

export type ApiErrorDetail = {
  /** Domain code, e.g. "already_registered". Empty when the API sent a bare string. */
  code: string;
  /** Human-facing message chosen by the backend, when it supplied one. */
  message: string;
  /** Extra domain fields, e.g. { unit_number: "TH086" }. */
  metadata: Record<string, any>;
};

/**
 * Read a domain error the same way regardless of which envelope the API used.
 *
 * `backend/utils/error_response.py` rewraps a FastAPI `detail` dict into
 * `{ error: { code, message, metadata } }`, hoisting `code`/`message` and pushing every
 * remaining key into `metadata`. Call sites written against the older `detail` shape read
 * `data.detail.code`, get `undefined`, and silently fall through to a generic failure
 * message — which is how five hand-written registration conflict messages (and the
 * "Reset password" banner that goes with them) went dead in the UI while the backend was
 * returning them correctly the whole time.
 *
 * Both shapes are accepted so this keeps working whichever envelope a route uses.
 */
export function getApiErrorDetail(error: any): ApiErrorDetail {
  const data = error?.response?.data;
  const envelope = data?.error;
  if (envelope && typeof envelope === "object") {
    return {
      code: envelope.code || "",
      message: envelope.message || "",
      metadata: envelope.metadata || {},
    };
  }
  const detail = data?.detail;

  // FastAPI request-validation errors (HTTP 422) send `detail` as an ARRAY of
  // { type, loc, msg, input } entries — not the object shape above. `typeof [] === "object"`
  // in JS, so the object branch below used to destructure the array, yield
  // code/message === undefined, and hand every call site an empty message. The caller then
  // fell back to its own generic string and the actual reason never reached the user.
  //
  // Observed 2026-08-28: PUT /api/meetings/{id} returned 422 "List should have at most 50
  // items after validation, not 80" for an over-long agenda, and the page showed only
  // "Failed to update meeting" — leaving no way to know the agenda was the problem.
  //
  // `loc` is ["body", "<field>", <index>?]; we drop the leading "body" and join the rest so
  // the field name leads the message, which is the part a person can act on.
  if (Array.isArray(detail)) {
    const parts = detail
      .map((entry: any) => {
        const path = Array.isArray(entry?.loc)
          ? entry.loc.filter((seg: any) => seg !== "body").join(".")
          : "";
        const msg = entry?.msg || entry?.type || "is invalid";
        return path ? `${path}: ${msg}` : msg;
      })
      .filter(Boolean);
    return {
      code: "validation_error",
      // Cap the joined text: a list-length failure emits one entry per bad item, and a
      // toast rendering 80 of them is no more useful than rendering none.
      message: parts.slice(0, 3).join("; ")
        + (parts.length > 3 ? ` (and ${parts.length - 3} more)` : ""),
      metadata: { errors: detail },
    };
  }

  if (detail && typeof detail === "object") {
    const { code, message, ...rest } = detail;
    return { code: code || "", message: message || "", metadata: rest || {} };
  }
  return { code: "", message: typeof detail === "string" ? detail : "", metadata: {} };
}
