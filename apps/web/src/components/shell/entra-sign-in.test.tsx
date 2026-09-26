import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";

import { server } from "@/test/server";
import { render, screen, waitFor } from "@/test/render";

import { MicrosoftSignIn } from "./microsoft-sign-in";
import { SignInNotice } from "./sign-in-notice";
import { SignOutButton } from "./sign-out-button";

const navigation = vi.hoisted(() => ({ pathname: "/", search: "" }));

vi.mock("next/navigation", () => ({
  usePathname: () => navigation.pathname,
  useSearchParams: () => new URLSearchParams(navigation.search),
}));

beforeEach(() => {
  navigation.pathname = "/";
  navigation.search = "";
});

describe("MicrosoftSignIn", () => {
  it("is a same-origin form POST to the sign-in route, returning to this page", () => {
    navigation.pathname = "/jobs/4";
    navigation.search = "tab=a&auth_error=failed&session=expired";
    const { container } = render(<MicrosoftSignIn />);
    expect(screen.getByRole("heading", { level: 1, name: "Sign in to EasyApply" })).toBeInTheDocument();
    const form = container.querySelector("form")!;
    expect(form.getAttribute("method")).toBe("post");
    expect(form.getAttribute("action")).toBe("/api/auth/sign-in");
    // The notices are not carried into the next page.
    expect((form.querySelector("input[name=return_to]") as HTMLInputElement).value).toBe("/jobs/4?tab=a");
    expect(screen.getByRole("button", { name: "Sign in with Microsoft" })).toBeEnabled();
  });

  it("returns to the home page after signing out, not to the signed-out page", () => {
    navigation.pathname = "/signed-out";
    const { container } = render(<MicrosoftSignIn />);
    expect((container.querySelector("input[name=return_to]") as HTMLInputElement).value).toBe("/");
  });

  it("says it is redirecting once pressed", async () => {
    const { container } = render(<MicrosoftSignIn />);
    container.querySelector("form")!.addEventListener("submit", (event) => event.preventDefault());
    await userEvent.click(screen.getByRole("button", { name: "Sign in with Microsoft" }));
    expect(screen.getByRole("button", { name: "Redirecting to Microsoft…" })).toBeDisabled();
  });
});

describe("SignInNotice", () => {
  it.each([
    ["auth_error=cancelled", "Sign-in was cancelled. Nothing was changed."],
    ["auth_error=expired", "That sign-in took too long or was already used. Sign in again."],
    ["auth_error=unavailable", "Microsoft sign-in could not be reached. Try again in a moment."],
    ["auth_error=failed", "Signing in with Microsoft did not work. Try again."],
    ["auth_error=<script>", "Signing in with Microsoft did not work. Try again."],
    ["auth_error=__proto__", "Signing in with Microsoft did not work. Try again."],
    ["auth_error=constructor", "Signing in with Microsoft did not work. Try again."],
    ["session=expired", "Your EasyApply session expired. Sign in again."],
    ["session=changed", "This browser was signed in to a different EasyApply account in another tab, so this page was reloaded for that account."],
    ["session=signed_out", "You signed out of EasyApply in another tab."],
  ])("?%s says so", (search, message) => {
    navigation.search = search;
    render(<SignInNotice />);
    expect(screen.getByText(message)).toBeInTheDocument();
  });

  it("confirms a sign-out", () => {
    navigation.pathname = "/signed-out";
    render(<SignInNotice />);
    expect(screen.getByText("You signed out of EasyApply.")).toBeInTheDocument();
  });

  it("says nothing otherwise, and never echoes what the URL says", () => {
    for (const search of ["session=<b>owned</b>", "session=__proto__", "session=toString"]) {
      navigation.search = search;
      const { container, unmount } = render(<SignInNotice />);
      expect(container).toBeEmptyDOMElement();
      unmount();
    }
  });
});

describe("SignOutButton under real sign-in", () => {
  it("goes on to Microsoft's end-session URL when the server hands one back", async () => {
    server.use(
      http.post("/api/auth/sign-out", () =>
        HttpResponse.json({ mode: "entra", authenticated: false, email: null, logoutUrl: "https://tenant.ciamlogin.com/t/oauth2/v2.0/logout?post_logout_redirect_uri=x" }),
      ),
    );
    const onSignedOut = vi.fn();
    render(<SignOutButton onSignedOut={onSignedOut} />);
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(onSignedOut).toHaveBeenCalledWith("https://tenant.ciamlogin.com/t/oauth2/v2.0/logout?post_logout_redirect_uri=x"));
  });

  it("accepts the IPv6 loopback a developer's server may be on", async () => {
    server.use(http.post("/api/auth/sign-out", () => HttpResponse.json({ logoutUrl: "http://[::1]:3000/signed-out" })));
    const onSignedOut = vi.fn();
    render(<SignOutButton onSignedOut={onSignedOut} />);
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(onSignedOut).toHaveBeenCalledWith("http://[::1]:3000/signed-out"));
  });

  it("tells other tabs only about a sign-out that happened", async () => {
    const posted: unknown[] = [];
    const listener = new BroadcastChannel("easyapply-session");
    listener.onmessage = (event) => posted.push(event.data);
    try {
      server.use(http.post("/api/auth/sign-out", () => HttpResponse.json({ detail: "x" }, { status: 500 })));
      const first = render(<SignOutButton onSignedOut={() => undefined} />);
      await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(posted).toEqual([]);
      first.unmount();

      server.use(http.post("/api/auth/sign-out", () => HttpResponse.json({ mode: "entra", authenticated: false })));
      render(<SignOutButton onSignedOut={() => undefined} />);
      await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
      await waitFor(() => expect(posted).toEqual([{ signedOut: true }]));
    } finally {
      listener.close();
    }
  });

  it("refuses to navigate anywhere odd", async () => {
    server.use(http.post("/api/auth/sign-out", () => HttpResponse.json({ logoutUrl: "javascript:alert(1)" })));
    const onSignedOut = vi.fn();
    render(<SignOutButton onSignedOut={onSignedOut} />);
    await userEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(onSignedOut).toHaveBeenCalledWith(null));
  });
});
