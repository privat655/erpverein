import json

import frappe
from frappe.tests import IntegrationTestCase

from erpverein.custom_fields import BANK_ACCOUNT_MANAGED_FIELDNAME, BANK_ACCOUNT_SYNC_STATE_FIELDNAME, sync_custom_fields
from erpverein.services.sepa_mandat_service import activate_replacement_mandate


class TestSEPAMandatDoctype(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()

    def test_user_facing_labels_are_german(self):
        meta = frappe.get_meta("SEPA Mandat", cached=False)

        self.assertEqual(meta.get_field("mandat_section").label, "SEPA-Mandat")
        self.assertEqual(meta.get_field("bezugs_doctype").label, "Referenztyp")
        self.assertEqual(meta.get_field("customer").label, "Kunde")
        self.assertEqual(meta.get_field("bank_account").label, "Bankkonto")

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
        self.assertTrue(bank_account.account_name.startswith(mitglied.mitglied_name))
        self.assertEqual(bank_account.bank, bank.name)
        self.assertEqual(bank_account.party_type, "Customer")
        self.assertEqual(bank_account.party, customer.name)
        self.assertEqual(bank_account.iban, iban)
        self.assertEqual(bank_account.branch_code, "COBADEFFXXX")
        self.assertEqual(bank_account.get(BANK_ACCOUNT_MANAGED_FIELDNAME), 1)
        self.assertEqual(bank_account.disabled, 0)

        state = json.loads(bank_account.get(BANK_ACCOUNT_SYNC_STATE_FIELDNAME))
        self.assertEqual(state["schema_version"], 2)
        self.assertTrue(state["managed"])
        self.assertEqual(state["fields"]["iban"], iban)
        self.assertEqual(state["fields"]["party"], customer.name)
        self.assertEqual(state["sources"][mandate.name], {"doctype": "SEPA Mandat", "name": mandate.name})

    def test_rent_category_is_supported_for_mieter(self):
        customer = make_customer("SEPA Rent")
        mieter = make_mieter(customer=customer.name)
        mandate = make_rent_sepa_mandat(mieter).insert(ignore_permissions=True)
        mieter.reload()

        self.assertEqual(mandate.customer, customer.name)
        self.assertEqual(mieter.sepa_mandat, mandate.name)

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

    def test_revoke_is_blocked_when_it_would_orphan_lastschrift(self):
        customer = make_customer("SEPA Lastschrift")
        mitglied = make_mitglied(customer=customer.name)
        mandate = make_sepa_mandat(mitglied).insert(ignore_permissions=True)
        mitglied.reload()
        mitglied.abrechnungsart = "Lastschrift"
        mitglied.save(ignore_permissions=True)

        mandate.status = "Widerrufen"
        self.assertRaises(frappe.ValidationError, mandate.save, ignore_permissions=True)

    def test_replacement_activation_atomically_replaces_active_mandate(self):
        customer = make_customer("SEPA Replacement")
        mitglied = make_mitglied(customer=customer.name)
        old = make_sepa_mandat(mitglied).insert(ignore_permissions=True)
        mitglied.reload()
        mitglied.abrechnungsart = "Lastschrift"
        mitglied.save(ignore_permissions=True)
        replacement = make_sepa_mandat(
            mitglied,
            status="Entwurf",
            mandatsreferenz=f"MR-{frappe.generate_hash(length=10)}",
            iban=make_german_iban(),
        ).insert(ignore_permissions=True)

        result = activate_replacement_mandate(replacement.name)
        old.reload()
        replacement.reload()
        mitglied.reload()

        self.assertEqual(result["replaced"], old.name)
        self.assertEqual(old.status, "Ersetzt")
        self.assertEqual(replacement.status, "Aktiv")
        self.assertEqual(mitglied.sepa_mandat, replacement.name)

    def test_matching_unmanaged_bank_account_is_linked_without_modification(self):
        customer = make_customer("SEPA Manual Account")
        mitglied = make_mitglied(customer=customer.name)
        bank = make_bank()
        iban = make_german_iban()
        account = make_bank_account(customer, bank, iban, managed=0, disabled=1, branch_code="cobadeffxxx")
        before = account.as_dict().copy()

        mandate = make_sepa_mandat(mitglied, bank=bank.name, iban=iban).insert(ignore_permissions=True)
        account.reload()

        self.assertEqual(mandate.bank_account, account.name)
        self.assertEqual(account.disabled, before["disabled"])
        self.assertEqual(account.get(BANK_ACCOUNT_MANAGED_FIELDNAME), 0)
        self.assertEqual(account.modified, before["modified"])
        self.assertFalse(account.get(BANK_ACCOUNT_SYNC_STATE_FIELDNAME))

    def test_incompatible_unmanaged_bank_account_is_not_adopted(self):
        customer = make_customer("SEPA Incompatible Account")
        mitglied = make_mitglied(customer=customer.name)
        bank = make_bank()
        other_bank = make_bank()
        iban = make_german_iban()
        manual = make_bank_account(customer, other_bank, iban, managed=0)

        mandate = make_sepa_mandat(mitglied, bank=bank.name, iban=iban)
        with self.assertRaises(frappe.ValidationError):
            mandate.insert(ignore_permissions=True)
        manual.reload()

        self.assertEqual(manual.bank, other_bank.name)
        self.assertEqual(manual.get(BANK_ACCOUNT_MANAGED_FIELDNAME), 0)
        self.assertEqual(
            frappe.db.count("Bank Account", {"party_type": "Customer", "party": customer.name, "iban": iban}),
            1,
        )

    def test_manual_edit_to_managed_bank_account_blocks_sync(self):
        customer = make_customer("SEPA Guarded Account")
        mitglied = make_mitglied(customer=customer.name)
        mandate = make_sepa_mandat(mitglied).insert(ignore_permissions=True)
        account = frappe.get_doc("Bank Account", mandate.bank_account)
        account.account_name = "Manual Owner"
        account.save(ignore_permissions=True)

        mandate.kontoinhaber = "New Mandate Owner"
        self.assertRaises(frappe.ValidationError, mandate.save, ignore_permissions=True)

    def test_delete_does_not_disable_or_modify_unmanaged_bank_account(self):
        customer = make_customer("SEPA Delete Manual")
        mitglied = make_mitglied(customer=customer.name)
        bank = make_bank()
        iban = make_german_iban()
        account = make_bank_account(customer, bank, iban, managed=0, disabled=0)
        mandate = make_sepa_mandat(mitglied, bank=bank.name, iban=iban).insert(ignore_permissions=True)
        before_modified = account.modified

        mandate.delete(ignore_permissions=True)
        account.reload()
        mitglied.reload()

        self.assertFalse(mitglied.sepa_mandat)
        self.assertEqual(account.disabled, 0)
        self.assertEqual(account.get(BANK_ACCOUNT_MANAGED_FIELDNAME), 0)
        self.assertEqual(account.modified, before_modified)

    def test_delete_clears_backlink_and_disables_unused_managed_account(self):
        customer = make_customer("SEPA Delete Managed")
        mitglied = make_mitglied(customer=customer.name)
        mandate = make_sepa_mandat(mitglied).insert(ignore_permissions=True)
        bank_account = mandate.bank_account

        mandate.delete(ignore_permissions=True)
        mitglied.reload()

        self.assertFalse(mitglied.sepa_mandat)
        self.assertEqual(frappe.db.get_value("Bank Account", bank_account, "disabled"), 1)

    def test_delete_is_blocked_when_it_would_orphan_lastschrift(self):
        customer = make_customer("SEPA Delete Lastschrift")
        mitglied = make_mitglied(customer=customer.name)
        mandate = make_sepa_mandat(mitglied).insert(ignore_permissions=True)
        mitglied.reload()
        mitglied.abrechnungsart = "Lastschrift"
        mitglied.save(ignore_permissions=True)

        self.assertRaises(frappe.ValidationError, mandate.delete, ignore_permissions=True)

    def test_customer_is_cleared_when_reference_has_no_customer(self):
        mitglied = make_mitglied()
        mandate = make_sepa_mandat(mitglied, status="Entwurf")
        mandate.customer = make_customer("Stale Customer").name
        mandate.insert(ignore_permissions=True)

        self.assertFalse(mandate.customer)

    def test_iban_is_not_search_indexed_or_in_search_fields(self):
        meta = frappe.get_meta("SEPA Mandat", cached=False)
        self.assertFalse(meta.get_field("iban").search_index)
        self.assertNotIn("iban", [field.strip() for field in meta.search_fields.split(",")])

    def test_invalid_iban_error_is_masked(self):
        customer = make_customer("SEPA Mask")
        mitglied = make_mitglied(customer=customer.name)
        invalid = "DE00370400440532013000"
        mandate = make_sepa_mandat(mitglied, iban=invalid)

        with self.assertRaises(frappe.ValidationError) as error:
            mandate.insert(ignore_permissions=True)
        self.assertNotIn(invalid, str(error.exception))
        self.assertIn("DE", str(error.exception))

    def test_revoked_mandate_cannot_be_reactivated(self):
        customer = make_customer("SEPA Terminal")
        mitglied = make_mitglied(customer=customer.name)
        mandate = make_sepa_mandat(mitglied).insert(ignore_permissions=True)
        mandate.status = "Widerrufen"
        mandate.save(ignore_permissions=True)
        mandate.status = "Aktiv"

        with self.assertRaises(frappe.ValidationError):
            mandate.save(ignore_permissions=True)

    def test_active_mandate_identity_is_immutable(self):
        customer = make_customer("SEPA Immutable")
        mitglied = make_mitglied(customer=customer.name)
        mandate = make_sepa_mandat(mitglied).insert(ignore_permissions=True)
        mandate.iban = make_german_iban()

        with self.assertRaises(frappe.ValidationError):
            mandate.save(ignore_permissions=True)

    def test_draft_cannot_disable_forged_managed_bank_account(self):
        first_customer = make_customer("SEPA Managed Owner")
        first_member = make_mitglied(customer=first_customer.name)
        active = make_sepa_mandat(first_member).insert(ignore_permissions=True)
        second_member = make_mitglied()
        draft = make_sepa_mandat(second_member, status="Entwurf")
        draft.bank_account = active.bank_account
        draft.insert(ignore_permissions=True)

        self.assertFalse(draft.bank_account)
        self.assertEqual(frappe.db.get_value("Bank Account", active.bank_account, "disabled"), 0)

    def test_bank_account_provenance_marker_cannot_be_forged(self):
        customer = make_customer("SEPA Forged Marker")
        bank = make_bank()
        account = frappe.get_doc(
            {
                "doctype": "Bank Account",
                "account_name": "Forged Marker",
                "bank": bank.name,
                "party_type": "Customer",
                "party": customer.name,
                "iban": make_german_iban(),
                BANK_ACCOUNT_MANAGED_FIELDNAME: 1,
            }
        )

        with self.assertRaises(frappe.ValidationError):
            account.insert(ignore_permissions=True)


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


def make_mieter(**overrides):
    data = {
        "doctype": "Mieter",
        "vorname": "Rita",
        "nachname": "Miete",
        "mietbeginn": "2026-01-01",
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


def make_rent_sepa_mandat(mieter, bank: str | None = None, **overrides):
    bank = bank or make_bank().name
    data = {
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
        "bank": bank,
    }
    data.update(overrides)
    return frappe.get_doc(data)


def make_bank_account(
    customer,
    bank,
    iban: str,
    *,
    managed: int,
    disabled: int = 0,
    branch_code: str = "COBADEFFXXX",
):
    return frappe.get_doc(
        {
            "doctype": "Bank Account",
            "account_name": f"Manual {frappe.generate_hash(length=8)}",
            "bank": bank.name,
            "party_type": "Customer",
            "party": customer.name,
            "iban": iban,
            "branch_code": branch_code,
            "is_company_account": 0,
            "disabled": disabled,
            BANK_ACCOUNT_MANAGED_FIELDNAME: managed,
        }
    ).insert(ignore_permissions=True)


def make_german_iban() -> str:
    account_no = int(frappe.generate_hash(length=8), 36) % 10_000_000_000
    bban = f"37040044{account_no:010d}"
    check_digits = 98 - (int(f"{bban}131400") % 97)
    return f"DE{check_digits:02d}{bban}"
