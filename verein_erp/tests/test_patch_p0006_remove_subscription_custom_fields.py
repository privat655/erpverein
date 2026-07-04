import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.tests import IntegrationTestCase

from verein_erp.patches.v0_1 import p0006_remove_subscription_custom_fields


class TestPatchP0006RemoveSubscriptionCustomFields(IntegrationTestCase):
    def test_patch_removes_old_subscription_custom_fields(self):
        create_custom_fields(
            {
                "Subscription": [
                    {"fieldname": "verein_erp_managed", "label": "Verein ERP Managed", "fieldtype": "Check"},
                    {"fieldname": "verein_erp_payer_mitglied", "label": "Old Payer Field", "fieldtype": "Data"},
                    {"fieldname": "verein_erp_generation_run", "label": "Verein ERP Subscription Lauf", "fieldtype": "Data"},
                    {"fieldname": "verein_erp_sync_state", "label": "Verein ERP Sync State", "fieldtype": "Long Text"},
                ]
            },
            update=True,
        )

        p0006_remove_subscription_custom_fields.execute()
        p0006_remove_subscription_custom_fields.execute()

        for fieldname in [
            "verein_erp_managed",
            "verein_erp_payer_mitglied",
            "verein_erp_generation_run",
            "verein_erp_sync_state",
        ]:
            self.assertFalse(frappe.db.exists("Custom Field", {"dt": "Subscription", "fieldname": fieldname}))
