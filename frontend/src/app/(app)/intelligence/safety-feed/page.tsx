// @featuretrace:safety-feed — Route wrapper for the building safety events log.
// Layer: frontend
// Data flow: /intelligence/safety-feed -> SafetyFeedPage -> GET /safety/events
//            -> safety_events (MongoDB, building-scoped).
// Related: backend/routers/safety.py
//          frontend/src/pages/dashboard/SafetyFeedPage.tsx

"use client";

// This route rendered an unconditional feature-disabled shell: it never
// consulted the toggle, so the page reported "feature disabled" even though
// safety_feed seeds enabled and backend/routers/safety.py has been implemented
// and registered the whole time. The real page now reads the toggle itself and
// shows the disabled state only when it is genuinely off.
import SafetyFeedPage from "@/pages/dashboard/SafetyFeedPage";

export default function Page() {
    return <SafetyFeedPage/>;
}
