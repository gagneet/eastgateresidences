// @featuretrace:smart-request — Leaving a single request returns to the queue, not the catalogue.
// Layer: test
// Data flow: /requests/{id} back link -> /requests?tab=my-requests (building-scoped).
// Related: frontend/src/pages/dashboard/RequestStatusPage.jsx
//          frontend/src/pages/dashboard/RequestsPage.jsx
/**
 * RequestsPage treats a missing ?tab= as the request-DEFINITIONS view — the "create a
 * request" catalogue. So every back link from a single request sent the user to a page
 * for starting a new request rather than to the queue they had been reading, with no
 * obvious route back to it.
 *
 * Asserted against the source rather than by rendering: the page needs a live request
 * fetch, an auth context and a router to mount, and the property under test is simply
 * which href these three links carry.
 */
import fs from "fs";
import path from "path";

const SOURCE = fs.readFileSync(
  path.join(__dirname, "../../../../frontend/src/pages/dashboard/RequestStatusPage.jsx"),
  "utf8",
);

describe("RequestStatusPage back links", () => {
  it("no link points at bare /requests", () => {
    expect(SOURCE).not.toContain('<Link href="/requests">');
  });

  it("the queue href names the my-requests tab", () => {
    expect(SOURCE).toContain('const REQUESTS_QUEUE_HREF = "/requests?tab=my-requests"');
  });

  it("every back link uses the shared constant", () => {
    const uses = SOURCE.match(/<Link href=\{REQUESTS_QUEUE_HREF\}>/g) || [];
    // "All Requests", "Back to Requests" on the error state, and "View all requests"
    // after closing one — all three lead away from a single request.
    expect(uses).toHaveLength(3);
  });

  it("keeps them on one constant so a future link cannot drift", () => {
    const declarations = SOURCE.match(/REQUESTS_QUEUE_HREF\s*=/g) || [];
    expect(declarations).toHaveLength(1);
  });
});
