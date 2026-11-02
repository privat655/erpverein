from datetime import date

import frappe
from frappe.tests import UnitTestCase

from erpverein.services.payment_schedule_service import (
    PRENOTIFICATION_MANUAL,
    PRENOTIFICATION_NOT_REQUIRED,
    PRENOTIFICATION_PENDING,
    PRENOTIFICATION_RESEND,
    PRENOTIFICATION_SENT,
    assign_payment_terms,
    next_prenotification_status,
    plan_mandate_rows,
    schedule_fingerprint,
)
from erpverein.services.prenotification_service import (
    get_prenotification_readiness,
    has_prenotification_attachment,
)
from erpverein.services.sepa_collection_schedule_service import next_target_business_day
from erpverein.services.sepa_export_profile_service import is_valid_creditor_identifier
from erpverein.setup_data import (
    MODE_OF_PAYMENT_DIRECT_DEBIT,
    MODE_OF_PAYMENT_TRANSFER,
    PAYMENT_TERM_COUNT,
    payment_term_name,
)


def annual_config(interval: str, *dates: tuple[int, int, float]) -> dict:
    return {
        "interval": interval,
        "start": "2026-01-01",
        "end": None,
        "weekday": None,
        "month_day": None,
        "regular_amount": None,
        "annual_dates": [{"month": month, "day": day, "amount": amount} for month, day, amount in dates],
        "currency": "EUR",
    }


class TestPaymentSchedulePlanning(UnitTestCase):
    def test_half_yearly_mandate_splits_invoice_into_two_direct_debits(self):
        config = annual_config("Halbjaehrlich", (3, 31, 175.0), (9, 30, 175.0))

        rows, conflict = plan_mandate_rows(config, "2027-01-01", "2027-12-31", "2027-01-01", 350)

        self.assertIsNone(conflict)
        self.assertEqual(
            [(row["due_date"], row["payment_amount"], row["mode_of_payment"]) for row in rows],
            [
                (date(2027, 3, 31), 175.0, MODE_OF_PAYMENT_DIRECT_DEBIT),
                (date(2027, 9, 30), 175.0, MODE_OF_PAYMENT_DIRECT_DEBIT),
            ],
        )

    def test_yearly_mandate_due_on_sunday_is_collected_on_next_business_day(self):
        config = annual_config("Jaehrlich", (1, 31, 350.0))

        rows, conflict = plan_mandate_rows(config, "2027-01-01", "2027-12-31", "2027-01-01", 350)

        self.assertIsNone(conflict)
        self.assertEqual([row["due_date"] for row in rows], [date(2027, 1, 31)])
        self.assertEqual(next_target_business_day(rows[0]["due_date"]), date(2027, 2, 1))

    def test_smaller_mandate_total_adds_transfer_rest_row(self):
        config = annual_config("Jaehrlich", (1, 31, 300.0))

        rows, conflict = plan_mandate_rows(config, "2027-01-01", "2027-12-31", "2027-01-01", 350)

        self.assertIsNone(conflict)
        self.assertEqual(
            [(row["due_date"], row["payment_amount"], row["mode_of_payment"]) for row in rows],
            [
                (date(2027, 1, 1), 50.0, MODE_OF_PAYMENT_TRANSFER),
                (date(2027, 1, 31), 300.0, MODE_OF_PAYMENT_DIRECT_DEBIT),
            ],
        )
        self.assertEqual(sum(row["payment_amount"] for row in rows), 350)

    def test_rest_row_moves_to_next_free_day(self):
        config = annual_config("Jaehrlich", (1, 31, 300.0))

        rows, _conflict = plan_mandate_rows(config, "2027-01-01", "2027-12-31", "2027-01-01", 350, date(2027, 1, 31))

        self.assertEqual([row["due_date"] for row in rows], [date(2027, 1, 31), date(2027, 2, 1)])
        self.assertEqual(rows[1]["mode_of_payment"], MODE_OF_PAYMENT_TRANSFER)

    def test_larger_mandate_total_is_conflict_without_direct_debit(self):
        config = annual_config("Jaehrlich", (1, 31, 400.0))

        rows, conflict = plan_mandate_rows(config, "2027-01-01", "2027-12-31", "2027-01-01", 350)

        self.assertEqual(rows, [])
        self.assertIn("groesser", conflict)

    def test_no_mandate_date_in_period_is_conflict(self):
        config = annual_config("Jaehrlich", (1, 31, 350.0))

        rows, conflict = plan_mandate_rows(config, "2027-02-01", "2027-12-31", "2027-02-01", 350)

        self.assertEqual(rows, [])
        self.assertIn("Kein Mandatstermin", conflict)

    def test_mandate_date_before_invoice_date_is_conflict(self):
        config = annual_config("Jaehrlich", (1, 31, 350.0))

        rows, conflict = plan_mandate_rows(config, "2027-01-01", "2027-12-31", "2027-12-31", 350)

        self.assertEqual(rows, [])
        self.assertIn("vor dem Rechnungsdatum", conflict)

    def test_monthly_rent_mandate_uses_month_day(self):
        config = annual_config("Monatlich")
        config.update({"month_day": 3, "regular_amount": 500.0, "annual_dates": []})

        rows, conflict = plan_mandate_rows(config, "2027-02-01", "2027-02-28", "2027-01-15", 500)

        self.assertIsNone(conflict)
        self.assertEqual([(row["due_date"], row["payment_amount"]) for row in rows], [(date(2027, 2, 3), 500.0)])

    def test_weekly_mandate_creates_one_row_per_week(self):
        config = annual_config("Woechentlich")
        config.update({"weekday": "Montag", "regular_amount": 10.0, "annual_dates": []})

        rows, conflict = plan_mandate_rows(config, "2027-03-01", "2027-03-31", "2027-03-01", 50)

        self.assertIsNone(conflict)
        self.assertEqual([row["due_date"].day for row in rows], [1, 8, 15, 22, 29])

    def test_payment_terms_are_unique_and_follow_due_dates(self):
        rows = [
            {"due_date": "2027-09-30", "payment_term": None},
            {"due_date": "2027-03-31", "payment_term": None},
            {"due_date": "2027-06-30", "payment_term": payment_term_name(1)},
        ]

        assign_payment_terms(rows, reserved=[payment_term_name(2)])

        self.assertEqual(
            [row["payment_term"] for row in rows],
            [payment_term_name(4), payment_term_name(3), payment_term_name(1)],
        )

    def test_duplicate_payment_terms_are_rejected(self):
        rows = [
            {"due_date": "2027-03-31", "payment_term": payment_term_name(1)},
            {"due_date": "2027-09-30", "payment_term": payment_term_name(1)},
        ]

        with self.assertRaises(frappe.ValidationError):
            assign_payment_terms(rows)

    def test_too_many_rows_are_rejected(self):
        rows = [{"due_date": f"2027-01-{(index % 28) + 1:02d}", "payment_term": None} for index in range(PAYMENT_TERM_COUNT + 1)]

        with self.assertRaises(frappe.ValidationError):
            assign_payment_terms(rows)

    def test_fingerprint_ignores_row_order_and_amounts_of_portion_rows(self):
        first = {"due_date": "2027-03-31", "payment_amount": 175, "mode_of_payment": MODE_OF_PAYMENT_DIRECT_DEBIT, "payment_term": "A", "idx": 1}
        second = {"due_date": "2027-09-30", "payment_amount": 175, "mode_of_payment": MODE_OF_PAYMENT_DIRECT_DEBIT, "payment_term": "B", "idx": 2}
        portion = {"due_date": "2027-01-01", "invoice_portion": 100, "payment_amount": 350, "mode_of_payment": MODE_OF_PAYMENT_TRANSFER, "payment_term": "C", "idx": 1}

        self.assertEqual(schedule_fingerprint([first, second]), schedule_fingerprint([second, first]))
        self.assertNotEqual(
            schedule_fingerprint([first, second]),
            schedule_fingerprint([first, {**second, "mode_of_payment": MODE_OF_PAYMENT_TRANSFER}]),
        )
        self.assertEqual(schedule_fingerprint([portion]), schedule_fingerprint([{**portion, "payment_amount": 400}]))

    def test_prenotification_status_transitions(self):
        self.assertEqual(next_prenotification_status(None, False, False), PRENOTIFICATION_NOT_REQUIRED)
        self.assertEqual(next_prenotification_status(None, True, True), PRENOTIFICATION_PENDING)
        self.assertEqual(next_prenotification_status(PRENOTIFICATION_SENT, True, False), PRENOTIFICATION_SENT)
        self.assertEqual(next_prenotification_status(PRENOTIFICATION_SENT, True, True), PRENOTIFICATION_RESEND)
        self.assertEqual(next_prenotification_status(PRENOTIFICATION_MANUAL, True, True), PRENOTIFICATION_RESEND)
        self.assertEqual(next_prenotification_status(PRENOTIFICATION_RESEND, True, False), PRENOTIFICATION_RESEND)
        self.assertEqual(next_prenotification_status(None, True, True, required=False), PRENOTIFICATION_NOT_REQUIRED)
        self.assertEqual(
            next_prenotification_status(PRENOTIFICATION_NOT_REQUIRED, True, False, required=True), PRENOTIFICATION_PENDING
        )

    def test_prenotification_readiness_requires_fresh_and_timely_email(self):
        profile = frappe._dict(prenotification_days=14, creation="2027-01-01 00:00:00", rent_prenotification_required=0)
        doc = frappe._dict(
            company=None,
            erpverein_billing_kind="Mitgliedsbeitrag",
            erpverein_prenotification_status=PRENOTIFICATION_SENT,
            erpverein_prenotification_sent_at="2027-01-10 09:00:00",
        )
        row = frappe._dict(erpverein_changed_at="2027-01-01 08:00:00")

        self.assertEqual(get_prenotification_readiness(doc, row, date(2027, 2, 1), profile), (True, None))
        self.assertFalse(get_prenotification_readiness(doc, row, date(2027, 1, 20), profile)[0])
        changed = frappe._dict(erpverein_changed_at="2027-01-11 08:00:00")
        self.assertFalse(get_prenotification_readiness(doc, changed, date(2027, 2, 1), profile)[0])
        pending = frappe._dict(doc, erpverein_prenotification_status=PRENOTIFICATION_PENDING)
        self.assertFalse(get_prenotification_readiness(pending, row, date(2027, 2, 1), profile)[0])
        before_profile = frappe._dict(profile, creation="2027-01-10 10:00:00")
        self.assertIn("Exportprofils", get_prenotification_readiness(doc, row, date(2027, 2, 1), before_profile)[1])

    def test_rent_needs_no_email_prenotification_unless_profile_requires_it(self):
        profile = frappe._dict(prenotification_days=14, creation="2027-01-01 00:00:00", rent_prenotification_required=0)
        rent = frappe._dict(
            company=None,
            erpverein_billing_kind="Miete",
            erpverein_prenotification_status=PRENOTIFICATION_NOT_REQUIRED,
            erpverein_prenotification_sent_at=None,
        )
        row = frappe._dict(erpverein_changed_at="2027-01-20 08:00:00")

        self.assertEqual(get_prenotification_readiness(rent, row, date(2027, 2, 1), profile), (True, None))
        required = frappe._dict(profile, rent_prenotification_required=1)
        self.assertFalse(get_prenotification_readiness(rent, row, date(2027, 2, 1), required)[0])

    def test_prenotification_attachment_must_use_erpverein_print_format(self):
        attachment = [
            {
                "print_format_attachment": 1,
                "print_format": "ERPverein Rechnung DIN 5008",
                "doctype": "Sales Invoice",
                "name": "SINV-TEST-0001",
            }
        ]

        self.assertTrue(has_prenotification_attachment(frappe.as_json(attachment), "SINV-TEST-0001"))
        self.assertFalse(has_prenotification_attachment(frappe.as_json(attachment), "SINV-TEST-0002"))
        self.assertFalse(
            has_prenotification_attachment(frappe.as_json([{**attachment[0], "print_format": "Standard"}]), "SINV-TEST-0001")
        )
        self.assertFalse(has_prenotification_attachment("not json", "SINV-TEST-0001"))

    def test_creditor_identifier_checksum(self):
        self.assertTrue(is_valid_creditor_identifier("DE98ZZZ09999999999"))
        self.assertTrue(is_valid_creditor_identifier(" de98 zzz0 9999 9999 99 "))
        self.assertFalse(is_valid_creditor_identifier("DE97ZZZ09999999999"))
        self.assertFalse(is_valid_creditor_identifier("DE98ZZZ"))
        self.assertFalse(is_valid_creditor_identifier(None))
