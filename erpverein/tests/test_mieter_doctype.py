import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpverein.custom_fields import CUSTOMER_MIETER_FIELDNAME, CUSTOMER_SYNC_STATE_FIELDNAME, sync_custom_fields
from erpverein.setup_data import sync_salutations
from erpverein.services.mieter_customer_sync_service import create_or_sync_customer_for_mieter


class TestMieterDoctype(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()
        sync_salutations()

    def test_mieter_generates_id_and_syncs_to_customer(self):
        customer = make_customer("Mieter Sync")
        mieter = frappe.get_doc(
            {
                "doctype": "Mieter",
                "anrede": " Herr ",
                "vorname": "  Max ",
                "nachname": " Mieter ",
                "mietbeginn": "2026-01-01",
                "email": " MAX@example.org ",
                "abrechnungsart": "Rechnung",
                "customer": customer.name,
            }
        ).insert(ignore_permissions=True)

        self.assertTrue(mieter.mieter_id.startswith("MIE-"))
        self.assertEqual(mieter.name, mieter.mieter_id)
        self.assertEqual(mieter.mieter_name, "Max Mieter")
        self.assertEqual(mieter.email, "max@example.org")
        self.assertEqual(frappe.db.get_value("Customer", customer.name, CUSTOMER_MIETER_FIELDNAME), mieter.name)

    def test_mieter_fields_match_rental_slice(self):
        meta = frappe.get_meta("Mieter", cached=False)

        self.assertEqual(meta.get_field("anrede").options, "\nHerr\nFrau")
        self.assertEqual(meta.get_field("abrechnungsart").options, "Rechnung\nLastschrift")
        self.assertIsNotNone(meta.get_field("fremd_id"))
        self.assertIsNotNone(meta.get_field("mietbeginn"))
        self.assertIsNotNone(meta.get_field("mietende"))
        self.assertIsNotNone(meta.get_field("anschrift"))
        self.assertIsNotNone(meta.get_field("stadt"))
        self.assertIsNone(meta.get_field("iban"))
        self.assertIsNone(meta.get_field("kontoinhaber"))
        self.assertIsNone(meta.get_field("bic"))
        self.assertIsNone(meta.get_field("bank_name"))
        self.assertIsNone(meta.get_field("bankdaten_section"))
        self.assertEqual(meta.get_field("sepa_mandat").mandatory_depends_on, "eval:doc.abrechnungsart == 'Lastschrift'")
        self.assertIsNone(meta.get_field("picture_url"))
        self.assertIsNone(meta.get_field("geburtsdatum"))
        self.assertIsNone(meta.get_field("eintrittsdatum"))
        self.assertIsNone(meta.get_field("austrittsdatum"))
        self.assertIsNone(meta.get_field("jahresbeitrag"))

    def test_mietende_must_not_precede_mietbeginn(self):
        mieter = frappe.get_doc(
            {
                "doctype": "Mieter",
                "vorname": "Datum",
                "nachname": "Test",
                "mietbeginn": "2026-02-01",
                "mietende": "2026-01-01",
                "abrechnungsart": "Rechnung",
            }
        )

        self.assertRaises(frappe.ValidationError, mieter.insert, ignore_permissions=True)

    def test_lastschrift_requires_active_mandate(self):
        mieter = frappe.get_doc(
            {
                "doctype": "Mieter",
                "vorname": "Lena",
                "nachname": "Lastschrift",
                "mietbeginn": "2026-01-01",
                "abrechnungsart": "Lastschrift",
            }
        )

        self.assertRaises(frappe.ValidationError, mieter.insert, ignore_permissions=True)

    def test_customer_button_sync_only_creates_customer_address_and_contact(self):
        mieter = make_mieter(
            anschrift="Musterweg 1",
            plz="10115",
            stadt="Berlin",
            email="tenant@example.org",
            telefon="01234",
        )

        result = create_or_sync_customer_for_mieter(mieter.name)
        mieter.reload()
        customer = frappe.get_doc("Customer", result["customer"])

        self.assertTrue(result["created"])
        self.assertEqual(mieter.customer, customer.name)
        self.assertEqual(customer.get(CUSTOMER_MIETER_FIELDNAME), mieter.name)
        self.assertTrue(customer.customer_primary_contact)
        self.assertTrue(customer.customer_primary_address)
        state = json.loads(customer.get(CUSTOMER_SYNC_STATE_FIELDNAME))
        self.assertEqual(
            state["sources"][f"Mieter:{mieter.name}"]["source"],
            {"doctype": "Mieter", "name": mieter.name},
        )
        self.assertFalse(mieter.sepa_mandat)
        self.assertFalse(
            frappe.db.get_value(
                "SEPA Mandat",
                {"bezugs_doctype": "Mieter", "bezugs_name": mieter.name},
                "name",
            )
        )
        self.assertFalse(frappe.db.get_value("Bank Account", {"party_type": "Customer", "party": customer.name}, "name"))

    def test_lastschrift_accepts_active_rent_mandate(self):
        customer = make_customer("Rent Mandate")
        mieter = make_mieter(customer=customer.name)
        bank_name = f"Mieter Test Bank {frappe.generate_hash(length=8)}"
        bank = frappe.get_doc({"doctype": "Bank", "bank_name": bank_name}).insert(ignore_permissions=True)
        mandate = frappe.get_doc(
            {
                "doctype": "SEPA Mandat",
                "mandatsreferenz": f"RM-{frappe.generate_hash(length=10)}",
                "mandatskategorie": "Miete",
                "status": "Aktiv",
                "bezugs_doctype": "Mieter",
                "bezugs_name": mieter.name,
                "mandatsdatum": "2026-01-01",
                "einzugsmodus": "Jaehrlich",
                "kontoinhaber": mieter.mieter_name,
                "iban": make_german_iban(),
                "bic": "COBADEFFXXX",
                "bank": bank.name,
            }
        ).insert(ignore_permissions=True)

        mieter.reload()
        mieter.abrechnungsart = "Lastschrift"
        mieter.save(ignore_permissions=True)
        self.assertEqual(mieter.sepa_mandat, mandate.name)

    def test_unchanged_customer_link_does_not_check_customer_write_permission(self):
        customer = make_customer("Unchanged Rent Customer")
        mieter = make_mieter(customer=customer.name)
        mieter.vorname = "Updated"

        with patch("erpverein.services.mieter_service.assert_customer_write_permission") as permission_check:
            mieter.save(ignore_permissions=True)

        permission_check.assert_not_called()


def make_customer(label: str):
    suffix = frappe.generate_hash(length=8)
    customer = frappe.get_doc({"doctype": "Customer", "customer_name": f"{label} {suffix}", "customer_type": "Individual"})

    customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
    if customer_group and customer.meta.has_field("customer_group"):
        customer.customer_group = customer_group

    territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
    if territory and customer.meta.has_field("territory"):
        customer.territory = territory

    return customer.insert(ignore_permissions=True)


def make_mieter(**overrides):
    data = {
        "doctype": "Mieter",
        "anrede": "Herr",
        "vorname": "Max",
        "nachname": f"Mieter {frappe.generate_hash(length=6)}",
        "mietbeginn": "2026-01-01",
        "abrechnungsart": "Rechnung",
    }
    data.update(overrides)
    return frappe.get_doc(data).insert(ignore_permissions=True)


def make_german_iban() -> str:
    account_no = int(frappe.generate_hash(length=8), 36) % 10_000_000_000
    bban = f"37040044{account_no:010d}"
    check_digits = 98 - (int(f"{bban}131400") % 97)
    return f"DE{check_digits:02d}{bban}"
