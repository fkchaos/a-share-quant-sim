# coding: utf-8
"""China A-share statutory holiday calendar.

Sources (State Council General Office notices):
  - 2026 arrangement: OFFICIAL, notice dated 2025-11-04
  - 2027 arrangement: ESTIMATE ONLY -- official notice not yet
    published (expected early November 2026). Dates are best-effort
    projections based on solar terms and current scheduling rules
    (New Year 3d; Spring Festival 8d from Eve; Qingming 3d; Labor
    Day 5d; Dragon Boat / Mid-Autumn single day when falling on
    Wednesday, else 3d block; National Day 7d). UPDATE this section
    when the official notice publishes.

A-share exchanges are closed on statutory holidays. Weekend make-up
workdays are still non-trading days and are handled by the weekday
check in callers, not by this module.

SAFETY: callers (account_runner.is_trade_day) keep the DB data check
as the final arbiter, so a bad estimate can at worst skip one real
trading day (no trade executed). It can never force a trade on a
closed day: a missed holiday still fails the DB data check.

IMPORTANT: update CN_HOLIDAYS every year when the State Council
publishes the next-year arrangement (usually early November).
"""

from datetime import datetime

# All days inside each holiday block (weekend days inside a block are
# included so the set is self-documenting; callers still check weekday
# first, so this is redundant but harmless).
CN_HOLIDAYS = set(
    # ---- 2026 (OFFICIAL, State Council notice dated 2025-11-04) ----
    [
        # New Year's Day: Jan 1 - Jan 3
        "2026-01-01", "2026-01-02", "2026-01-03",
        # Spring Festival: Feb 15 - Feb 23
        "2026-02-15", "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19",
        "2026-02-20", "2026-02-21", "2026-02-22", "2026-02-23",
        # Qingming Festival: Apr 4 - Apr 6
        "2026-04-04", "2026-04-05", "2026-04-06",
        # Labor Day: May 1 - May 5
        "2026-05-01", "2026-05-02", "2026-05-03", "2026-05-04", "2026-05-05",
        # Dragon Boat Festival: Jun 19 - Jun 21
        "2026-06-19", "2026-06-20", "2026-06-21",
        # Mid-Autumn Festival: Sep 25 - Sep 27
        "2026-09-25", "2026-09-26", "2026-09-27",
        # National Day: Oct 1 - Oct 7
        "2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04",
        "2026-10-05", "2026-10-06", "2026-10-07",
        # ---- 2027 (ESTIMATE ONLY, replace with official notice) ----
        # New Year's Day: Jan 1 (Fri) - Jan 3 (Sun), no swap needed
        "2027-01-01", "2027-01-02", "2027-01-03",
        # Spring Festival: Eve Feb 5 (Fri) - Feb 12 (Fri), 8 days
        # (CNY day = Feb 6, Saturday)
        "2027-02-05", "2027-02-06", "2027-02-07", "2027-02-08",
        "2027-02-09", "2027-02-10", "2027-02-11", "2027-02-12",
        # Qingming Festival: Apr 3 (Sat) - Apr 5 (Mon), 3 days
        "2027-04-03", "2027-04-04", "2027-04-05",
        # Labor Day: May 1 (Sat) - May 5 (Wed), 5 days
        "2027-05-01", "2027-05-02", "2027-05-03", "2027-05-04", "2027-05-05",
        # Dragon Boat Festival: Jun 9 (Wed) only -- falls on Wednesday
        "2027-06-09",
        # Mid-Autumn Festival: Sep 15 (Wed) only -- falls on Wednesday
        "2027-09-15",
        # National Day: Oct 1 (Fri) - Oct 7 (Thu), 7 days
        "2027-10-01", "2027-10-02", "2027-10-03", "2027-10-04",
        "2027-10-05", "2027-10-06", "2027-10-07",
    ]
)


def is_cn_holiday(date_str):
    """Return True if date_str (YYYY-MM-DD) is a China statutory holiday.

    Invalid date format raises ValueError (fail loudly, no silent skip).
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d")  # raises ValueError on bad format
    return dt.strftime("%Y-%m-%d") in CN_HOLIDAYS
