// @featuretrace:error-recovery-framework — Pins the API error envelope both frontends must read.
// Layer: test
// Data flow: backend/utils/error_response.py envelope -> lib/api-error getApiErrorDetail -> RegisterPage banner (global).
// Related: frontend/src/lib/api-error.ts
//          frontend/src/pages/auth/RegisterPage.jsx
//          backend/utils/error_response.py
/**
 * getApiErrorDetail must read a domain error from either envelope the API can send.
 *
 * Regression for a live registration UX bug (2026-08-27): POST /auth/register returns a
 * 409 carrying a written, actionable message ("You are already registered for Unit
 * TH086. Please try to login, or reset your password."), but the register page read it
 * as `data.detail.code`. `backend/utils/error_response.py` had rewrapped it into
 * `data.error.{code,message,metadata}`, so every branch missed and the user saw a bare
 * "Registration failed" — with the Sign in / Reset password banner never rendering.
 */
import { getApiErrorDetail, classifyApiError } from "@/lib/api-error";

const enveloped = {
  response: {
    status: 409,
    data: {
      error: {
        code: "already_registered",
        message: "You are already registered for Unit TH086. Please try to login, or reset your password.",
        status: 409,
        request_id: "bfa014fd",
        retryable: false,
        metadata: { unit_number: "TH086" },
      },
    },
  },
};

describe("getApiErrorDetail", () => {
  it("reads the current { error: {...} } envelope, including metadata", () => {
    const d = getApiErrorDetail(enveloped);
    expect(d.code).toBe("already_registered");
    expect(d.message).toMatch(/reset your password/);
    expect(d.metadata.unit_number).toBe("TH086");
  });

  it("still reads the legacy { detail: {...} } shape", () => {
    const d = getApiErrorDetail({
      response: { status: 409, data: { detail: { code: "pending_approval", message: "Awaiting approval.", unit_number: "UA042" } } },
    });
    expect(d.code).toBe("pending_approval");
    expect(d.message).toBe("Awaiting approval.");
    expect(d.metadata.unit_number).toBe("UA042");
  });

  it("passes a bare string detail through as the message", () => {
    const d = getApiErrorDetail({ response: { status: 400, data: { detail: "Invalid role." } } });
    expect(d.code).toBe("");
    expect(d.message).toBe("Invalid role.");
    expect(d.metadata).toEqual({});
  });

  it("never throws on a network error with no response", () => {
    const d = getApiErrorDetail(new Error("Network Error"));
    expect(d).toEqual({ code: "", message: "", metadata: {} });
  });

  it("exposes metadata through classifyApiError too", () => {
    expect(classifyApiError(enveloped).metadata).toEqual({ unit_number: "TH086" });
  });
});

describe("registration conflict codes survive the envelope", () => {
  // The exact codes RegisterPage branches on. If the backend renames one, this fails
  // loudly rather than silently degrading to a generic toast the way the original bug did.
  const CODES = [
    "already_registered",
    "pending_now_approved",
    "pending_approval",
    "archived_user_return_request",
    "owner_exists_add_unit",
  ];
  it.each(CODES)("%s is readable from the error envelope", (code) => {
    const d = getApiErrorDetail({ response: { status: 409, data: { error: { code, message: "m" } } } });
    expect(d.code).toBe(code);
    expect(d.message).toBe("m");
  });
});
