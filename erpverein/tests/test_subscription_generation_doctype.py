import frappe
from frappe.tests import IntegrationTestCase


class TestSubscriptionGenerationDoctype(IntegrationTestCase):
    def test_run_doctype_has_no_invalid_title_field(self):
        meta = frappe.get_meta("Beitragsabrechnung", cached=False)

        self.assertFalse(meta.title_field)

    def test_run_doctype_has_background_processing_status(self):
        meta = frappe.get_meta("Beitragsabrechnung", cached=False)

        self.assertIn("In Bearbeitung", meta.get_field("status").options.split("\n"))
        self.assertIn("Eingereiht", meta.get_field("status").options.split("\n"))
        self.assertIn("Fehlgeschlagen", meta.get_field("status").options.split("\n"))
        self.assertEqual(meta.get_field("preview_hash").fieldtype, "Data")
        self.assertEqual(meta.get_field("queued_at").fieldtype, "Datetime")

    def test_period_child_table_has_only_period_and_plan_fields(self):
        meta = frappe.get_meta("Beitragsabrechnung Periode", cached=False)

        self.assertIsNotNone(meta.get_field("from_date"))
        self.assertIsNotNone(meta.get_field("to_date"))
        self.assertEqual(meta.get_field("subscription_plan").label, "Beitragsplan")
        self.assertEqual(meta.get_field("subscription_plan").options, "Subscription Plan")
        self.assertIsNone(meta.get_field("annual_amount"))
        self.assertIsNone(meta.get_field("apply_to_annual_fee"))

    def test_preview_child_table_fields_exist(self):
        meta = frappe.get_meta("Beitragsabrechnung Vorschau", cached=False)

        self.assertEqual(meta.get_field("payer_mitglied").options, "Mitglied")
        self.assertEqual(meta.get_field("customer").label, "Kunde")
        self.assertEqual(meta.get_field("customer").options, "Customer")
        self.assertEqual(meta.get_field("plans_json").fieldtype, "Long Text")
        self.assertEqual(meta.get_field("estimated_invoice_count").fieldtype, "Int")
        self.assertEqual(meta.get_field("generation_payload").fieldtype, "Long Text")
