import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.custom_fields import (
    BANK_ACCOUNT_MANAGED_FIELDNAME,
    BANK_ACCOUNT_SYNC_STATE_FIELDNAME,
    CUSTOMER_MITGLIED_FIELDNAME,
    CUSTOMER_SYNC_STATE_FIELDNAME,
    SUBSCRIPTION_GENERATION_RUN_FIELDNAME,
    SUBSCRIPTION_MANAGED_FIELDNAME,
    SUBSCRIPTION_PAYER_FIELDNAME,
    SUBSCRIPTION_SYNC_STATE_FIELDNAME,
    get_custom_fields,
    sync_custom_fields,
)


class TestCustomFields(IntegrationTestCase):
    def test_customer_mitglied_field_definition(self):
        customer_fields = get_custom_fields()["Customer"]
        field = next(field for field in customer_fields if field["fieldname"] == CUSTOMER_MITGLIED_FIELDNAME)

        self.assertEqual(field["fieldtype"], "Link")
        self.assertEqual(field["options"], "Mitglied")
        self.assertEqual(field["insert_after"], "customer_name")
        self.assertEqual(field["unique"], 1)

    def test_customer_sync_state_field_definition(self):
        customer_fields = get_custom_fields()["Customer"]
        field = next(field for field in customer_fields if field["fieldname"] == CUSTOMER_SYNC_STATE_FIELDNAME)

        self.assertEqual(field["fieldtype"], "Long Text")
        self.assertEqual(field["insert_after"], CUSTOMER_MITGLIED_FIELDNAME)
        self.assertEqual(field["hidden"], 1)
        self.assertEqual(field["read_only"], 1)

    def test_sync_custom_fields_creates_customer_mitglied_field(self):
        sync_custom_fields()
        meta = frappe.get_meta("Customer", cached=False)
        field = meta.get_field(CUSTOMER_MITGLIED_FIELDNAME)

        self.assertIsNotNone(field)
        self.assertEqual(field.fieldtype, "Link")
        self.assertEqual(field.options, "Mitglied")

        sync_state_field = meta.get_field(CUSTOMER_SYNC_STATE_FIELDNAME)
        self.assertIsNotNone(sync_state_field)
        self.assertEqual(sync_state_field.fieldtype, "Long Text")

    def test_bank_account_managed_field_definition(self):
        bank_account_fields = get_custom_fields()["Bank Account"]
        field = next(field for field in bank_account_fields if field["fieldname"] == BANK_ACCOUNT_MANAGED_FIELDNAME)

        self.assertEqual(field["fieldtype"], "Check")
        self.assertEqual(field["insert_after"], "party")
        self.assertEqual(field["no_copy"], 1)

    def test_bank_account_sync_state_field_definition(self):
        bank_account_fields = get_custom_fields()["Bank Account"]
        field = next(field for field in bank_account_fields if field["fieldname"] == BANK_ACCOUNT_SYNC_STATE_FIELDNAME)

        self.assertEqual(field["fieldtype"], "Long Text")
        self.assertEqual(field["insert_after"], BANK_ACCOUNT_MANAGED_FIELDNAME)
        self.assertEqual(field["hidden"], 1)
        self.assertEqual(field["read_only"], 1)

    def test_sync_custom_fields_creates_bank_account_fields(self):
        sync_custom_fields()
        meta = frappe.get_meta("Bank Account", cached=False)

        managed_field = meta.get_field(BANK_ACCOUNT_MANAGED_FIELDNAME)
        self.assertIsNotNone(managed_field)
        self.assertEqual(managed_field.fieldtype, "Check")

        sync_state_field = meta.get_field(BANK_ACCOUNT_SYNC_STATE_FIELDNAME)
        self.assertIsNotNone(sync_state_field)
        self.assertEqual(sync_state_field.fieldtype, "Long Text")

    def test_subscription_field_definitions(self):
        subscription_fields = get_custom_fields()["Subscription"]
        managed = next(field for field in subscription_fields if field["fieldname"] == SUBSCRIPTION_MANAGED_FIELDNAME)
        payer = next(field for field in subscription_fields if field["fieldname"] == SUBSCRIPTION_PAYER_FIELDNAME)
        run = next(field for field in subscription_fields if field["fieldname"] == SUBSCRIPTION_GENERATION_RUN_FIELDNAME)
        sync_state = next(field for field in subscription_fields if field["fieldname"] == SUBSCRIPTION_SYNC_STATE_FIELDNAME)

        self.assertEqual(managed["fieldtype"], "Check")
        self.assertEqual(payer["fieldtype"], "Link")
        self.assertEqual(payer["options"], "Mitglied")
        self.assertEqual(run["fieldtype"], "Link")
        self.assertEqual(run["options"], "Mitglied Subscription Lauf")
        self.assertEqual(sync_state["fieldtype"], "Long Text")
        self.assertEqual(sync_state["hidden"], 1)

    def test_sync_custom_fields_creates_subscription_fields(self):
        sync_custom_fields()
        meta = frappe.get_meta("Subscription", cached=False)

        self.assertEqual(meta.get_field(SUBSCRIPTION_MANAGED_FIELDNAME).fieldtype, "Check")
        self.assertEqual(meta.get_field(SUBSCRIPTION_PAYER_FIELDNAME).options, "Mitglied")
        self.assertEqual(meta.get_field(SUBSCRIPTION_GENERATION_RUN_FIELDNAME).options, "Mitglied Subscription Lauf")
        self.assertEqual(meta.get_field(SUBSCRIPTION_SYNC_STATE_FIELDNAME).fieldtype, "Long Text")
