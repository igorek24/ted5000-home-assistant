"""Phantom (standby) load tracking.

Every night the house settles to a floor: fridges, standby power supplies,
network gear. That floor is the phantom load. This module records the
minimum consumption seen inside a quiet window each day and averages the
last N days, so a creeping baseline (something left on, a failing
appliance) becomes visible instead of hiding inside the daily total.

`PhantomTracker` is deliberately free of Home Assistant imports so it can
be unit tested on its own.
"""

from __future__ import annotations

import datetime as dt
from collections import deque
from dataclasses import dataclass
from typing import Any

HOURS_PER_MONTH = 24 * 30.44


def parse_time(value: str, fallback: dt.time) -> dt.time:
    try:
        hour, _, minute = value.partition(":")
        return dt.time(int(hour), int(minute or 0))
    except (ValueError, AttributeError):
        return fallback


@dataclass
class DailyPhantom:
    day: str          # ISO date
    watts: float


class PhantomTracker:
    """Rolling record of the daily quiet-window minimum."""

    def __init__(
        self,
        days: int = 7,
        window_start: dt.time = dt.time(1, 0),
        window_end: dt.time = dt.time(5, 0),
    ) -> None:
        self.days = days
        self.window_start = window_start
        self.window_end = window_end
        self._history: deque[DailyPhantom] = deque(maxlen=days)
        self._current_day: str | None = None
        self._current_min: float | None = None
        self._committed = True

    # -- window helpers -----------------------------------------------------

    def in_window(self, when: dt.datetime) -> bool:
        now = when.time()
        if self.window_start <= self.window_end:
            return self.window_start <= now < self.window_end
        # window wraps past midnight (e.g. 23:00 -> 05:00)
        return now >= self.window_start or now < self.window_end

    def _window_day(self, when: dt.datetime) -> str:
        """The day a sample belongs to.

        For a wrapping window, samples after midnight belong to the day the
        window started on, so one night is one entry.
        """
        if self.window_start > self.window_end and when.time() < self.window_end:
            return (when.date() - dt.timedelta(days=1)).isoformat()
        return when.date().isoformat()

    # -- recording ----------------------------------------------------------

    def update(self, when: dt.datetime, watts: float | None) -> None:
        if watts is None:
            return
        if self.in_window(when):
            day = self._window_day(when)
            if self._current_day != day:
                self._commit()
                self._current_day = day
                self._current_min = watts
                self._committed = False
            elif self._current_min is None or watts < self._current_min:
                self._current_min = watts
        else:
            self._commit()

    def _commit(self) -> None:
        if self._committed or self._current_day is None or self._current_min is None:
            return
        self._history.append(DailyPhantom(self._current_day, self._current_min))
        self._committed = True

    # -- results ------------------------------------------------------------

    @property
    def current(self) -> float | None:
        """Tonight's running minimum, or the most recent completed night."""
        if self._current_min is not None and not self._committed:
            return self._current_min
        return self._history[-1].watts if self._history else None

    @property
    def average(self) -> float | None:
        """Mean of the completed nights on record."""
        if not self._history:
            return None
        return sum(item.watts for item in self._history) / len(self._history)

    @property
    def history(self) -> list[DailyPhantom]:
        return list(self._history)

    def monthly_cost(self, rate: float | None) -> float | None:
        """What the averaged phantom load costs per month at `rate` $/kWh."""
        average = self.average
        if average is None or rate is None:
            return None
        return average / 1000 * HOURS_PER_MONTH * rate

    # -- persistence --------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        return {
            "history": [[item.day, item.watts] for item in self._history],
            "current_day": self._current_day,
            "current_min": self._current_min,
            "committed": self._committed,
        }

    def restore(self, data: dict[str, Any] | None) -> None:
        if not data:
            return
        try:
            for day, watts in data.get("history", []):
                self._history.append(DailyPhantom(str(day), float(watts)))
            self._current_day = data.get("current_day")
            current = data.get("current_min")
            self._current_min = float(current) if current is not None else None
            self._committed = bool(data.get("committed", True))
        except (TypeError, ValueError):
            self._history.clear()
            self._current_day = None
            self._current_min = None
            self._committed = True
