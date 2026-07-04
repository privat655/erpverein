import json

import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.custom_fields import CUSTOMER_SYNC_STATE_FIELDNAME, sync_custom_fields
from verein_erp.services.customer_sync_service import create_or_sync_customer_for_mitglied


class TestCustomerSyncService(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()

    def test_initial_sync_creates_customer_and_address(self):
        mitglied = make_mitglied(
            email="todor@example.org",
            telefon="01234",
            picture_url="https://example.org/todor.jpg",
            strasse="Musterstrasse 1",
            plz="10115",
            ort="Berlin",
        )

        result = create_or_sync_customer_for_mitglied(mitglied.name)
        customer = frappe.get_doc("Customer", result["customer"])
        mitglied.reload()

        self.assertTrue(result["created"])
        self.assertEqual(mitglied.customer, customer.name)
        self.assertEqual(customer.mitglied, mitglied.name)
        self.assertEqual(customer.customer_name, "Todor Sync")
        self.assertEqual(customer.customer_type, "Individual")
        self.assertEqual(customer.customer_group, "Individual")
        self.assertEqual(customer.territory, "Germany")
        self.assertEqual(customer.gender, "Male")
        self.assertEqual(customer.default_currency, "EUR")
        self.assertEqual(customer.image, "https://example.org/todor.jpg")
        self.assertEqual(customer.email_id, "todor@example.org")
        self.assertEqual(customer.mobile_no, "01234")
        self.assertEqual(customer.first_name, "Todor")
        self.assertEqual(customer.last_name, "Sync")
        self.assertTrue(customer.customer_primary_address)

        address = frappe.get_doc("Address", customer.customer_primary_address)
        self.assertEqual(address.address_line1, "Musterstrasse 1")
        self.assertEqual(address.pincode, "10115")
        self.assertEqual(address.city, "Berlin")
        self.assertEqual(address.country, "Germany")

        state = json.loads(customer.get(CUSTOMER_SYNC_STATE_FIELDNAME))
        self.assertEqual(state["customer_fields"]["email_id"], "todor@example.org")
        self.assertEqual(state["address"]["name"], address.name)

    def test_running_sync_updates_auto_managed_customer_fields(self):
        mitglied = make_mitglied(email="old@example.org", telefon="111", picture_url="https://example.org/old.jpg")
        result = create_or_sync_customer_for_mitglied(mitglied.name)

        mitglied.email = "new@example.org"
        mitglied.telefon = "222"
        mitglied.picture_url = "https://example.org/new.jpg"
        mitglied.nachname = "Updated"
        mitglied.save(ignore_permissions=True)

        customer = frappe.get_doc("Customer", result["customer"])
        self.assertEqual(customer.customer_name, "Todor Updated")
        self.assertEqual(customer.email_id, "new@example.org")
        self.assertEqual(customer.mobile_no, "222")
        self.assertEqual(customer.image, "https://example.org/new.jpg")
        self.assertEqual(customer.last_name, "Updated")

    def test_running_sync_keeps_manually_changed_customer_field(self):
        mitglied = make_mitglied(email="old@example.org")
        result = create_or_sync_customer_for_mitglied(mitglied.name)
        customer = frappe.get_doc("Customer", result["customer"])
        customer.email_id = "manual@example.org"
        customer.save(ignore_permissions=True)

        mitglied.email = "new@example.org"
        mitglied.save(ignore_permissions=True)

        customer.reload()
        self.assertEqual(customer.email_id, "manual@example.org")

    def test_running_sync_keeps_manually_changed_address(self):
        mitglied = make_mitglied(strasse="Musterstrasse 1", plz="10115", ort="Berlin")
        result = create_or_sync_customer_for_mitglied(mitglied.name)
        customer = frappe.get_doc("Customer", result["customer"])
        address = frappe.get_doc("Address", customer.customer_primary_address)
        address.address_line1 = "Manuelle Strasse 9"
        address.save(ignore_permissions=True)

        mitglied.strasse = "Neue Strasse 2"
        mitglied.save(ignore_permissions=True)

        address.reload()
        self.assertEqual(address.address_line1, "Manuelle Strasse 9")

    def test_initial_sync_creates_customer_without_address_when_address_is_incomplete(self):
        mitglied = make_mitglied(strasse="", plz="", ort="")

        result = create_or_sync_customer_for_mitglied(mitglied.name)
        customer = frappe.get_doc("Customer", result["customer"])

        self.assertTrue(result["created"])
        self.assertFalse(customer.customer_primary_address)


def make_mitglied(**overrides):
    data = {
        "doctype": "Mitglied",
        "vorname": "Todor",
        "nachname": "Sync",
        "eintrittsdatum": "2026-01-01",
        "abrechnungsart": "Rechnung",
    }
    data.update(overrides)
    return frappe.get_doc(data).insert(ignore_permissions=True)
