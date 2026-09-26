import type { Snapshot } from "@easyapply/shared";
import { describe, expect, it } from "vitest";

import { render, screen } from "@/test/render";
import { emptySnapshot, filledSnapshot } from "@/test/fixtures";

import { SnapshotView } from "./snapshot-view";

function withExperience(overrides: Record<string, unknown>): Snapshot {
  return {
    ...emptySnapshot,
    experiences: [
      {
        company: "Flipkart",
        job_title: "Senior Backend Engineer",
        employment_type: null,
        location: null,
        start_date: "2022-03-01",
        start_date_precision: "month",
        end_date: null,
        end_date_precision: null,
        description: null,
        ...overrides,
      },
    ],
  } as Snapshot;
}

describe("SnapshotView date precision", () => {
  it("shows a month-precision date without inventing a day", () => {
    /** Regression: this rendered "Mar 1, 2022" for a resume that said
        "March 2022", putting back the false precision the schema prevents. */
    render(<SnapshotView snapshot={withExperience({})} />);

    expect(screen.getByText(/March 2022/)).toBeInTheDocument();
    expect(screen.queryByText(/Mar 1, 2022/)).not.toBeInTheDocument();
  });

  it("shows a year-precision date as just the year", () => {
    render(
      <SnapshotView
        snapshot={withExperience({
          start_date: "2020-01-01",
          start_date_precision: "year",
        })}
      />,
    );

    expect(screen.getByText(/2020 — Present/)).toBeInTheDocument();
    expect(screen.queryByText(/January/)).not.toBeInTheDocument();
  });

  it("shows a day-precision date in full, because that one is known", () => {
    render(
      <SnapshotView
        snapshot={withExperience({
          start_date: "2024-05-15",
          start_date_precision: "day",
        })}
      />,
    );

    expect(screen.getByText(/May 15, 2024/)).toBeInTheDocument();
  });

  it("marks an open-ended role as current rather than storing a flag", () => {
    render(<SnapshotView snapshot={withExperience({})} />);
    expect(screen.getByText(/Present/)).toBeInTheDocument();
  });
});

describe("SnapshotView contact, summary and links", () => {
  it("shows the candidate's name and how to reach them", () => {
    /** Regression: a resume's name, phone, address and summary were read off
        the page and then had nowhere to go, so the profile showed none of
        them. */
    render(<SnapshotView snapshot={filledSnapshot} />);

    expect(screen.getByText("K Subhash")).toBeInTheDocument();
    expect(screen.getByText(/subhash@example\.com/)).toBeInTheDocument();
    expect(screen.getByText(/\+91 6301998030/)).toBeInTheDocument();
    expect(screen.getByText(/Visakhapatnam, India/)).toBeInTheDocument();
  });

  it("shows the summary the resume wrote", () => {
    render(<SnapshotView snapshot={filledSnapshot} />);
    expect(screen.getByText(/focused on backend engineering/)).toBeInTheDocument();
  });

  it("shows each link under the words the resume wrote it as", () => {
    render(<SnapshotView snapshot={filledSnapshot} />);

    const github = screen.getByRole("link", { name: "GitHub" });
    expect(github).toHaveAttribute("href", "https://github.com/example");
    expect(screen.getByRole("link", { name: "LinkedIn" })).toHaveAttribute(
      "href",
      "https://www.linkedin.com/in/example/",
    );
  });

  it("shows a project's own links under that project", () => {
    render(<SnapshotView snapshot={filledSnapshot} />);

    expect(screen.getByRole("link", { name: "Repository" })).toHaveAttribute(
      "href",
      "https://github.com/example/easyapply",
    );
  });

  it("falls back to the URL when the resume printed no caption", () => {
    render(
      <SnapshotView
        snapshot={
          {
            ...emptySnapshot,
            links: [{ url: "https://example.com/cv", label: null }],
          } as Snapshot
        }
      />,
    );

    expect(
      screen.getByRole("link", { name: "https://example.com/cv" }),
    ).toBeInTheDocument();
  });

  it("does not call a profile empty when it holds contact details", () => {
    /** The empty check covered only the five career collections, so a profile
        carrying a name, a summary and every link still read "no career
        data". */
    render(
      <SnapshotView
        snapshot={
          {
            ...emptySnapshot,
            contact: {
              full_name: "K Subhash",
              email: null,
              phone: null,
              location: null,
            },
          } as Snapshot
        }
      />,
    );

    expect(screen.queryByText(/no career data yet/)).not.toBeInTheDocument();
    expect(screen.getByText("K Subhash")).toBeInTheDocument();
  });

  it("still says so when there is genuinely nothing", () => {
    render(<SnapshotView snapshot={emptySnapshot} />);
    expect(screen.getByText(/no career data yet/)).toBeInTheDocument();
  });
});
