// @featuretrace:error-recovery-framework — Global fallback for unrecoverable App Router failures.
// Layer: frontend
// Data flow: root render failure -> global-error.tsx -> safe HTML recovery actions (global).
// Related: frontend/src/app/error.tsx
//          docs/architecture/error-recovery-framework.md

"use client";
/**
 * @generated FunctionHeader
 * Function: GlobalError
 * Path: frontend/src/app/global-error.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body>
        <main style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 24, fontFamily: "sans-serif" }}>
          <section style={{ maxWidth: 680, border: "1px solid #d1d5db", borderRadius: 8, padding: 24 }}>
            <p style={{ margin: 0, fontSize: 12, textTransform: "uppercase", color: "#64748b", fontWeight: 700 }}>
              server_error
            </p>
            <h1 style={{ margin: "8px 0 0", fontSize: 28 }}>East Gate could not load</h1>
            <p style={{ color: "#334155", lineHeight: 1.6 }}>
              A critical page error occurred. Sensitive technical details are hidden.
            </p>
            {error.digest ? (
              <p style={{ color: "#475569", fontFamily: "monospace", fontSize: 12 }}>Request ID: {error.digest}</p>
            ) : null}
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 20 }}>
              <button type="button" onClick={reset}>Retry</button>
              <a href="/dashboard">Go to dashboard</a>
              <a href="/login">Sign in</a>
            </div>
          </section>
        </main>
      </body>
    </html>
  );
}
