/**
 * building-config.js
 *
 * Replaces [data-bldg] placeholder elements with live building settings fetched
 * from GET /api/settings.  Runs after DOMContentLoaded so auth-guard.js (which
 * redirects unauthenticated users to /login) always fires first.
 *
 * Tokens:
 *   data-bldg="name"          → settings.building_name
 *   data-bldg="address"       → settings.building_address
 *   data-bldg="contact_email" → settings.contact_email
 *   data-bldg="ec_email"      → settings.ec_email
 *   data-bldg="contact_phone" → settings.ec_contact_phone || settings.contact_phone
 *
 * For <a> elements the href is also updated:
 *   - email tokens  → href="mailto:<value>"
 *   - non-email     → href="<value>" (https:// prepended if bare domain)
 *
 * Fails silently — placeholder text remains visible if the API is unreachable.
 */
( function () {
    async function applyBuildingConfig() {
        try {
            // Resolve building_id from the NextAuth session (same cookie the
            // auth-guard already verified), then fetch settings for that building.
            let buildingId = '';
            try {
                const sessionRes = await fetch('/api/auth/session');
                if (sessionRes.ok) {
                    const session = await sessionRes.json();
                    const user = ( session && session.user && ( session.user.data || session.user ) ) || {};
                    buildingId = user.building_id || user.buildingId || '';
                }
            } catch (_) { /* session fetch failed — fall through to default */
            }

            const url = buildingId
                ? '/api/settings?building_id=' + encodeURIComponent(buildingId)
                : '/api/settings';

            const res = await fetch(url);
            if (!res.ok) return;
            const cfg = await res.json();

            const vals = {
                name: cfg.building_name || '',
                address: cfg.building_address || '',
                contact_email: cfg.contact_email || '',
                ec_email: cfg.ec_email || '',
                contact_phone: cfg.ec_contact_phone || cfg.contact_phone || '',
            };

            const EMAIL_KEYS = new Set(['contact_email', 'ec_email']);

            document.querySelectorAll('[data-bldg]').forEach(function (el) {
                const key = el.getAttribute('data-bldg');
                const val = vals[ key ];
                if (!val) return;

                if (el.tagName === 'A') {
                    el.href = EMAIL_KEYS.has(key)
                        ? 'mailto:' + val
                        : ( val.startsWith('http') ? val : 'https://' + val );
                }
                el.textContent = val;
            });
        } catch (_) {
            // Silently fail — placeholder text remains in DOM.
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', applyBuildingConfig);
    } else {
        applyBuildingConfig();
    }
} )();
