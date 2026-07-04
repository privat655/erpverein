import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.custom_fields import BANK_ACCOUNT_MANAGED_FIELDNAME, BANK_ACCOUNT_SYNC_STATE_FIELDNAME
from verein_erp.patches.v0_1 import p0004_sync_sepa_bank_account_fields


class TestPatchP0004SyncSEPABankAccountFields(IntegrationTestCase):
    def test_patch_can_rerun_without_duplicate_custom_fields(self):
        p0004_sync_sepa_bank_account_fields.execute()
        p0004_sync_sepa_bank_account_fields.execute()

        self.assertEqual(
            frappe.db.count("Custom Field", {"dt": "Bank Account", "fieldname": BANK_ACCOUNT_MANAGED_FIELDNAME}),
            1,
        )
        self.assertEqual(
            frappe.db.count("Custom Field", {"dt": "Bank Account", "fieldname": BANK_ACCOUNT_SYNC_STATE_FIELDNAME}),
            1,
        )

        meta = frappe.get_meta("Bank Account", cached=False)
        self.assertEqual(meta.get_field(BANK_ACCOUNT_MANAGED_FIELDNAME).fieldtype, "Check")
        self.assertEqual(meta.get_field(BANK_ACCOUNT_SYNC_STATE_FIELDNAME).fieldtype, "Long Text")
