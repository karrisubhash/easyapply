"""What a profile version actually claims, indexed and graded for lookup.

The matchers do not walk the ORM graph. They ask this index a question — "what
does the profile say about `postgresql`, and how strongly?" — and get back a
`TokenEvidence` whose tier is the answer:

    DECLARED_CORROBORATED   a Skill or ProjectTechnology row AND a paid role
                            whose description demonstrates it
    EMPLOYMENT_DESCRIBED    a paid role demonstrates it; the skills list is
                            silent
    DECLARED_ONLY           a declaration with nothing corroborating it
    MENTIONED               a project description, or a clause that says the
                            work was contemplated rather than done

matching_v1 had two bins here — declared, and "prose" — and the real-data
benchmark showed the cost at both edges: six years of daily React described in
a current role scored UNCERTAIN because React was not in the skills list, and a
graduate's uncorroborated Kubernetes entry scored MATCHED at 0.95 against a
posting demanding production use. See `evidence.py`.

Prose is read CLAUSE BY CLAUSE, not as a bag of words. "Evaluated React Native
for a companion app, but the project was cancelled" names React Native in a
sentence that says it was not used, so it grades as MENTIONED rather than as
employment evidence. That is the whole difference between reading a description
and scanning it.

Nothing here invents evidence. Every `Evidence` carries the source table, the
row id, a label built from that row's own fields, and — for prose — the clause
the token was actually found in, so a result can always be traced back to
something the user wrote.
"""

from __future__ import annotations

from app.models.match import (
    EVIDENCE_CERTIFICATION,
    EVIDENCE_EDUCATION,
    EVIDENCE_EXPERIENCE,
    EVIDENCE_PROJECT,
    EVIDENCE_PROJECT_TECHNOLOGY,
    EVIDENCE_SKILL,
)
from app.models.profile_version import ProfileVersion
from app.services.matching import span_split
from app.services.matching.evidence import (
    SETTING_EMPLOYMENT,
    SETTING_PROJECT,
    Evidence,
    TokenEvidence,
    demonstrates_title,
    demonstrates_v5,
)
from app.services.matching.morphology import action_lemma
from app.services.matching.normalization import canonical, phrase_variants
from app.services.matching.vocabulary import is_technology, sentences


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ProfileIndex:
    """A confirmed profile version, arranged and graded for matching.

    Built once per match and read many times. Holds the original rows too —
    experience matching needs date ranges, education matching needs degree
    text — so this is an index over the version rather than a replacement for
    it.
    """

    def __init__(self, version: ProfileVersion) -> None:
        self.version = version
        self.experiences = list(version.experiences)
        self.education = list(version.education)
        self.certifications = list(version.certifications)
        self.projects = list(version.projects)
        self.skills = list(version.skills)

        self._tokens: dict[str, TokenEvidence] = {}
        #: A SECOND index, keyed by work-action lemma rather than by surface
        #: form — "Lead four engineers" and "leading a team" file together.
        #: Deliberately separate from `_tokens`: the only reader is the scan
        #: for requirements that name no technology, so skill matching,
        #: technology matching and aliases cannot reach it even by accident.
        #: See morphology.py for why this is a hand-written table and not a
        #: stemmer.
        self._actions: dict[str, TokenEvidence] = {}
        self._skill_by_token: dict[str, object] = {}
        #: Canonical tokens the profile ASSERTS as skills or project
        #: technologies. Unioned with the curated vocabulary when resolving an
        #: experience requirement's subject, which is what lets a technology
        #: nobody has added to `vocabulary.TECHNOLOGIES` still resolve.
        self.declared_tokens: set[str] = set()

        self._index_skills()
        self._index_project_technologies()
        self._index_employment()
        self._index_projects()

    # --- construction --------------------------------------------------------

    def _bucket(self, token: str) -> TokenEvidence:
        if token not in self._tokens:
            self._tokens[token] = TokenEvidence()
        return self._tokens[token]

    def _action_bucket(self, token: str) -> TokenEvidence | None:
        """The action index's bucket for a token, if it is a work-action word.

        None for everything else, which is almost everything — a token the
        morphology table has never heard of is simply not filed here.
        """
        lemma = action_lemma(token)
        if lemma is None:
            return None
        if lemma not in self._actions:
            self._actions[lemma] = TokenEvidence()
        return self._actions[lemma]

    def _index_skills(self) -> None:
        for skill in self.skills:
            token = canonical(skill.name)
            if not token:
                continue
            detail = None
            if skill.years_of_experience is not None:
                detail = f"{skill.years_of_experience} years stated"
            elif skill.proficiency:
                detail = f"proficiency: {skill.proficiency}"
            self._bucket(token).declarations.append(
                Evidence(
                    source_type=EVIDENCE_SKILL,
                    source_id=skill.id,
                    label=f"Skill: {_truncate(skill.name, 200)}",
                    detail=detail,
                )
            )
            self.declared_tokens.add(token)
            # Last one wins, but the unique index on (profile_version_id,
            # lower(name)) means there can only be a second row here when two
            # DIFFERENT spellings alias to one token — in which case either is
            # an equally true answer to "does this profile claim postgresql".
            self._skill_by_token[token] = skill

    def _index_project_technologies(self) -> None:
        """A technology attached to a project is a declaration.

        The user put it in a field whose purpose is to name it, exactly as
        with a skill row. What it is NOT is paid work — that distinction is
        made by the experience rules, which read `employment` and nothing else.
        """
        for project in self.projects:
            for technology in project.technologies:
                token = canonical(technology.name)
                if not token:
                    continue
                self._bucket(token).declarations.append(
                    Evidence(
                        source_type=EVIDENCE_PROJECT_TECHNOLOGY,
                        source_id=project.id,
                        label=f"Project: {_truncate(project.name, 200)}",
                        detail=f"Technology: {_truncate(technology.name, 120)}",
                        # A declaration made in a project's own field says
                        # where, as well as what — unlike a skills row, which
                        # asserts the skill and nothing about its setting.
                        setting=SETTING_PROJECT,
                    )
                )
                self.declared_tokens.add(token)

    def _index_employment(self) -> None:
        """Paid roles, read clause by clause, and — new in matching_v5 — span
        by span within each clause.

        The title is treated as a clause of its own: "Python Developer" names
        the technology as plainly as any sentence could, and a title cannot
        describe something that was not done. The title clause is graded as
        one whole, exactly as before matching_v5; it is not split, because
        `span_split.split_into_spans` is a refinement of how a *description*
        clause is read, not the title.

        Each description clause is additionally divided into spans wherever
        a marker's own scope plausibly ends (`span_split.split_into_spans`),
        so that "Deprecated the legacy PHP monolith while building its
        replacement in Go, which now serves all production traffic" grades
        `php` and `go` independently rather than broadcasting one boolean
        computed for the whole clause to both. A clause with no marker, or
        whose marker has no following scope-reset point, is returned
        unchanged as a single span — the exact behaviour this replaces.
        """
        for experience in self.experiences:
            label = (
                f"Experience: {_truncate(experience.job_title, 120)} at "
                f"{_truncate(experience.company, 100)}"
            )
            title_clause = experience.job_title or ""
            if title_clause:
                # matching_v5 step 4: a title is graded by demonstrates_title,
                # never by demonstrates()/demonstrates_v5 -- its grammar does
                # not carry an ordinary marker's caveat meaning (see
                # evidence.demonstrates_title's docstring), though an
                # explicit negation can still weaken it.
                self._index_experience_clause(
                    experience, label, title_clause, demonstrates_title(title_clause)
                )
            for clause in sentences(experience.description):
                self._index_experience_description_clause(experience, label, clause)

    def _index_experience_description_clause(self, experience, label: str, clause: str) -> None:
        """One description clause, graded span by span.

        A token found in more than one span of this SAME clause is resolved
        by the rule matching_v5 needs and matching_v4 never had to state: a
        span that says the work was NOT done pins that token to `mentions`
        for this clause, and no LATER span merely repeating the token's own
        name here can promote it back to `employment` — "Evaluated Kafka,
        before abandoning Kafka entirely for cost reasons" must not read as
        employment evidence for `kafka` just because "abandoning" (the
        gerund) is not itself a listed marker the way "abandoned" is. This is
        deliberately narrower than the tier's existing "employment beats
        mentions" precedence: that rule is sound for evidence from two
        DIFFERENT clauses or roles, which is genuinely independent
        corroboration; it is not automatically sound for two spans produced
        by splitting the SAME clause, which are not independent statements.
        A token introduced by reattachment (matching_v5 step 2) is a
        different, later addition to this rule: a span that opens with a
        relative pronoun or a bare present participle ("which have handled
        every internal call in production since 2017") names no subject of
        its own, so its verdict belongs to the nearest technology token an
        EARLIER span of this clause named, not to whatever `phrase_variants`
        happens to find literally inside it (usually nothing — "which" is
        not a technology). That verdict MAY promote (or demote) the token
        even past an earlier span's literal verdict, because it is a
        distinct, separately-written piece of information, unlike a bare
        literal repeat of the same word.
        """
        per_token: dict[str, tuple[bool, str]] = {}
        spans = span_split.split_into_spans(clause)
        for index, span in enumerate(spans):
            # matching_v5 step 3: demonstrates_v5 additionally recognises
            # explicit plain-English negation ("no Rust code has shipped
            # yet") that names no NON_DOING_MARKERS word at all -- see
            # negation.py's module docstring for why this is a separate,
            # unioned check rather than a change to demonstrates() itself.
            shows = demonstrates_v5(experience.description, span)
            literal_tokens = set(phrase_variants(span))

            for token in literal_tokens:
                existing = per_token.get(token)
                if existing is None or (existing[0] and not shows):
                    per_token[token] = (shows, span)
                # else: an existing True is never displaced by a later
                # literal True, and an existing False is never displaced by
                # a later literal repeat of the same token.

            if span_split.is_reattachment_opener(span):
                reattached = span_split.nearest_preceding_token(
                    spans[:index],
                    is_relevant=lambda token: is_technology(token, declared=self.declared_tokens),
                )
                if reattached is not None and reattached not in literal_tokens:
                    per_token[reattached] = (shows, span)

        for token, (shows, span) in per_token.items():
            evidence = Evidence(
                source_type=EVIDENCE_EXPERIENCE,
                source_id=experience.id,
                label=label,
                detail=_truncate(experience.description, 480)
                if experience.description
                else None,
                quote=_truncate(span, 300),
                setting=SETTING_EMPLOYMENT,
            )
            self._file_experience_evidence(experience, token, shows, evidence)

    def _index_experience_clause(self, experience, label: str, clause: str, shows: bool) -> None:
        """One whole, unsplit clause — today, only the job title takes this
        path. Preserved as its own method because it is exactly matching_v4's
        original per-clause behaviour, kept legible as what it is rather than
        folded into the span-aware path it is deliberately not part of."""
        evidence = Evidence(
            source_type=EVIDENCE_EXPERIENCE,
            source_id=experience.id,
            label=label,
            detail=_truncate(experience.description, 480) if experience.description else None,
            quote=_truncate(clause, 300),
            setting=SETTING_EMPLOYMENT,
        )
        for token in phrase_variants(clause):
            self._file_experience_evidence(experience, token, shows, evidence)

    def _file_experience_evidence(self, experience, token: str, shows: bool, evidence: Evidence) -> None:
        for bucket in (self._bucket(token), self._action_bucket(token)):
            if bucket is None:
                continue
            target = bucket.employment if shows else bucket.mentions
            # One clause per role per token: a description that says
            # "Django" three times is one piece of evidence, and counting it
            # three times would make repetition look like corroboration.
            if not any(item.source_id == experience.id for item in target):
                target.append(evidence)
            # matching_v6 Phase 1. A clause saying the work was NOT done stays
            # in `mentions` above — unmoved, so `tier` cannot shift — and is
            # ALSO recorded as the denial it is. Before this, "named in a
            # project" and "said outright it was never used" were the same
            # list with no way to tell them apart.
            if not shows and not any(
                item.source_id == experience.id for item in bucket.disclaimers
            ):
                bucket.disclaimers.append(evidence)

    def _index_projects(self) -> None:
        """Project descriptions: real, and not paid work.

        Filed as mentions regardless of how the clause reads, because the
        strongest thing a project description can establish is that the person
        chose to work with something — which the experience rules must not
        count as professional duration.

        matching_v6 Phase 1 adds a second, independent filing beside that one.
        The `mentions` rule below — including its suppression of a description
        that merely repeats a technology the same project already declared —
        is unchanged to the line, so `tier` is what it always was. What changed
        is that the suppressed sentence is no longer DISCARDED: it is recorded
        as a corroboration or a disclaimer according to whether it says the
        work was done. Before this, a project that listed Docker and narrated
        deploying with it was indistinguishable from one that listed Docker and
        said nothing, and a project whose description said a technology was
        never used was indistinguishable from both.
        """
        for project in self.projects:
            label = f"Project: {_truncate(project.name, 200)}"
            # Description sentences first, then the project's own name, which is
            # the order the mentions rule has always seen them in. The name is
            # graded like a title rather than like prose: "Kafka migration
            # evaluation" names a project, it does not caveat one.
            clauses = [
                (clause, demonstrates_v5(project.description, clause))
                for clause in sentences(project.description)
            ]
            if project.name:
                clauses.append((project.name, demonstrates_title(project.name)))

            for clause, shows in clauses:
                if not clause:
                    continue
                evidence = Evidence(
                    source_type=EVIDENCE_PROJECT,
                    source_id=project.id,
                    label=label,
                    detail=_truncate(project.description, 480)
                    if project.description
                    else None,
                    quote=_truncate(clause, 300),
                    setting=SETTING_PROJECT,
                )
                for token in phrase_variants(clause):
                    bucket = self._bucket(token)
                    action = self._action_bucket(token)

                    # matching_v6 Phase 1, and deliberately BEFORE the
                    # suppression below: this is the filing that keeps what
                    # the suppression throws away.
                    for target in (bucket, action):
                        if target is None:
                            continue
                        facet = target.corroborations if shows else target.disclaimers
                        if not any(item.source_id == project.id for item in facet):
                            facet.append(evidence)

                    # --- unchanged from matching_v5 below this line ----------
                    # A project that lists the technology explicitly has
                    # already produced a declaration; its description
                    # repeating the word adds nothing but noise.
                    if any(
                        item.source_type == EVIDENCE_PROJECT_TECHNOLOGY
                        and item.source_id == project.id
                        for item in bucket.declarations
                    ):
                        continue
                    if not any(item.source_id == project.id for item in bucket.mentions):
                        bucket.mentions.append(evidence)
                    if action is not None and not any(
                        item.source_id == project.id for item in action.mentions
                    ):
                        action.mentions.append(evidence)

    # --- lookup --------------------------------------------------------------

    def evidence_for(self, token: str) -> TokenEvidence:
        """Everything the profile says about a canonical token, graded.

        Surface form only. "leading" and "lead" are different keys here, which
        is correct for technologies and wrong for work-action verbs — see
        `action_evidence_for`.
        """
        return self._tokens.get(token, TokenEvidence())

    def action_evidence_for(self, token: str) -> TokenEvidence:
        """What the profile says about a work-action CONCEPT, across its forms.

        "Lead four engineers", "led a team" and "leading engineers" all answer
        a requirement about leading. Empty for any token the morphology table
        does not list, which is the overwhelming majority — so this can widen
        a match only for the words written down in morphology.py.
        """
        lemma = action_lemma(token)
        if lemma is None:
            return TokenEvidence()
        return self._actions.get(lemma, TokenEvidence())

    def employment_mentioning(self, token: str) -> list:
        """The Experience rows whose description or title demonstrates a token.

        The only attribution the duration rules use. Project evidence is
        deliberately unreachable from here: a side project is not professional
        duration, and the way to keep that true is to make the wrong rows
        impossible to ask for rather than to remember not to count them.
        """
        ids = {
            item.source_id
            for item in self.evidence_for(token).employment
            if item.source_id is not None
        }
        return [item for item in self.experiences if item.id in ids]

    def current_location(self) -> "Place | None":
        """Where the candidate's employment places them, or None.

        There is no residence field anywhere in the profile schema — not on
        `Profile`, not on `ProfileVersion`, not on `User`. The only geographic
        signal a candidate gives is where their JOBS were, so this is a proxy
        and is treated as one: it can support a MATCHED, and its absence can
        never support a MISSING.

        The rules, in order, and each one refuses to guess:

        1. **Ongoing roles first.** A role with no end date is where the person
           is now; an ended one is where they were.
        2. **Agreement is required.** Two concurrent roles in different
           COUNTRIES resolve to nothing — picking one would be inventing a
           fact, and the profile genuinely does not say. Two concurrent roles
           in different cities of the SAME country keep the country and drop
           the city, which asserts only what both rows agree on.
        3. **Fall back to the most recent ended role** only when no ongoing
           role states a place. Someone whose current role is remote has not
           told us where they are.
        4. **Nothing else counts.** Education, projects and free text are never
           read for location: a university in Berlin is not an address, and
           this engine does not infer one.

        Deterministic: the fallback sorts by end date then start date then row
        id, so two runs over the same version always pick the same row.
        """
        from app.services.matching.geography import parse_location_field

        ongoing = [
            (experience, place)
            for experience in self.experiences
            if experience.end_date is None
            and (place := parse_location_field(experience.location)) is not None
        ]
        resolved = self._agree(ongoing)
        if resolved is not None:
            return resolved
        if ongoing:
            # Ongoing roles exist and disagree about the country. The profile
            # does not say where this person is, and neither will we.
            return None

        ended = [
            (experience, place)
            for experience in self.experiences
            if experience.end_date is not None
            and (place := parse_location_field(experience.location)) is not None
        ]
        if not ended:
            return None
        ended.sort(
            key=lambda pair: (pair[0].end_date, pair[0].start_date, pair[0].id or 0),
            reverse=True,
        )
        newest_end = ended[0][0].end_date
        # Everything that ended on the same day is equally recent, so they all
        # have to agree before any of them is used.
        return self._agree([pair for pair in ended if pair[0].end_date == newest_end])

    @staticmethod
    def _agree(pairs) -> "Place | None":
        """The one place a set of roles agrees on, or None if they do not."""
        from app.services.matching.geography import Place

        if not pairs:
            return None
        countries = {place.country for _, place in pairs}
        if len(countries) > 1:
            return None
        cities = {place.city for _, place in pairs if place.city}
        country = countries.pop()
        if len(cities) == 1:
            return Place(country=country, city=cities.pop())
        # Same country, different (or unstated) cities: assert only the part
        # every row agrees on.
        return Place(country=country, city=None)

    def experience_at(self, place: "Place"):
        """The experience rows that put the candidate in a place.

        Used to ground a location verdict in a real profile row — a MATCHED
        location must cite the employment it was read from, never the posting
        that asked the question.
        """
        from app.services.matching.geography import parse_location_field

        rows = []
        for experience in self.experiences:
            parsed = parse_location_field(experience.location)
            if parsed is None:
                continue
            if parsed.country != place.country:
                continue
            if place.city is not None and parsed.city != place.city:
                continue
            rows.append(experience)
        # Ongoing roles first, then most recently ended; stable and repeatable.
        rows.sort(
            key=lambda item: (
                item.end_date is None,
                item.end_date or item.start_date,
                item.id or 0,
            ),
            reverse=True,
        )
        return rows

    def skill_for(self, token: str):
        """The Skill row backing a token, if the profile claims it as a skill.

        Experience matching needs this: `Skill.years_of_experience` is the
        profile's own statement of duration.
        """
        return self._skill_by_token.get(token)

    def location_evidence(self, experience) -> Evidence:
        """An employment row, cited for where it placed the candidate.

        The quote is the location string exactly as the profile holds it, so a
        reader sees "Berlin, Germany" and judges the claim themselves rather
        than trusting that it was parsed correctly.
        """
        return Evidence(
            source_type=EVIDENCE_EXPERIENCE,
            source_id=experience.id,
            label=f"Experience: {_truncate(experience.job_title, 120)} at "
            f"{_truncate(experience.company, 100)}",
            detail=_format_span(experience),
            quote=_truncate(experience.location, 300) if experience.location else None,
        )

    def certification_evidence(self, certification) -> Evidence:
        return Evidence(
            source_type=EVIDENCE_CERTIFICATION,
            source_id=certification.id,
            label=f"Certification: {_truncate(certification.name, 200)}",
            detail=(
                f"Issued by {_truncate(certification.issuing_organization, 120)}"
                if certification.issuing_organization
                else None
            ),
        )

    def education_evidence(self, education) -> Evidence:
        parts = [part for part in (education.degree, education.field_of_study) if part]
        return Evidence(
            source_type=EVIDENCE_EDUCATION,
            source_id=education.id,
            label=f"Education: {_truncate(', '.join(parts) or education.institution, 200)}",
            detail=f"{_truncate(education.institution, 200)}",
        )

    def experience_evidence(self, experience) -> Evidence:
        return Evidence(
            source_type=EVIDENCE_EXPERIENCE,
            source_id=experience.id,
            label=f"Experience: {_truncate(experience.job_title, 120)} at "
            f"{_truncate(experience.company, 100)}",
            detail=_format_span(experience),
        )

    @property
    def is_empty(self) -> bool:
        return not (
            self.skills
            or self.experiences
            or self.education
            or self.certifications
            or self.projects
        )


def _format_span(experience) -> str:
    start = experience.start_date.isoformat() if experience.start_date else "unknown"
    end = experience.end_date.isoformat() if experience.end_date else "present"
    return f"{start} – {end}"
