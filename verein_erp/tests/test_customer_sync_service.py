import json

import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.custom_fields import CUSTOMER_SYNC_STATE_FIELDNAME, sync_custom_fields
from verein_erp.setup_data import sync_salutations
from verein_erp.services.customer_sync_service import create_or_sync_customer_for_mitglied


class TestCustomerSyncService(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()
        sync_salutations()

    def test_initial_sync_creates_customer_and_address(self):
        mitglied = make_mitglied(
            email="todor@example.org",
            telefon="01234",
            picture_url="https://example.org/todor.jpg",
            strasse="Musterstrasse 1",
            plz="10115",
            ort="Berlin",
            anrede="Herrn",
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
        self.assertTrue(customer.customer_primary_contact)
        self.assertTrue(customer.customer_primary_address)

        contact = frappe.get_doc("Contact", customer.customer_primary_contact)
        self.assertEqual(contact.first_name, "Todor")
        self.assertFalse(contact.middle_name)
        self.assertEqual(contact.last_name, "Sync")
        self.assertEqual(contact.salutation, "Mr")
        self.assertEqual(contact.email_id, "todor@example.org")
        self.assertEqual(contact.mobile_no, "01234")

        address = frappe.get_doc("Address", customer.customer_primary_address)
        self.assertEqual(address.address_line1, "Musterstrasse 1")
        self.assertEqual(address.pincode, "10115")
        self.assertEqual(address.city, "Berlin")
        self.assertEqual(address.country, "Germany")
        self.assertEqual(contact.address, address.name)
        if contact.meta.has_field("is_billing_contact"):
            self.assertEqual(contact.is_billing_contact, 1)

        state = json.loads(customer.get(CUSTOMER_SYNC_STATE_FIELDNAME))
        self.assertEqual(state["customer_fields"]["customer_name"], "Todor Sync")
        self.assertEqual(state["customer_fields"]["image"], "https://example.org/todor.jpg")
        self.assertNotIn("email_id", state["customer_fields"])
        self.assertNotIn("mobile_no", state["customer_fields"])
        self.assertNotIn("first_name", state["customer_fields"])
        self.assertNotIn("last_name", state["customer_fields"])
        self.assertEqual(state["address"]["name"], address.name)
        self.assertEqual(state["contact"]["name"], contact.name)
        self.assertEqual(state["contact"]["fields"]["first_name"], "Todor")
        self.assertFalse(state["contact"]["fields"]["middle_name"])
        self.assertEqual(state["contact"]["fields"]["last_name"], "Sync")
        self.assertEqual(state["contact"]["fields"]["salutation"], "Mr")
        self.assertEqual(state["contact"]["fields"]["email_id"], "todor@example.org")
        self.assertEqual(state["contact"]["fields"]["mobile_no"], "01234")
        self.assertEqual(state["contact"]["fields"]["address"], address.name)
        if contact.meta.has_field("is_billing_contact"):
            self.assertEqual(state["contact"]["fields"]["is_billing_contact"], "1")

    def test_running_sync_updates_auto_managed_customer_name_and_image(self):
        mitglied = make_mitglied(email="old@example.org", telefon="111", picture_url="https://example.org/old.jpg", anrede="Frau")
        result = create_or_sync_customer_for_mitglied(mitglied.name)

        mitglied.email = "new@example.org"
        mitglied.telefon = "222"
        mitglied.picture_url = "https://example.org/new.jpg"
        mitglied.nachname = "Updated"
        mitglied.save(ignore_permissions=True)

        customer = frappe.get_doc("Customer", result["customer"])
        self.assertEqual(customer.customer_name, "Todor Updated")
        self.assertEqual(customer.image, "https://example.org/new.jpg")
        contact = frappe.get_doc("Contact", customer.customer_primary_contact)
        self.assertEqual(contact.first_name, "Todor")
        self.assertFalse(contact.middle_name)
        self.assertEqual(contact.last_name, "Updated")
        self.assertEqual(contact.salutation, "Ms")
        self.assertEqual(contact.email_id, "new@example.org")
        self.assertEqual(contact.mobile_no, "222")

    def test_running_sync_updates_contact_address_when_auto_managed_address_is_created_later(self):
        mitglied = make_mitglied(email="person@example.org")
        result = create_or_sync_customer_for_mitglied(mitglied.name)
        customer = frappe.get_doc("Customer", result["customer"])
        contact = frappe.get_doc("Contact", customer.customer_primary_contact)
        self.assertFalse(contact.address)

        mitglied.strasse = "Neue Strasse 1"
        mitglied.plz = "10115"
        mitglied.ort = "Berlin"
        mitglied.save(ignore_permissions=True)

        customer.reload()
        contact.reload()
        self.assertEqual(contact.address, customer.customer_primary_address)

    def test_running_sync_keeps_manually_changed_customer_field(self):
        mitglied = make_mitglied(picture_url="https://example.org/old.jpg")
        result = create_or_sync_customer_for_mitglied(mitglied.name)
        customer = frappe.get_doc("Customer", result["customer"])
        customer.image = "https://example.org/manual.jpg"
        customer.save(ignore_permissions=True)

        mitglied.picture_url = "https://example.org/new.jpg"
        mitglied.save(ignore_permissions=True)

        customer.reload()
        self.assertEqual(customer.image, "https://example.org/manual.jpg")

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

    def test_running_sync_keeps_manually_changed_contact(self):
        mitglied = make_mitglied(email="old@example.org", telefon="111")
        result = create_or_sync_customer_for_mitglied(mitglied.name)
        customer = frappe.get_doc("Customer", result["customer"])
        contact = frappe.get_doc("Contact", customer.customer_primary_contact)
        contact.first_name = "Manual"
        contact.email_ids[0].email_id = "manual@example.org"
        contact.save(ignore_permissions=True)

        mitglied.vorname = "Updated"
        mitglied.email = "new@example.org"
        mitglied.save(ignore_permissions=True)

        contact.reload()
        self.assertEqual(contact.first_name, "Manual")
        self.assertEqual(contact.email_id, "manual@example.org")

    def test_initial_sync_creates_customer_without_address_when_address_is_incomplete(self):
        mitglied = make_mitglied(strasse="", plz="", ort="")

        result = create_or_sync_customer_for_mitglied(mitglied.name)
        customer = frappe.get_doc("Customer", result["customer"])

        self.assertTrue(result["created"])
        self.assertFalse(customer.customer_primary_address)

    def test_initial_sync_does_not_create_contact_without_email_or_phone(self):
        mitglied = make_mitglied()

        result = create_or_sync_customer_for_mitglied(mitglied.name)
        customer = frappe.get_doc("Customer", result["customer"])

        self.assertTrue(result["created"])
        self.assertFalse(customer.customer_primary_contact)


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
