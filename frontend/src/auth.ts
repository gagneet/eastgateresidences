import NextAuth from "next-auth"
import Credentials from "next-auth/providers/credentials"
import axios from "axios"

if (!process.env.AUTH_SECRET) {
    throw new Error("AUTH_SECRET environment variable is required. Generate one with: openssl rand -base64 32");
}

export const {handlers, signIn, signOut, auth} = NextAuth({
    providers: [
        // ── Primary credential provider: email + password ────────────────────────
        Credentials({
            id: "credentials",
            name: "Credentials",
            credentials: {
                email: {label: "Email", type: "email"},
                password: {label: "Password", type: "password"},
            },
            async authorize(credentials, request) {
                if (!credentials?.email || !credentials?.password) return null;

                // Forward real client IP headers to the backend so FastAPI can log
                // the actual user IP. Without this, Next.js calls the backend directly
                // via BACKEND_INTERNAL_URL (bypassing nginx/Cloudflare), so FastAPI
                // only sees 127.0.0.1 instead of the real client IP.
                const ipHeaders: Record<string, string> = {};
                const cfIp = request.headers.get("cf-connecting-ip");
                const xForwardedFor = request.headers.get("x-forwarded-for");
                const xRealIp = request.headers.get("x-real-ip");
                const cfCountry = request.headers.get("cf-ipcountry");
                if (cfIp) ipHeaders["CF-Connecting-IP"] = cfIp;
                if (xForwardedFor) ipHeaders["X-Forwarded-For"] = xForwardedFor;
                if (xRealIp) ipHeaders["X-Real-IP"] = xRealIp;
                if (cfCountry) ipHeaders["CF-IPCountry"] = cfCountry;

                try {
                    const res = await axios.post(
                        `${process.env.BACKEND_INTERNAL_URL || process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:8003"}/api/auth/login`,
                        {
                            email: credentials.email,
                            password: credentials.password,
                        },
                        {headers: ipHeaders}
                    );

                    if (res.data && res.data.token) {
                        return {
                            id: res.data.user.id,
                            name: res.data.user.full_name,
                            email: res.data.user.email,
                            role: res.data.user.role,
                            token: res.data.token,
                            user_data: res.data.user,
                        };
                    }

                    // TOTP required — return a minimal user object with a special flag.
                    // The JWT callback stores the pending token; the session callback exposes
                    // it so LoginPage can redirect to /login/totp-challenge.
                    if (res.data && res.data.status === "totp_required" && res.data.totp_token) {
                        return {
                            id: "totp-pending",
                            name: "",
                            email: credentials.email as string,
                            totp_required: true,
                            totp_token: res.data.totp_token,
                        };
                    }

                    return null;
                } catch (e: any) {
                    // Re-throw 429 so NextAuth returns result.error = "CallbackRouteError"
                    // instead of "CredentialsSignin" — the login page uses this distinction
                    // to show "too many attempts" rather than "wrong password".
                    if (e?.response?.status === 429) {
                        throw e;
                    }
                    // 403 pending_approval: surface the message via a special session flag
                    // (same pattern as totp_required) so AuthContext can show the right banner.
                    if (e?.response?.status === 403) {
                        const detail = e?.response?.data?.detail;
                        if (detail?.code === "pending_approval") {
                            return {
                                id: `pending:${credentials.email}`,
                                name: "",
                                email: credentials.email as string,
                                is_pending_approval: true,
                                pending_message: detail?.message,
                            };
                        }
                    }
                    return null;
                }
            },
        }),

        // ── Token-exchange provider: used by the TOTP challenge page ────────────
        // After the TOTP challenge succeeds the backend returns a full JWT.
        // The challenge page calls signIn("token-exchange", { token }) to
        // convert that JWT into a proper NextAuth session — replacing the
        // totp-pending session with a fully-authenticated one.
        Credentials({
            id: "token-exchange",
            name: "Token Exchange",
            credentials: {
                token: {type: "text"},
            },
            async authorize(credentials) {
                if (!credentials?.token) return null;
                try {
                    const backendUrl =
                        process.env.BACKEND_INTERNAL_URL ||
                        process.env.NEXT_PUBLIC_BACKEND_URL ||
                        "http://localhost:8003";
                    const res = await axios.get(`${backendUrl}/api/auth/me`, {
                        headers: {Authorization: `Bearer ${credentials.token}`},
                    });
                    if (res.data && res.data.id) {
                        return {
                            id: res.data.id,
                            name: res.data.full_name,
                            email: res.data.email,
                            role: res.data.role,
                            token: credentials.token as string,
                            user_data: res.data,
                        };
                    }
                    return null;
                } catch (e) {
                    return null;
                }
            },
        }),
    ],
    callbacks: {
        async jwt({token, user}) {
            if (user) {
                const u = user as any;
                // TOTP pending — store the pending token so the challenge page can use it
                if (u.totp_required) {
                    token.totp_required = true;
                    token.totp_token = u.totp_token;
                    token.totp_email = u.email;
                    return token;
                }
                // Pending approval — surface message so AuthContext can show the right banner.
                if (u.is_pending_approval) {
                    (token as any).is_pending_approval = true;
                    (token as any).pending_message = u.pending_message;
                    return token;
                }
                // Normal or token-exchange login: store the full access token
                token.accessToken = u.token;
                token.role = u.role;
                token.userData = u.user_data;
                // Clear any stale totp-pending state
                delete (token as any).totp_required;
                delete (token as any).totp_token;
                delete (token as any).totp_email;
                delete (token as any).is_pending_approval;
                delete (token as any).pending_message;
            }
            return token;
        },
        // IMPORTANT: Each statement MUST end with a semicolon.
        // Without them, the minifier strips newlines and the JS engine treats
        //   (token as any).role\n(session.user as any).data
        // as a function call  →  t.role(session.user)  →  TypeError.
        async session({session, token}) {
            const tok = token as any;
            const sess = session as any;
            // Expose TOTP pending state so the frontend can redirect to the challenge page.
            if (tok.totp_required) {
                sess.totp_required = true;
                sess.totp_token = tok.totp_token;
                sess.totp_email = tok.totp_email;
                return session;
            }
            // Expose pending approval state so AuthContext.login can show the right message.
            if (tok.is_pending_approval) {
                sess.is_pending_approval = true;
                sess.pending_message = tok.pending_message;
                return session;
            }
            if (session.user) {
                sess.user.role = tok.role;
                sess.user.data = tok.userData;
            }
            sess.accessToken = tok.accessToken;
            return session;
        },
    },
    pages: {
        signIn: "/login",
    },
    secret: process.env.AUTH_SECRET,
    // trustHost allows NextAuth to work behind Cloudflare + Nginx reverse proxy
    // where X-Forwarded-Proto / X-Forwarded-Host headers are set by the proxy.
    trustHost: true,
});
