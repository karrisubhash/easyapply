import { UNNAMED_ACCOUNT_LABEL } from "@easyapply/shared";

import { getSession, isSessionState, isSessionTag, sessionEndpoint, signInUrl } from "../src/api/session";
import { createFakeApi, fakeSessionTag } from "./support/fake-api";

const WEB = "http://localhost:3000";

/** What the web app answers, verbatim, for one question. */
async function answering(body: unknown) {
  const api = createFakeApi();
  api.respondNextWith({ status: 200, body: JSON.stringify(body) }, "/api/auth/session");
  return getSession({ webUrl: WEB, fetch: api.fetch });
}

describe("asking the web app who this browser is", () => {
  it("asks the web app's own session route, presenting the browser's session", async () => {
    const api = createFakeApi();
    await getSession({ webUrl: WEB, fetch: api.fetch });
    expect(sessionEndpoint(WEB)).toBe("http://localhost:3000/api/auth/session");
    expect(api.requests).toEqual([
      {
        url: "http://localhost:3000/api/auth/session",
        method: "GET",
        headers: { accept: "application/json" },
        body: "",
        credentials: "include",
        redirect: "error",
      },
    ]);
  });

  it("development: the configured identity, and its account's tag", async () => {
    const api = createFakeApi();
    expect(await getSession({ webUrl: WEB, fetch: api.fetch })).toEqual({
      kind: "signed_in",
      mode: "development",
      label: "dev@easyapply.local",
      sessionTag: fakeSessionTag("dev@easyapply.local"),
    });
  });

  it("dev-session: whoever the browser is signed in as", async () => {
    const api = createFakeApi({ session: { mode: "dev-session", email: "owner@easyapply.test" } });
    expect(await getSession({ webUrl: WEB, fetch: api.fetch })).toEqual({
      kind: "signed_in",
      mode: "dev-session",
      label: "owner@easyapply.test",
      sessionTag: fakeSessionTag("owner@easyapply.test"),
    });
  });

  it("dev-session: signed out", async () => {
    const api = createFakeApi({ session: null });
    expect(await getSession({ webUrl: WEB, fetch: api.fetch })).toEqual({ kind: "signed_out" });
  });

  it("entra: signed in with an email", async () => {
    expect(
      await answering({ mode: "entra", authenticated: true, email: "ada@example.test", name: "Ada Lovelace", sessionTag: "tagAda_01-x" }),
    ).toEqual({ kind: "signed_in", mode: "entra", label: "ada@example.test", sessionTag: "tagAda_01-x" });
  });

  it("entra: signed in with no email is still signed in, and called by its name", async () => {
    expect(await answering({ mode: "entra", authenticated: true, email: null, name: "Ada Lovelace", sessionTag: "tagAda" })).toEqual({
      kind: "signed_in",
      mode: "entra",
      label: "Ada Lovelace",
      sessionTag: "tagAda",
    });
  });

  it("entra: signed in with neither email nor name is 'your EasyApply account'", async () => {
    const state = await answering({ mode: "entra", authenticated: true, email: null, name: null, sessionTag: "tagX" });
    expect(state).toEqual({ kind: "signed_in", mode: "entra", label: UNNAMED_ACCOUNT_LABEL, sessionTag: "tagX" });
    expect(UNNAMED_ACCOUNT_LABEL).toBe("your EasyApply account");
  });

  it("entra: an empty email is no email", async () => {
    expect(await answering({ mode: "entra", authenticated: true, email: "", name: "Ada", sessionTag: "t" })).toMatchObject({ label: "Ada" });
  });

  it("entra: not authenticated is signed out, whatever else it says", async () => {
    expect(await answering({ mode: "entra", authenticated: false, email: "ada@example.test", sessionTag: "tagAda" })).toEqual({
      kind: "signed_out",
    });
  });

  it("authenticated is the ONLY signed-in test: a truthy non-boolean is not a session at all", async () => {
    expect((await answering({ mode: "entra", authenticated: "yes", email: "a@b.test" })).kind).toBe("unknown");
  });

  it("a server older than names and tags: both absent, still signed in, account tag unknown", async () => {
    expect(await answering({ mode: "dev-session", authenticated: true, email: "a@b.test" })).toEqual({
      kind: "signed_in",
      mode: "dev-session",
      label: "a@b.test",
      sessionTag: null,
    });
  });

  it.each([
    ["empty", ""],
    ["too long", "a".repeat(129)],
    ["not URL-safe", "tag with spaces"],
    ["a header injection", "abc\r\nX-Dev-User-Email: x@y.test"],
    ["a number", 12345],
    ["an object", { tag: "abc" }],
  ])("a session tag that is %s is no tag — an unknown account, not a refusal", async (_what, sessionTag) => {
    expect(await answering({ mode: "entra", authenticated: true, email: "a@b.test", sessionTag })).toEqual({
      kind: "signed_in",
      mode: "entra",
      label: "a@b.test",
      sessionTag: null,
    });
  });

  it("the longest tag it carries is 128 characters", () => {
    expect(isSessionTag("a".repeat(128))).toBe(true);
    expect(isSessionTag("a".repeat(129))).toBe(false);
    expect(isSessionTag("Az09_-")).toBe(true);
  });

  it.each([
    [{ status: 500, body: JSON.stringify({ detail: "Sign-in is misconfigured on this server.", code: "auth_misconfigured" }) }, /answered 500/],
    [{ status: 200, body: "<html>another app</html>", contentType: "text/html" }, /not the way EasyApply does/],
    [{ status: 200, body: JSON.stringify({ mode: "saml", authenticated: true, email: "a@b.test" }) }, /not the way EasyApply does/],
    [{ status: 200, body: JSON.stringify({ mode: "entra", authenticated: true }) }, /not the way EasyApply does/],
    [{ status: 200, body: JSON.stringify({ mode: "entra", authenticated: true, email: 7 }) }, /not the way EasyApply does/],
    [{ status: 200, body: JSON.stringify({ mode: "entra", authenticated: true, email: null, name: 7 }) }, /not the way EasyApply does/],
  ])("anything else is unknown, not signed in or out (%#)", async (response, message) => {
    const api = createFakeApi();
    api.respondNextWith(response, "/api/auth/session");
    const state = await getSession({ webUrl: WEB, fetch: api.fetch });
    expect(state.kind).toBe("unknown");
    expect(state.kind === "unknown" && state.message).toMatch(message);
  });

  it("nothing listening is unknown", async () => {
    const api = createFakeApi();
    api.respondNextWith("network-error", "/api/auth/session");
    expect(await getSession({ webUrl: WEB, fetch: api.fetch })).toEqual({
      kind: "unknown",
      message: "Could not reach EasyApply at http://localhost:3000.",
    });
  });

  it("sends people to the web app to sign in", () => {
    expect(signInUrl(WEB)).toBe("http://localhost:3000/");
  });
});

describe("isSessionState — what the popup accepts from the worker", () => {
  it.each([
    [{ kind: "signed_in", mode: "development", label: "a@b.test", sessionTag: "tagA" }, true],
    [{ kind: "signed_in", mode: "entra", label: "your EasyApply account", sessionTag: null }, true],
    [{ kind: "signed_out" }, true],
    [{ kind: "unknown", message: "down" }, true],
    [{ kind: "signed_in" }, false],
    [{ kind: "signed_in", mode: "entra", label: "a" }, false],
    [{ kind: "signed_in", mode: "entra", label: "a", sessionTag: "bad tag" }, false],
    [{ kind: "signed_in", mode: "saml", label: "a", sessionTag: null }, false],
    [{ kind: "signed_in", mode: "entra", email: "a@b.test", sessionTag: null }, false],
    [{ kind: "unknown" }, false],
    [{ kind: "admin" }, false],
    [undefined, false],
  ])("%j -> %s", (value, expected) => {
    expect(isSessionState(value)).toBe(expected);
  });
});
