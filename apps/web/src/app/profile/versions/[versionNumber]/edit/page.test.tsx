import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { confirmedVersion, draftVersion, filledSnapshot } from "@/test/fixtures";
import { render, screen } from "@/test/render";
import { apiError, server } from "@/test/server";

import EditVersionPage from "./page";

const push = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: (...args: unknown[]) => push(...args) }),
  useParams: () => ({ versionNumber: "1" }),
}));

describe("EditVersionPage", () => {
  it("will not edit a confirmed version, and says why", async () => {
    server.use(
      http.get("/api/backend/profile/versions/:n", () => HttpResponse.json(confirmedVersion())),
    );

    render(<EditVersionPage />);

    expect(await screen.findByText("Version 1 is confirmed")).toBeInTheDocument();
    expect(screen.getByText(/permanent and cannot be edited/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save draft/ })).not.toBeInTheDocument();
  });

  it("loads the existing snapshot into the form", async () => {
    server.use(
      http.get("/api/backend/profile/versions/:n", () =>
        HttpResponse.json(draftVersion({ snapshot: filledSnapshot })),
      ),
    );

    render(<EditVersionPage />);

    expect(await screen.findByDisplayValue("APTRANSCO")).toBeInTheDocument();
    expect(screen.getByDisplayValue("AI Developer Intern")).toBeInTheDocument();
    expect(screen.getByDisplayValue("FastAPI, PostgreSQL")).toBeInTheDocument();
  });

  it("sends the whole snapshot on save and then goes to review", async () => {
    let received: unknown = null;
    server.use(
      http.get("/api/backend/profile/versions/:n", () =>
        HttpResponse.json(draftVersion({ snapshot: filledSnapshot })),
      ),
      http.put("/api/backend/profile/versions/:n/snapshot", async ({ request }) => {
        received = await request.json();
        return HttpResponse.json(draftVersion({ snapshot: filledSnapshot }));
      }),
    );

    render(<EditVersionPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Save draft" }));

    await vi.waitFor(() => expect(received).not.toBeNull());
    expect(received).toMatchObject({
      experiences: [expect.objectContaining({ company: "APTRANSCO" })],
      skills: [
        expect.objectContaining({ name: "Django" }),
        expect.objectContaining({ name: "Python" }),
      ],
    });
    await vi.waitFor(() => expect(push).toHaveBeenCalledWith("/profile/versions/1"));
  });

  it("shows the database's own message when a save is rejected", async () => {
    server.use(
      http.get("/api/backend/profile/versions/:n", () =>
        HttpResponse.json(draftVersion({ snapshot: filledSnapshot })),
      ),
      http.put("/api/backend/profile/versions/:n/snapshot", () =>
        apiError(
          422,
          "The same skill is listed twice in this snapshot.",
          "uq_skills_profile_version_id_name_lower",
        ),
      ),
    );

    render(<EditVersionPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Save draft" }));

    expect(
      await screen.findByText("The same skill is listed twice in this snapshot."),
    ).toBeInTheDocument();
    expect(screen.getByText(/uq_skills_profile_version_id_name_lower/)).toBeInTheDocument();
  });

  it("reports a conflict if the version was confirmed underneath the editor", async () => {
    server.use(
      http.get("/api/backend/profile/versions/:n", () =>
        HttpResponse.json(draftVersion({ snapshot: filledSnapshot })),
      ),
      http.put("/api/backend/profile/versions/:n/snapshot", () =>
        apiError(409, "Version 1 is confirmed and cannot be modified.", "conflict"),
      ),
    );

    render(<EditVersionPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Save draft" }));

    expect(
      await screen.findByText("Version 1 is confirmed and cannot be modified."),
    ).toBeInTheDocument();
  });

  it("adds and removes snapshot rows", async () => {
    server.use(
      http.get("/api/backend/profile/versions/:n", () => HttpResponse.json(draftVersion())),
    );

    render(<EditVersionPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Add skill/ }));

    const nameField = screen.getByRole("textbox", { name: /Name/ });
    await userEvent.type(nameField, "Rust");
    expect(screen.getByDisplayValue("Rust")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Remove Skill 1" }));
    expect(screen.queryByDisplayValue("Rust")).not.toBeInTheDocument();
  });

  it("says which required field is empty, on the field itself", async () => {
    let saved = false;
    server.use(
      http.get("/api/backend/profile/versions/:n", () => HttpResponse.json(draftVersion())),
      http.put("/api/backend/profile/versions/:n/snapshot", () => {
        saved = true;
        return HttpResponse.json(draftVersion());
      }),
    );

    render(<EditVersionPage />);
    await userEvent.click(await screen.findByRole("button", { name: /Add skill/ }));
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));

    const name = screen.getByRole("textbox", { name: /Name/ });
    expect(await screen.findByText("Name is required.")).toBeInTheDocument();
    expect(name).toHaveAttribute("aria-invalid", "true");
    expect(name).toHaveAttribute("aria-required", "true");
    expect(name).toHaveAccessibleDescription("Name is required.");
    // Said beside the Save button too, which is where the user is looking.
    expect(screen.getByText("Not saved. Some required fields are empty.")).toBeInTheDocument();
    expect(saved).toBe(false);
  });

  it("refuses a project link with no address, on the field itself", async () => {
    // The API only bounds a link's length, so "GitHub" alone was saved as the
    // address and shown as a link to nowhere.
    let saved = false;
    server.use(
      http.get("/api/backend/profile/versions/:n", () =>
        HttpResponse.json(draftVersion({ snapshot: filledSnapshot })),
      ),
      http.put("/api/backend/profile/versions/:n/snapshot", () => {
        saved = true;
        return HttpResponse.json(draftVersion());
      }),
    );

    render(<EditVersionPage />);
    const links = await screen.findByRole("textbox", { name: "Project links" });
    await userEvent.clear(links);
    await userEvent.type(links, "GitHub");
    await userEvent.click(screen.getByRole("button", { name: "Save draft" }));

    const reason = /^This line does not end with a web address/;
    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(links).toHaveAttribute("aria-invalid", "true");
    expect(links).toHaveAccessibleDescription(reason);
    expect(screen.getByText("Not saved. Some fields need fixing; each one says why.")).toBeInTheDocument();
    expect(saved).toBe(false);
  });

  it("moves focus to a rejected save's reason and says so beside Save", async () => {
    server.use(
      http.get("/api/backend/profile/versions/:n", () =>
        HttpResponse.json(draftVersion({ snapshot: filledSnapshot })),
      ),
      http.put("/api/backend/profile/versions/:n/snapshot", () =>
        apiError(409, "Version 1 is confirmed and cannot be modified.", "conflict"),
      ),
    );

    render(<EditVersionPage />);
    await userEvent.click(await screen.findByRole("button", { name: "Save draft" }));

    const reason = await screen.findByText("Version 1 is confirmed and cannot be modified.");
    expect(reason.closest("[tabindex='-1']")).toHaveFocus();
    expect(
      screen.getByText("Not saved. The reason is shown at the top of the form."),
    ).toBeInTheDocument();
  });
});
