"""
Unit tests for core/holidays.py (China A-share statutory holiday calendar).

2026 dates are official (State Council notice dated 2025-11-04).
2027 dates are ESTIMATES (official notice usually publishes early Nov;
update CN_HOLIDAYS when it lands).

Usage:
    python -m pytest tests/test_holidays.py -v
"""

import os, sys, pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from core.holidays import CN_HOLIDAYS, is_cn_holiday


class Test2026Official:
    """2026 dates verified against State Council notice dated 2025-11-04."""

    def test_new_year_2026(self):
        assert is_cn_holiday("2026-01-01")
        assert is_cn_holiday("2026-01-03")
        assert not is_cn_holiday("2026-01-05")  # Monday after holiday

    def test_spring_festival_2026(self):
        assert is_cn_holiday("2026-02-15")  # holiday start (Sunday)
        assert is_cn_holiday("2026-02-16")  # Monday inside holiday
        assert is_cn_holiday("2026-02-23")  # holiday end (Monday)
        assert not is_cn_holiday("2026-02-24")  # Tuesday, market resumes

    def test_qingming_2026(self):
        assert is_cn_holiday("2026-04-06")  # Monday
        assert not is_cn_holiday("2026-04-07")

    def test_labor_day_2026(self):
        assert is_cn_holiday("2026-05-01")
        assert is_cn_holiday("2026-05-05")  # Tuesday
        assert not is_cn_holiday("2026-05-06")

    def test_dragon_boat_2026(self):
        assert is_cn_holiday("2026-06-19")
        assert not is_cn_holiday("2026-06-22")  # Monday

    def test_mid_autumn_2026(self):
        assert is_cn_holiday("2026-09-25")
        assert not is_cn_holiday("2026-09-28")  # Monday

    def test_national_day_2026(self):
        assert is_cn_holiday("2026-10-01")
        assert is_cn_holiday("2026-10-05")  # Monday, mid-holiday (today)
        assert is_cn_holiday("2026-10-07")
        assert not is_cn_holiday("2026-10-08")  # Thursday, market resumes

    def test_2026_block_day_count(self):
        days_2026 = [d for d in CN_HOLIDAYS if d.startswith("2026")]
        # 3 + 9 + 3 + 5 + 3 + 3 + 7 = 33
        assert len(days_2026) == 33


class Test2027Estimates:
    """2027 dates are estimates until the official notice publishes."""

    def test_new_year_2027_est(self):
        assert is_cn_holiday("2027-01-01")
        assert is_cn_holiday("2027-01-03")

    def test_spring_festival_2027_est(self):
        # CNY 2027 = Feb 6 (Saturday); 8-day block from Eve Feb 5
        assert is_cn_holiday("2027-02-05")  # Eve
        assert is_cn_holiday("2027-02-06")  # CNY day
        assert is_cn_holiday("2027-02-12")  # estimated last day
        assert not is_cn_holiday("2027-02-15")  # Monday after

    def test_qingming_2027_est(self):
        assert is_cn_holiday("2027-04-05")  # Monday
        assert not is_cn_holiday("2027-04-06")

    def test_labor_day_2027_est(self):
        assert is_cn_holiday("2027-05-01")
        assert is_cn_holiday("2027-05-05")
        assert not is_cn_holiday("2027-05-06")

    def test_single_day_festivals_2027_est(self):
        # Dragon boat Jun 9 (Wed) and Mid-Autumn Sep 15 (Wed):
        # rule "if it falls on Wednesday, only that day off"
        assert is_cn_holiday("2027-06-09")
        assert is_cn_holiday("2027-09-15")

    def test_national_day_2027_est(self):
        assert is_cn_holiday("2027-10-01")
        assert is_cn_holiday("2027-10-07")
        assert not is_cn_holiday("2027-10-08")


class TestApiContract:

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            is_cn_holiday("2026/10/01")
        with pytest.raises(ValueError):
            is_cn_holiday("not-a-date")
        with pytest.raises(ValueError):
            is_cn_holiday("")

    def test_no_2025_dates(self):
        # Scope is 2026 + 2027 only
        assert not any(d.startswith("2025") for d in CN_HOLIDAYS)
