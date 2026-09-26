"""What counts as a technology, and what is just a word.

matching_v1 decided what an experience requirement was ABOUT by removing a
stopword list and keeping whatever was left. The real-data benchmark showed
exactly how that fails: "3+ years building production services with Django"
kept `building` as a subject, and against a profile whose unrelated role said
"building credit-risk models" the engine attributed 7.7 years of Django
experience to someone who had never used it. The same defect ran the other
way — "5+ years of professional software engineering experience" attributed
only the one role whose title happened to contain the word "software", so a
candidate with 7.3 continuous years was measured at 1.7.

The fix is to stop treating "not a stopword" as "a technology". A word is a
technology subject here only if it is one of:

    TECHNOLOGIES        a curated, canonical vocabulary — explicit and
                        reviewable, the same posture `normalization.ALIASES`
                        takes toward equivalence
    the profile's own   whatever the candidate declared as a skill or attached
    declarations        to a project. This is what keeps the vocabulary from
                        having to be exhaustive: a profile that declares
                        "Svelte" makes Svelte resolvable for that match
                        without anyone editing this file.

Everything else is not a subject. `building`, `services`, `production`,
`working`, `designing` name the ACT of the work, not the thing worked with,
and a requirement made only of those is a requirement about employment in
general rather than about any particular technology.

Nothing here invents equivalence. The tokens below are already-canonical forms
— what `normalization.canonical()` produces — so adding a spelling variant
belongs in `ALIASES`, not here. This file answers one question only: is this
token the name of a technology at all?
"""

from __future__ import annotations

import re

#: Words describing the KIND of work rather than a technology. A duration
#: requirement whose content words are all drawn from here is a question about
#: professional employment as such — see `rules.judge_experience`.
#:
#: The line: a word belongs here when it could appear in the title of a job in
#: any technology stack. "backend" and "infrastructure" qualify; "Django" and
#: "Kubernetes" do not. Domain words ("fintech", "healthcare") are deliberately
#: ABSENT — domain matching is a separate decision and treating a domain as
#: generic would quietly answer it with total employment.
GENERIC_WORK_WORDS = frozenset(
    {
        # the act of working
        "building", "build", "built", "develop", "developing", "development",
        "designing", "design", "creating", "create", "delivering", "deliver",
        "working", "work", "shipping", "ship", "maintaining", "maintain",
        "writing", "write", "implementing", "implement", "engineering",
        "programming", "coding", "architecting",
        # what is produced
        "software", "services", "service", "systems", "system", "applications",
        "application", "apps", "app", "products", "product", "solutions",
        "solution", "code", "codebase", "platforms", "platform", "tools",
        "tooling", "features", "feature",
        # the kind of engineer
        "engineer", "engineers", "developer", "developers", "programmer",
        "backend", "frontend", "fullstack", "full", "stack", "web", "mobile",
        "infrastructure", "devops", "sre", "reliability", "data", "technical",
        "technology", "tech", "it",
        # qualities of the work, not the work
        "production", "professional", "commercial", "enterprise", "scalable",
        "scale", "modern", "complex", "large", "high", "quality", "senior",
        "lead", "principal", "staff", "junior", "mid", "level",
        "end", "ends", "side", "sides", "front", "back",
    }
)

#: Verbs and outcomes that describe NOT having done something. A technology
#: named in a sentence carrying one of these is a mention, never a
#: demonstration — "Evaluated React Native for a companion app, but the project
#: was cancelled" says the opposite of what a keyword scan would read out of it.
#:
#: Deliberately small and deliberately one-sided: every entry is a word that
#: only ever weakens a claim, so a false positive here costs an UNCERTAIN
#: rather than an invented MATCHED.
NON_DOING_MARKERS = frozenset(
    {
        "evaluated", "evaluating", "evaluation", "considered", "considering",
        "explored", "exploring", "investigated", "investigating", "trialled",
        "trialed", "piloted", "prototyped", "researched",
        "cancelled", "canceled", "abandoned", "shelved", "rejected",
        "deprecated", "retired", "retiring", "sunset", "decommissioned",
        "planned", "planning", "proposed", "upcoming", "intend", "intends",
        "migrating away", "moved away", "moving away", "replacing",
        "learning", "learn", "studying", "familiar", "familiarity",
        "exposure", "interested", "curious", "keen",
    }
)

#: Names in which a NON_DOING_MARKERS word is part of what the work was ABOUT,
#: not a caveat about it.
#:
#: "learn" and "learning" are in the marker list because "currently learning
#: Rust" is a claim about the future. But `scikit-learn` is the name of a
#: library and `machine learning` is the name of a field, and reading the
#: marker out of either inverts the sentence: "Demand forecasting models for
#: logistics clients, in Python and scikit-learn" describes work that was
#: done, and it was being graded as a disclaimer that it had not been.
#:
#: Written with single spaces; `is_non_doing` matches them across any run of
#: spaces or hyphens, so one entry covers "scikit-learn" and "scikit learn"
#: alike. Entries stay curated rather than derived because the question each
#: one answers — is this word a name here, or a caveat? — is a judgement.
#:
#: Deliberately NOT part of TECHNOLOGIES. These names are neutralised for
#: non-doing analysis only; making "machine learning" a technology would let a
#: requirement resolve its subject to a field, which is a different decision
#: from this one. `assert_markers_do_not_shadow_names` holds the two in step.
MARKER_BEARING_NAMES = frozenset(
    {
        # libraries whose own name carries a marker word
        "scikit learn",
        # fields and disciplines, none of which are technology subjects
        "machine learning",
        "deep learning",
        "reinforcement learning",
        "supervised learning",
        "unsupervised learning",
        "transfer learning",
        "federated learning",
        "statistical learning",
        "learning management system",
    }
)

#: The curated technology vocabulary, in canonical form.
#:
#: Not exhaustive and not trying to be — the union with a profile's own
#: declarations is what makes coverage adequate. What this list must be is
#: CORRECT: every entry names a technology, so that resolving a requirement
#: against it cannot invent a subject. Entries are grouped only to make review
#: possible.
TECHNOLOGIES: frozenset[str] = frozenset(
    {
        # languages
        "python", "java", "javascript", "typescript", "go", "rust", "ruby",
        "php", "c", "c++", "c#", ".net", "scala", "kotlin", "swift", "objective c",
        "perl", "r", "matlab", "elixir", "erlang", "haskell", "clojure",
        "groovy", "lua", "dart", "julia", "solidity", "bash", "powershell",
        "sql", "plsql", "t sql", "html", "css", "sass", "scss", "less",
        # web and application frameworks
        "django", "flask", "fastapi", "pyramid", "tornado", "rails",
        "ruby on rails", "laravel", "symfony", "spring", "spring boot",
        "express", "nest.js", "node.js", "deno", "next.js", "nuxt", "remix",
        "react", "react native", "angular", "angularjs", "vue", "svelte",
        "sveltekit", "ember", "backbone", "jquery", "htmx", "blazor",
        "asp.net", "gin", "echo", "fiber", "actix", "axum", "phoenix",
        "struts", "hibernate", "jpa", "entity framework",
        # mobile
        "android", "ios", "flutter", "xamarin", "ionic", "cordova",
        # data stores
        "postgresql", "mysql", "mariadb", "sqlite", "oracle", "sql server",
        "mongodb", "cassandra", "dynamodb", "couchdb", "couchbase", "neo4j",
        "redis", "memcached", "elasticsearch", "opensearch", "solr",
        "clickhouse", "snowflake", "bigquery", "redshift", "databricks",
        "influxdb", "timescaledb", "cockroachdb", "firestore", "supabase",
        # messaging and streaming
        "kafka", "rabbitmq", "activemq", "nats", "pulsar", "sqs", "sns",
        "celery", "sidekiq", "bullmq", "temporal", "airflow", "dagster",
        "prefect", "luigi", "event hubs", "service bus",
        # infrastructure and platform
        "kubernetes", "docker", "podman", "containerd", "openshift", "nomad",
        "terraform", "pulumi", "cloudformation", "ansible", "chef", "puppet",
        "saltstack", "vagrant", "helm", "kustomize", "argocd", "flux",
        "istio", "linkerd", "envoy", "consul", "vault", "packer",
        "jenkins", "circleci", "travis", "teamcity", "bamboo", "gitlab ci",
        "github actions", "azure devops", "spinnaker", "octopus deploy",
        # clouds
        "aws", "azure", "gcp", "digitalocean", "heroku", "linode", "vercel",
        "netlify", "cloudflare", "fly.io", "railway", "render",
        "ec2", "s3", "lambda", "ecs", "eks", "fargate", "rds", "aurora",
        "cloud functions", "cloud run", "app engine", "app service",
        # observability
        "prometheus", "grafana", "datadog", "new relic", "splunk", "sentry",
        "opentelemetry", "jaeger", "zipkin", "kibana", "logstash", "fluentd",
        "pagerduty", "nagios", "zabbix",
        # data and machine learning
        "pandas", "numpy", "scipy", "scikit learn", "sklearn", "pytorch",
        "tensorflow", "keras", "jax", "xgboost", "lightgbm", "catboost",
        "huggingface", "transformers", "spacy", "nltk", "opencv",
        "apache spark", "spark", "hadoop", "hive", "presto", "trino", "flink",
        "beam", "dbt", "looker", "tableau", "power bi", "superset",
        "mlflow", "kubeflow", "sagemaker", "vertex ai",
        # api and protocol
        "rest", "graphql", "grpc", "soap", "websocket", "websockets",
        "openapi", "swagger", "protobuf", "avro", "thrift", "oauth",
        "oidc", "saml", "jwt", "ldap", "kerberos",
        # testing
        "jest", "vitest", "mocha", "chai", "jasmine", "karma", "cypress",
        "playwright", "selenium", "puppeteer", "testcafe", "pytest",
        "unittest", "junit", "testng", "rspec", "cucumber", "phpunit",
        "storybook", "k6", "locust", "jmeter", "gatling",
        # front-end tooling and styling
        "webpack", "vite", "rollup", "parcel", "esbuild", "babel", "turbopack",
        "tailwind", "bootstrap", "material ui", "mui", "chakra ui",
        "styled components", "emotion", "redux", "mobx", "zustand",
        "react query", "rxjs", "apollo", "relay",
        # version control and collaboration
        "git", "github", "gitlab", "bitbucket", "subversion", "svn",
        "mercurial", "jira", "confluence",
        # operating systems and runtimes
        "linux", "unix", "ubuntu", "debian", "centos", "rhel", "windows",
        "macos", "jvm", "wasm", "webassembly",
        # practices named as technologies in postings
        "ci/cd", "ci", "cd", "microservices", "serverless", "graphql federation",
        "event sourcing", "cqrs", "tdd", "bdd", "ddd", "agile", "scrum",
        "kanban",
    }
)

#: Sentence boundaries, for scoping a non-doing marker to the clause it
#: actually qualifies. Without this, one "evaluated" anywhere in a description
#: would weaken every technology named in it.
_SENTENCE = re.compile(r"[.;!?\n]+")

#: Multi-word non-doing markers have to be matched against the raw sentence
#: rather than a token set.
_MULTIWORD_MARKERS = tuple(marker for marker in NON_DOING_MARKERS if " " in marker)
_SINGLE_MARKERS = frozenset(marker for marker in NON_DOING_MARKERS if " " not in marker)

#: Separators prose uses where this module expects an ASCII hyphen.
#:
#: Names are neutralised by matching their words across `[\s-]+`, which covers
#: the ASCII hyphen and — because `\s` is Unicode — every Zs space including
#: NBSP. It does not cover the Pd dashes, which are punctuation rather than
#: whitespace, nor the Cf characters, which are neither. A CV extracted from a
#: PDF that writes "scikit‐learn" with U+2010 therefore split into
#: `{"scikit", "learn"}` and fired `learn`: the exact collision this module was
#: fixed to prevent, reached through a different character.
#:
#: NFKD does not do this — no dash here decomposes to an ASCII hyphen, and
#: U+2011 decomposes only as far as U+2010. `normalization.canonical` does
#: fold them, via its negated punctuation class, which is why the technology
#: side of the engine never saw this and the evidence side did. Kept as a
#: local copy rather than an import for the reason `rules._DEGREE_PATTERNS`
#: and `geography.LOCATION_LEAD` are local: a vocabulary this file's meaning
#: depends on should be readable in this file.
_SEPARATOR_FOLD: dict[int, str | None] = {
    # Pd dashes, written where a hyphen is meant.
    **{ord(char): "-" for char in "‐‑‒–—―−"},
    # Cf characters that are invisible and separate nothing. Removed rather
    # than hyphenated, so a soft hyphen inside a marker ("eval­uated")
    # rejoins into the marker instead of being split away from it.
    **{ord(char): None for char in "­​"},
}

#: Any run of whitespace becomes one space, so the multi-word marker test — a
#: literal substring match for "migrating away" — is not defeated by a tab or
#: by the double space a line-wrapped CV leaves behind.
_WHITESPACE_RUN = re.compile(r"\s+")


def _name_alternation(names: frozenset[str] | set[str]) -> re.Pattern[str]:
    """`MARKER_BEARING_NAMES` as one word-bounded pattern.

    Longest first, so "unsupervised learning" cannot be consumed as the
    "learning" of a shorter entry, and joined on `[\\s-]+` so a single curated
    spelling covers every separator prose actually uses.
    """
    ordered = sorted(names, key=lambda name: (-len(name), name))
    return re.compile(
        r"\b(?:%s)\b"
        % "|".join(r"[\s\-]+".join(re.escape(word) for word in name.split()) for name in ordered)
    )


_MARKER_BEARING = _name_alternation(MARKER_BEARING_NAMES)

#: Curated multi-word technologies as prose actually hyphenates them —
#: "react native" is written "React-Native", "scikit learn" is written
#: "scikit-learn". A token matching one of these names a technology, so the
#: words inside it are not claims about it.
_HYPHENATED_TECHNOLOGY_NAMES = frozenset(
    name.replace(" ", "-") for name in TECHNOLOGIES if " " in name
)

#: A hyphenated compound first, so it is seen whole before it can be split.
_WORD = re.compile(r"[a-z]+(?:-[a-z]+)+|[a-z]+")


def sentences(text: str | None) -> list[str]:
    """A description, split into the clauses a marker can qualify."""
    if not text:
        return []
    return [part.strip() for part in _SENTENCE.split(text) if part.strip()]


def _claim_words(text: str) -> set[str]:
    """The words of a clause that can carry a claim about a technology.

    A hyphenated compound is read whole first. If it names a technology it is
    dropped, because its parts are spelling, not assertions — `scikit-learn`
    is not a sentence about learning. If it does not, both the compound and
    its parts are kept, so "Re-evaluated Kafka and dropped it" still reads as
    an evaluation. That asymmetry is the point: the curated vocabulary is the
    only thing allowed to silence a marker.
    """
    words: set[str] = set()
    for token in _WORD.findall(text):
        if "-" not in token:
            words.add(token)
        elif token not in _HYPHENATED_TECHNOLOGY_NAMES:
            words.add(token)
            words.update(token.split("-"))
    return words


def is_non_doing(sentence: str) -> bool:
    """Whether this clause says the technology was NOT used.

    Word-boundary matched, never substring: "planning" must not fire on
    "unplanned", and "learn" must not fire on "learned lessons" — the second
    is a real risk, which is why the list holds only words that weaken a claim
    in every reading.

    A marker word is also not a claim when it is part of a NAME. The first
    version of this tokenised on `[a-z]+`, which is not the word boundary the
    paragraph above promises: a hyphen is not in that class, so "scikit-learn"
    arrived as `{"scikit", "learn"}` and fired. One of the only two non-doing
    suppressions in the whole real-data benchmark was that collision, silently
    demoting an eleven-year engineer's Python to a passing mention. Names are
    neutralised before the split, and the split no longer breaks them.

    Separators are folded first, before any of that. Neutralising a name is
    only worth doing if it cannot be stepped around by writing the name with
    the dash a PDF happened to emit, and the multi-word markers are matched as
    literal substrings, so the single space in "migrating away" has to be the
    only spelling that reaches them.
    """
    folded = sentence.casefold().translate(_SEPARATOR_FOLD)
    lowered = _MARKER_BEARING.sub(" ", _WHITESPACE_RUN.sub(" ", folded))
    if any(marker in lowered for marker in _MULTIWORD_MARKERS):
        return True
    return bool(_claim_words(lowered) & _SINGLE_MARKERS)


def is_generic(token: str) -> bool:
    """Whether a token names the kind of work rather than a technology."""
    parts = token.split()
    return bool(parts) and all(
        part in GENERIC_WORK_WORDS or _is_numeral(part) for part in parts
    )


def _is_numeral(part: str) -> bool:
    """"5", "5+", "3.5" — a quantity, never a subject.

    `.rstrip("+")` is load-bearing: `"5+".isdigit()` is False, and without it
    "5+ years of professional experience" keeps "5+" as a content word and is
    never recognised as a question about total employment.
    """
    return part.rstrip("+").replace(".", "", 1).isdigit()


def is_technology(token: str, *, declared: frozenset[str] | set[str] = frozenset()) -> bool:
    """Whether a canonical token names a technology.

    `declared` is the set of canonical tokens the profile under evaluation
    asserts as skills or project technologies. Including it is what lets a
    real technology the curated list has never heard of still resolve — and it
    cannot create a false subject, because a token only enters it when the
    candidate themselves called it a skill.
    """
    if not token:
        return False
    if is_generic(token):
        # A profile that declares a skill called "Platform" does not make
        # "platform" a technology subject for everyone else's requirements.
        return False
    return token in TECHNOLOGIES or token in declared


def assert_markers_do_not_shadow_names(
    markers: frozenset[str] | set[str] = NON_DOING_MARKERS,
    technologies: frozenset[str] | set[str] = TECHNOLOGIES,
    names: frozenset[str] | set[str] = MARKER_BEARING_NAMES,
) -> None:
    """No marker word may be readable as part of a technology's own name.

    Checked at import for the same reason `morphology` checks its table there:
    the failure is silent. `scikit learn` sat in TECHNOLOGIES with `learn` in
    the marker list for three algorithm versions, and every profile that wrote
    "scikit-learn" had the clause around it read as a disclaimer. Nothing
    failed, no test covered it, and the only reason it surfaced was a hand
    audit of why a senior profile graded `declared_only`.

    Two conditions. A single-word marker may not BE a technology — there would
    be no way to tell the two uses apart. And a multi-word technology
    containing a marker word must appear in `names`, or the marker inside it
    fires on every profile that spells the technology out.

    All three sets are parameters so a test can prove the guard fires without
    mutating this module's globals — a mutation that leaked into a later test
    is how the equivalent morphology test first failed.
    """
    single = {marker for marker in markers if " " not in marker}

    shadowed = sorted(single & set(technologies))
    if shadowed:
        raise AssertionError(
            f"non-doing markers collide with the technology vocabulary: {shadowed}. "
            f"A word that names a technology can never also mean it was not used."
        )

    unguarded = sorted(
        name
        for name in technologies
        if " " in name and set(name.split()) & single and name not in names
    )
    if unguarded:
        raise AssertionError(
            f"technology names contain a non-doing marker and are not in "
            f"MARKER_BEARING_NAMES: {unguarded}. Every profile that spells one of "
            f"these out would have the clause read as a disclaimer."
        )


assert_markers_do_not_shadow_names()


__all__ = [
    "GENERIC_WORK_WORDS",
    "MARKER_BEARING_NAMES",
    "NON_DOING_MARKERS",
    "TECHNOLOGIES",
    "assert_markers_do_not_shadow_names",
    "is_generic",
    "is_non_doing",
    "is_technology",
    "sentences",
]
