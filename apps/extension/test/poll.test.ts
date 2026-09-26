import { pollUntil, REQUIREMENTS_POLL, sleep, TAILORING_POLL, type Sleep } from "../src/workflow/poll";

/** Records each wait instead of waiting. */
function instant() {
  const waits: number[] = [];
  const wait: Sleep = async (ms, signal) => {
    if (signal.aborted) throw signal.reason;
    waits.push(ms);
  };
  return { waits, wait };
}

describe("polling", () => {
  it("stops as soon as the answer is final", async () => {
    const { waits, wait } = instant();
    const answers = ["queued", "processing", "completed", "never-read"];
    const outcome = await pollUntil(async () => answers.shift(), (value) => value === "completed", {
      policy: { delaysMs: [10, 20, 30, 40] },
      signal: new AbortController().signal,
      wait,
    });
    expect(outcome).toEqual({ value: "completed", settled: true, checks: 3 });
    expect(waits).toEqual([10, 20]);
    expect(answers).toEqual(["never-read"]);
  });

  it("is bounded: when the budget runs out it says so rather than pretending", async () => {
    const { waits, wait } = instant();
    let checks = 0;
    const outcome = await pollUntil(
      async () => {
        checks += 1;
        return "processing";
      },
      () => false,
      { policy: { delaysMs: [1, 2, 3] }, signal: new AbortController().signal, wait },
    );
    expect(outcome).toEqual({ value: "processing", settled: false, checks: 4 });
    expect(checks).toBe(4);
    expect(waits).toEqual([1, 2, 3]);
  });

  it("reports progress between checks", async () => {
    const { wait } = instant();
    const answers = ["queued", "generating", "completed"];
    const seen: string[] = [];
    await pollUntil(async () => answers.shift() ?? "", (value) => value === "completed", {
      policy: { delaysMs: [1, 1, 1] },
      signal: new AbortController().signal,
      wait,
      onProgress: (value) => seen.push(value),
    });
    expect(seen).toEqual(["queued", "generating"]);
  });

  it("stops when aborted, and a late answer is not returned", async () => {
    const controller = new AbortController();
    const { wait } = instant();
    let checks = 0;
    const polling = pollUntil(
      async () => {
        checks += 1;
        if (checks === 2) controller.abort(new Error("the view moved on"));
        return "processing";
      },
      () => false,
      { policy: { delaysMs: [1, 1, 1, 1] }, signal: controller.signal, wait },
    );
    await expect(polling).rejects.toThrow("the view moved on");
    expect(checks).toBe(2);
  });

  it("backs off and stays within a known number of requests", () => {
    for (const policy of [REQUIREMENTS_POLL, TAILORING_POLL]) {
      const delays = policy.delaysMs;
      expect(delays.length).toBeLessThanOrEqual(12);
      for (let index = 1; index < delays.length; index += 1) {
        expect(delays[index]).toBeGreaterThanOrEqual(delays[index - 1] ?? 0);
      }
      expect(delays[0]).toBeGreaterThanOrEqual(1_000);
    }
    const total = (delays: readonly number[]) => delays.reduce((sum, ms) => sum + ms, 0);
    expect(total(REQUIREMENTS_POLL.delaysMs)).toBeLessThanOrEqual(60_000);
    expect(total(TAILORING_POLL.delaysMs)).toBeLessThanOrEqual(180_000);
  });

  it("has a real timer that an abort cuts short", async () => {
    const controller = new AbortController();
    const started = Date.now();
    const waiting = sleep(10_000, controller.signal);
    controller.abort(new Error("closed"));
    await expect(waiting).rejects.toThrow("closed");
    expect(Date.now() - started).toBeLessThan(1_000);
  });
});
