"use client";

/**
 * Server state lives in TanStack Query, not in React state.
 *
 * The distinction matters here: a profile version is not UI state, it is a
 * cached copy of something the server owns. Every mutation invalidates rather
 * than patching a local copy, so what the screen shows after a write is what
 * the API actually stored — including the server-side ordering of skills and
 * the confirmed_at the server stamped.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
} from "@tanstack/react-query";
import type { ConfirmVersionRequest, Profile, ProfileVersion, SnapshotInput } from "@easyapply/shared";

import { ApiError } from "./client";
import * as api from "./profile";

export const queryKeys = {
  profile: ["profile"] as const,
  versions: ["profile", "versions"] as const,
  version: (versionNumber: number) => ["profile", "versions", versionNumber] as const,
  currentVersion: ["profile", "versions", "current"] as const,
};

/** A 404 here means "no profile yet", which is an empty state, not an error. */
function retryUnlessExpected(failureCount: number, error: unknown): boolean {
  if (error instanceof ApiError && (error.isSessionFailure || error.isNotFound || error.isConflict)) {
    return false;
  }
  return failureCount < 2;
}

export function useProfile() {
  return useQuery({
    queryKey: queryKeys.profile,
    queryFn: api.getProfile,
    retry: retryUnlessExpected,
  });
}

export function useVersions(enabled = true) {
  return useQuery({
    queryKey: queryKeys.versions,
    queryFn: api.listVersions,
    retry: retryUnlessExpected,
    enabled,
  });
}

export function useVersion(versionNumber: number) {
  return useQuery({
    queryKey: queryKeys.version(versionNumber),
    queryFn: () => api.getVersion(versionNumber),
    retry: retryUnlessExpected,
    enabled: Number.isFinite(versionNumber),
  });
}

export function useCurrentVersion(enabled = true) {
  return useQuery({
    queryKey: queryKeys.currentVersion,
    queryFn: api.getCurrentVersion,
    retry: retryUnlessExpected,
    enabled,
  });
}

export function useCreateProfile(): UseMutationResult<Profile, ApiError, void> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.createProfile,
    onSuccess: () => client.invalidateQueries({ queryKey: queryKeys.profile }),
  });
}

export function useCreateDraftVersion(): UseMutationResult<ProfileVersion, ApiError, void> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: api.createDraftVersion,
    onSuccess: () => client.invalidateQueries({ queryKey: ["profile"] }),
  });
}

export function useSaveSnapshot(
  versionNumber: number,
): UseMutationResult<ProfileVersion, ApiError, SnapshotInput> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (snapshot: SnapshotInput) => api.saveSnapshot(versionNumber, snapshot),
    onSuccess: () => client.invalidateQueries({ queryKey: ["profile"] }),
  });
}

export function useMakeVersionCurrent(
  versionNumber: number,
): UseMutationResult<ProfileVersion, ApiError, void> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api.makeVersionCurrent(versionNumber),
    // Promoting demotes whichever version was current, so — as with
    // confirming — the whole profile subtree is stale.
    onSuccess: () => client.invalidateQueries({ queryKey: ["profile"] }),
  });
}

export function useConfirmVersion(
  versionNumber: number,
): UseMutationResult<ProfileVersion, ApiError, ConfirmVersionRequest | undefined> {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload?: ConfirmVersionRequest) => api.confirmVersion(versionNumber, payload),
    // Confirming demotes a sibling version, so the whole subtree is stale.
    onSuccess: () => client.invalidateQueries({ queryKey: ["profile"] }),
  });
}
