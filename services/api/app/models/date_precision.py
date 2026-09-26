"""Date precision.

A resume that says "May 2026" does not say which day. Storing 2026-05-01 and
forgetting that the day was invented turns a guess into a fact, and no later
migration can recover which dates were guessed. Every date therefore carries the
precision it was actually known to.
"""

PRECISION_YEAR = "year"
PRECISION_MONTH = "month"
PRECISION_DAY = "day"

PRECISION_VALUES = (PRECISION_YEAR, PRECISION_MONTH, PRECISION_DAY)

_ALLOWED = ", ".join(f"'{value}'" for value in PRECISION_VALUES)


def precision_values_check(precision_column: str) -> str:
    """The precision column holds one of the known precisions, or nothing."""
    return f"{precision_column} IS NULL OR {precision_column} IN ({_ALLOWED})"


def precision_pairing_check(date_column: str, precision_column: str) -> str:
    """A date and its precision are present together or absent together."""
    return f"({date_column} IS NULL) = ({precision_column} IS NULL)"
