from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpverein.custom_fields import CUSTOMER_MITGLIED_FIELDNAME, sync_custom_fields


class TestMitgliedDoctype(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()

    def test_mitglied_generates_id_and_syncs_to_customer(self):
        customer = make_customer("Mitglied Sync")
        mitglied = frappe.get_doc(
            {
                "doctype": "Mitglied",
                "vorname": "  Erika ",
                "nachname": " Musterfrau ",
                "eintrittsdatum": "2026-01-01",
                "email": " ERIKA@example.org ",
                "abrechnungsart": "Rechnung",
                "customer": customer.name,
            }
        ).insert(ignore_permissions=True)

        self.assertTrue(mitglied.mitglied_id.startswith("MIT-"))
        self.assertEqual(mitglied.name, mitglied.mitglied_id)
        self.assertEqual(mitglied.mitglied_name, "Erika Musterfrau")
        self.assertEqual(mitglied.email, "erika@example.org")
        self.assertEqual(frappe.db.get_value("Customer", customer.name, CUSTOMER_MITGLIED_FIELDNAME), mitglied.name)

    def test_crm_sync_fields_exist_with_expected_metadata(self):
        meta = frappe.get_meta("Mitglied", cached=False)

        anrede = meta.get_field("anrede")
        self.assertIsNotNone(anrede)
        self.assertEqual(anrede.fieldtype, "Data")

        picture_url = meta.get_field("picture_url")
        self.assertIsNotNone(picture_url)
        self.assertEqual(picture_url.fieldtype, "Data")
        self.assertEqual(picture_url.label, "Bild-URL")
        self.assertEqual(picture_url.options, "URL")

        fremd_id = meta.get_field("fremd_id")
        self.assertIsNotNone(fremd_id)
        self.assertEqual(fremd_id.fieldtype, "Data")
        self.assertEqual(fremd_id.label, "Externe ID")
        self.assertEqual(fremd_id.unique, 1)
        self.assertEqual(fremd_id.search_index, 1)

        abrechnungsart = meta.get_field("abrechnungsart")
        self.assertEqual(
            abrechnungsart.options,
            "Rechnung\nLastschrift\nBeitrag wird uebernommen\nBeitragsfrei",
        )

        jahresbeitrag = meta.get_field("jahresbeitrag")
        self.assertIsNotNone(jahresbeitrag)
        self.assertEqual(jahresbeitrag.fieldtype, "Currency")
        self.assertEqual(jahresbeitrag.default, "350")

        sepa_mandat = meta.get_field("sepa_mandat")
        self.assertIsNotNone(sepa_mandat)
        self.assertEqual(sepa_mandat.fieldtype, "Link")
        self.assertEqual(sepa_mandat.label, "SEPA-Mandat")
        self.assertEqual(sepa_mandat.options, "SEPA Mandat")
        self.assertEqual(sepa_mandat.read_only, 1)

        beitragszahler = meta.get_field("beitragszahler")
        self.assertIsNotNone(beitragszahler)
        self.assertEqual(beitragszahler.fieldtype, "Link")
        self.assertEqual(beitragszahler.options, "Mitglied")

        customer = meta.get_field("customer")
        self.assertIsNotNone(customer)
        self.assertEqual(customer.label, "Kunde")

        self.assertIsNone(meta.get_field("mandat_id"))
        self.assertIsNone(meta.get_field("mandatsdatum"))

    def test_mitglied_stores_crm_sync_fields(self):
        fremd_id = f"CRM-{frappe.generate_hash(length=8)}"
        picture_url = (
            "https://coalpicturestorage.blob.core.windows.net/images/"
            "2adc5c65-dbcb-4162-9f84-60d9b492d448.jpeg"
        )
        mitglied = frappe.get_doc(
            {
                "doctype": "Mitglied",
                "fremd_id": fremd_id,
                "anrede": "Frau",
                "vorname": "Clara",
                "nachname": "CRM",
                "eintrittsdatum": "2026-01-01",
                "email": "clara.crm@example.org",
                "picture_url": picture_url,
                "abrechnungsart": "Rechnung",
            }
        ).insert(ignore_permissions=True)

        stored = frappe.db.get_value(
            "Mitglied",
            mitglied.name,
            ["fremd_id", "anrede", "picture_url"],
            as_dict=True,
        )
        self.assertEqual(stored.fremd_id, fremd_id)
        self.assertEqual(stored.anrede, "Frau")
        self.assertEqual(stored.picture_url, picture_url)

    def test_customer_link_syncs_back_to_mitglied(self):
        customer = make_customer("Customer Backlink")
        mitglied = frappe.get_doc(
            {
                "doctype": "Mitglied",
                "vorname": "Max",
                "nachname": "Mustermann",
                "eintrittsdatum": "2026-01-01",
                "abrechnungsart": "Rechnung",
            }
        ).insert(ignore_permissions=True)

        customer.set(CUSTOMER_MITGLIED_FIELDNAME, mitglied.name)
        customer.save(ignore_permissions=True)

        self.assertEqual(frappe.db.get_value("Mitglied", mitglied.name, "customer"), customer.name)

    def test_lastschrift_requires_mandate_data(self):
        mitglied = frappe.get_doc(
            {
                "doctype": "Mitglied",
                "vorname": "Laura",
                "nachname": "Lastschrift",
                "eintrittsdatum": "2026-01-01",
                "abrechnungsart": "Lastschrift",
            }
        )

        self.assertRaises(frappe.ValidationError, mitglied.insert, ignore_permissions=True)

    def test_rechnung_defaults_jahresbeitrag(self):
        mitglied = make_mitglied()

        self.assertEqual(mitglied.jahresbeitrag, 350)

    def test_beitragsfrei_sets_jahresbeitrag_to_zero(self):
        mitglied = make_mitglied(abrechnungsart="Beitragsfrei", jahresbeitrag=350)

        self.assertEqual(mitglied.jahresbeitrag, 0)

    def test_beitragsfrei_rejects_sepa_mandat(self):
        customer = make_customer("Beitragsfrei Mandat")
        mitglied = make_mitglied(customer=customer.name)
        make_active_sepa_mandat(mitglied)

        mitglied.reload()
        mitglied.abrechnungsart = "Beitragsfrei"

        self.assertRaises(frappe.ValidationError, mitglied.save, ignore_permissions=True)

    def test_covered_contribution_requires_payer(self):
        mitglied = frappe.get_doc(
            {
                "doctype": "Mitglied",
                "vorname": "Ohne",
                "nachname": "Zahler",
                "eintrittsdatum": "2026-01-01",
                "abrechnungsart": "Beitrag wird uebernommen",
            }
        )

        self.assertRaises(frappe.ValidationError, mitglied.insert, ignore_permissions=True)

    def test_covered_contribution_accepts_valid_payer(self):
        payer = make_mitglied(vorname="Zahlendes", nachname="Mitglied")
        covered = make_mitglied(
            vorname="Uebernommenes",
            nachname="Mitglied",
            abrechnungsart="Beitrag wird uebernommen",
            beitragszahler=payer.name,
        )

        self.assertEqual(covered.beitragszahler, payer.name)

    def test_covered_contribution_rejects_self_as_payer(self):
        mitglied = make_mitglied()
        mitglied.abrechnungsart = "Beitrag wird uebernommen"
        mitglied.beitragszahler = mitglied.name

        self.assertRaises(frappe.ValidationError, mitglied.save, ignore_permissions=True)

    def test_covered_contribution_rejects_covered_payer(self):
        payer = make_mitglied(vorname="Zahlendes", nachname="Mitglied")
        covered_payer = make_mitglied(
            vorname="Ketten",
            nachname="Zahler",
            abrechnungsart="Beitrag wird uebernommen",
            beitragszahler=payer.name,
        )
        covered = frappe.get_doc(
            {
                "doctype": "Mitglied",
                "vorname": "Ungueltig",
                "nachname": "Kette",
                "eintrittsdatum": "2026-01-01",
                "abrechnungsart": "Beitrag wird uebernommen",
                "beitragszahler": covered_payer.name,
            }
        )

        self.assertRaises(frappe.ValidationError, covered.insert, ignore_permissions=True)

    def test_covered_contribution_rejects_free_payer(self):
        payer = make_mitglied(vorname="Freies", nachname="Mitglied", abrechnungsart="Beitragsfrei")
        covered = frappe.get_doc(
            {
                "doctype": "Mitglied",
                "vorname": "Ungueltig",
                "nachname": "Frei",
                "eintrittsdatum": "2026-01-01",
                "abrechnungsart": "Beitrag wird uebernommen",
                "beitragszahler": payer.name,
            }
        )

        self.assertRaises(frappe.ValidationError, covered.insert, ignore_permissions=True)

    def test_lastschrift_accepts_active_sepa_mandat(self):
        customer = make_customer("Lastschrift Mandat")
        mitglied = frappe.get_doc(
            {
                "doctype": "Mitglied",
                "vorname": "Mara",
                "nachname": "Mandat",
                "eintrittsdatum": "2026-01-01",
                "abrechnungsart": "Rechnung",
                "customer": customer.name,
            }
        ).insert(ignore_permissions=True)

        mandate = make_active_sepa_mandat(mitglied)
        mitglied.reload()
        self.assertEqual(mitglied.sepa_mandat, mandate.name)

        mitglied.abrechnungsart = "Lastschrift"
        mitglied.save(ignore_permissions=True)

        self.assertEqual(frappe.db.get_value("Mitglied", mitglied.name, "abrechnungsart"), "Lastschrift")

    def test_active_mandate_blocks_customer_change(self):
        customer = make_customer("Active Customer")
        other_customer = make_customer("Other Customer")
        mitglied = make_mitglied(customer=customer.name)
        make_active_sepa_mandat(mitglied)
        mitglied.reload()
        mitglied.customer = other_customer.name

        self.assertRaises(frappe.ValidationError, mitglied.save, ignore_permissions=True)

        customer.reload()
        customer.set(CUSTOMER_MITGLIED_FIELDNAME, None)
        self.assertRaises(frappe.ValidationError, customer.save, ignore_permissions=True)

    def test_unchanged_customer_link_does_not_check_customer_write_permission(self):
        customer = make_customer("Unchanged Customer")
        mitglied = make_mitglied(customer=customer.name)
        mitglied.vorname = "Updated"

        with patch("erpverein.services.mitglied_service.assert_customer_write_permission") as permission_check:
            mitglied.save(ignore_permissions=True)

        permission_check.assert_not_called()

    def test_austrittsdatum_must_not_precede_eintrittsdatum(self):
        mitglied = frappe.get_doc(
            {
                "doctype": "Mitglied",
                "vorname": "Datum",
                "nachname": "Test",
                "eintrittsdatum": "2026-02-01",
                "austrittsdatum": "2026-01-01",
                "abrechnungsart": "Rechnung",
            }
        )

        self.assertRaises(frappe.ValidationError, mitglied.insert, ignore_permissions=True)


def make_customer(label: str):
    suffix = frappe.generate_hash(length=8)
    customer = frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": f"{label} {suffix}",
            "customer_type": "Individual",
        }
    )

    customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
    if customer_group and customer.meta.has_field("customer_group"):
        customer.customer_group = customer_group

    territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
    if territory and customer.meta.has_field("territory"):
        customer.territory = territory

    return customer.insert(ignore_permissions=True)


def make_mitglied(**overrides):
    data = {
        "doctype": "Mitglied",
        "vorname": "Mira",
        "nachname": "Mitglied",
        "eintrittsdatum": "2026-01-01",
        "abrechnungsart": "Rechnung",
    }
    data.update(overrides)
    return frappe.get_doc(data).insert(ignore_permissions=True)


def make_bank():
    bank_name = f"SEPA Test Bank {frappe.generate_hash(length=8)}"
    return frappe.get_doc({"doctype": "Bank", "bank_name": bank_name}).insert(ignore_permissions=True)


def make_active_sepa_mandat(mitglied, **overrides):
    bank = make_bank()
    data = {
        "doctype": "SEPA Mandat",
        "mandatsreferenz": f"MR-{frappe.generate_hash(length=10)}",
        "mandatskategorie": "Mitgliedsbeitrag",
        "status": "Aktiv",
        "bezugs_doctype": "Mitglied",
        "bezugs_name": mitglied.name,
        "mandatsdatum": "2026-01-01",
        "einzugsintervall": "Jaehrlich",
        "einzugsplan_ab": "2026-01-01",
        "einzugstermine": [{"monat": 3, "tag": 31}],
        "kontoinhaber": mitglied.mitglied_name,
        "iban": make_german_iban(),
        "bic": "COBADEFFXXX",
        "bank": bank.name,
    }
    data.update(overrides)
    return frappe.get_doc(data).insert(ignore_permissions=True)


def make_german_iban() -> str:
    account_no = int(frappe.generate_hash(length=8), 36) % 10_000_000_000
    bban = f"37040044{account_no:010d}"
    check_digits = 98 - (int(f"{bban}131400") % 97)
    return f"DE{check_digits:02d}{bban}"
