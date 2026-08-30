// @featuretrace:error-recovery-framework — App Router component crash recovery page.
// Layer: frontend
// Data flow: failed render/dynamic import -> error.tsx -> RecoveryPanel retry/dashboard actions (global).
// Related: frontend/src/app/global-error.tsx
//          frontend/src/components/shared/RecoveryPanel.tsx

"use client";

import { useEffect } from "react";
import RecoveryPanel from "@/components/shared/RecoveryPanel";
import { logClientRecoveryEvent } from "@/lib/api-error";
/**
 * @generated FunctionHeader
 * Function: Error
 * Path: frontend/src/app/error.tsx
 *
 * @remarks Generated inventory header. Replace or expand this with reviewed business-purpose documentation before relying on it as source commentary.
 */
/** Session key for the one-shot reload guard. Session-scoped so a genuinely
 *  broken chunk cannot put the tab into a refresh loop across restarts. */
const CHUNK_RELOAD_KEY = "chunkReloadAttemptedAt";

/**
 * A ChunkLoadError almost always means the CLIENT is stale, not that the page is
 * broken.
 *
 * Next.js fingerprints each JS chunk. When the server is redeployed the old
 * hashes stop existing, so any tab still holding the previous build asks for a
 * chunk that 404s and React unmounts the route — which surfaced to the user as
 * "component_crash" on /reports, with no hint that the remedy is simply to
 * reload.
 *
 * Detected by name rather than by message text, because the message is not
 * stable across Next versions while the `ChunkLoadError` name is.
 */
function isStaleChunkError(error: Error & { digest?: string }): boolean {
  return (
    error?.name === "ChunkLoadError" ||
    /Loading chunk .* failed|Failed to load chunk|Importing a module script failed/i.test(
      error?.message || "",
    )
  );
}

export default function Error({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  const staleChunk = isStaleChunkError(error);

  useEffect(() => {
    if (!staleChunk) return;
    // Reload ONCE per session. If the reload does not fix it the chunk is
    // genuinely missing, and looping would trap the user in a blank refreshing
    // tab with no way to read the error — worse than the crash screen.
    let alreadyTried = true;
    try {
      alreadyTried = Boolean(window.sessionStorage.getItem(CHUNK_RELOAD_KEY));
      if (!alreadyTried) {
        window.sessionStorage.setItem(CHUNK_RELOAD_KEY, String(Date.now()));
      }
    } catch {
      // sessionStorage can throw in private mode or with site data blocked.
      // Treat that as "already tried" so we show the panel instead of risking
      // a loop we cannot detect.
      alreadyTried = true;
    }
    if (!alreadyTried) {
      window.location.reload();
    }
  }, [staleChunk]);

  useEffect(() => {
    logClientRecoveryEvent(
      {
        category: "server_error",
        message: staleChunk ? "Stale build chunk" : "Component crash",
        technicalCode: error.digest || error.name || "COMPONENT_CRASH",
        retryable: true,
        suggestedAction: staleChunk
          ? "The app was updated. Reload to fetch the new version."
          : "Retry the page or return to your dashboard.",
      },
      { surface: "app-error-boundary" },
    );
  }, [error, staleChunk]);

  if (staleChunk) {
    return (
      <RecoveryPanel
        variant="server_error"
        title="A new version is available"
        message="This tab was running an older build, so part of the page could not be downloaded. Reloading will pick up the new version."
        category="stale_build"
        requestId={error.digest}
        retryable
        onRetry={() => window.location.reload()}
        testId="app-stale-chunk-recovery"
      />
    );
  }

  return (
    <RecoveryPanel
      variant="server_error"
      title="This page could not load"
      message="The page failed to render. You can retry safely or return to your dashboard."
      category="component_crash"
      requestId={error.digest}
      retryable
      onRetry={reset}
      testId="app-error-recovery"
    />
  );
}
