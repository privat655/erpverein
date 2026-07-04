import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.custom_fields import (
    SUBSCRIPTION_GENERATION_RUN_FIELDNAME,
    SUBSCRIPTION_MANAGED_FIELDNAME,
    SUBSCRIPTION_PAYER_FIELDNAME,
    SUBSCRIPTION_SYNC_STATE_FIELDNAME,
)
from verein_erp.patches.v0_1 import p0005_sync_subscription_fields


class TestPatchP0005SyncSubscriptionFields(IntegrationTestCase):
    def test_patch_can_rerun_without_duplicate_custom_fields(self):
        p0005_sync_subscription_fields.execute()
        p0005_sync_subscription_fields.execute()

        for fieldname in [
            SUBSCRIPTION_MANAGED_FIELDNAME,
            SUBSCRIPTION_PAYER_FIELDNAME,
            SUBSCRIPTION_GENERATION_RUN_FIELDNAME,
            SUBSCRIPTION_SYNC_STATE_FIELDNAME,
        ]:
            self.assertEqual(frappe.db.count("Custom Field", {"dt": "Subscription", "fieldname": fieldname}), 1)

        meta = frappe.get_meta("Subscription", cached=False)
        self.assertEqual(meta.get_field(SUBSCRIPTION_MANAGED_FIELDNAME).fieldtype, "Check")
        self.assertEqual(meta.get_field(SUBSCRIPTION_PAYER_FIELDNAME).options, "Mitglied")
        self.assertEqual(meta.get_field(SUBSCRIPTION_GENERATION_RUN_FIELDNAME).options, "Mitglied Subscription Lauf")
        self.assertEqual(meta.get_field(SUBSCRIPTION_SYNC_STATE_FIELDNAME).fieldtype, "Long Text")
