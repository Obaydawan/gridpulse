from datetime import date

import pytest

from gridpulse.ingestion.sgcc_contract import (
    SchemaError,
    parse_header,
    validate_header,
)


def day_cols(*days: tuple[int, int, int]) -> list[str]:
    """Build non-zero-padded M/D/YYYY column names from (year, month, day)."""
    return [f"{m}/{d}/{y}" for (y, m, d) in days]


JAN_1_TO_3 = day_cols((2014, 1, 1), (2014, 1, 2), (2014, 1, 3))


def test_valid_header_is_accepted():
    report = validate_header(JAN_1_TO_3 + ["CONS_NO", "FLAG"])
    assert report.n_fields == 5
    assert report.n_date_columns == 3
    assert report.first_date == date(2014, 1, 1)
    assert report.last_date == date(2014, 1, 3)
    assert report.missing_calendar_dates == ()


def test_column_position_does_not_matter():
    report = validate_header(["CONS_NO", "FLAG"] + JAN_1_TO_3)
    assert report.n_date_columns == 3


def test_calendar_gap_is_reported_not_rejected():
    header = day_cols((2014, 1, 1), (2014, 1, 2), (2014, 1, 4)) + ["CONS_NO", "FLAG"]
    report = validate_header(header)
    assert report.missing_calendar_dates == (date(2014, 1, 3),)


def test_missing_label_column_is_rejected():
    with pytest.raises(SchemaError, match="FLAG"):
        validate_header(JAN_1_TO_3 + ["CONS_NO"])


def test_duplicate_column_is_rejected():
    header = JAN_1_TO_3 + ["1/2/2014", "CONS_NO", "FLAG"]
    with pytest.raises(SchemaError, match="duplicate"):
        validate_header(header)


def test_unparseable_date_column_is_rejected():
    with pytest.raises(SchemaError, match="unparseable"):
        validate_header(JAN_1_TO_3 + ["hello", "CONS_NO", "FLAG"])


def test_descending_dates_are_rejected():
    header = list(reversed(JAN_1_TO_3)) + ["CONS_NO", "FLAG"]
    with pytest.raises(SchemaError, match="ascending"):
        validate_header(header)


def test_parse_header_handles_bom_and_crlf():
    line = "\ufeff1/1/2014,1/2/2014,CONS_NO,FLAG\r\n"
    assert parse_header(line) == ["1/1/2014", "1/2/2014", "CONS_NO", "FLAG"]


def test_all_problems_are_reported_together():
    with pytest.raises(SchemaError) as excinfo:
        validate_header(JAN_1_TO_3 + ["hello", "CONS_NO"])
    message = str(excinfo.value)
    assert "FLAG" in message
    assert "unparseable" in message
