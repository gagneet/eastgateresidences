/**
 * Mint a NextAuth session cookie for the Lighthouse performance harness.
 *
 * Lighthouse drives a real browser, so the authenticated dashboards cannot be
 * measured without a session cookie. Rather than scripting a login form, this
 * encodes the same JWT that `auth.ts`'s `jwt()` callback would produce, using the
 * app's own AUTH_SECRET — so the cookie is indistinguishable from a real one and
 * stays valid only as long as a normal session would.
 *
 * Read-only with respect to application data: it creates no user and writes
 * nothing to any database. It only signs a token for an account that already
 * exists.
 *
 * Usage (from repo root):
 *   node frontend/scripts/mint_perf_session_cookie.mjs <backend-jwt> <path-to-me.json>
 *
 * Prints the Cookie header value on stdout.
 */
import fs from 'node:fs';
import path from 'node:path';
import {encode} from 'next-auth/jwt';

const [, , accessToken, mePath] = process.argv;
if (!accessToken || !mePath) {
    console.error('usage: mint_perf_session_cookie.mjs <backend-jwt> <me.json>');
    process.exit(2);
}

// Read AUTH_SECRET from the frontend env the running server uses. Not logged.
function readEnv(file, key) {
    if (!fs.existsSync(file)) return null;
    for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
        const m = line.match(new RegExp(`^\\s*${key}\\s*=\\s*(.*)\\s*$`));
        if (m) return m[1].replace(/^["']|["']$/g, '');
    }
    return null;
}

const envFile = path.join(process.cwd(), 'frontend/.env.local');
const secret = process.env.AUTH_SECRET || readEnv(envFile, 'AUTH_SECRET');
if (!secret) {
    console.error(`AUTH_SECRET not found (checked $AUTH_SECRET and ${envFile})`);
    process.exit(2);
}

const me = JSON.parse(fs.readFileSync(mePath, 'utf8'));

// auth.ts stores exactly these three on the token; the session() callback reads
// them back as session.accessToken / session.user.role / session.user.data.
const token = {
    name: me.full_name,
    email: me.email,
    sub: me.id,
    accessToken,
    role: me.role,
    userData: me,
};

// Cookie name — this is NOT just a label. Auth.js v5 uses the cookie name as the
// encryption salt, so getting it wrong yields a token the server silently decodes
// to `null` (an empty session, not an error), which looks exactly like a bad secret.
//
// auth.ts sets no `cookies` override, so the name follows `useSecureCookies`, which
// Auth.js derives from AUTH_URL. This deployment sets
// AUTH_URL=https://eastgateresidences.com.au, so secure cookies are on and the
// `__Secure-` prefix applies — even when the harness talks to http://localhost:3020.
// Override via SESSION_COOKIE_NAME if AUTH_URL ever becomes http://.
//
// Chrome will not put a `__Secure-` cookie in its jar over plain http, but the
// Lighthouse harness injects this as an extra HTTP header rather than a cookie, so
// the prefix rule does not apply to it.
const cookieName = process.env.SESSION_COOKIE_NAME || '__Secure-authjs.session-token';

const jwt = await encode({
    token,
    secret,
    salt: cookieName,
    maxAge: 60 * 60,
});

process.stdout.write(`${cookieName}=${jwt}`);
