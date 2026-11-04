from datetime import date
from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from erpverein.services.sepa_collection_service import (
    add_target_business_days,
    collection_generation_key,
    comparable_value,
    earliest_collection_date,
    group_rows_into_runs,
    iban_hash,
    plan_collection_date,
    render_purpose,
    upload_window,
)
from erpverein.services.sepa_export_profile_service import validate_creditor_name, validate_purpose_template


def purpose_values(**overrides) -> dict:
    values = {
        "invoice": "ACC-SINV-2027-00001",
        "customer": "Jürgen Müller",
        "source_id": "MIT-2026-00001",
        "period_from": "01.01.2027",
        "period_to": "31.12.2027",
        "due_date": "31.03.2027",
        "collection_date": "31.03.2027",
    }
    values.update(overrides)
    return values


def preview_row(collection_date: str, invoice: str, billing_kind: str = "Mitgliedsbeitrag") -> dict:
    return {"collection_date": collection_date, "invoice": invoice, "billing_kind": billing_kind, "due_date": collection_date}


class TestSEPACollectionPlanning(UnitTestCase):
    def test_target_business_days_skip_weekends_and_holidays(self):
        self.assertEqual(add_target_business_days(date(2027, 1, 28), 1), date(2027, 1, 29))
        self.assertEqual(add_target_business_days(date(2027, 1, 29), 1), date(2027, 2, 1))
        self.assertEqual(add_target_business_days(date(2027, 3, 25), 1), date(2027, 3, 30))
        self.assertEqual(add_target_business_days(date(2026, 12, 23), 2), date(2026, 12, 28))
        self.assertEqual(add_target_business_days(date(2027, 2, 1), -1), date(2027, 1, 29))
        self.assertEqual(add_target_business_days(date(2027, 2, 1), 0), date(2027, 2, 1))

    def test_earliest_collection_date_keeps_the_upload_deadline_ahead(self):
        self.assertEqual(earliest_collection_date(1, date(2027, 1, 28)), date(2027, 2, 1))
        self.assertEqual(earliest_collection_date(1, date(2027, 1, 26)), date(2027, 1, 28))
        self.assertEqual(earliest_collection_date(2, date(2027, 1, 26)), date(2027, 1, 29))

    def test_collection_date_moves_to_next_business_day_but_never_before_earliest(self):
        self.assertEqual(plan_collection_date(date(2027, 1, 31), date(2027, 1, 20)), date(2027, 2, 1))
        self.assertEqual(plan_collection_date(date(2027, 3, 31), date(2027, 1, 20)), date(2027, 3, 31))
        self.assertEqual(plan_collection_date(date(2027, 1, 31), date(2027, 2, 3)), date(2027, 2, 3))

    def test_upload_window_of_first_collection(self):
        profile = frappe._dict(earliest_submission_business_days=15, lead_time_business_days=1)

        self.assertEqual(upload_window(date(2027, 2, 1), profile), (date(2027, 1, 11), date(2027, 1, 29)))

    def test_rows_are_grouped_by_collection_date_and_file_limit(self):
        rows = [
            preview_row("2027-03-31", "SINV-3"),
            preview_row("2027-02-01", "SINV-1", "Miete"),
            preview_row("2027-03-31", "SINV-2"),
            preview_row("2027-03-31", "SINV-4"),
        ]

        groups = group_rows_into_runs(rows, 2)

        self.assertEqual([[row["invoice"] for row in group] for group in groups], [["SINV-1"], ["SINV-2", "SINV-3"], ["SINV-4"]])
        self.assertEqual(len(group_rows_into_runs(rows, 1000)), 2)

    def test_purpose_uses_template_and_sepa_characters(self):
        self.assertEqual(render_purpose("Rechnung {invoice}", purpose_values()), "Rechnung ACC-SINV-2027-00001")
        self.assertEqual(
            render_purpose("Beitrag {period_from}-{period_to} {customer}", purpose_values()),
            "Beitrag 01.01.2027-31.12.2027 Juergen Mueller",
        )
        self.assertEqual(render_purpose("{unknown}", purpose_values()), "Rechnung ACC-SINV-2027-00001")
        self.assertEqual(render_purpose("€€€", purpose_values()), "Rechnung ACC-SINV-2027-00001")
        self.assertEqual(len(render_purpose("{customer} " * 40, purpose_values())), 140)

    def test_generation_key_and_iban_hash_are_stable(self):
        key = collection_generation_key("SINV-1", "ERPverein Rate 01", "2027-03-31")

        self.assertEqual(key, collection_generation_key("SINV-1", "ERPverein Rate 01", date(2027, 3, 31)))
        self.assertNotEqual(key, collection_generation_key("SINV-1", "ERPverein Rate 02", "2027-03-31"))
        self.assertNotEqual(key, collection_generation_key("SINV-1", "ERPverein Rate 01", "2027-09-30"))
        self.assertEqual(iban_hash("de02 1203 0000 0000 2020 51"), iban_hash("DE02120300000000202051"))
        self.assertNotIn("DE02120300000000202051", iban_hash("DE02120300000000202051"))
        self.assertIsNone(iban_hash(None))

    def test_values_are_compared_by_field_type(self):
        currency = frappe._dict(fieldtype="Currency")
        check = frappe._dict(fieldtype="Check")
        day = frappe._dict(fieldtype="Date")

        self.assertEqual(comparable_value(currency, 175), comparable_value(currency, 175.0))
        self.assertEqual(comparable_value(check, None), comparable_value(check, 0))
        self.assertEqual(comparable_value(day, "2027-03-31"), comparable_value(day, date(2027, 3, 31)))
        self.assertIsNone(comparable_value(frappe._dict(fieldtype="Data"), ""))

    def test_profile_rejects_unknown_placeholders_and_long_creditor_names(self):
        validate_purpose_template("Rechnung {invoice} {due_date}")
        for template in ("{iban}", "{0}", "{invoice"):
            with self.subTest(template=template), self.assertRaises(frappe.ValidationError):
                validate_purpose_template(template)

        valid = frappe._dict(creditor_name="  Turn-  und Sportverein Musterstadt e.V. ")
        validate_creditor_name(valid)
        self.assertEqual(valid.creditor_name, "Turn- und Sportverein Musterstadt e.V.")
        for name in ("", "€€", "Ä" * 36):
            with self.subTest(name=name), self.assertRaises(frappe.ValidationError):
                validate_creditor_name(frappe._dict(creditor_name=name))

    @patch("erpverein.api.sepa_collection.create_collection_runs", return_value={})
    @patch("erpverein.api.sepa_collection.build_collection_preview", return_value={})
    def test_api_accepts_the_empty_batch_booking_of_frappe_call(self, build_preview, create_runs):
        from erpverein.api.sepa_collection import create_runs as create_runs_api
        from erpverein.api.sepa_collection import get_preview

        # The dialog sends batch_booking null without "Einzelbuchung"; frappe.call turns it into "".
        # Whitelisted arguments are type-checked in requests and tests.
        get_preview(cutoff_date="2027-02-01", company="Verein", batch_booking="")
        create_runs_api(cutoff_date="2027-02-01", preview_hash="hash", company="Verein", batch_booking="")

        build_preview.assert_called_once_with("Verein", "2027-02-01", "")
        create_runs.assert_called_once_with("Verein", "2027-02-01", "hash", "", None)

    @patch("erpverein.services.sepa_collection_service.frappe.get_roles")
    def test_collection_records_need_an_accounts_role(self, get_roles):
        from erpverein.services.sepa_collection_service import collection_has_permission, collection_query_conditions

        get_roles.return_value = ["System Manager", "Sales User"]
        self.assertFalse(collection_has_permission(user="kassenwart@example.com"))
        self.assertEqual(collection_query_conditions("kassenwart@example.com"), "1=0")

        get_roles.return_value = ["System Manager", "Accounts User"]
        self.assertTrue(collection_has_permission(user="kassenwart@example.com"))
        self.assertEqual(collection_query_conditions("kassenwart@example.com"), "")
