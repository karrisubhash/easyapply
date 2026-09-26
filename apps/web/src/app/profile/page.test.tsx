import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { confirmedVersion, draftVersion, profile } from "@/test/fixtures";
import { render, screen, waitFor } from "@/test/render";
import { apiError, server } from "@/test/server";

import ProfilePage from "./page";

const nav = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push }),
  useParams: () => ({}),
}));

beforeEach(() => nav.push.mockClear());

describe("ProfilePage", () => {
  it("shows a loading state before anything arrives", () => {
    render(<ProfilePage />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading profile");
  });

  it("offers to create a profile when the user has none", async () => {
    server.use(
      http.get("/api/backend/profile", () => apiError(404, "No profile exists.", "not_found")),
    );

    render(<ProfilePage />);

    expect(await screen.findByText("You do not have a profile yet")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create profile" })).toBeInTheDocument();
  });

  it("creates the profile and then shows it", async () => {
    let created = false;
    server.use(
      http.get("/api/backend/profile", () =>
        created ? HttpResponse.json(profile) : apiError(404, "No profile exists.", "not_found"),
      ),
      http.post("/api/backend/profile", () => {
        created = true;
        return HttpResponse.json(profile, { status: 201 });
      }),
      http.get("/api/backend/profile/versions/current", () =>
        apiError(404, "This profile has no current version.", "not_found"),
      ),
    );

    render(<ProfilePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Create profile" }));

    expect(await screen.findByText("My Profile")).toBeInTheDocument();
  });

  it("surfaces a conflict when a profile already exists", async () => {
    server.use(
      http.get("/api/backend/profile", () => apiError(404, "No profile exists.", "not_found")),
      http.post("/api/backend/profile", () =>
        apiError(409, "A profile already exists for this user.", "uq_profiles_user_id"),
      ),
    );

    render(<ProfilePage />);
    await userEvent.click(await screen.findByRole("button", { name: "Create profile" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "A profile already exists for this user.",
    );
    expect(screen.getByText(/uq_profiles_user_id/)).toBeInTheDocument();
  });

  it("prompts for a first draft when there is no confirmed version", async () => {
    server.use(
      http.get("/api/backend/profile/versions/current", () =>
        apiError(404, "This profile has no current version.", "not_found"),
      ),
      // Nothing confirmed at all — the shared list has a confirmed version,
      // which is the "none current" state tested below.
      http.get("/api/backend/profile/versions", () => HttpResponse.json([draftVersion()])),
    );

    render(<ProfilePage />);
    expect(await screen.findByText("No confirmed version yet")).toBeInTheDocument();
  });

  it("reports a version list it could not read instead of calling nothing confirmed", async () => {
    server.use(
      http.get("/api/backend/profile/versions/current", () =>
        apiError(404, "This profile has no current version.", "not_found"),
      ),
      http.get("/api/backend/profile/versions", () =>
        apiError(409, "The versions could not be listed.", "conflict"),
      ),
    );

    render(<ProfilePage />);
    expect(await screen.findByText("The versions could not be listed.")).toBeInTheDocument();
    expect(screen.queryByText("No confirmed version yet")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });

  it("says no version is current, rather than none confirmed, when that is the state", async () => {
    server.use(
      http.get("/api/backend/profile/versions/current", () =>
        apiError(404, "This profile has no current version.", "not_found"),
      ),
      http.get("/api/backend/profile/versions", () =>
        HttpResponse.json([confirmedVersion({ version_number: 1, is_current: false })]),
      ),
    );

    render(<ProfilePage />);
    expect(await screen.findByText("No current version")).toBeInTheDocument();
    expect(screen.queryByText("No confirmed version yet")).not.toBeInTheDocument();
    expect(screen.getByText(/Version 1 is confirmed, but no version is marked current/)).toBeInTheDocument();
  });

  it("opens the new draft it creates", async () => {
    server.use(
      http.post("/api/backend/profile/versions", () =>
        HttpResponse.json(draftVersion({ id: 140, version_number: 4 }), { status: 201 }),
      ),
    );

    render(<ProfilePage />);
    await userEvent.click(await screen.findByRole("button", { name: "New draft" }));

    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/profile/versions/4/edit"));
    expect(document.title).toBe("My Profile · EasyApply");
  });

  it("renders the current profile and its snapshot", async () => {
    render(<ProfilePage />);

    expect(await screen.findByText("Current Profile")).toBeInTheDocument();
    expect(screen.getByText(/^Confirmed /)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Version History" })).toBeInTheDocument();
    expect(screen.getByText("AI Developer Intern")).toBeInTheDocument();
    expect(screen.getByText(/Python/)).toBeInTheDocument();
  });

  it("reports an unreachable API rather than pretending it is empty", async () => {
    server.use(
      http.get("/api/backend/profile", () =>
        apiError(502, "The EasyApply API is unreachable.", "api_unreachable"),
      ),
    );

    render(<ProfilePage />);
    // A 502 is a transport failure, so unlike 404/409 it is retried twice with
    // backoff before the error surfaces. That retry is the behaviour under
    // test, so the wait has to outlast it rather than switch it off.
    await waitFor(
      () => expect(screen.getByRole("alert")).toHaveTextContent("The EasyApply API is unreachable."),
      { timeout: 10_000 },
    );
  });
});
