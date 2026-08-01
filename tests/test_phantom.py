"""Tests for the phantom (standby) load tracker."""

import datetime as dt
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "ted_phantom", ROOT / "custom_components" / "ted5000_pro" / "phantom.py"
)
phantom = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = phantom
_spec.loader.exec_module(phantom)


def _at(day, hour, minute=0):
    return dt.datetime(2026, 7, day, hour, minute)


def test_records_nightly_minimum():
    t = phantom.PhantomTracker(days=7)
    t.update(_at(1, 1, 0), 800)
    t.update(_at(1, 2, 0), 450)      # the floor
    t.update(_at(1, 3, 0), 600)
    assert t.current == 450
    t.update(_at(1, 9, 0), 4000)     # outside window -> commit
    assert t.current == 450
    assert t.average == 450


def test_ignores_daytime_samples():
    t = phantom.PhantomTracker()
    t.update(_at(1, 12, 0), 5000)
    t.update(_at(1, 20, 0), 9000)
    assert t.current is None and t.average is None


def test_averages_multiple_days():
    t = phantom.PhantomTracker(days=7)
    for day, floor in ((1, 400), (2, 500), (3, 600)):
        t.update(_at(day, 2, 0), floor)
        t.update(_at(day, 10, 0), 3000)   # close the night
    assert t.average == 500
    assert [h.watts for h in t.history] == [400, 500, 600]


def test_window_only_keeps_configured_days():
    t = phantom.PhantomTracker(days=2)
    for day, floor in ((1, 100), (2, 200), (3, 300)):
        t.update(_at(day, 2, 0), floor)
        t.update(_at(day, 10, 0), 3000)
    assert [h.watts for h in t.history] == [200, 300]
    assert t.average == 250


def test_wrapping_window_groups_one_night():
    t = phantom.PhantomTracker(
        days=7, window_start=dt.time(23, 0), window_end=dt.time(5, 0)
    )
    t.update(_at(1, 23, 30), 700)     # before midnight
    t.update(_at(2, 1, 0), 350)       # after midnight, same night
    t.update(_at(2, 4, 0), 500)
    assert t.current == 350
    t.update(_at(2, 9, 0), 4000)
    assert len(t.history) == 1        # one night, not two
    assert t.history[0].watts == 350


def test_monthly_cost():
    t = phantom.PhantomTracker()
    t.update(_at(1, 2, 0), 500)
    t.update(_at(1, 10, 0), 3000)
    # 500 W for a month at $0.21/kWh
    cost = t.monthly_cost(0.21)
    assert abs(cost - (0.5 * 24 * 30.44 * 0.21)) < 0.01
    assert t.monthly_cost(None) is None


def test_persistence_round_trip():
    t = phantom.PhantomTracker(days=5)
    t.update(_at(1, 2, 0), 420)
    t.update(_at(1, 10, 0), 3000)
    t.update(_at(2, 2, 30), 380)      # night in progress
    state = t.as_dict()

    restored = phantom.PhantomTracker(days=5)
    restored.restore(state)
    assert [h.watts for h in restored.history] == [420]
    assert restored.current == 380
    restored.update(_at(2, 3, 0), 360)
    assert restored.current == 360


def test_handles_missing_values():
    t = phantom.PhantomTracker()
    t.update(_at(1, 2, 0), None)
    assert t.current is None
    t.update(_at(1, 2, 5), 300)
    assert t.current == 300


def test_parse_time():
    assert phantom.parse_time("23:30", dt.time(1, 0)) == dt.time(23, 30)
    assert phantom.parse_time("7", dt.time(1, 0)) == dt.time(7, 0)
    assert phantom.parse_time("nonsense", dt.time(1, 0)) == dt.time(1, 0)


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print(f"PASS {name}")
            except AssertionError as err:
                failures += 1; print(f"FAIL {name}: {err}")
    raise SystemExit(1 if failures else 0)
