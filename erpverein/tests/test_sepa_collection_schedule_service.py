from datetime import date

import frappe
from frappe.tests import UnitTestCase

from erpverein.services.sepa_collection_schedule_service import (
    INTERVAL_HALF_YEARLY,
    INTERVAL_MONTHLY,
    INTERVAL_QUARTERLY,
    INTERVAL_WEEKLY,
    INTERVAL_YEARLY,
    STATUS_DUE,
    STATUS_NOT_CONFIGURED,
    STATUS_PLANNED,
    clamp_month_day,
    get_schedule_config,
    is_target_business_day,
    next_collection_occurrence,
    next_target_business_day,
    schedule_fingerprint,
    validate_and_set_collection_schedule,
)


class TestSEPACollectionScheduleService(UnitTestCase):
    def test_clamp_month_day_returns_to_configured_day(self):
        self.assertEqual(clamp_month_day(2027, 2, 31), date(2027, 2, 28))
        self.assertEqual(clamp_month_day(2028, 2, 31), date(2028, 2, 29))
        self.assertEqual(clamp_month_day(2027, 3, 31), date(2027, 3, 31))
        self.assertEqual(clamp_month_day(2027, 4, 31), date(2027, 4, 30))

    def test_target_calendar_handles_weekends_and_holidays(self):
        self.assertFalse(is_target_business_day("2026-04-03"))  # Good Friday
        self.assertFalse(is_target_business_day("2026-04-06"))  # Easter Monday
        self.assertFalse(is_target_business_day("2026-05-01"))
        self.assertFalse(is_target_business_day("2026-10-03"))  # Saturday, not holiday rule
        self.assertTrue(is_target_business_day("2026-10-05"))
        self.assertEqual(next_target_business_day("2026-04-03"), date(2026, 4, 7))

    def test_monthly_day_31_uses_following_without_date_drift(self):
        config = self.make_config(INTERVAL_MONTHLY, month_day=31)

        nominal, effective = next_collection_occurrence(config, "2027-02-02")
        self.assertEqual(nominal, date(2027, 2, 28))
        self.assertEqual(effective, date(2027, 3, 1))

        nominal, effective = next_collection_occurrence(config, "2027-03-02")
        self.assertEqual(nominal, date(2027, 3, 31))
        self.assertEqual(effective, date(2027, 3, 31))

    def test_weekly_occurrence_uses_selected_weekday(self):
        config = self.make_config(INTERVAL_WEEKLY, weekday="Freitag")

        nominal, effective = next_collection_occurrence(config, "2026-07-20")

        self.assertEqual(nominal, date(2026, 7, 24))
        self.assertEqual(effective, date(2026, 7, 24))

    def test_arbitrary_half_yearly_dates_are_not_forced_six_months_apart(self):
        config = self.make_config(
            INTERVAL_HALF_YEARLY,
            annual_dates=[{"month": 3, "day": 31}, {"month": 7, "day": 31}],
        )

        first = next_collection_occurrence(config, "2027-01-01")
        second = next_collection_occurrence(config, "2027-04-01")

        self.assertEqual(first[0], date(2027, 3, 31))
        self.assertEqual(second[0], date(2027, 7, 31))
        self.assertEqual(second[1], date(2027, 8, 2))

    def test_quarterly_requires_four_distinct_dates(self):
        doc = self.make_doc(
            status="Aktiv",
            interval=INTERVAL_QUARTERLY,
            annual_dates=[
                {"monat": 1, "kalendertag": 15, "einzugsbetrag": 25},
                {"monat": 4, "kalendertag": 15, "einzugsbetrag": 25},
            ],
        )

        with self.assertRaises(frappe.ValidationError):
            validate_and_set_collection_schedule(doc, as_of="2026-01-01")

        duplicate = self.make_doc(
            interval=INTERVAL_QUARTERLY,
            annual_dates=[
                {"monat": 1, "kalendertag": 15, "einzugsbetrag": 25},
                {"monat": 1, "kalendertag": 15, "einzugsbetrag": 30},
                {"monat": 7, "kalendertag": 15, "einzugsbetrag": 25},
                {"monat": 10, "kalendertag": 15, "einzugsbetrag": 25},
            ],
        )
        with self.assertRaises(frappe.ValidationError):
            get_schedule_config(duplicate)

    def test_blank_draft_has_no_implicit_schedule(self):
        doc = self.make_doc(interval=None, start=None)

        validate_and_set_collection_schedule(doc, as_of="2026-07-19")

        self.assertEqual(doc.planungsstatus, STATUS_NOT_CONFIGURED)
        self.assertIsNone(doc.naechster_einzugstermin)

    def test_active_status_distinguishes_due_and_planned(self):
        due = self.make_doc(
            status="Aktiv",
            interval=INTERVAL_YEARLY,
            annual_dates=[{"monat": 7, "kalendertag": 19, "einzugsbetrag": 100}],
        )
        validate_and_set_collection_schedule(due, as_of="2027-07-19")
        self.assertEqual(due.planungsstatus, STATUS_DUE)

        planned = self.make_doc(
            status="Aktiv",
            interval=INTERVAL_YEARLY,
            annual_dates=[{"monat": 7, "kalendertag": 20, "einzugsbetrag": 100}],
        )
        validate_and_set_collection_schedule(planned, as_of="2027-07-19")
        self.assertEqual(planned.planungsstatus, STATUS_PLANNED)

    def test_fingerprint_is_independent_of_annual_row_order(self):
        first, complete = get_schedule_config(
            self.make_doc(
                interval=INTERVAL_HALF_YEARLY,
                annual_dates=[
                    {"monat": 7, "kalendertag": 31, "einzugsbetrag": 100},
                    {"monat": 3, "kalendertag": 31, "einzugsbetrag": 50},
                ],
            )
        )
        second, second_complete = get_schedule_config(
            self.make_doc(
                interval=INTERVAL_HALF_YEARLY,
                annual_dates=[
                    {"monat": 3, "kalendertag": 31, "einzugsbetrag": 50},
                    {"monat": 7, "kalendertag": 31, "einzugsbetrag": 100},
                ],
            )
        )

        self.assertTrue(complete and second_complete)
        self.assertEqual(schedule_fingerprint(first), schedule_fingerprint(second))

    def test_amount_changes_schedule_fingerprint(self):
        first, complete = get_schedule_config(
            self.make_doc(
                interval=INTERVAL_YEARLY,
                annual_dates=[{"monat": 3, "kalendertag": 31, "einzugsbetrag": 100}],
            )
        )
        second, second_complete = get_schedule_config(
            self.make_doc(
                interval=INTERVAL_YEARLY,
                annual_dates=[{"monat": 3, "kalendertag": 31, "einzugsbetrag": 125}],
            )
        )

        self.assertTrue(complete and second_complete)
        self.assertNotEqual(schedule_fingerprint(first), schedule_fingerprint(second))

    def test_weekly_schedule_requires_positive_regular_amount(self):
        missing = self.make_doc(status="Aktiv", interval=INTERVAL_WEEKLY, regular_amount=None)
        missing.wochentag = "Freitag"

        with self.assertRaises(frappe.ValidationError):
            validate_and_set_collection_schedule(missing, as_of="2026-07-19")

        invalid = self.make_doc(interval=INTERVAL_WEEKLY, regular_amount=-1)
        invalid.wochentag = "Freitag"
        with self.assertRaises(frappe.ValidationError):
            get_schedule_config(invalid)

    def test_annual_schedule_requires_amount_for_each_date(self):
        doc = self.make_doc(
            interval=INTERVAL_YEARLY,
            annual_dates=[{"monat": 3, "kalendertag": 31}],
        )

        with self.assertRaises(frappe.ValidationError):
            get_schedule_config(doc)

    def test_future_schedule_beyond_ten_years_is_not_ended(self):
        config = self.make_config(INTERVAL_YEARLY, annual_dates=[{"month": 3, "day": 31}])
        config["start"] = "2040-01-01"

        nominal, effective = next_collection_occurrence(config, "2026-07-19")

        self.assertEqual(nominal, date(2040, 3, 31))
        self.assertGreaterEqual(effective, nominal)

    def test_unbounded_clamped_nominal_collision_is_rejected(self):
        doc = self.make_doc(
            interval=INTERVAL_HALF_YEARLY,
            annual_dates=[
                {"monat": 2, "kalendertag": 28, "einzugsbetrag": 50},
                {"monat": 2, "kalendertag": 29, "einzugsbetrag": 50},
            ],
        )

        with self.assertRaises(frappe.ValidationError):
            get_schedule_config(doc)

    def test_leap_year_bounded_dates_may_remain_distinct(self):
        doc = self.make_doc(
            interval=INTERVAL_HALF_YEARLY,
            start="2028-01-01",
            annual_dates=[
                {"monat": 2, "kalendertag": 28, "einzugsbetrag": 50},
                {"monat": 2, "kalendertag": 29, "einzugsbetrag": 50},
            ],
        )
        doc.einzugsplan_bis = "2028-12-31"

        config, complete = get_schedule_config(doc)

        self.assertTrue(complete)
        self.assertEqual(len(config["annual_dates"]), 2)

    @staticmethod
    def make_config(interval, *, weekday=None, month_day=None, regular_amount=None, annual_dates=None):
        return {
            "interval": interval,
            "start": "2026-01-01",
            "end": None,
            "weekday": weekday,
            "month_day": month_day,
            "regular_amount": regular_amount,
            "annual_dates": annual_dates or [],
            "currency": "EUR",
        }

    @staticmethod
    def make_doc(
        *,
        status="Entwurf",
        interval=INTERVAL_YEARLY,
        start="2026-01-01",
        regular_amount=100,
        annual_dates=None,
    ):
        return frappe._dict(
            {
                "status": status,
                "einzugsintervall": interval,
                "einzugsplan_ab": start,
                "einzugsplan_bis": None,
                "wochentag": None,
                "monatstag": None,
                "regelmaessiger_einzugsbetrag": (
                    regular_amount if interval in {INTERVAL_WEEKLY, INTERVAL_MONTHLY} else None
                ),
                "einzugstermine": [frappe._dict(row) for row in (annual_dates or [])],
            }
        )
