import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.patches.v0_1 import p0002_add_customer_sync_state_field


class TestPatchP0002AddCustomerSyncStateField(IntegrationTestCase):
    def test_patch_can_rerun_without_duplicate_custom_field(self):
        p0002_add_customer_sync_state_field.execute()
        p0002_add_customer_sync_state_field.execute()

        self.assertEqual(
            frappe.db.count("Custom Field", {"dt": "Customer", "fieldname": "verein_erp_sync_state"}),
            1,
        )

        field = frappe.get_meta("Customer", cached=False).get_field("verein_erp_sync_state")
        self.assertIsNotNone(field)
        self.assertEqual(field.fieldtype, "Long Text")
        self.assertEqual(field.hidden, 1)
