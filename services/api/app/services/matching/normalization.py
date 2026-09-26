"""Turning two spellings of the same thing into one token — and nothing more.

Normalization is where a matching engine quietly becomes untrustworthy. Every
alias added here is a claim that two strings mean the same thing, and a wrong
claim produces a confident MATCHED against a skill the candidate does not have
— the single worst output this system can produce, because unlike a miss it is
invisible to the person reading it.

So the rules are deliberately mechanical and the alias table is deliberately
small:

  1. Case, whitespace and punctuation are typography, not meaning. "PostgreSQL",
     "postgresql" and "Postgre SQL " are one token.
  2. A trailing version is a version, not a different technology. "Python 3",
     "Java 11", "Angular 2+" normalize to their base. "C++" and "C#" do NOT —
     the symbols are part of the name, which is why punctuation stripping keeps
     `+` and `#`.
  3. Everything else needs an explicit entry in ALIASES, with a justification
     in the comment beside it.

What is NOT here matters as much as what is. These pairs stay distinct, and
the test suite pins each one:

    python      != django          a language is not a framework written in it
    react       != react native    sharing a name is not sharing a platform
    postgresql  != mysql           both SQL, different products
    java        != javascript      the classic, and the most expensive to get wrong
    aws         != azure

None of those are special-cased anywhere; they are distinct simply because no
alias claims otherwise. That is the design: equivalence is opt-in, one
justified line at a time, and the default is that two different strings are two
different things.

Matching is whole-token, never substring. "go" as a canonical token can only
match a skill whose entire name is Go; it cannot fire on "Django" or "MongoDB"
the way a substring scan would. That is what makes short tokens safe here even
though `app/services/job/structuring.py` had to exclude them from its own
prose-scanning alias list.
"""

from __future__ import annotations

import re

#: Characters that are part of a technology's name rather than punctuation
#: around it. Everything else in the ASCII punctuation range is separator.
_KEPT_SYMBOLS = "+#"

_PUNCTUATION = re.compile(r"[^\w\s+#]+", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")

#: "Python 3", "Java 11", "Angular 2", "Vue 3", ".NET 6" — a trailing numeric
#: version, optionally with a `+` or a minor part. Anchored at the end so
#: "3D modelling" and "Web 2.0 consulting" keep their words.
_TRAILING_VERSION = re.compile(r"\s+v?\d+(?:\.\d+)*\+?$")

#: Explicit equivalences. Every entry maps a spelling to the canonical token it
#: means, and every entry carries its justification. Keys are already
#: case/whitespace/punctuation-normalized, because this table is consulted
#: AFTER the mechanical rules run.
#:
#: The bar for adding an entry: the two strings must name the same thing to a
#: hiring manager, not merely related things. "postgres" and "postgresql" pass.
#: "react" and "react native" do not.
ALIASES: dict[str, str] = {
    # --- the same product, different spellings ---
    "postgres": "postgresql",
    "psql": "postgresql",
    "postgre sql": "postgresql",
    "mongo": "mongodb",
    "k8s": "kubernetes",
    "golang": "go",
    "node": "node.js",
    "nodejs": "node.js",
    "node js": "node.js",
    # --- abbreviations that are unambiguous in a skills context ---
    # "js" and "ts" are only ever consulted as a WHOLE token here, so they
    # cannot fire inside another word the way a prose scan's would.
    "js": "javascript",
    "ts": "typescript",
    "py": "python",
    # --- vendor names written out in full ---
    "amazon web services": "aws",
    "google cloud": "gcp",
    "google cloud platform": "gcp",
    "microsoft azure": "azure",
    # --- punctuation-normalized spellings that collapse to the same thing ---
    # `.` is stripped as punctuation, so "Node.js" arrives here as "node js"
    # and "React.js" as "react js"; these entries put them back together.
    "react js": "react",
    "reactjs": "react",
    "vue js": "vue",
    "vuejs": "vue",
    "next js": "next.js",
    "nextjs": "next.js",
    "dotnet": ".net",
    "net": ".net",
    "csharp": "c#",
    "c sharp": "c#",
    "cplusplus": "c++",
    # --- spelled-out forms of acronyms ---
    "continuous integration": "ci",
    "ci cd": "ci/cd",
    "cicd": "ci/cd",
    "restful": "rest",
    "rest api": "rest",
    "restful api": "rest",
}


def canonical(text: str | None) -> str:
    """The canonical token for a skill, technology or certification name.

    Mechanical rules first, then the alias table. Returns "" for anything that
    normalizes to nothing, which callers treat as "no token" rather than as a
    token that matches other empty strings.
    """
    if not text:
        return ""

    lowered = text.strip().casefold()
    # Separators become spaces rather than vanishing, so "react/redux" is two
    # words rather than the single nonsense token "reactredux".
    despunctuated = _PUNCTUATION.sub(" ", lowered)
    collapsed = _WHITESPACE.sub(" ", despunctuated).strip()
    if not collapsed:
        return ""

    # Applied before the alias lookup so "Python 3" and "python" hit the same
    # key, and repeatedly so "Angular 2.1+" loses its whole version tail.
    stripped = _TRAILING_VERSION.sub("", collapsed).strip()
    while stripped != collapsed:
        collapsed = stripped
        stripped = _TRAILING_VERSION.sub("", collapsed).strip()

    return ALIASES.get(collapsed, collapsed)


def canonical_words(text: str | None) -> list[str]:
    """The canonical tokens of each word in a phrase.

    Used for scanning prose (an experience description, a requirement
    sentence) for a technology mention. Word-boundary based, never substring:
    "Django REST API development" yields {"django", "rest", "api",
    "development"}, and a lookup for "go" cannot match any of them.
    """
    if not text:
        return []
    lowered = text.strip().casefold()
    despunctuated = _PUNCTUATION.sub(" ", lowered)
    return [word for word in _WHITESPACE.split(despunctuated) if word]


def phrase_variants(text: str | None) -> set[str]:
    """Canonical tokens a phrase could be referring to.

    Covers the single-word case ("Python"), the whole-phrase case ("Amazon Web
    Services", "Node.js" once punctuation-normalized), and adjacent word pairs,
    which is what lets "google cloud platform" and "rest api" find their alias
    entries inside a longer sentence.

    Adjacent pairs and triples only — not every subset — because an
    unordered-subset search over a sentence is how "experience with Java, and
    separately with Script-based tooling" becomes "javascript".
    """
    words = canonical_words(text)
    if not words:
        return set()

    variants: set[str] = set()
    whole = canonical(" ".join(words))
    if whole:
        variants.add(whole)

    for size in (1, 2, 3):
        for start in range(len(words) - size + 1):
            token = canonical(" ".join(words[start : start + size]))
            if token:
                variants.add(token)

    return variants


def is_alias_of(left: str | None, right: str | None) -> bool:
    """Whether two names mean the same thing under the rules above."""
    left_token, right_token = canonical(left), canonical(right)
    return bool(left_token) and left_token == right_token
