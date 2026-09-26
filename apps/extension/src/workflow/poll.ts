/**
 * Asking again, a bounded number of times, further apart each time.
 *
 * Used for the two things that can still be moving when the popup looks: a
 * job whose requirements are being read, and a tailoring run. The backend is
 * authoritative about both; the popup only re-reads it. Three rules:
 *
 *   - Bounded. Each policy is a fixed list of waits, so a run that never ends
 *     costs a known number of requests — and then the popup says it is still
 *     working and offers "Check again", rather than pretending it finished or
 *     asking forever.
 *   - Backing off. The first re-check is soon (most runs finish in seconds);
 *     later ones are tens of seconds apart.
 *   - Cancellable. The popup aborts a poll when what it is polling for stops
 *     being what is on screen (a different account, a new profile version, the
 *     popup closing), so a late answer is never written into someone else's
 *     view.
 */

export interface PollPolicy {
  /** The wait before each re-check, in order. Its length is the number of re-checks. */
  readonly delaysMs: readonly number[];
}

/** Job structuring is inline in the save today; this covers AI structuring and a reprocess seen mid-run. About a minute in all. */
export const REQUIREMENTS_POLL: PollPolicy = { delaysMs: [1_000, 2_000, 3_000, 5_000, 8_000, 13_000, 20_000] };

/** Tailoring runs on a worker; the deterministic writer takes seconds, a model can take minutes. About three minutes in all. */
export const TAILORING_POLL: PollPolicy = {
  delaysMs: [1_500, 2_000, 3_000, 5_000, 8_000, 10_000, 15_000, 15_000, 20_000, 20_000, 30_000, 30_000],
};

export type Sleep = (ms: number, signal: AbortSignal) => Promise<void>;

/** A timer that an abort cuts short. Rejects with the signal's reason. */
export const sleep: Sleep = (ms, signal) =>
  new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason);
      return;
    }
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });

export interface PollOutcome<T> {
  value: T;
  /** False when the budget ran out before `done` said yes. */
  settled: boolean;
  /** How many times `check` ran. */
  checks: number;
}

/**
 * Run `check` now and after each wait of the policy, until `done` says the
 * answer is final. `onProgress` sees every answer that was not final, so the
 * screen can show the run's current stage while it waits.
 *
 * An abort stops it between checks and rejects with the signal's reason; a
 * check already in flight is left to finish, and its answer is dropped.
 */
export async function pollUntil<T>(
  check: () => Promise<T>,
  done: (value: T) => boolean,
  {
    policy,
    signal,
    wait = sleep,
    onProgress,
  }: { policy: PollPolicy; signal: AbortSignal; wait?: Sleep; onProgress?: (value: T, checks: number) => void },
): Promise<PollOutcome<T>> {
  let checks = 0;
  let value = await check();
  checks += 1;
  if (signal.aborted) throw signal.reason;
  for (const delay of policy.delaysMs) {
    if (done(value)) return { value, settled: true, checks };
    onProgress?.(value, checks);
    await wait(delay, signal);
    value = await check();
    checks += 1;
    if (signal.aborted) throw signal.reason;
  }
  return { value, settled: done(value), checks };
}
