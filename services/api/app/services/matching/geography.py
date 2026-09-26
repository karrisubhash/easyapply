"""Places, for matching — a curated table, not a geocoder.

matching_v4 answers exactly two geographic questions: is the candidate in the
city the posting named, and failing that, are they in the same country. Nothing
here computes a distance, reads a timezone, resolves a region, or calls
anything over a network. Those are deferred, and the deferral is the design:
the real-data benchmark showed that city-or-country equality reproduces every
human location judgment in the dataset, and that reading the "within two hours
of CET" clause literally would have flipped two correct verdicts to wrong ones.

**The vocabulary is finite and it is meant to be.** A city this table has never
heard of parses to nothing, and a requirement about it is answered UNCERTAIN —
never MATCHED, never MISSING. Growing the table can only let the matcher
recognise one more real place; it can never make it invent one. That is the
same posture `normalization.ALIASES` and `vocabulary.TECHNOLOGIES` take, and
for the same reason.

**Ambiguous names are excluded rather than resolved.** "Cambridge" is a serious
employment market in both England and Massachusetts; "Perth" is in Australia
and in Scotland. Guessing would produce a confident MATCHED against the wrong
continent — the error class this project treats as worst, because nothing in
the result invites the reader to check it. Those names live in
`AMBIGUOUS_CITY_NAMES`, are asserted at import to be absent from the table, and
stay unknown.

The bar for including a city: one referent must be the dominant employment
market by a wide margin, so that a posting naming it without a country is
overwhelmingly likely to mean that one. London, Dublin and San Francisco pass.
Cambridge and Birmingham do not.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# --- normalising a place name ---------------------------------------------------
#
# Geography gets its own normaliser rather than reusing
# `normalization.canonical()`: that one preserves `+` and `#` because they are
# part of technology names, and it applies the technology alias table. Neither
# is wanted here, and running place names through a technology vocabulary is
# exactly the kind of accidental coupling this codebase keeps refusing.

_COMBINING = re.compile(r"[̀-ͯ]")
_NOT_PLACE = re.compile(r"[^a-z0-9\- ]+")
_SPACES = re.compile(r"\s+")


def normalize_place(text: str | None) -> str:
    """The canonical form of a place name.

    Accents are folded, so "Zürich", "Zurich" and "ZÜRICH" are one key and
    "São Paulo" matches "Sao Paulo". Hyphens survive because they are part of
    names like "Cluj-Napoca"; everything else punctuation-shaped becomes a
    separator.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKD", text).casefold()
    stripped = _COMBINING.sub("", folded)
    cleaned = _NOT_PLACE.sub(" ", stripped)
    return _SPACES.sub(" ", cleaned).strip()


# --- countries -------------------------------------------------------------------

#: Canonical country names. A value in `CITY_COUNTRY` must be one of these.
COUNTRIES: frozenset[str] = frozenset(
    {
        "argentina", "australia", "austria", "belgium", "brazil", "bulgaria",
        "canada", "chile", "china", "colombia", "croatia", "czech republic",
        "denmark", "egypt", "estonia", "finland", "france", "germany", "greece",
        "hong kong", "hungary", "iceland", "india", "indonesia", "ireland",
        "israel", "italy", "japan", "kenya", "latvia", "lithuania", "luxembourg",
        "malaysia", "mexico", "netherlands", "new zealand", "nigeria", "norway",
        "peru", "philippines", "poland", "portugal", "qatar", "romania",
        "saudi arabia", "serbia", "singapore", "slovakia", "slovenia",
        "south africa", "south korea", "spain", "sweden", "switzerland",
        "taiwan", "thailand", "turkey", "ukraine", "united arab emirates",
        "united kingdom", "united states", "uruguay", "vietnam",
    }
)

#: Spellings that mean a canonical country. Keys are already normalised.
#:
#: The constituent nations of the UK are here because a posting saying
#: "Scotland" means the United Kingdom for the purpose of this comparison. Two
#: letter forms ("uk", "us") are included for the structured
#: "City, UK" shape but are deliberately NOT scanned for in prose — see
#: `_prose_country_terms`, where a bare "us" inside "authorized to work in the
#: US" would otherwise be read as a country.
COUNTRY_ALIASES: dict[str, str] = {
    "uk": "united kingdom",
    "u k": "united kingdom",
    "great britain": "united kingdom",
    "britain": "united kingdom",
    "england": "united kingdom",
    "scotland": "united kingdom",
    "wales": "united kingdom",
    "northern ireland": "united kingdom",
    "us": "united states",
    "u s": "united states",
    "usa": "united states",
    "u s a": "united states",
    "united states of america": "united states",
    "america": "united states",
    "uae": "united arab emirates",
    "holland": "netherlands",
    "the netherlands": "netherlands",
    "czechia": "czech republic",
    "korea": "south korea",
    "republic of korea": "south korea",
    "republic of ireland": "ireland",
    "eire": "ireland",
    "deutschland": "germany",
    "espana": "spain",
    "brasil": "brazil",
    "turkiye": "turkey",
}

#: Country forms too short or too collision-prone to look for in free prose.
#: "Based in the US" is a real location statement, but so is "authorized to
#: work in the US", and the second is a work-authorisation requirement this
#: milestone deliberately leaves alone.
_UNSAFE_IN_PROSE: frozenset[str] = frozenset({"uk", "u k", "us", "u s", "u s a", "america"})


# --- cities ----------------------------------------------------------------------

#: City names whose referent is genuinely contested between countries. Excluded
#: on purpose and asserted absent from `CITY_COUNTRY` at import. A posting
#: naming one of these parses to no city, which produces UNCERTAIN — the honest
#: answer, and the safe one.
AMBIGUOUS_CITY_NAMES: frozenset[str] = frozenset(
    {
        "cambridge",      # England and Massachusetts, both major employers
        "birmingham",     # England and Alabama
        "perth",          # Australia and Scotland
        "newcastle",      # England and Australia
        "santiago",       # Chile and Spain (de Compostela)
        "valencia",       # Spain and Venezuela
        "cordoba",        # Spain and Argentina
        "hamilton",       # Canada, New Zealand and Bermuda
        "london",         # see the note below — kept, but the name is shared
        "richmond", "springfield", "columbia", "kingston", "victoria",
        "wellington",     # New Zealand and a UK/other usage
        "san jose",       # California and Costa Rica
        "vancouver",      # British Columbia and Washington state
        "portland",       # Oregon and Maine
    }
) - {"london"}
#: `london` is subtracted back out: London, Ontario exists, but in employment
#: terms London, United Kingdom is dominant by orders of magnitude, which is
#: the stated bar for inclusion. It is listed above so the exception is
#: visible rather than silently absent.

#: The curated table: city -> canonical country. Keys are already normalised.
CITY_COUNTRY: dict[str, str] = {
    # --- Germany ---
    "berlin": "germany", "munich": "germany", "muenchen": "germany",
    "frankfurt": "germany", "frankfurt am main": "germany",
    "hamburg": "germany", "cologne": "germany", "koln": "germany",
    "stuttgart": "germany", "dusseldorf": "germany", "leipzig": "germany",
    "dresden": "germany", "nuremberg": "germany", "karlsruhe": "germany",
    # --- United Kingdom ---
    "london": "united kingdom", "manchester": "united kingdom",
    "edinburgh": "united kingdom", "glasgow": "united kingdom",
    "bristol": "united kingdom", "leeds": "united kingdom",
    "sheffield": "united kingdom", "liverpool": "united kingdom",
    "belfast": "united kingdom", "cardiff": "united kingdom",
    "oxford": "united kingdom", "reading": "united kingdom",
    # --- Ireland ---
    "dublin": "ireland", "cork": "ireland", "galway": "ireland",
    "limerick": "ireland",
    # --- France ---
    "paris": "france", "lyon": "france", "toulouse": "france",
    "bordeaux": "france", "nantes": "france", "lille": "france",
    "marseille": "france", "grenoble": "france", "sophia antipolis": "france",
    # --- Netherlands / Belgium / Luxembourg ---
    "amsterdam": "netherlands", "rotterdam": "netherlands",
    "utrecht": "netherlands", "eindhoven": "netherlands",
    "the hague": "netherlands", "den haag": "netherlands",
    "brussels": "belgium", "antwerp": "belgium", "ghent": "belgium",
    "leuven": "belgium", "luxembourg": "luxembourg",
    # --- Iberia ---
    "madrid": "spain", "barcelona": "spain", "seville": "spain",
    "bilbao": "spain", "malaga": "spain", "zaragoza": "spain",
    "lisbon": "portugal", "lisboa": "portugal", "porto": "portugal",
    "braga": "portugal", "coimbra": "portugal",
    # --- Italy ---
    "milan": "italy", "milano": "italy", "rome": "italy", "roma": "italy",
    "turin": "italy", "bologna": "italy", "florence": "italy",
    "naples": "italy", "padua": "italy",
    # --- Switzerland / Austria ---
    "zurich": "switzerland", "geneva": "switzerland",
    "lausanne": "switzerland", "basel": "switzerland", "bern": "switzerland",
    "zug": "switzerland",
    "vienna": "austria", "wien": "austria", "graz": "austria",
    "salzburg": "austria", "linz": "austria",
    # --- Nordics ---
    "stockholm": "sweden", "gothenburg": "sweden", "malmo": "sweden",
    "uppsala": "sweden",
    "oslo": "norway", "bergen": "norway", "trondheim": "norway",
    "copenhagen": "denmark", "aarhus": "denmark", "odense": "denmark",
    "helsinki": "finland", "espoo": "finland", "tampere": "finland",
    "oulu": "finland", "reykjavik": "iceland",
    # --- Central and Eastern Europe ---
    "warsaw": "poland", "krakow": "poland", "wroclaw": "poland",
    "gdansk": "poland", "poznan": "poland", "lodz": "poland",
    "katowice": "poland",
    "prague": "czech republic", "praha": "czech republic",
    "brno": "czech republic", "ostrava": "czech republic",
    "budapest": "hungary", "debrecen": "hungary",
    "bucharest": "romania", "cluj-napoca": "romania", "timisoara": "romania",
    "iasi": "romania",
    "sofia": "bulgaria", "plovdiv": "bulgaria",
    "athens": "greece", "thessaloniki": "greece",
    "zagreb": "croatia", "ljubljana": "slovenia", "bratislava": "slovakia",
    "tallinn": "estonia", "tartu": "estonia",
    "riga": "latvia", "vilnius": "lithuania", "kaunas": "lithuania",
    "belgrade": "serbia", "novi sad": "serbia",
    "kyiv": "ukraine", "kiev": "ukraine", "lviv": "ukraine",
    "istanbul": "turkey", "ankara": "turkey", "izmir": "turkey",
    # --- United States ---
    "new york": "united states", "new york city": "united states",
    "san francisco": "united states", "los angeles": "united states",
    "san diego": "united states", "seattle": "united states",
    "austin": "united states", "boston": "united states",
    "chicago": "united states", "denver": "united states",
    "atlanta": "united states", "dallas": "united states",
    "houston": "united states", "philadelphia": "united states",
    "miami": "united states", "minneapolis": "united states",
    "detroit": "united states", "pittsburgh": "united states",
    "nashville": "united states", "charlotte": "united states",
    "raleigh": "united states", "phoenix": "united states",
    "salt lake city": "united states", "washington dc": "united states",
    "palo alto": "united states", "mountain view": "united states",
    "sunnyvale": "united states", "santa monica": "united states",
    "brooklyn": "united states",
    # --- Canada ---
    "toronto": "canada", "montreal": "canada", "ottawa": "canada",
    "calgary": "canada", "edmonton": "canada", "waterloo": "canada",
    "halifax": "canada", "winnipeg": "canada", "quebec city": "canada",
    # --- Latin America ---
    "sao paulo": "brazil", "rio de janeiro": "brazil",
    "belo horizonte": "brazil", "porto alegre": "brazil",
    "brasilia": "brazil", "curitiba": "brazil", "recife": "brazil",
    "buenos aires": "argentina", "bogota": "colombia",
    "medellin": "colombia", "lima": "peru",
    "mexico city": "mexico", "guadalajara": "mexico",
    "monterrey": "mexico", "montevideo": "uruguay",
    # --- Middle East and Africa ---
    "tel aviv": "israel", "jerusalem": "israel", "haifa": "israel",
    "herzliya": "israel",
    "dubai": "united arab emirates", "abu dhabi": "united arab emirates",
    "riyadh": "saudi arabia", "doha": "qatar", "cairo": "egypt",
    "nairobi": "kenya", "lagos": "nigeria",
    "cape town": "south africa", "johannesburg": "south africa",
    "durban": "south africa", "pretoria": "south africa",
    # --- Asia Pacific ---
    "singapore": "singapore", "hong kong": "hong kong",
    "tokyo": "japan", "osaka": "japan", "kyoto": "japan",
    "yokohama": "japan", "fukuoka": "japan",
    "seoul": "south korea", "busan": "south korea",
    "bangalore": "india", "bengaluru": "india", "mumbai": "india",
    "new delhi": "india", "hyderabad": "india", "chennai": "india",
    "pune": "india", "kolkata": "india", "gurgaon": "india",
    "gurugram": "india", "noida": "india", "ahmedabad": "india",
    "shanghai": "china", "beijing": "china", "shenzhen": "china",
    "guangzhou": "china", "hangzhou": "china",
    "taipei": "taiwan", "bangkok": "thailand", "jakarta": "indonesia",
    "kuala lumpur": "malaysia", "manila": "philippines", "cebu": "philippines",
    "ho chi minh city": "vietnam", "hanoi": "vietnam",
    "sydney": "australia", "melbourne": "australia", "brisbane": "australia",
    "adelaide": "australia", "canberra": "australia",
    "auckland": "new zealand", "christchurch": "new zealand",
}

#: `Job.workplace_type`'s value for a remote posting. A local copy of the job
#: domain's closed vocabulary rather than an import — the same posture
#: `rules._DEGREE_PATTERNS` takes, and one constant does not justify making the
#: job domain export internals to matching.
WORKPLACE_REMOTE = "remote"

#: Values a location field can hold that name no place at all. Recognised so
#: they can be reported as "states no place" rather than mistaken for a city.
NON_PLACE_VALUES: frozenset[str] = frozenset(
    {"remote", "fully remote", "remote worldwide", "anywhere", "distributed",
     "work from home", "wfh", "global", "various", "multiple locations", "n a", "tbd"}
)


# --- what a parsed place is ------------------------------------------------------


@dataclass(frozen=True)
class Place:
    """A location resolved as far as this table can resolve it.

    `city` may be None while `country` is known ("Germany" on its own, or two
    concurrent roles in different German cities). `country` is never None on a
    Place that exists — a city with no known country is not representable, by
    construction, because such a city is not in the table.
    """

    country: str
    city: str | None = None

    def same_city(self, other: "Place") -> bool:
        return self.city is not None and self.city == other.city

    def same_country(self, other: "Place") -> bool:
        return self.country == other.country


def _country_for(term: str) -> str | None:
    """The canonical country a normalised term names, if it names one."""
    if term in COUNTRIES:
        return term
    return COUNTRY_ALIASES.get(term)


def parse_location_field(text: str | None) -> Place | None:
    """Parse a location FIELD — `Experience.location` or `Job.location_raw`.

    Handles the shapes these fields actually hold: "Berlin, Germany",
    "Berlin, Germany (hybrid)", "London, UK", "Remote". A parenthetical is
    dropped before parsing, because "(hybrid)" describes the arrangement rather
    than the place.

    Returns None for anything this table cannot resolve, including "Remote" —
    the caller must treat that as "no location stated", never as a mismatch.
    """
    if not text:
        return None

    without_parens = re.sub(r"\([^)]*\)", " ", text)
    if normalize_place(without_parens) in NON_PLACE_VALUES:
        return None

    # Split on the raw commas BEFORE normalising: `normalize_place` treats a
    # comma as punctuation and removes it, so normalising first would collapse
    # "Berlin, Germany" into the single unknown token "berlin germany".
    parts = [normalize_place(part) for part in without_parens.split(",")]
    parts = [part for part in parts if part]
    if not parts:
        return None

    # Right to left: the country, when stated, is the last component.
    country: str | None = None
    for part in reversed(parts):
        country = _country_for(part)
        if country:
            break

    for part in parts:
        city = part if part in CITY_COUNTRY else None
        if city:
            city_country = CITY_COUNTRY[city]
            # A stated country wins over the table only if they agree; a
            # disagreement ("Berlin, France") is a contradiction we refuse to
            # resolve rather than silently pick a side on.
            if country and country != city_country:
                return None
            return Place(country=city_country, city=city)

    if country:
        return Place(country=country, city=None)
    return None


@dataclass(frozen=True)
class RequiredPlaces:
    """The places a job requirement names.

    A requirement can name several ("Berlin, or elsewhere in Germany"), and
    `conflicting` records the case this milestone refuses to judge: two
    different countries in one line. Multi-location postings are deferred, and
    guessing which one the posting meant would be exactly the invention this
    engine exists to avoid.
    """

    cities: frozenset[str] = frozenset()
    countries: frozenset[str] = frozenset()

    @property
    def is_empty(self) -> bool:
        return not self.cities and not self.countries

    @property
    def conflicting(self) -> bool:
        return len(self.countries) > 1


def _prose_country_terms() -> list[str]:
    """Country spellings safe to look for inside a sentence, longest first."""
    terms = set(COUNTRIES) | {
        alias for alias in COUNTRY_ALIASES if alias not in _UNSAFE_IN_PROSE
    }
    return sorted(terms, key=len, reverse=True)


_PROSE_COUNTRIES = _prose_country_terms()
_PROSE_CITIES = sorted(CITY_COUNTRY, key=len, reverse=True)


def places_in_text(text: str | None) -> RequiredPlaces:
    """Every place this table recognises inside a requirement's own words.

    Whole-token matching only, longest name first, so "new york" is found
    before "york" would be and "Based in Berlin, or within two hours of CET"
    yields Berlin while CET is simply not a place. Nothing is inferred: a word
    that is not in the table contributes nothing, which is what keeps a
    requirement about an unlisted city UNCERTAIN instead of wrong.
    """
    normalised = normalize_place(text)
    if not normalised:
        return RequiredPlaces()

    padded = f" {normalised} "
    cities: set[str] = set()
    countries: set[str] = set()

    for city in _PROSE_CITIES:
        if f" {city} " in padded:
            cities.add(city)
            countries.add(CITY_COUNTRY[city])

    for term in _PROSE_COUNTRIES:
        if f" {term} " in padded:
            resolved = _country_for(term)
            if resolved:
                countries.add(resolved)

    return RequiredPlaces(cities=frozenset(cities), countries=frozenset(countries))


# --- recognising that a requirement is about location ----------------------------

#: A local copy of the lead-ins the job structurer files location lines by.
#:
#: Deliberately a copy rather than an import of
#: `app.services.job.structuring._LOCATION_LEAD`: reading a requirement's
#: intent is matching's own concern, and making the job domain export internals
#: to the matching domain would couple them the wrong way round — the same
#: reasoning `rules._DEGREE_PATTERNS` gives for holding its own degree
#: vocabulary. `tests/test_matching_location.py` asserts the two stay in step.
LOCATION_LEAD = re.compile(
    r"^(?:"
    r"based\s+(?:in|out\s+of|near)\b"
    r"|located\s+(?:in|near)\b"
    r"|locations?\s*[:\-–]"
    r"|(?:fully\s+|100%\s+)?remote\s+(?:within|in|from|anywhere)\b"
    r"|hybrid\s+(?:within|in|from|out\s+of)\b"
    r"|(?:fully\s+)?remote\s*$"
    r"|hybrid\s*$"
    r")",
    re.IGNORECASE,
)


def is_location_requirement(text: str | None) -> bool:
    """Whether a qualification line is a statement about where the job is.

    Anchored at the start and demanding a locative continuation, so "Remote
    debugging experience" and "Hybrid cloud architecture" — ordinary competency
    statements that open with the same word — are not location requirements.
    """
    return bool(text) and bool(LOCATION_LEAD.match(text.strip()))


# --- integrity -------------------------------------------------------------------


def assert_vocabulary_is_unambiguous() -> None:
    """Every table invariant, checked at import.

    Checked here rather than only in a test because each failure it prevents is
    silent: a city pointing at the wrong country, or a name with two real
    referents, produces a confident MATCHED on the wrong continent and nothing
    in the result invites anyone to look.
    """
    problems: list[str] = []

    for city, country in CITY_COUNTRY.items():
        if normalize_place(city) != city:
            problems.append(f"city key {city!r} is not in normalised form")
        if country not in COUNTRIES:
            problems.append(f"city {city!r} points at unknown country {country!r}")
        if city in AMBIGUOUS_CITY_NAMES:
            problems.append(f"city {city!r} is listed as ambiguous and must not be resolvable")

    for alias, country in COUNTRY_ALIASES.items():
        if normalize_place(alias) != alias:
            problems.append(f"country alias {alias!r} is not in normalised form")
        if country not in COUNTRIES:
            problems.append(f"country alias {alias!r} points at unknown country {country!r}")
        if alias in COUNTRIES:
            problems.append(f"{alias!r} is both a canonical country and an alias")

    # A name that is both a city and a country would make "Singapore" or
    # "Luxembourg" resolve differently depending on which loop ran first.
    for city in CITY_COUNTRY:
        if city in COUNTRY_ALIASES:
            problems.append(f"{city!r} is both a city and a country alias")

    overlap = set(CITY_COUNTRY) & set(NON_PLACE_VALUES)
    if overlap:
        problems.append(f"names are both a city and a non-place value: {sorted(overlap)}")

    if problems:
        raise AssertionError(
            "geography vocabulary is not internally consistent:\n  " + "\n  ".join(problems)
        )


assert_vocabulary_is_unambiguous()


__all__ = [
    "AMBIGUOUS_CITY_NAMES",
    "CITY_COUNTRY",
    "COUNTRIES",
    "COUNTRY_ALIASES",
    "LOCATION_LEAD",
    "NON_PLACE_VALUES",
    "Place",
    "RequiredPlaces",
    "WORKPLACE_REMOTE",
    "assert_vocabulary_is_unambiguous",
    "is_location_requirement",
    "normalize_place",
    "parse_location_field",
    "places_in_text",
]
