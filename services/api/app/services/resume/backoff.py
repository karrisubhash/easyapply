"""How long to wait before trying again.

Pure arithmetic over the clock: no database, no session, no knowledge of what a
resume is. That is what makes the policy testable without a schedule and
configurable without touching the code that uses it.

The shape is exponential with jitter:

    delay   = min(BASE * MULTIPLIER ** (attempt - 2), MAX)
    waited  = delay * (1 - JITTER) + random(0, delay * JITTER)

The exponent is `attempt - 2`, not `attempt - 1`, because `attempt` counts from
1 and attempt 1 is not a retry: the first retry is attempt 2 and waits one BASE.
`ceiling_for` writes the same off-by-one out in full.

Exponential because a transient failure is usually something nearby having a bad
minute, and retrying into that minute turns one rate limit into a rate-limit
loop. Each attempt gives it longer to end.

Jitter because the failures worth retrying are usually *shared*. One provider
outage fails every in-flight resume at the same instant; without jitter they
would all wake at the same instant too, hit the recovering provider together,
and fail together — a stampede the backoff was supposed to prevent. Spreading
the wake-ups is most of the value.

The jitter is subtractive, so the configured formula is the CEILING: whatever
JITTER is set to, a delay never exceeds `delay`. That makes the bound assertable
(`lower <= waited <= delay`) instead of merely probable, and means turning
jitter up can never turn a 15-minute maximum into a 30-minute one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.core.config import settings


@dataclass(frozen=True)
class BackoffPolicy:
    """The four numbers that decide when a retry may run.

    A value object rather than four reads of `settings` scattered through the
    code: a policy can be constructed for a test, logged as a unit, and — when
    per-provider backoff eventually matters — varied without every caller
    learning about it.
    """

    base_seconds: float
    multiplier: float
    max_seconds: float
    jitter: float

    @classmethod
    def from_settings(cls) -> "BackoffPolicy":
        return cls(
            base_seconds=settings.RESUME_RETRY_BACKOFF_BASE_SECONDS,
            multiplier=settings.RESUME_RETRY_BACKOFF_MULTIPLIER,
            max_seconds=settings.RESUME_RETRY_BACKOFF_MAX_SECONDS,
            jitter=settings.RESUME_RETRY_JITTER,
        )

    # --- the formula ----------------------------------------------------------

    def ceiling_for(self, attempt: int) -> float:
        """The undelayed delay for an attempt, before jitter.

        `attempt` is the number of the attempt about to be scheduled, counting
        from 1. So the first retry — attempt 2 — waits one base delay, which is
        what makes `multiplier ** (attempt - 2)`'s off-by-one worth writing out:
        the exponent is how many retries have already been waited through.
        """
        if attempt <= 1:
            # The first attempt is not a retry and waits for nothing.
            return 0.0

        retries_so_far = attempt - 2
        try:
            raw = self.base_seconds * (self.multiplier**retries_so_far)
        except OverflowError:
            # A large multiplier and a large attempt overflow float before they
            # reach any sane maximum. The cap is the answer either way.
            return max(self.max_seconds, 0.0)

        return max(0.0, min(raw, self.max_seconds))

    def delay_for(self, attempt: int, *, rng: random.Random | None = None) -> float:
        """The delay actually applied, jitter included.

        `rng` is injectable so a test can pin the randomness rather than assert
        a range and hope. Production passes nothing and gets the module-level
        generator, which is what `random.uniform` would have used anyway.
        """
        ceiling = self.ceiling_for(attempt)
        if ceiling <= 0:
            return 0.0

        spread = ceiling * _clamped_jitter(self.jitter)
        if spread <= 0:
            return ceiling

        source = rng or random
        return ceiling - spread + source.uniform(0.0, spread)

    def bounds_for(self, attempt: int) -> tuple[float, float]:
        """The interval `delay_for` is guaranteed to land in.

        Exists so the guarantee is stated once and asserted against, rather than
        re-derived in every test that cares about it.
        """
        ceiling = self.ceiling_for(attempt)
        return ceiling * (1.0 - _clamped_jitter(self.jitter)), ceiling

    def next_retry_at(
        self,
        attempt: int,
        *,
        now: datetime,
        rng: random.Random | None = None,
    ) -> datetime | None:
        """When this attempt becomes eligible, or None if it is eligible now.

        None rather than `now` on purpose: it is stored in a nullable column
        where NULL means "nothing is holding this back", and a timestamp that
        merely happens to be in the past would make every query carry a
        comparison it does not need.
        """
        delay = self.delay_for(attempt, rng=rng)
        if delay <= 0:
            return None
        return now + timedelta(seconds=delay)


def _clamped_jitter(jitter: float) -> float:
    """Jitter outside [0, 1] is a configuration mistake, not an instruction.

    Above 1 it would make a delay negative; below 0 it would push a delay past
    the maximum the operator configured. Both are refused quietly here rather
    than loudly at the point a retry is scheduled, because the wrong time to
    discover a bad setting is while recovering from an outage.
    """
    return min(1.0, max(0.0, jitter))


def policy() -> BackoffPolicy:
    """The configured policy. One place the settings are read."""
    return BackoffPolicy.from_settings()
