"""Reporting periods.

Every period is held as a real start and end date. Labels such as "2026 H1" are
never used to line periods up, because they mean different things in different
places: the FCA's H1 is January to June, firms report on their own financial
half-years, and the Ombudsman's "H1 2026" is April to September 2026.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime

_SEPARATOR = re.compile(r"\s+to\s+", re.IGNORECASE)
_LABEL = re.compile(r"^\s*(\d{4})\s*H([12])\s*$", re.IGNORECASE)
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d-%m-%Y",
    "%d %B %Y",
    "%d %B %y",
    "%d %b %Y",
    "%d %b %y",
    "%d-%B-%Y",
    "%d-%b-%Y",
)


@dataclass(frozen=True, order=True)
class Period:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"period ends before it starts: {self.start} to {self.end}")

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def months(self) -> int:
        """Length in whole months, rounded (a calendar half-year is 6)."""
        return round(self.days / 30.4375)

    def overlap_days(self, other: Period) -> int:
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        return max(0, (end - start).days + 1)

    def overlap_share(self, other: Period) -> float:
        """Share of `other` that falls inside this period."""
        return self.overlap_days(other) / other.days


def parse_date(text: str) -> date:
    cleaned = re.sub(r"\s+", " ", str(text).strip()).replace("Sept ", "Sep ")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date: {text!r}")


def parse_period(value: object) -> Period | None:
    """Parse "01 July 2016 to 31 December 2016" and the other published layouts.

    Returns None when the value is not a period (a blank, a label, a number).
    """
    if not isinstance(value, str):
        return None
    parts = _SEPARATOR.split(value.strip())
    if len(parts) != 2:
        return None
    try:
        start, end = parse_date(parts[0]), parse_date(parts[1])
    except ValueError:
        return None
    if end < start:
        return None
    return Period(start, end)


def label_parts(label: str) -> tuple[int, int]:
    match = _LABEL.match(str(label))
    if not match:
        raise ValueError(f"not a half-year label: {label!r}")
    return int(match.group(1)), int(match.group(2))


def normalise_label(label: str) -> str:
    year, half = label_parts(label)
    return f"{year} H{half}"


def calendar_half(label: str) -> Period:
    """The calendar half-year a label names: H1 is January to June, H2 July to December."""
    year, half = label_parts(label)
    if half == 1:
        return Period(date(year, 1, 1), date(year, 6, 30))
    return Period(date(year, 7, 1), date(year, 12, 31))


def label_sort_key(label: str) -> int:
    year, half = label_parts(label)
    return year * 10 + half
