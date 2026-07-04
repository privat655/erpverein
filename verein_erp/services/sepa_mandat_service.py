import json

import frappe
from frappe import _
from frappe.utils import cstr

from verein_erp.custom_fields import BANK_ACCOUNT_MANAGED_FIELDNAME, BANK_ACCOUNT_SYNC_STATE_FIELDNAME


MANDATE_CATEGORY_MEMBERSHIP = "Mitgliedsbeitrag"
MANDATE_CATEGORY_RENT = "Miete"
MANDATE_STATUS_DRAFT = "Entwurf"
MANDATE_STATUS_ACTIVE = "Aktiv"
MANDATE_STATUS_REVOKED = "Widerrufen"
MANDATE_STATUS_REPLACED = "Ersetzt"
MANDATE_MODE_YEARLY = "Jaehrlich"
MANDATE_MODE_HALF_YEARLY = "Halbjaehrlich"

ALLOWED_STATUSES = {
    MANDATE_STATUS_DRAFT,
    MANDATE_STATUS_ACTIVE,
    MANDATE_STATUS_REVOKED,
    MANDATE_STATUS_REPLACED,
}
ALLOWED_CATEGORIES = {MANDATE_CATEGORY_MEMBERSHIP, MANDATE_CATEGORY_RENT}
ALLOWED_MODES = {MANDATE_MODE_YEARLY, MANDATE_MODE_HALF_YEARLY}
SUPPORTED_REFERENCE_DOCTYPES = {MANDATE_CATEGORY_MEMBERSHIP: "Mitglied"}


def normalize_text(value: object) -> str | None:
    value = " ".join(cstr(value).strip().split())
    return value or None


def normalize_iban(value: object) -> str | None:
    value = normalize_text(value)
    return value.replace(" ", "").upper() if value else None


def validate_iban(value: str | None) -> bool:
    if not value:
        return False

    iban = normalize_iban(value)
    if not iban or len(iban) < 15 or len(iban) > 34:
        return False
    if not iban[:2].isalpha() or not iban[2:4].isdigit():
        return False
    if not iban.isalnum():
        return False

    rearranged = iban[4:] + iban[:4]
    converted = "".join(str(int(char, 36)) for char in rearranged)
    return int(converted) % 97 == 1


def validate_sepa_mandat(doc) -> None:
    normalize_sepa_mandat(doc)
    validate_category_and_reference(doc)
    set_customer_from_reference(doc)
    validate_unique_mandate_reference(doc)
    validate_active_mandate(doc)


def normalize_sepa_mandat(doc) -> None:
    doc.mandatsreferenz = normalize_text(doc.mandatsreferenz)
    doc.mandatskategorie = normalize_text(doc.mandatskategorie) or MANDATE_CATEGORY_MEMBERSHIP
    doc.status = normalize_text(doc.status) or MANDATE_STATUS_DRAFT
    doc.bezugs_doctype = normalize_text(doc.bezugs_doctype)
    doc.bezugs_name = normalize_text(doc.bezugs_name)
    doc.customer = normalize_text(doc.customer)
    doc.einzugsmodus = normalize_text(doc.einzugsmodus)
    doc.kontoinhaber = normalize_text(doc.kontoinhaber)
    doc.kontoinhaber_adresse = normalize_text(doc.kontoinhaber_adresse)
    doc.iban = normalize_iban(doc.iban)
    doc.bic = normalize_text(doc.bic)
    doc.bank = normalize_text(doc.bank)
    doc.bank_name_freitext = normalize_text(doc.bank_name_freitext)

    if doc.mandatskategorie == MANDATE_CATEGORY_MEMBERSHIP and not doc.bezugs_doctype:
        doc.bezugs_doctype = "Mitglied"


def validate_category_and_reference(doc) -> None:
    if doc.status not in ALLOWED_STATUSES:
        frappe.throw(_("Ungueltiger SEPA-Mandatsstatus: {0}").format(frappe.bold(doc.status)))

    if doc.mandatskategorie not in ALLOWED_CATEGORIES:
        frappe.throw(_("Ungueltige Mandatskategorie: {0}").format(frappe.bold(doc.mandatskategorie)))

    expected_doctype = SUPPORTED_REFERENCE_DOCTYPES.get(doc.mandatskategorie)
    if not expected_doctype:
        frappe.throw(
            _("Mandatskategorie {0} wird in dieser Version noch nicht unterstuetzt.").format(
                frappe.bold(doc.mandatskategorie)
            )
        )

    if doc.bezugs_doctype != expected_doctype:
        frappe.throw(
            _("Mandatskategorie {0} muss auf {1} verweisen.").format(
                frappe.bold(doc.mandatskategorie), frappe.bold(expected_doctype)
            )
        )

    if not doc.bezugs_name or not frappe.db.exists(doc.bezugs_doctype, doc.bezugs_name):
        frappe.throw(_("Das Bezugsdokument fuer das SEPA-Mandat ist ungueltig."))

    if doc.einzugsmodus and doc.einzugsmodus not in ALLOWED_MODES:
        frappe.throw(_("Ungueltiger Einzugsmodus: {0}").format(frappe.bold(doc.einzugsmodus)))

    if doc.iban and not validate_iban(doc.iban):
        frappe.throw(_("IBAN {0} ist ungueltig.").format(frappe.bold(doc.iban)))


def set_customer_from_reference(doc) -> None:
    customer = get_customer_from_reference(doc.bezugs_doctype, doc.bezugs_name)
    if customer:
        doc.customer = customer


def get_customer_from_reference(doctype: str | None, name: str | None) -> str | None:
    if doctype == "Mitglied" and name:
        return frappe.db.get_value("Mitglied", name, "customer")
    return None


def validate_unique_mandate_reference(doc) -> None:
    if not doc.mandatsreferenz:
        return

    other_mandat = frappe.db.get_value(
        "SEPA Mandat",
        {"mandatsreferenz": doc.mandatsreferenz, "name": ("!=", doc.name)},
        "name",
    )
    if other_mandat:
        frappe.throw(
            _("Mandatsreferenz {0} ist bereits bei SEPA Mandat {1} eingetragen.").format(
                frappe.bold(doc.mandatsreferenz), frappe.bold(other_mandat)
            )
        )


def validate_active_mandate(doc) -> None:
    if doc.status != MANDATE_STATUS_ACTIVE:
        return

    required_fields = {
        "mandatsreferenz": _("Mandatsreferenz"),
        "mandatsdatum": _("Mandatsdatum"),
        "einzugsmodus": _("Einzugsmodus"),
        "kontoinhaber": _("Kontoinhaber"),
        "iban": _("IBAN"),
        "bank": _("Bank"),
        "customer": _("Customer"),
    }
    missing = [label for fieldname, label in required_fields.items() if not doc.get(fieldname)]
    if missing:
        frappe.throw(_("Fuer aktive SEPA-Mandate fehlen: {0}").format(", ".join(missing)))

    other_active = frappe.db.get_value(
        "SEPA Mandat",
        {
            "mandatskategorie": doc.mandatskategorie,
            "bezugs_doctype": doc.bezugs_doctype,
            "bezugs_name": doc.bezugs_name,
            "status": MANDATE_STATUS_ACTIVE,
            "name": ("!=", doc.name),
        },
        "name",
    )
    if other_active:
        frappe.throw(
            _("Fuer {0} {1} existiert bereits ein aktives SEPA Mandat: {2}").format(
                frappe.bold(doc.bezugs_doctype), frappe.bold(doc.bezugs_name), frappe.bold(other_active)
            )
        )


def sync_sepa_mandat(doc) -> None:
    clear_previous_reference_link(doc)
    sync_mandat_link_to_reference(doc)
    sync_bank_account_from_mandat(doc)


def clear_mandat_link_from_reference(doc) -> None:
    clear_reference_link(doc.bezugs_doctype, doc.bezugs_name, doc.name)


def clear_previous_reference_link(doc) -> None:
    previous_doctype = get_value_before_save(doc, "bezugs_doctype")
    previous_name = get_value_before_save(doc, "bezugs_name")
    if previous_doctype and previous_name and (previous_doctype != doc.bezugs_doctype or previous_name != doc.bezugs_name):
        clear_reference_link(previous_doctype, previous_name, doc.name)


def sync_mandat_link_to_reference(doc) -> None:
    if doc.bezugs_doctype != "Mitglied" or not doc.bezugs_name or not frappe.db.exists("Mitglied", doc.bezugs_name):
        return

    if doc.status == MANDATE_STATUS_ACTIVE:
        assert_reference_write_permission(doc.bezugs_doctype, doc.bezugs_name)
        set_value_if_changed("Mitglied", doc.bezugs_name, "sepa_mandat", doc.name)
    else:
        clear_reference_link(doc.bezugs_doctype, doc.bezugs_name, doc.name)


def clear_reference_link(doctype: str | None, name: str | None, mandat: str | None) -> None:
    if doctype != "Mitglied" or not name or not mandat or not frappe.db.exists("Mitglied", name):
        return

    current_mandat = frappe.db.get_value("Mitglied", name, "sepa_mandat")
    if current_mandat == mandat:
        assert_reference_write_permission(doctype, name)
        set_value_if_changed("Mitglied", name, "sepa_mandat", None)


def sync_bank_account_from_mandat(doc) -> None:
    if not should_sync_bank_account(doc):
        return

    bank_account_name = get_existing_bank_account_for_mandat(doc)
    is_new = not bank_account_name
    bank_account = frappe.new_doc("Bank Account") if is_new else frappe.get_doc("Bank Account", bank_account_name)

    if not is_new:
        bank_account.check_permission("write")

    desired = get_desired_bank_account_fields(doc, bank_account)
    ensure_shared_bank_account_is_safe(doc, bank_account, desired)
    apply_bank_account_fields(bank_account, desired, force=is_new or not bank_account.get(BANK_ACCOUNT_MANAGED_FIELDNAME))

    if is_new:
        bank_account.insert()
    else:
        bank_account.save()

    if doc.bank_account != bank_account.name:
        frappe.db.set_value(doc.doctype, doc.name, "bank_account", bank_account.name, update_modified=False)
        doc.bank_account = bank_account.name


def should_sync_bank_account(doc) -> bool:
    if doc.status == MANDATE_STATUS_ACTIVE:
        return bool(doc.customer and doc.iban and doc.bank)
    return bool(doc.bank_account and frappe.db.exists("Bank Account", doc.bank_account))


def get_existing_bank_account_for_mandat(doc) -> str | None:
    if doc.bank_account and frappe.db.exists("Bank Account", doc.bank_account):
        return doc.bank_account

    matches = frappe.db.get_list(
        "Bank Account",
        filters={
            "party_type": "Customer",
            "party": doc.customer,
            "iban": doc.iban,
            "is_company_account": 0,
        },
        pluck="name",
    )
    if len(matches) > 1:
        frappe.throw(
            _("Mehrere Bank Accounts fuer Customer {0} und IBAN {1} gefunden.").format(
                frappe.bold(doc.customer), frappe.bold(doc.iban)
            )
        )
    return matches[0] if matches else None


def get_desired_bank_account_fields(doc, bank_account) -> dict:
    disabled = 0 if doc.status == MANDATE_STATUS_ACTIVE or other_active_mandates_use_bank_account(doc, bank_account.name) else 1
    return {
        "account_name": doc.kontoinhaber or bank_account.get("account_name") or doc.customer,
        "bank": doc.bank or bank_account.get("bank"),
        "party_type": "Customer",
        "party": doc.customer,
        "iban": doc.iban or bank_account.get("iban"),
        "branch_code": doc.bic or bank_account.get("branch_code"),
        "is_company_account": 0,
        "disabled": disabled,
        BANK_ACCOUNT_MANAGED_FIELDNAME: 1,
    }


def ensure_shared_bank_account_is_safe(doc, bank_account, desired: dict) -> None:
    if not bank_account.name or not other_active_mandates_use_bank_account(doc, bank_account.name):
        return

    for fieldname in ["party_type", "party", "iban", "bank", "branch_code"]:
        current = normalize_text(bank_account.get(fieldname))
        target = normalize_text(desired.get(fieldname))
        if current and target and current != target:
            frappe.throw(
                _("Bank Account {0} wird von weiteren aktiven Mandaten genutzt und darf nicht widerspruechlich geaendert werden.").format(
                    frappe.bold(bank_account.name)
                )
            )


def other_active_mandates_use_bank_account(doc, bank_account: str | None = None) -> bool:
    bank_account = bank_account or doc.bank_account
    if not bank_account:
        return False

    return bool(
        frappe.db.get_value(
            "SEPA Mandat",
            {
                "bank_account": bank_account,
                "status": MANDATE_STATUS_ACTIVE,
                "name": ("!=", doc.name),
            },
            "name",
        )
    )


def apply_bank_account_fields(bank_account, desired: dict, force: bool = False) -> None:
    meta = frappe.get_meta("Bank Account")
    previous = load_bank_account_sync_state(bank_account)
    next_state = {}

    for fieldname, value in desired.items():
        if not meta.has_field(fieldname):
            continue
        current = normalize_text(bank_account.get(fieldname))
        previous_value = normalize_text(previous.get(fieldname))
        desired_value = normalize_text(value)

        if force or not previous or current in {None, ""} or current == previous_value or fieldname in {BANK_ACCOUNT_MANAGED_FIELDNAME, "disabled"}:
            bank_account.set(fieldname, value)
            next_state[fieldname] = value
        elif current != desired_value:
            frappe.throw(
                _("Bank Account {0} wurde manuell geaendert. Bitte pruefen Sie die Bankdaten vor dem Mandats-Sync.").format(
                    frappe.bold(bank_account.name)
                )
            )
        else:
            next_state[fieldname] = value

    if meta.has_field(BANK_ACCOUNT_SYNC_STATE_FIELDNAME):
        bank_account.set(BANK_ACCOUNT_SYNC_STATE_FIELDNAME, json.dumps(next_state, sort_keys=True))


def load_bank_account_sync_state(bank_account) -> dict:
    if not frappe.get_meta("Bank Account").has_field(BANK_ACCOUNT_SYNC_STATE_FIELDNAME):
        return {}

    raw_state = bank_account.get(BANK_ACCOUNT_SYNC_STATE_FIELDNAME)
    if not raw_state:
        return {}

    try:
        return json.loads(raw_state)
    except (TypeError, ValueError):
        return {}


def set_value_if_changed(doctype: str, name: str, fieldname: str, value: str | None) -> None:
    current_value = frappe.db.get_value(doctype, name, fieldname) or None
    value = value or None
    if current_value != value:
        frappe.db.set_value(doctype, name, fieldname, value)


def assert_reference_write_permission(doctype: str | None, name: str | None) -> None:
    if doctype != "Mitglied" or not name or skip_cross_link_permission_check():
        return

    frappe.get_doc("Mitglied", name).check_permission("write")


def skip_cross_link_permission_check() -> bool:
    return bool(frappe.flags.in_install or frappe.flags.in_patch or frappe.flags.in_migrate)


def get_value_before_save(doc, fieldname: str):
    if hasattr(doc, "get_value_before_save"):
        return doc.get_value_before_save(fieldname)

    previous = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
    return previous.get(fieldname) if previous else None
