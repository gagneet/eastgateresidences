// @featuretrace:error-recovery-framework — getApiErrorDetail must read FastAPI's 422 array shape.
// Layer: test
// Data flow: axios error -> getApiErrorDetail() -> human-facing message in a toast/banner.
// Related: frontend/src/lib/api-error.ts
//          frontend/src/pages/dashboard/MeetingsPage.tsx
/**
 * FastAPI sends request-validation errors (HTTP 422) as an ARRAY of
 * { type, loc, msg, input } entries — not the { code, message, ... } object shape the
 * rest of this codebase's errors use.
 *
 * `typeof [] === "object"` in JavaScript, so the object branch destructured the array,
 * produced `code`/`message` === undefined, and handed every call site an empty message.
 * The caller then fell back to its own generic string, and the real reason never
 * reached the user.
 *
 * Observed live 2026-08-28: PUT /api/meetings/{id} returned 422
 * "List should have at most 50 items after validation, not 80" for an over-long agenda,
 * and the page showed only "Failed to update meeting".
 */
import { getApiErrorDetail } from "@/lib/api-error";

const axiosError = (data: unknown, status = 422) => ({ response: { status, data } });

describe("getApiErrorDetail — FastAPI 422 array shape", () => {
  it("extracts the field name and message from a single validation error", () => {
    const err = axiosError({
      detail: [
        {
          type: "too_long",
          loc: ["body", "agenda"],
          msg: "List should have at most 50 items after validation, not 80",
          input: Array.from({ length: 80 }, (_, i) => `item ${i}`),
        },
      ],
    });

    const { code, message } = getApiErrorDetail(err);
    expect(code).toBe("validation_error");
    expect(message).toBe(
      "agenda: List should have at most 50 items after validation, not 80",
    );
  });

  it("drops the leading 'body' segment so the field name leads", () => {
    const err = axiosError({
      detail: [
        { type: "string_type", loc: ["body", "agenda", 0], msg: "Input should be a valid string" },
      ],
    });
    expect(getApiErrorDetail(err).message).toBe(
      "agenda.0: Input should be a valid string",
    );
  });

  it("caps a long error list rather than rendering every entry", () => {
    // A per-item failure emits one entry per bad item; a toast with 80 of them is no
    // more useful than one with none.
    const err = axiosError({
      detail: Array.from({ length: 80 }, (_, i) => ({
        type: "string_type",
        loc: ["body", "agenda", i],
        msg: "Input should be a valid string",
      })),
    });
    const { message } = getApiErrorDetail(err);
    expect(message).toContain("and 77 more");
    expect(message.split(";").length).toBeLessThanOrEqual(3);
  });

  it("still handles the structured object envelope", () => {
    const err = axiosError(
      { error: { code: "already_registered", message: "That email is taken.", metadata: { unit_number: "TH086" } } },
      409,
    );
    const detail = getApiErrorDetail(err);
    expect(detail.code).toBe("already_registered");
    expect(detail.message).toBe("That email is taken.");
    expect(detail.metadata.unit_number).toBe("TH086");
  });

  it("still handles the legacy detail-object shape", () => {
    const err = axiosError({ detail: { code: "locked", message: "Period is locked." } }, 409);
    const detail = getApiErrorDetail(err);
    expect(detail.code).toBe("locked");
    expect(detail.message).toBe("Period is locked.");
  });

  it("still handles a bare string detail", () => {
    const err = axiosError({ detail: "Not authorized" }, 403);
    expect(getApiErrorDetail(err).message).toBe("Not authorized");
  });

  it("returns empty rather than throwing on an unrecognised body", () => {
    expect(getApiErrorDetail({}).message).toBe("");
    expect(getApiErrorDetail(axiosError(null)).message).toBe("");
  });
});
