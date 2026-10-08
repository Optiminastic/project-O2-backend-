from datetime import date, datetime, timezone

from app.services.numbering import business_date, financial_year, next_number


def test_financial_year_runs_april_to_march():
    assert financial_year(date(2026, 4, 1)) == "2026-27"
    assert financial_year(date(2027, 3, 31)) == "2026-27"
    assert financial_year(date(2027, 4, 1)) == "2027-28"
    assert financial_year(date(2099, 12, 31)) == "2099-00"


def test_business_date_uses_india_time_not_utc():
    # 19:00 UTC on 31 March is 00:30 IST on 1 April: a new financial year in India.
    late_utc = datetime(2027, 3, 31, 19, 0, tzinfo=timezone.utc)
    assert business_date(late_utc) == date(2027, 4, 1)
    assert financial_year(business_date(late_utc)) == "2027-28"


def test_numbers_are_consecutive_per_series(db):
    on = date(2026, 10, 6)
    assert next_number(db, "INV", on) == "INV/2026-27/0001"
    assert next_number(db, "INV", on) == "INV/2026-27/0002"
    assert next_number(db, "PI", on) == "PI/2026-27/0001"
    assert next_number(db, "INV", date(2027, 4, 1)) == "INV/2027-28/0001"
    db.commit()


def test_tax_invoice_number_fits_gst_rule_46_length(db):
    number = next_number(db, "INV", date(2026, 10, 6))
    db.commit()
    assert len(number) <= 16


def test_rolled_back_number_is_reused_so_series_has_no_gaps(db):
    on = date(2026, 10, 6)
    next_number(db, "INV", on)
    db.rollback()
    assert next_number(db, "INV", on) == "INV/2026-27/0001"
    db.commit()
