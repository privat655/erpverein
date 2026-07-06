import frappe
from frappe.tests import IntegrationTestCase


class TestRentalSubscriptionGenerationDoctype(IntegrationTestCase):
    def test_run_doctype_has_background_processing_status(self):
        meta = frappe.get_meta("Mietabrechnung", cached=False)

        self.assertIn("In Bearbeitung", meta.get_field("status").options.split("\n"))

    def test_forecast_child_table_has_period_and_plan_fields(self):
        meta = frappe.get_meta("Mietabrechnung Prognose", cached=False)

        self.assertIsNotNone(meta.get_field("from_date"))
        self.assertIsNotNone(meta.get_field("to_date"))
        self.assertEqual(meta.get_field("subscription_plan").label, "Mietplan")
        self.assertEqual(meta.get_field("subscription_plan").options, "Subscription Plan")

    def test_preview_child_table_fields_exist(self):
        meta = frappe.get_meta("Mietabrechnung Vorschau", cached=False)

        self.assertEqual(meta.get_field("mieter").options, "Mieter")
        self.assertEqual(meta.get_field("customer").label, "Kunde")
        self.assertEqual(meta.get_field("customer").options, "Customer")
        self.assertEqual(meta.get_field("mietbeginn").fieldtype, "Date")
        self.assertEqual(meta.get_field("mietende").fieldtype, "Date")
        self.assertEqual(meta.get_field("plans_json").fieldtype, "Long Text")
        self.assertEqual(meta.get_field("estimated_invoice_count").fieldtype, "Int")
