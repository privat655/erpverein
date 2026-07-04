import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.patches.v0_1 import p0003_sync_salutations


class TestPatchP0003SyncSalutations(IntegrationTestCase):
    def test_patch_can_rerun_without_duplicate_salutations(self):
        p0003_sync_salutations.execute()
        p0003_sync_salutations.execute()

        self.assertTrue(frappe.db.exists("Salutation", "Mr"))
        self.assertTrue(frappe.db.exists("Salutation", "Ms"))
        self.assertEqual(frappe.db.count("Salutation", {"name": "Mr"}), 1)
        self.assertEqual(frappe.db.count("Salutation", {"name": "Ms"}), 1)
