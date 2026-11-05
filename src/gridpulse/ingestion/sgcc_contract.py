"""Schema contract for the raw SGCC CSV header.

The contract states what the pipeline REQUIRES of the file. It is based on the
measured profile in docs/data_sources.md, not on the mirror's description.
It is deliberately independent of column position.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

ID_COL = "CONS_NO"
LABEL_COL = "FLAG"
DATE_FORMAT = "%m/%d/%Y"


class SchemaError(ValueError):
    """Raised when the file header violates the contract."""


@dataclass(frozen=True)
class HeaderReport:
    """Facts about a header that passed validation."""

    n_fields: int
    n_date_columns: int
    first_date: date
    last_date: date
    missing_calendar_dates: tuple[date, ...]


def parse_header(line: str) -> list[str]:
    """Split a raw header line into column names (tolerates BOM and CRLF)."""
    return line.lstrip("\ufeff").rstrip("\r\n").split(",")


def read_header(path: Path) -> list[str]:
    """Read only the first line of a CSV file and return its column names."""
    with path.open(newline="", encoding="utf-8") as f:
        return parse_header(f.readline())


def validate_header(header: list[str]) -> HeaderReport:
    """Check a header against the contract.

    Raises SchemaError listing every problem found. Returns a HeaderReport
    otherwise. Calendar dates with no column are reported, not rejected.
    """
    problems: list[str] = []

    counts = Counter(header)
    duplicates = sorted(name for name, n in counts.items() if n > 1)
    if duplicates:
        problems.append(f"duplicate column names: {duplicates[:5]}")

    for required in (ID_COL, LABEL_COL):
        if counts[required] != 1:
            problems.append(
                f"column {required!r} must appear exactly once, found {counts[required]}"
            )

    parsed: list[date] = []
    unparseable: list[str] = []
    for name in header:
        if name in (ID_COL, LABEL_COL):
            continue
        try:
            parsed.append(datetime.strptime(name, DATE_FORMAT).date())
        except ValueError:
            unparseable.append(name)

    if unparseable:
        problems.append(f"unparseable date columns (first 5): {unparseable[:5]}")
    if not parsed:
        problems.append("no date columns found")
    elif any(later <= earlier for earlier, later in zip(parsed, parsed[1:])):
        problems.append("date columns are not in strictly ascending order")

    if problems:
        raise SchemaError("; ".join(problems))

    first, last = parsed[0], parsed[-1]
    present = set(parsed)
    n_days = (last - first).days + 1
    missing = tuple(
        day
        for day in (first + timedelta(days=i) for i in range(n_days))
        if day not in present
    )
    return HeaderReport(
        n_fields=len(header),
        n_date_columns=len(parsed),
        first_date=first,
        last_date=last,
        missing_calendar_dates=missing,
    )
