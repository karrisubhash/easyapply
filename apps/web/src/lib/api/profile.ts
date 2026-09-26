/**
 * The Profile API, one function per endpoint.
 *
 * Every signature is written in terms of the generated contract, so a backend
 * schema change becomes a TypeScript error here rather than a runtime surprise
 * in a component.
 */

import type {
  ConfirmVersionRequest,
  Profile,
  ProfileVersion,
  ProfileVersionSummary,
  SnapshotInput,
} from "@easyapply/shared";

import { request } from "./client";

export function getProfile(): Promise<Profile> {
  return request<Profile>("profile");
}

export function createProfile(): Promise<Profile> {
  return request<Profile>("profile", { method: "POST" });
}

export function listVersions(): Promise<ProfileVersionSummary[]> {
  return request<ProfileVersionSummary[]>("profile/versions");
}

export function getVersion(versionNumber: number): Promise<ProfileVersion> {
  return request<ProfileVersion>(`profile/versions/${versionNumber}`);
}

export function getCurrentVersion(): Promise<ProfileVersion> {
  return request<ProfileVersion>("profile/versions/current");
}

export function createDraftVersion(): Promise<ProfileVersion> {
  return request<ProfileVersion>("profile/versions", { method: "POST" });
}

export function saveSnapshot(
  versionNumber: number,
  snapshot: SnapshotInput,
): Promise<ProfileVersion> {
  return request<ProfileVersion>(`profile/versions/${versionNumber}/snapshot`, {
    method: "PUT",
    body: JSON.stringify(snapshot),
  });
}

/** Promote an already-confirmed version to current. 409 for a draft. */
export function makeVersionCurrent(versionNumber: number): Promise<ProfileVersion> {
  return request<ProfileVersion>(`profile/versions/${versionNumber}/make-current`, {
    method: "POST",
  });
}

export function confirmVersion(
  versionNumber: number,
  payload: ConfirmVersionRequest = { make_current: true },
): Promise<ProfileVersion> {
  return request<ProfileVersion>(`profile/versions/${versionNumber}/confirm`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
