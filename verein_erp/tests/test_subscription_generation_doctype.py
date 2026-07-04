import frappe
from frappe.tests import IntegrationTestCase


class TestSubscriptionGenerationDoctype(IntegrationTestCase):
    def test_run_doctype_fields_exist(self):
        meta = frappe.get_meta("Mitglied Subscription Lauf", cached=False)

        self.assertEqual(meta.get_field("scope").fieldtype, "Select")
        self.assertEqual(meta.get_field("company").options, "Company")
        self.assertEqual(meta.get_field("cost_center").options, "Cost Center")
        self.assertEqual(meta.get_field("periods").options, "Mitglied Subscription Lauf Periode")
        self.assertEqual(meta.get_field("preview_rows").options, "Mitglied Subscription Lauf Vorschau")

    def test_period_child_table_fields_exist(self):
        meta = frappe.get_meta("Mitglied Subscription Lauf Periode", cached=False)

        self.assertEqual(meta.get_field("from_date").fieldtype, "Date")
        self.assertEqual(meta.get_field("subscription_plan").options, "Subscription Plan")
        self.assertEqual(meta.get_field("apply_to_annual_fee").fieldtype, "Currency")

    def test_preview_child_table_fields_exist(self):
        meta = frappe.get_meta("Mitglied Subscription Lauf Vorschau", cached=False)

        self.assertEqual(meta.get_field("payer_mitglied").options, "Mitglied")
        self.assertEqual(meta.get_field("customer").options, "Customer")
        self.assertEqual(meta.get_field("subscription_plan").options, "Subscription Plan")
        self.assertEqual(meta.get_field("plans_json").fieldtype, "Long Text")
        self.assertEqual(meta.get_field("estimated_invoice_count").fieldtype, "Int")
