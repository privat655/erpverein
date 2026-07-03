import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.patches.v0_1 import p0001_sync_mitglied_custom_fields


class TestPatchP0001SyncMitgliedCustomFields(IntegrationTestCase):
    def test_patch_can_rerun_without_duplicate_custom_field(self):
        p0001_sync_mitglied_custom_fields.execute()
        p0001_sync_mitglied_custom_fields.execute()

        self.assertEqual(
            frappe.db.count("Custom Field", {"dt": "Customer", "fieldname": "mitglied"}),
            1,
        )

        field = frappe.get_meta("Customer", cached=False).get_field("mitglied")
        self.assertIsNotNone(field)
        self.assertEqual(field.fieldtype, "Link")
        self.assertEqual(field.options, "Mitglied")
