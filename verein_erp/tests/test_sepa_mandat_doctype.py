import json

import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.custom_fields import BANK_ACCOUNT_MANAGED_FIELDNAME, BANK_ACCOUNT_SYNC_STATE_FIELDNAME, sync_custom_fields


class TestSEPAMandatDoctype(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()

    def test_active_membership_mandat_syncs_member_and_bank_account(self):
        customer = make_customer("SEPA Sync")
        mitglied = make_mitglied(customer=customer.name)
        bank = make_bank()
        iban = make_german_iban()

        mandate = make_sepa_mandat(mitglied, bank=bank.name, iban=iban).insert(ignore_permissions=True)
        mandate.reload()
        mitglied.reload()

        self.assertEqual(mandate.customer, customer.name)
        self.assertEqual(mitglied.sepa_mandat, mandate.name)
        self.assertTrue(mandate.bank_account)

        bank_account = frappe.get_doc("Bank Account", mandate.bank_account)
        self.assertEqual(bank_account.account_name, mitglied.mitglied_name)
        self.assertEqual(bank_account.bank, bank.name)
        self.assertEqual(bank_account.party_type, "Customer")
        self.assertEqual(bank_account.party, customer.name)
        self.assertEqual(bank_account.iban, iban)
        self.assertEqual(bank_account.branch_code, "COBADEFFXXX")
        self.assertEqual(bank_account.get(BANK_ACCOUNT_MANAGED_FIELDNAME), 1)
        self.assertEqual(bank_account.disabled, 0)

        state = json.loads(bank_account.get(BANK_ACCOUNT_SYNC_STATE_FIELDNAME))
        self.assertEqual(state["iban"], iban)
        self.assertEqual(state["party"], customer.name)

    def test_rent_category_is_not_supported_yet(self):
        customer = make_customer("SEPA Rent")
        mitglied = make_mitglied(customer=customer.name)
        mandate = make_sepa_mandat(mitglied, mandatskategorie="Miete")

        self.assertRaises(frappe.ValidationError, mandate.insert, ignore_permissions=True)

    def test_only_one_active_membership_mandat_per_member(self):
        customer = make_customer("SEPA Duplicate")
        mitglied = make_mitglied(customer=customer.name)
        bank = make_bank()
        make_sepa_mandat(mitglied, bank=bank.name).insert(ignore_permissions=True)

        duplicate = make_sepa_mandat(
            mitglied,
            bank=bank.name,
            mandatsreferenz=f"MR-{frappe.generate_hash(length=10)}",
            iban=make_german_iban(),
        )

        self.assertRaises(frappe.ValidationError, duplicate.insert, ignore_permissions=True)

    def test_inactive_mandat_clears_member_link_and_disables_managed_bank_account(self):
        customer = make_customer("SEPA Revoke")
        mitglied = make_mitglied(customer=customer.name)
        bank = make_bank()
        mandate = make_sepa_mandat(mitglied, bank=bank.name).insert(ignore_permissions=True)
        mandate.reload()

        mandate.status = "Widerrufen"
        mandate.save(ignore_permissions=True)
        mandate.reload()
        mitglied.reload()

        self.assertFalse(mitglied.sepa_mandat)
        self.assertEqual(frappe.db.get_value("Bank Account", mandate.bank_account, "disabled"), 1)


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
        "vorname": "Tessa",
        "nachname": "SEPA",
        "eintrittsdatum": "2026-01-01",
        "abrechnungsart": "Rechnung",
    }
    data.update(overrides)
    return frappe.get_doc(data).insert(ignore_permissions=True)


def make_bank():
    bank_name = f"SEPA Test Bank {frappe.generate_hash(length=8)}"
    return frappe.get_doc({"doctype": "Bank", "bank_name": bank_name}).insert(ignore_permissions=True)


def make_sepa_mandat(mitglied, bank: str | None = None, **overrides):
    bank = bank or make_bank().name
    data = {
        "doctype": "SEPA Mandat",
        "mandatsreferenz": f"MR-{frappe.generate_hash(length=10)}",
        "mandatskategorie": "Mitgliedsbeitrag",
        "status": "Aktiv",
        "bezugs_doctype": "Mitglied",
        "bezugs_name": mitglied.name,
        "mandatsdatum": "2026-01-01",
        "einzugsmodus": "Jaehrlich",
        "kontoinhaber": mitglied.mitglied_name,
        "iban": make_german_iban(),
        "bic": "COBADEFFXXX",
        "bank": bank,
    }
    data.update(overrides)
    return frappe.get_doc(data)


def make_german_iban() -> str:
    account_no = int(frappe.generate_hash(length=8), 36) % 10_000_000_000
    bban = f"37040044{account_no:010d}"
    check_digits = 98 - (int(f"{bban}131400") % 97)
    return f"DE{check_digits:02d}{bban}"
