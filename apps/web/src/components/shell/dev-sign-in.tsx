"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { parseBody } from "@/lib/api/client";

/**
 * The development sign-in, shown in place of every page when this server runs
 * AUTH_MODE=dev-session and the browser has no session.
 *
 * Says plainly what it is. There is no password because there is nothing to
 * check it against: this issues a session for whatever address is typed, so
 * that the per-browser session both the web app and the Chrome extension
 * present can be used before a real identity provider issues it.
 *
 * A full reload on success, not a client-side refresh: every cached query in
 * this tab belongs to whoever was here before, and the only way to be sure
 * none of it survives into the next person's view is to start the page again.
 */
export function DevSignIn({
  suggestedEmail,
  onSignedIn = () => window.location.reload(),
}: {
  suggestedEmail: string | null;
  /** What happens once the cookie is set. A full reload; replaceable in tests. */
  onSignedIn?: () => void;
}) {
  const [email, setEmail] = React.useState(suggestedEmail ?? "");
  const [error, setError] = React.useState<string | null>(null);
  const [pending, setPending] = React.useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const response = await fetch("/api/auth/sign-in", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ email }),
      });
      if (response.ok) {
        onSignedIn();
        return;
      }
      const body = parseBody(await response.text()) as { detail?: unknown } | null;
      setError(typeof body?.detail === "string" ? body.detail : "Signing in failed.");
    } catch {
      setError("The EasyApply web app did not answer.");
    }
    setPending(false);
  }

  return (
    <Card className="mx-auto max-w-md">
      <CardHeader>
        {/* The page's one heading: this is what every page is while signed out. */}
        <h1 className="text-lg font-semibold leading-none tracking-tight">Sign in</h1>
        <CardDescription>
          Development sign-in: there is no password, and any address works. Everything you do here —
          and every job the EasyApply extension saves from this browser — belongs to the address you
          enter.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={submit} className="space-y-3" noValidate>
          <div className="space-y-1">
            <Label htmlFor="sign-in-email">Email</Label>
            <Input
              id="sign-in-email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? "sign-in-error" : undefined}
            />
          </div>
          {error ? (
            <p id="sign-in-error" role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
          <Button type="submit" disabled={pending}>
            {pending ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
