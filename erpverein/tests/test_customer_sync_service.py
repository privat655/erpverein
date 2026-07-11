import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpverein.custom_fields import CUSTOMER_MITGLIED_FIELDNAME, CUSTOMER_SYNC_STATE_FIELDNAME, sync_custom_fields
from erpverein.setup_data import sync_salutations
from erpverein.services.customer_sync_service import create_or_sync_customer_for_mitglied, sync_unlinked_mitglieder


class TestCustomerSyncService(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()
        sync_salutations()

    def test_initial_sync_creates_customer_and_address(self):
        mitglied = make_mitglied(
            email="member@example.org",
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
        self.assertEqual(customer.get(CUSTOMER_MITGLIED_FIELDNAME), mitglied.name)
        self.assertEqual(customer.customer_name, "Sample Sync")
        self.assertEqual(customer.customer_type, "Individual")
        self.assertEqual(customer.customer_group, "Individual")
        self.assertEqual(customer.territory, "Germany")
        self.assertEqual(customer.gender, "Male")
        self.assertEqual(customer.default_currency, "EUR")
        self.assertEqual(customer.image, "https://example.org/todor.jpg")
        self.assertTrue(customer.customer_primary_contact)
        self.assertTrue(customer.customer_primary_address)

        contact = frappe.get_doc("Contact", customer.customer_primary_contact)
        self.assertEqual(contact.first_name, "Sample")
        self.assertFalse(contact.middle_name)
        self.assertEqual(contact.last_name, "Sync")
        self.assertEqual(contact.salutation, "Mr")
        self.assertEqual(contact.email_id, "member@example.org")
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
        self.assertEqual(state["schema_version"], 2)
        source_state = state["sources"][f"Mitglied:{mitglied.name}"]
        self.assertEqual(source_state["source"], {"doctype": "Mitglied", "name": mitglied.name})
        self.assertTrue(source_state["customer"]["managed"])
        customer_fields = source_state["customer"]["fields"]
        self.assertEqual(customer_fields["customer_name"], "Sample Sync")
        self.assertEqual(customer_fields["image"], "https://example.org/todor.jpg")
        self.assertNotIn("email_id", customer_fields)
        self.assertEqual(source_state["address"]["name"], address.name)
        self.assertTrue(source_state["address"]["managed"])
        self.assertEqual(source_state["contact"]["name"], contact.name)
        self.assertTrue(source_state["contact"]["managed"])
        contact_fields = source_state["contact"]["fields"]
        self.assertEqual(contact_fields["first_name"], "Sample")
        self.assertFalse(contact_fields["middle_name"])
        self.assertEqual(contact_fields["last_name"], "Sync")
        self.assertEqual(contact_fields["salutation"], "Mr")
        self.assertEqual(contact_fields["email_id"], "member@example.org")
        self.assertEqual(contact_fields["mobile_no"], "01234")
        self.assertEqual(contact_fields["address"], address.name)
        if contact.meta.has_field("is_billing_contact"):
            self.assertEqual(contact_fields["is_billing_contact"], "1")

    def test_running_sync_updates_auto_managed_customer_name_and_image(self):
        mitglied = make_mitglied(email="old@example.org", telefon="111", picture_url="https://example.org/old.jpg", anrede="Frau")
        result = create_or_sync_customer_for_mitglied(mitglied.name)
        mitglied.reload()

        mitglied.email = "new@example.org"
        mitglied.telefon = "222"
        mitglied.picture_url = "https://example.org/new.jpg"
        mitglied.nachname = "Updated"
        mitglied.save(ignore_permissions=True)
        create_or_sync_customer_for_mitglied(mitglied.name)

        customer = frappe.get_doc("Customer", result["customer"])
        self.assertEqual(customer.customer_name, "Sample Updated")
        self.assertEqual(customer.image, "https://example.org/new.jpg")
        contact = frappe.get_doc("Contact", customer.customer_primary_contact)
        self.assertEqual(contact.first_name, "Sample")
        self.assertFalse(contact.middle_name)
        self.assertEqual(contact.last_name, "Updated")
        self.assertEqual(contact.salutation, "Ms")
        self.assertEqual(contact.email_id, "new@example.org")
        self.assertEqual(contact.mobile_no, "222")

    def test_running_sync_updates_contact_address_when_auto_managed_address_is_created_later(self):
        mitglied = make_mitglied(email="person@example.org")
        result = create_or_sync_customer_for_mitglied(mitglied.name)
        mitglied.reload()
        customer = frappe.get_doc("Customer", result["customer"])
        contact = frappe.get_doc("Contact", customer.customer_primary_contact)
        self.assertFalse(contact.address)

        mitglied.strasse = "Neue Strasse 1"
        mitglied.plz = "10115"
        mitglied.ort = "Berlin"
        mitglied.save(ignore_permissions=True)
        create_or_sync_customer_for_mitglied(mitglied.name)

        customer.reload()
        contact.reload()
        self.assertEqual(contact.address, customer.customer_primary_address)

    def test_running_sync_keeps_manually_changed_customer_field(self):
        mitglied = make_mitglied(picture_url="https://example.org/old.jpg")
        result = create_or_sync_customer_for_mitglied(mitglied.name)
        mitglied.reload()
        customer = frappe.get_doc("Customer", result["customer"])
        customer.image = "https://example.org/manual.jpg"
        customer.save(ignore_permissions=True)

        mitglied.picture_url = "https://example.org/new.jpg"
        mitglied.save(ignore_permissions=True)
        create_or_sync_customer_for_mitglied(mitglied.name)

        customer.reload()
        self.assertEqual(customer.image, "https://example.org/manual.jpg")

    def test_running_sync_keeps_manually_changed_address(self):
        mitglied = make_mitglied(strasse="Musterstrasse 1", plz="10115", ort="Berlin")
        result = create_or_sync_customer_for_mitglied(mitglied.name)
        mitglied.reload()
        customer = frappe.get_doc("Customer", result["customer"])
        address = frappe.get_doc("Address", customer.customer_primary_address)
        address.address_line1 = "Manuelle Strasse 9"
        address.save(ignore_permissions=True)

        mitglied.strasse = "Neue Strasse 2"
        mitglied.save(ignore_permissions=True)
        create_or_sync_customer_for_mitglied(mitglied.name)

        address.reload()
        self.assertEqual(address.address_line1, "Manuelle Strasse 9")

    def test_running_sync_keeps_manually_changed_contact(self):
        mitglied = make_mitglied(email="old@example.org", telefon="111")
        result = create_or_sync_customer_for_mitglied(mitglied.name)
        mitglied.reload()
        customer = frappe.get_doc("Customer", result["customer"])
        contact = frappe.get_doc("Contact", customer.customer_primary_contact)
        contact.first_name = "Manual"
        contact.email_ids[0].email_id = "manual@example.org"
        contact.save(ignore_permissions=True)

        mitglied.vorname = "Updated"
        mitglied.email = "new@example.org"
        mitglied.save(ignore_permissions=True)
        create_or_sync_customer_for_mitglied(mitglied.name)

        contact.reload()
        self.assertEqual(contact.first_name, "Manual")
        self.assertEqual(contact.email_id, "manual@example.org")

    def test_running_sync_preserves_secondary_contact_rows(self):
        mitglied = make_mitglied(email="primary@example.org", telefon="111")
        result = create_or_sync_customer_for_mitglied(mitglied.name)
        mitglied.reload()
        customer = frappe.get_doc("Customer", result["customer"])
        contact = frappe.get_doc("Contact", customer.customer_primary_contact)
        contact.add_email("secondary@example.org", is_primary=False)
        contact.add_phone("222", is_primary_mobile_no=False)
        contact.save(ignore_permissions=True)

        mitglied.email = "updated@example.org"
        mitglied.telefon = "333"
        mitglied.save(ignore_permissions=True)
        create_or_sync_customer_for_mitglied(mitglied.name)
        contact.reload()

        self.assertEqual(contact.email_id, "updated@example.org")
        self.assertEqual(contact.mobile_no, "333")
        self.assertIn("secondary@example.org", [row.email_id for row in contact.email_ids])
        self.assertIn("222", [row.phone for row in contact.phone_nos])

    def test_clearing_source_data_deletes_unchanged_owned_address_and_contact(self):
        mitglied = make_mitglied(
            email="remove@example.org",
            telefon="111",
            strasse="Owned Street 1",
            ort="Berlin",
        )
        result = create_or_sync_customer_for_mitglied(mitglied.name)
        mitglied.reload()
        customer = frappe.get_doc("Customer", result["customer"])
        address_name = customer.customer_primary_address
        contact_name = customer.customer_primary_contact

        mitglied.email = None
        mitglied.telefon = None
        mitglied.strasse = None
        mitglied.plz = None
        mitglied.ort = None
        mitglied.save(ignore_permissions=True)
        create_or_sync_customer_for_mitglied(mitglied.name)
        customer.reload()

        self.assertFalse(customer.customer_primary_address)
        self.assertFalse(customer.customer_primary_contact)
        self.assertFalse(frappe.db.exists("Address", address_name))
        self.assertFalse(frappe.db.exists("Contact", contact_name))

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

    def test_sync_never_adopts_or_replaces_manual_primary_records(self):
        customer = make_customer("Manual Customer")
        manual_address = frappe.get_doc(
            {
                "doctype": "Address",
                "address_title": "Manual",
                "address_type": "Billing",
                "address_line1": "Manual Street 1",
                "city": "Berlin",
                "country": "Germany",
                "links": [{"link_doctype": "Customer", "link_name": customer.name}],
            }
        ).insert(ignore_permissions=True)
        manual_contact = frappe.get_doc(
            {
                "doctype": "Contact",
                "first_name": "Manual",
                "is_primary_contact": 1,
                "links": [{"link_doctype": "Customer", "link_name": customer.name}],
            }
        ).insert(ignore_permissions=True)
        customer.reload()
        customer.customer_primary_address = manual_address.name
        customer.customer_primary_contact = manual_contact.name
        customer.save(ignore_permissions=True)
        original_customer_name = customer.customer_name

        mitglied = make_mitglied(
            customer=customer.name,
            strasse="Managed Street 2",
            ort="Berlin",
            email="managed@example.org",
        )
        create_or_sync_customer_for_mitglied(mitglied.name)
        customer.reload()

        self.assertEqual(customer.customer_primary_address, manual_address.name)
        self.assertEqual(customer.customer_primary_contact, manual_contact.name)
        self.assertEqual(customer.customer_name, original_customer_name)
        state = json.loads(customer.get(CUSTOMER_SYNC_STATE_FIELDNAME))
        source_state = state["sources"][f"Mitglied:{mitglied.name}"]
        self.assertNotEqual(source_state["address"]["name"], manual_address.name)
        self.assertNotEqual(source_state["contact"]["name"], manual_contact.name)

    def test_non_administrator_with_required_cross_doctype_roles_can_run_sync(self):
        user = make_erpverein_manager_user()
        mitglied = make_mitglied(email="permission@example.org")
        try:
            frappe.set_user(user.name)
            result = create_or_sync_customer_for_mitglied(mitglied.name)
        finally:
            frappe.set_user("Administrator")

        self.assertTrue(result["created"])

    def test_bulk_sync_rolls_back_item_and_sanitizes_error(self):
        full_iban = "DE89370400440532013000"
        with (
            patch("erpverein.services.customer_sync_service.get_unlinked_mitglied_names", return_value=["MIT-ERROR"]),
            patch(
                "erpverein.services.customer_sync_service.create_or_sync_customer_for_mitglied",
                side_effect=frappe.ValidationError(f"IBAN {full_iban}"),
            ),
            patch.object(frappe.db, "rollback", wraps=frappe.db.rollback) as rollback,
            patch.object(frappe.db, "release_savepoint", wraps=frappe.db.release_savepoint) as release,
        ):
            result = sync_unlinked_mitglieder()

        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["errors"]), 1)
        self.assertNotIn(full_iban, result["errors"][0]["error"])
        rollback.assert_called_once()
        release.assert_called_once()


def make_customer(label: str):
    customer = frappe.get_doc({"doctype": "Customer", "customer_name": label, "customer_type": "Individual"})
    customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
    if customer_group:
        customer.customer_group = customer_group
    territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
    if territory:
        customer.territory = territory
    return customer.insert(ignore_permissions=True)


def make_erpverein_manager_user():
    email = f"erpverein-sync-{frappe.generate_hash(length=8)}@example.org"
    user = frappe.get_doc(
        {
            "doctype": "User",
            "email": email,
            "first_name": "ERPverein",
            "last_name": "Sync",
            "enabled": 1,
            "send_welcome_email": 0,
        }
    ).insert(ignore_permissions=True)
    user.add_roles("System Manager", "Sales User", "Sales Master Manager", "Accounts User")
    return user


def make_mitglied(**overrides):
    data = {
        "doctype": "Mitglied",
        "vorname": "Sample",
        "nachname": "Sync",
        "eintrittsdatum": "2026-01-01",
        "abrechnungsart": "Rechnung",
    }
    data.update(overrides)
    return frappe.get_doc(data).insert(ignore_permissions=True)
