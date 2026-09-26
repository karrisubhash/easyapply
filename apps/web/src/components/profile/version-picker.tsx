"use client";

import type { ProfileVersionSummary } from "@easyapply/shared";
import * as React from "react";

import { Label } from "@/components/ui/label";
import { ApiError } from "@/lib/api/client";
import { useVersions } from "@/lib/api/queries";

/**
 * Which confirmed profile version to use.
 *
 * Only confirmed versions are offered, and drafts are not shown as disabled
 * options either — a picker that lists something it will not accept is a picker
 * that invites a 409. The backend refuses a draft regardless; this is the UI
 * agreeing with it rather than the UI enforcing it.
 *
 * `undefined` means "the server's current confirmed version", which is a real
 * and useful answer rather than an absence: it is well-defined, immutable, and
 * it is what the API uses when the field is omitted. So the default option says
 * so in words instead of leaving the control empty.
 *
 * With exactly one confirmed version there is no choice to make, so the
 * component states which version will be used rather than rendering a select
 * with a single option. It still has to say it: a page that silently builds
 * from an unnamed snapshot is asking the user to trust a document they cannot
 * trace, which is the one thing every screen in this flow is arranged to avoid.
 *
 * With NO confirmed version it renders nothing at all. That message belongs to
 * the page, because the page is what must also withhold its primary action; a
 * picker that rendered its own notice put two identical "Go to profile" buttons
 * on the match page and left a Match button beside them that could only ever
 * 409. `useConfirmedVersions` below is how a page asks the question once and
 * answers it in one place.
 *
 * With confirmed versions but NONE of them current — a version confirmed with
 * "Make this the current version" unticked — "the server's current version" is
 * not an answer at all: omitting the id gets a 409 saying nothing is confirmed.
 * So there is no "Current" option then, the newest confirmed version is what
 * the control shows, and `resolveVersion` makes the page send that id
 * explicitly rather than an omission the server cannot resolve.
 */
export function VersionPicker({
  value,
  onChange,
  label = "Profile version",
}: {
  value: number | undefined;
  onChange: (versionId: number | undefined) => void;
  label?: string;
}) {
  const { confirmed, current } = useConfirmedVersions();
  const id = React.useId();

  // No confirmed version at all is the page's problem to explain — it has to
  // withhold its primary action too, which this component cannot do.
  if (confirmed.length === 0) return null;

  // A choice between one thing is not a choice. State it instead of rendering a
  // select with one option: the page still has to SAY which version it will
  // build from, or the user is trusting an unnamed snapshot.
  if (confirmed.length === 1) {
    const only = confirmed[0];
    return (
      <p className="text-sm text-muted-foreground">
        {label}:{" "}
        <span className="font-medium text-foreground">
          {only.is_current
            ? `current confirmed profile (version ${only.version_number})`
            : `version ${only.version_number}`}
        </span>
      </p>
    );
  }

  // Newest first: the version someone wants is almost always the one they just
  // confirmed, and the API returns them ascending.
  const options = [...confirmed].reverse();

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <select
        id={id}
        className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm sm:w-auto sm:min-w-64"
        // With nothing current there is no "" option to show, so the control
        // shows the version `resolveVersion` will actually send.
        value={value ?? (current ? "" : options[0].id)}
        onChange={(event) =>
          onChange(event.target.value === "" ? undefined : Number(event.target.value))
        }
      >
        {current ? (
          <option value="">
            Current confirmed profile (version {current.version_number})
          </option>
        ) : null}
        {options.map((version) => (
          // `id` is the value the API takes and `version_number` is the one a
          // person reads. They are different numbers on the same row, and
          // sending the wrong one would address a valid version belonging to
          // somebody else — resolved server-side to a 404, but still a bug
          // worth not writing.
          <option key={version.id} value={version.id}>
            Version {version.version_number}
            {version.is_current ? " · current" : ""}
          </option>
        ))}
      </select>
    </div>
  );
}

export interface ConfirmedVersions {
  confirmed: ProfileVersionSummary[];
  /** The one marked current, if any is. Confirmed versions can exist without one. */
  current: ProfileVersionSummary | undefined;
  isPending: boolean;
  /** No profile at all, as opposed to a profile with nothing confirmed in it. */
  noProfile: boolean;
  /**
   * Whether anything can be matched or tailored yet.
   *
   * False is the state both the match and tailor pages have to handle before
   * offering their primary action: the API answers 409 without a confirmed
   * version, and a button whose only outcome is an error is a button that
   * should not be there.
   *
   * Also false while the list could not be READ — which is not the same fact,
   * and `isError` is how a page tells the two apart. Reporting a failed request
   * as "you have no confirmed version" sent people to confirm a profile that was
   * already confirmed.
   */
  hasConfirmedVersion: boolean;
  /** The list could not be read, for a reason other than there being no profile. */
  isError: boolean;
  error: unknown;
  refetch: () => void;
}

/**
 * The confirmed versions, as a page needs to reason about them.
 *
 * Shares the query key with the picker, so a page using both makes one request.
 */
export function useConfirmedVersions(): ConfirmedVersions {
  const versions = useVersions();

  const noProfile =
    versions.isError && versions.error instanceof ApiError && versions.error.isNotFound;
  const failed = versions.isError && !noProfile;

  const confirmed = (versions.data ?? []).filter(
    (version) => version.status === "confirmed",
  );

  return {
    confirmed,
    current: confirmed.find((version) => version.is_current),
    isPending: versions.isPending,
    noProfile,
    hasConfirmedVersion: confirmed.length > 0,
    isError: failed,
    error: failed ? versions.error : null,
    refetch: () => void versions.refetch(),
  };
}

/**
 * What the picker's value means, and what to put on the wire for it.
 *
 * `version` is the confirmed version the action will use — what a page shows
 * results for and names. `send` is the id for the request body: omitted for
 * "current", which is the server's own well-defined default, and explicit in
 * every other case. In particular it is explicit when nothing is current — the
 * server cannot resolve an omission then, and answers 409 — which is why the
 * default there is the newest confirmed version, the one the picker shows.
 *
 * `version` is undefined only while the list is unknown (loading or failed) or
 * empty, or when `value` names a version that is not a confirmed one of these.
 */
export function resolveVersion(
  value: number | undefined,
  { confirmed, current }: Pick<ConfirmedVersions, "confirmed" | "current">,
): { version: ProfileVersionSummary | undefined; send: number | undefined } {
  if (value !== undefined) {
    return { version: confirmed.find((version) => version.id === value), send: value };
  }
  if (current) return { version: current, send: undefined };
  const newest = confirmed[confirmed.length - 1];
  return { version: newest, send: newest?.id };
}

/** A positive integer id from a query-string value, or undefined for anything else. */
export function idParam(raw: string | null | undefined): number | undefined {
  const value = Number(raw);
  return raw && Number.isInteger(value) && value > 0 ? value : undefined;
}
