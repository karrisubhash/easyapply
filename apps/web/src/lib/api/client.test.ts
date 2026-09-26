import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";

import { apiError, server, validationError } from "@/test/server";

import { ApiError, request } from "./client";

/** Narrows to ApiError so the assertions below are typed, not `unknown`. */
async function catchApiError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
    throw new Error("expected the request to reject");
  } catch (error) {
    if (!(error instanceof ApiError)) throw error;
    return error;
  }
}

describe("request", () => {
  it("returns the parsed body on success", async () => {
    server.use(http.get("/api/backend/thing", () => HttpResponse.json({ id: 7 })));
    await expect(request<{ id: number }>("thing")).resolves.toEqual({ id: 7 });
  });

  it("turns a 404 into a not-found ApiError", async () => {
    server.use(
      http.get("/api/backend/thing", () =>
        apiError(404, "No profile exists for this user.", "not_found"),
      ),
    );

    const error = await catchApiError(request("thing"));
    expect(error).toBeInstanceOf(ApiError);
    expect(error.isNotFound).toBe(true);
    expect(error.isConflict).toBe(false);
    expect(error.message).toBe("No profile exists for this user.");
  });

  it("carries the backend code, which is the database constraint name", async () => {
    server.use(
      http.post("/api/backend/thing", () =>
        apiError(409, "A profile already exists for this user.", "uq_profiles_user_id"),
      ),
    );

    const error = await catchApiError(request("thing", { method: "POST" }));
    expect(error.isConflict).toBe(true);
    expect(error.code).toBe("uq_profiles_user_id");
  });

  it("maps a database CHECK violation to a validation error", async () => {
    server.use(
      http.put("/api/backend/thing", () =>
        apiError(422, "An experience cannot end before it starts.", "ck_experiences_end_date_after_start_date"),
      ),
    );

    const error = await catchApiError(request("thing", { method: "PUT", body: "{}" }));
    expect(error.isValidation).toBe(true);
    expect(error.code).toBe("ck_experiences_end_date_after_start_date");
  });

  it("reports a non-JSON failure as an ApiError, not a parser crash", async () => {
    // A crash in the proxy route, or anything that answers before it, replies
    // with HTML. Letting JSON.parse throw on that shows the user "Unexpected
    // token <" instead of something ApiErrorAlert can render.
    server.use(
      http.get("/api/backend/thing", () =>
        HttpResponse.text("<!doctype html><title>502 Bad Gateway</title>", { status: 502 }),
      ),
    );

    const error = await catchApiError(request("thing"));
    expect(error.status).toBe(502);
    expect(error.message).toBe("Something went wrong.");
  });

  it("leaves FormData to set its own multipart Content-Type", async () => {
    // Declaring application/json over multipart strips the boundary the server
    // needs to find any part, so the upload arrives with no file in it.
    let sent: string | null = "unset";
    server.use(
      http.post("/api/backend/thing", ({ request: received }) => {
        sent = received.headers.get("content-type");
        return HttpResponse.json({ ok: true });
      }),
    );

    const body = new FormData();
    body.append("file", new File(["x"], "a.pdf"));
    await request("thing", { method: "POST", body });

    expect(sent).toMatch(/^multipart\/form-data; boundary=/);
  });

  it("understands FastAPI's own field-issue shape", async () => {
    server.use(
      http.put("/api/backend/thing", () =>
        validationError(["body", "skills", 0, "name"], "Field required"),
      ),
    );

    const error = await catchApiError(request("thing", { method: "PUT", body: "{}" }));
    expect(error.issues).toHaveLength(1);
    expect(error.issues[0].msg).toBe("Field required");
  });
});
