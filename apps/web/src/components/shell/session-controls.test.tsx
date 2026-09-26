import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { apiError, server } from "@/test/server";
import { render, screen, waitFor } from "@/test/render";

import { DevSignIn } from "./dev-sign-in";
import { SignOutButton } from "./sign-out-button";

describe("DevSignIn", () => {
  it("says what it is: no password, any address, and the extension's jobs follow it", () => {
    render(<DevSignIn suggestedEmail={null} onSignedIn={() => undefined} />);
    expect(screen.getByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText(/no password, and any address works/)).toBeInTheDocument();
    expect(screen.getByText(/every job the EasyApply extension saves from this browser/)).toBeInTheDocument();
  });

  it("offers the configured address, and signs in with what was typed", async () => {
    let sent: unknown = null;
    server.use(
      http.post("/api/auth/sign-in", async ({ request }) => {
        sent = await request.json();
        return HttpResponse.json({ mode: "dev-session", authenticated: true, email: "owner@easyapply.test" });
      }),
    );
    const onSignedIn = vi.fn();
    render(<DevSignIn suggestedEmail="dev@easyapply.local" onSignedIn={onSignedIn} />);

    const field = screen.getByLabelText("Email");
    expect(field).toHaveValue("dev@easyapply.local");
    await userEvent.clear(field);
    await userEvent.type(field, "owner@easyapply.test");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(onSignedIn).toHaveBeenCalledOnce());
    expect(sent).toEqual({ email: "owner@easyapply.test" });
  });

  it("shows the server's refusal and stays on the form", async () => {
    server.use(http.post("/api/auth/sign-in", () => apiError(422, "Enter an email address.", "invalid_email")));
    const onSignedIn = vi.fn();
    render(<DevSignIn suggestedEmail={null} onSignedIn={onSignedIn} />);

    await userEvent.type(screen.getByLabelText("Email"), "nobody");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter an email address.");
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
    expect(onSignedIn).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("says when the web app does not answer", async () => {
    server.use(http.post("/api/auth/sign-in", () => HttpResponse.error()));
    render(<DevSignIn suggestedEmail="a@b.test" onSignedIn={() => undefined} />);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The EasyApply web app did not answer.");
  });
});

describe("SignOutButton", () => {
  it("ends the session, then leaves the page", async () => {
    let called = false;
    server.use(
      http.post("/api/auth/sign-out", () => {
        called = true;
        return HttpResponse.json({ mode: "dev-session", authenticated: false, email: null });
      }),
    );
    const onSignedOut = vi.fn();
    render(<SignOutButton onSignedOut={onSignedOut} />);
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(onSignedOut).toHaveBeenCalledOnce());
    expect(called).toBe(true);
  });

  it("leaves the page even if the request failed — a reload shows the truth either way", async () => {
    server.use(http.post("/api/auth/sign-out", () => HttpResponse.error()));
    const onSignedOut = vi.fn();
    render(<SignOutButton onSignedOut={onSignedOut} />);
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(onSignedOut).toHaveBeenCalledOnce());
  });
});
