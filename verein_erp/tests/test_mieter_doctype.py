import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.custom_fields import sync_custom_fields
from verein_erp.setup_data import sync_salutations
from verein_erp.services.mieter_customer_sync_service import create_or_sync_customer_for_mieter


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
        self.assertEqual(frappe.db.get_value("Customer", customer.name, "mieter"), mieter.name)

    def test_mieter_fields_match_rental_slice(self):
        meta = frappe.get_meta("Mieter", cached=False)

        self.assertEqual(meta.get_field("anrede").options, "\nHerr\nFrau")
        self.assertEqual(meta.get_field("abrechnungsart").options, "Rechnung\nLastschrift")
        self.assertIsNotNone(meta.get_field("fremd_id"))
        self.assertIsNotNone(meta.get_field("mietbeginn"))
        self.assertIsNotNone(meta.get_field("mietende"))
        self.assertIsNotNone(meta.get_field("anschrift"))
        self.assertIsNotNone(meta.get_field("stadt"))
        self.assertIsNotNone(meta.get_field("iban"))
        self.assertIsNotNone(meta.get_field("kontoinhaber"))
        self.assertIsNotNone(meta.get_field("bic"))
        self.assertIsNotNone(meta.get_field("bank_name"))
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

    def test_lastschrift_requires_bank_data(self):
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

    def test_customer_button_sync_creates_customer_address_bank_and_rent_mandate(self):
        mieter = make_mieter(
            abrechnungsart="Lastschrift",
            anschrift="Musterweg 1",
            plz="10115",
            stadt="Berlin",
            email="tenant@example.org",
            telefon="01234",
            kontoinhaber="Max Mieter",
            iban=make_german_iban(),
            bic="COBADEFFXXX",
            bank_name=f"Mieter Test Bank {frappe.generate_hash(length=8)}",
        )

        result = create_or_sync_customer_for_mieter(mieter.name)
        mieter.reload()
        customer = frappe.get_doc("Customer", result["customer"])

        self.assertTrue(result["created"])
        self.assertEqual(mieter.customer, customer.name)
        self.assertEqual(customer.mieter, mieter.name)
        self.assertTrue(customer.customer_primary_contact)
        self.assertTrue(customer.customer_primary_address)
        self.assertTrue(mieter.sepa_mandat)

        mandate = frappe.get_doc("SEPA Mandat", mieter.sepa_mandat)
        self.assertEqual(mandate.mandatskategorie, "Miete")
        self.assertEqual(mandate.bezugs_doctype, "Mieter")
        self.assertEqual(mandate.bezugs_name, mieter.name)
        self.assertEqual(mandate.customer, customer.name)
        self.assertTrue(mandate.bank_account)

        bank_account = frappe.get_doc("Bank Account", mandate.bank_account)
        self.assertEqual(bank_account.party_type, "Customer")
        self.assertEqual(bank_account.party, customer.name)
        self.assertEqual(bank_account.iban, mieter.iban)


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
