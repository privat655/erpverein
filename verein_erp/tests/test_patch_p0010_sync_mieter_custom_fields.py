import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.custom_fields import CUSTOMER_MIETER_FIELDNAME
from verein_erp.patches.v0_1 import p0010_sync_mieter_custom_fields


class TestPatchP0010SyncMieterCustomFields(IntegrationTestCase):
    def test_patch_can_rerun_without_duplicate_custom_fields(self):
        p0010_sync_mieter_custom_fields.execute()
        p0010_sync_mieter_custom_fields.execute()

        self.assertEqual(
            frappe.db.count("Custom Field", {"dt": "Customer", "fieldname": CUSTOMER_MIETER_FIELDNAME}),
            1,
        )

        meta = frappe.get_meta("Customer", cached=False)
        self.assertEqual(meta.get_field(CUSTOMER_MIETER_FIELDNAME).fieldtype, "Link")
        self.assertEqual(meta.get_field(CUSTOMER_MIETER_FIELDNAME).options, "Mieter")
