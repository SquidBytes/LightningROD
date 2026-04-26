"""Base factory class with seeded RNG and counter for deterministic test data."""

import random
from datetime import UTC, datetime, timedelta


class BaseFactory:
    """Base class for test data factories.

    Provides seeded random number generation and an incrementing counter
    for creating unique, deterministic test data across all factory classes.
    """

    _counter: int = 0
    _rng: random.Random = random.Random(42)

    @classmethod
    def reset_seed(cls, seed: int = 42) -> None:
        """Reset the RNG seed and counter for deterministic test runs."""
        cls._counter = 0
        cls._rng = random.Random(seed)

    @classmethod
    def _next_id(cls) -> int:
        """Return an incrementing counter value."""
        cls._counter += 1
        return cls._counter

    @classmethod
    def _random_datetime(
        cls,
        days_back: int = 30,
        days_forward: int = 0,
    ) -> datetime:
        """Generate a random datetime within a range relative to now (UTC)."""
        now = datetime.now(UTC)
        start = now - timedelta(days=days_back)
        end = now + timedelta(days=days_forward) if days_forward else now
        delta = (end - start).total_seconds()
        offset = cls._rng.uniform(0, delta)
        return start + timedelta(seconds=offset)

    @classmethod
    def _random_float(cls, low: float, high: float, decimals: int = 2) -> float:
        """Generate a random float rounded to given decimal places."""
        return round(cls._rng.uniform(low, high), decimals)

    @classmethod
    def _random_int(cls, low: int, high: int) -> int:
        """Generate a random integer in [low, high]."""
        return cls._rng.randint(low, high)

    @classmethod
    def _random_choice(cls, options: list):
        """Pick a random item from a list."""
        return cls._rng.choice(options)
