/**
 * A function-scope refusal must reach the person it refused.
 *
 * @featuretrace:manager-function-scoping — frontend classification of the refusal.
 * Layer: test
 * Data flow: 403 MANAGER_FUNCTION_SCOPE -> classifyApiError() -> AuthContext toast (global).
 * Related: frontend/src/lib/api-error.ts
 *          frontend/src/contexts/AuthContext.tsx
 *          backend/utils/route_guards.py (require_manager_surface)
 *
 * The backend has always named the exact surface it refused. Until 2026-08-29 none of
 * the nine gated routers had a page that rendered a classified error, so the reason
 * reached the browser and died there: a narrowed levies manager opened an insurance
 * page, saw "failed to load", and had no way to learn why.
 */
import {
  classifyApiError,
  deniedSurface,
  isFunctionScopeDenied,
  MANAGER_FUNCTION_SCOPE_CODE,
} from "@/lib/api-error";

/** The envelope backend/utils/error_response.py actually produces for this refusal. */
const scopeRefusal = (surface = "whs") => ({
  response: {
    status: 403,
    data: {
      error: {
        code: MANAGER_FUNCTION_SCOPE_CODE,
        message: `Your appointment at this agency does not cover ${surface}.`,
        retryable: false,
        metadata: { surface, functions: ["LEVIES_MANAGER"] },
      },
    },
  },
});

describe("function-scope refusals are classified apart from plain 403s", () => {
  it("does not collapse into 'forbidden'", () => {
    // Both arrive as HTTP 403. Classifying on status alone would discard the reason,
    // and the recoveries differ: "forbidden" means go away, this means ask your
    // agency to widen your appointment.
    expect(classifyApiError(scopeRefusal()).category).toBe("function_scope_denied");
    expect(
      classifyApiError({ response: { status: 403, data: {} } }).category,
    ).toBe("forbidden");
  });

  it("keeps the backend's wording, which names the surface", () => {
    const classified = classifyApiError(scopeRefusal("invoices"));
    expect(classified.message).toBe(
      "Your appointment at this agency does not cover invoices.",
    );
    // The generic fallback must never win over a message the backend chose.
    expect(classified.message).not.toBe(
      "Your appointment at this agency does not cover this area.",
    );
  });

  it("offers a recovery the person can actually act on", () => {
    expect(classifyApiError(scopeRefusal()).suggestedAction).toMatch(/widen your appointment/i);
  });

  it("is not retryable — retrying cannot change an appointment", () => {
    expect(classifyApiError(scopeRefusal()).retryable).toBe(false);
  });

  it("carries the surface and functions through for the UI", () => {
    const classified = classifyApiError(scopeRefusal("compliance"));
    expect(classified.metadata).toEqual({
      surface: "compliance",
      functions: ["LEVIES_MANAGER"],
    });
  });
});

describe("the helpers pages should use instead of comparing strings", () => {
  it("recognises the refusal from a raw axios error", () => {
    expect(isFunctionScopeDenied(scopeRefusal())).toBe(true);
  });

  it("recognises it from an already-classified error", () => {
    expect(isFunctionScopeDenied(classifyApiError(scopeRefusal()))).toBe(true);
  });

  it("does not fire on an ordinary 403", () => {
    expect(isFunctionScopeDenied({ response: { status: 403, data: {} } })).toBe(false);
    expect(isFunctionScopeDenied(undefined)).toBe(false);
  });

  it("extracts the surface from either shape", () => {
    expect(deniedSurface(scopeRefusal("defects"))).toBe("defects");
    expect(deniedSurface(classifyApiError(scopeRefusal("levies")))).toBe("levies");
  });

  it("returns undefined rather than guessing when no surface was named", () => {
    expect(deniedSurface({ response: { status: 403, data: {} } })).toBeUndefined();
  });
});

describe("the toast key", () => {
  it("dedupes per surface, so parallel requests do not stack toasts", () => {
    // One page load can fire several requests into the same denied router. The
    // interceptor reuses `manager-function-scope:<surface>` as the sonner id so the
    // later toasts replace the first rather than piling up.
    const surfaces = [scopeRefusal("whs"), scopeRefusal("whs"), scopeRefusal("insurance")]
      .map((e) => `manager-function-scope:${deniedSurface(e)}`);
    expect(new Set(surfaces).size).toBe(2);
  });
});

describe("the interceptor branch that surfaces it", () => {
  // The tests above prove the DECISION. They cannot prove the interceptor still calls
  // it: that branch lives inside a useMemo in AuthContext, behind Next.js and provider
  // setup that a unit test should not have to stand up.
  //
  // So this is a static guard, the same shape as the AST check that caught the
  // FORCE-RLS bug on the backend. It cannot verify the toast renders — a browser test
  // would be needed for that — but it does catch the realistic regression: someone
  // tidying the interceptor and removing the only place this refusal becomes visible.
  const fs = require("fs") as typeof import("fs");
  const path = require("path") as typeof import("path");
  const source = fs.readFileSync(
    path.join(__dirname, "../../../frontend/src/contexts/AuthContext.tsx"),
    "utf8",
  );

  it("still branches on the classified category, not on a raw status or literal", () => {
    expect(source).toContain("function_scope_denied");
    // A status check would collapse this with every other 403; a literal check would
    // duplicate the code that canonical_owners.yaml makes lib/api-error.ts own.
    expect(source).not.toContain('"MANAGER_FUNCTION_SCOPE"');
  });

  it("still raises it to the user", () => {
    const branch = source.slice(source.indexOf("function_scope_denied"));
    expect(branch).toContain("toast.error");
  });

  it("still dedupes by surface, so parallel requests collapse to one toast", () => {
    const branch = source.slice(source.indexOf("function_scope_denied"));
    expect(branch).toContain("manager-function-scope:");
    expect(branch).toContain("deniedSurface(");
  });
});
