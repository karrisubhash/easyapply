import { ADAPTERS, extractJob, findAdapter, supportedSites } from "../src/extraction/registry";
import { JOB_URL, jobDocument } from "./support/fixtures";

describe("the adapter registry", () => {
  it("supports exactly two sites in this version: Greenhouse and Lever", () => {
    expect(ADAPTERS.map((adapter) => adapter.id)).toEqual(["greenhouse", "lever"]);
    expect(supportedSites()).toBe("Greenhouse, Lever");
  });

  it("finds the adapter for a supported URL, and none otherwise", () => {
    expect(findAdapter(new URL(JOB_URL))?.id).toBe("greenhouse");
    expect(findAdapter(new URL("https://jobs.lever.co/acme/3f2a9c1e-5b7d-4e8a-9c0f-1a2b3c4d5e6f"))?.id).toBe("lever");
    expect(findAdapter(new URL("https://www.linkedin.com/jobs/view/123"))).toBeNull();
    // Only the hosts that were checked: not Lever's other hosts, not a look-alike.
    expect(findAdapter(new URL("https://jobs.eu.lever.co/acme/3f2a9c1e-5b7d-4e8a-9c0f-1a2b3c4d5e6f"))).toBeNull();
    expect(findAdapter(new URL("https://jobs.lever.co.evil.example/acme/x"))).toBeNull();
    expect(findAdapter(new URL("http://jobs.lever.co/acme/3f2a9c1e-5b7d-4e8a-9c0f-1a2b3c4d5e6f"))).toBeNull();
  });

  it.each(["https://www.linkedin.com/jobs/view/123", "https://example.com/careers", "not a url", ""])(
    "reports %j as unsupported rather than guessing",
    (href) => {
      expect(extractJob(jobDocument(), href)).toEqual({
        status: "unsupported",
        message: "EasyApply can't read jobs on this page yet. Supported: Greenhouse, Lever job boards.",
      });
    },
  );

  it("turns an adapter that throws into a failure, not a crash", () => {
    const adapter = ADAPTERS[0];
    if (!adapter) throw new Error("no adapter");
    const spy = vi.spyOn(adapter, "extract").mockImplementation(() => {
      throw new Error("selector engine exploded");
    });
    try {
      expect(extractJob(jobDocument(), JOB_URL)).toEqual({
        status: "failed",
        site: "Greenhouse",
        missing: [],
        message: "Reading this Greenhouse page failed unexpectedly. Nothing has been saved.",
      });
    } finally {
      spy.mockRestore();
    }
  });
});
