import { blockText, cleanLabel, cleanMultiline, labelText } from "../src/extraction/text";
import { parseDocument } from "./support/fixtures";

function text(html: string): string {
  return blockText(parseDocument(`<div id="root">${html}</div>`).getElementById("root"));
}

describe("cleanLabel — the API's _clean_label, applied before the review screen", () => {
  it.each([
    ["  Senior   Backend\n\tEngineer  ", "Senior Backend Engineer"],
    ["Acme Robotics", "Acme Robotics"],
    ["Remote,\u0000 Berlin\u0007", "Remote, Berlin"],
    ["", ""],
    ["   ", ""],
  ])("%j -> %j", (input, expected) => {
    expect(cleanLabel(input)).toBe(expected);
  });

  it("treats null and undefined as empty", () => {
    expect(cleanLabel(null)).toBe("");
    expect(cleanLabel(undefined)).toBe("");
  });
});

describe("cleanMultiline — tidy, never reword", () => {
  it("unifies line endings, drops controls, trims line ends and collapses blank runs", () => {
    expect(cleanMultiline("  First\r\nSecond \r\n\r\n\r\n\r\nThird\u0000 line\t\n\n")).toBe(
      "First\nSecond\n\nThird line",
    );
  });

  it("keeps tabs inside a line and every word", () => {
    expect(cleanMultiline("a\tb — c")).toBe("a\tb — c");
  });
});

describe("blockText — page markup to the text the structurer reads", () => {
  it("separates paragraphs with a blank line and keeps inline text on one line", () => {
    expect(text("<p>One <strong>bold</strong> <em>claim</em>.</p><p>Two.</p>")).toBe("One bold claim.\n\nTwo.");
  });

  it("turns <br> into a line break, and two into a blank line", () => {
    expect(text("<p>a<br>b<br><br>c</p>")).toBe("a\nb\n\nc");
  });

  it("puts headings on their own lines", () => {
    expect(text("<p>Intro</p><h3>Requirements</h3><p>Python</p>")).toBe("Intro\n\nRequirements\n\nPython");
  });

  it("marks unordered items with '- ' and ordered items with their number", () => {
    expect(text("<ul><li>Python</li><li>SQL</li></ul><ol start='3'><li>Go</li><li>Rust</li></ol>")).toBe(
      "- Python\n- SQL\n\n3. Go\n4. Rust",
    );
  });

  it("keeps an item's text beside its marker when it is wrapped in <p>", () => {
    expect(text("<ul><li><p>Python</p></li><li><p>SQL</p><p>and more</p></li></ul>")).toBe(
      "- Python\n- SQL\nand more",
    );
  });

  it("indents nested lists", () => {
    expect(text("<ul><li>Backend<ul><li>Python</li><li>Go</li></ul></li><li>Data</li></ul>")).toBe(
      "- Backend\n  - Python\n  - Go\n- Data",
    );
  });

  it("does not turn empty wrappers into blank lines", () => {
    expect(text("<div><div><div></div><p>Only</p></div><div> </div></div>")).toBe("Only");
  });

  it("skips scripts, styles, SVG, forms, buttons and hidden content", () => {
    expect(
      text(
        "<p>Seen</p><script>no()</script><style>.no{}</style><svg><title>no</title></svg>" +
          "<form><label>no</label><input value='no'></form><button>no</button>" +
          "<p hidden>no</p><p style='display:none'>no</p><p>Also seen</p>",
      ),
    ).toBe("Seen\n\nAlso seen");
  });

  it("reads table rows as lines with cells separated", () => {
    expect(text("<table><tr><th>Level</th><th>Range</th></tr><tr><td>L3</td><td>$100k</td></tr></table>")).toBe(
      "Level | Range\nL3 | $100k",
    );
  });

  it("returns an empty string for a missing element", () => {
    expect(blockText(null)).toBe("");
    expect(labelText(undefined)).toBe("");
  });
});
