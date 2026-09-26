import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { draftVersion } from "@/test/fixtures";
import { render, screen, waitFor } from "@/test/render";
import { server } from "@/test/server";

import VersionsPage from "./page";

const nav = vi.hoisted(() => ({ push: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: nav.push }),
  useParams: () => ({}),
}));

beforeEach(() => nav.push.mockClear());

describe("VersionsPage", () => {
  it("opens the new draft it creates, as the profile page does", async () => {
    // The button used to create the draft and leave the page as it was.
    server.use(
      http.post("/api/backend/profile/versions", () =>
        HttpResponse.json(draftVersion({ id: 140, version_number: 4 }), { status: 201 }),
      ),
    );

    render(<VersionsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "New draft" }));

    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/profile/versions/4/edit"));
  });

  it("opens the first draft from the empty state too", async () => {
    server.use(
      http.get("/api/backend/profile/versions", () => HttpResponse.json([])),
      http.post("/api/backend/profile/versions", () =>
        HttpResponse.json(draftVersion({ id: 101, version_number: 1 }), { status: 201 }),
      ),
    );

    render(<VersionsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Create a draft" }));

    await waitFor(() => expect(nav.push).toHaveBeenCalledWith("/profile/versions/1/edit"));
  });
});
