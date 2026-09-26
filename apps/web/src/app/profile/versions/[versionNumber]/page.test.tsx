import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { confirmedVersion, draftVersion, filledSnapshot } from "@/test/fixtures";
import { render, screen } from "@/test/render";
import { apiError, server } from "@/test/server";

import VersionPage from "./page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useParams: () => ({ versionNumber: "1" }),
}));

describe("VersionPage", () => {
  it("marks a draft as a draft and offers to edit and confirm it", async () => {
    server.use(
      http.get("/api/backend/profile/versions/:n", () =>
        HttpResponse.json(draftVersion({ snapshot: filledSnapshot })),
      ),
    );

    render(<VersionPage />);

    expect(await screen.findByLabelText("Draft version")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Edit" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm version" })).toBeInTheDocument();
    expect(screen.getByText("This is a draft")).toBeInTheDocument();
  });

  it("refuses to offer editing for a confirmed version", async () => {
    server.use(
      http.get("/api/backend/profile/versions/:n", () => HttpResponse.json(confirmedVersion())),
    );

    render(<VersionPage />);

    expect(await screen.findByLabelText("Confirmed version")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Edit" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Confirm version" })).not.toBeInTheDocument();
    expect(screen.getByText("This version is confirmed")).toBeInTheDocument();
  });

  it("offers to make a confirmed version that is not current the current one", async () => {
    let promoted = false;
    server.use(
      http.get("/api/backend/profile/versions/:n", () =>
        HttpResponse.json(confirmedVersion({ is_current: promoted })),
      ),
      http.post("/api/backend/profile/versions/:n/make-current", () => {
        promoted = true;
        return HttpResponse.json(confirmedVersion({ is_current: true }));
      }),
    );

    render(<VersionPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Make this the current version" }),
    );

    // The page re-reads the version rather than trusting its own guess.
    expect(await screen.findByLabelText("Current version")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Make this the current version" }),
    ).not.toBeInTheDocument();
  });

  it("does not offer to make the current version current", async () => {
    server.use(
      http.get("/api/backend/profile/versions/:n", () =>
        HttpResponse.json(confirmedVersion({ is_current: true })),
      ),
    );

    render(<VersionPage />);

    expect(await screen.findByLabelText("Confirmed version")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Make this the current version" }),
    ).not.toBeInTheDocument();
  });

  it("shows the reason when making a version current is refused", async () => {
    server.use(
      http.get("/api/backend/profile/versions/:n", () =>
        HttpResponse.json(confirmedVersion({ is_current: false })),
      ),
      http.post("/api/backend/profile/versions/:n/make-current", () =>
        apiError(409, "Version 1 is a draft. Confirm it before making it current.", "conflict"),
      ),
    );

    render(<VersionPage />);
    await userEvent.click(
      await screen.findByRole("button", { name: "Make this the current version" }),
    );

    expect(await screen.findByText(/Confirm it before making it current/)).toBeInTheDocument();
  });

  it("asks before confirming, and says the change is permanent", async () => {
    server.use(
      http.get("/api/backend/profile/versions/:n", () => HttpResponse.json(draftVersion())),
    );

    render(<VersionPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Confirm version" }));

    expect(await screen.findByText("Confirm version 1?")).toBeInTheDocument();
    expect(screen.getByText(/cannot be edited or reopened/)).toBeInTheDocument();
  });

  it("keeps the dialog open and shows the reason when confirmation is refused", async () => {
    server.use(
      http.get("/api/backend/profile/versions/:n", () => HttpResponse.json(draftVersion())),
      http.post("/api/backend/profile/versions/:n/confirm", () =>
        apiError(409, "Version 1 is already confirmed.", "conflict"),
      ),
    );

    render(<VersionPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Confirm version" }));
    await userEvent.click(await screen.findByRole("button", { name: "Confirm" }));

    expect(await screen.findByText("Version 1 is already confirmed.")).toBeInTheDocument();
    expect(screen.getByText("Confirm version 1?")).toBeInTheDocument();
  });

  it("shows an empty state for a version that does not exist", async () => {
    server.use(
      http.get("/api/backend/profile/versions/:n", () =>
        apiError(404, "Version 1 does not exist.", "not_found"),
      ),
    );

    render(<VersionPage />);
    expect(await screen.findByText("Version 1 does not exist")).toBeInTheDocument();
  });
});
