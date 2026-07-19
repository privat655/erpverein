import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cstr

from erpverein.custom_fields import BANK_ACCOUNT_MANAGED_FIELDNAME, BANK_ACCOUNT_SYNC_STATE_FIELDNAME
from erpverein.services.sepa_collection_schedule_service import validate_and_set_collection_schedule


MANDATE_CATEGORY_MEMBERSHIP = "Mitgliedsbeitrag"
MANDATE_CATEGORY_RENT = "Miete"
MANDATE_STATUS_DRAFT = "Entwurf"
MANDATE_STATUS_ACTIVE = "Aktiv"
MANDATE_STATUS_REVOKED = "Widerrufen"
MANDATE_STATUS_REPLACED = "Ersetzt"
BANK_ACCOUNT_STATE_SCHEMA_VERSION = 2

ALLOWED_STATUSES = {
    MANDATE_STATUS_DRAFT,
    MANDATE_STATUS_ACTIVE,
    MANDATE_STATUS_REVOKED,
    MANDATE_STATUS_REPLACED,
}
ALLOWED_CATEGORIES = {MANDATE_CATEGORY_MEMBERSHIP, MANDATE_CATEGORY_RENT}
SUPPORTED_REFERENCE_DOCTYPES = {MANDATE_CATEGORY_MEMBERSHIP: "Mitglied", MANDATE_CATEGORY_RENT: "Mieter"}


def normalize_text(value: object) -> str | None:
    value = " ".join(cstr(value).strip().split())
    return value or None


def normalize_iban(value: object) -> str | None:
    value = normalize_text(value)
    return value.replace(" ", "").upper() if value else None


def normalize_bic(value: object) -> str | None:
    value = normalize_text(value)
    return value.replace(" ", "").upper() if value else None


def mask_iban(value: object) -> str:
    iban = normalize_iban(value) or ""
    if len(iban) < 8:
        return "****"
    return f"{iban[:2]}{'*' * max(4, len(iban) - 6)}{iban[-4:]}"


def validate_iban(value: str | None) -> bool:
    if not value:
        return False
    iban = normalize_iban(value)
    if not iban or len(iban) < 15 or len(iban) > 34:
        return False
    if not iban[:2].isalpha() or not iban[2:4].isdigit() or not iban.isalnum():
        return False
    rearranged = iban[4:] + iban[:4]
    converted = "".join(str(int(char, 36)) for char in rearranged)
    return int(converted) % 97 == 1


def validate_sepa_mandat(doc) -> None:
    normalize_sepa_mandat(doc)
    validate_mandate_state_transition(doc)
    validate_category_and_reference(doc)
    lock_mandate_references(doc)
    set_customer_from_reference(doc)
    validate_unique_mandate_reference(doc)
    validate_active_mandate(doc)
    validate_and_set_collection_schedule(doc)
    validate_active_reference_is_not_orphaned(doc)


def validate_mandate_state_transition(doc) -> None:
    previous = doc.get_doc_before_save()
    if not previous:
        if doc.status not in {MANDATE_STATUS_DRAFT, MANDATE_STATUS_ACTIVE}:
            frappe.throw(_("Ein neues SEPA-Mandat muss als Entwurf oder Aktiv angelegt werden."))
        return

    if previous.status in {MANDATE_STATUS_REVOKED, MANDATE_STATUS_REPLACED} and doc.status != previous.status:
        frappe.throw(_("Widerrufene oder ersetzte SEPA-Mandate sind abgeschlossen und duerfen nicht reaktiviert werden."))
    if previous.status == MANDATE_STATUS_DRAFT and doc.status not in {MANDATE_STATUS_DRAFT, MANDATE_STATUS_ACTIVE}:
        frappe.throw(_("Ein Entwurf kann nur als Entwurf gespeichert oder aktiviert werden."))
    if previous.status == MANDATE_STATUS_ACTIVE and doc.status not in {
        MANDATE_STATUS_ACTIVE,
        MANDATE_STATUS_REVOKED,
        MANDATE_STATUS_REPLACED,
    }:
        frappe.throw(_("Ein aktives SEPA-Mandat kann nur widerrufen oder durch ein Ersatzmandat ersetzt werden."))
    if previous.status == MANDATE_STATUS_ACTIVE and doc.status == MANDATE_STATUS_REPLACED and not doc.flags.get(
        "erpverein_replacement_transition"
    ):
        frappe.throw(_("Der Status Ersetzt darf nur durch die Ersatzmandat-Funktion gesetzt werden."))


def normalize_sepa_mandat(doc) -> None:
    doc.mandatsreferenz = normalize_text(doc.mandatsreferenz)
    doc.mandatskategorie = normalize_text(doc.mandatskategorie) or MANDATE_CATEGORY_MEMBERSHIP
    doc.status = normalize_text(doc.status) or MANDATE_STATUS_DRAFT
    doc.bezugs_doctype = normalize_text(doc.bezugs_doctype)
    doc.bezugs_name = normalize_text(doc.bezugs_name)
    doc.customer = normalize_text(doc.customer)
    doc.einzugsintervall = normalize_text(doc.get("einzugsintervall"))
    doc.wochentag = normalize_text(doc.get("wochentag"))
    doc.kontoinhaber = normalize_text(doc.kontoinhaber)
    doc.kontoinhaber_adresse = normalize_text(doc.kontoinhaber_adresse)
    doc.iban = normalize_iban(doc.iban)
    doc.bic = normalize_bic(doc.bic)
    doc.bank = normalize_text(doc.bank)
    doc.bank_name_freitext = normalize_text(doc.bank_name_freitext)
    if doc.mandatskategorie == MANDATE_CATEGORY_MEMBERSHIP and not doc.bezugs_doctype:
        doc.bezugs_doctype = "Mitglied"
    if doc.mandatskategorie == MANDATE_CATEGORY_RENT and not doc.bezugs_doctype:
        doc.bezugs_doctype = "Mieter"


def validate_category_and_reference(doc) -> None:
    if doc.status not in ALLOWED_STATUSES:
        frappe.throw(_("Ungueltiger SEPA-Mandatsstatus: {0}").format(frappe.bold(doc.status)))
    if doc.mandatskategorie not in ALLOWED_CATEGORIES:
        frappe.throw(_("Ungueltige Mandatskategorie: {0}").format(frappe.bold(doc.mandatskategorie)))

    expected_doctype = SUPPORTED_REFERENCE_DOCTYPES.get(doc.mandatskategorie)
    if doc.bezugs_doctype != expected_doctype:
        frappe.throw(
            _("Mandatskategorie {0} muss auf {1} verweisen.").format(
                frappe.bold(doc.mandatskategorie), frappe.bold(expected_doctype)
            )
        )
    if not doc.bezugs_name or not frappe.db.exists(doc.bezugs_doctype, doc.bezugs_name):
        frappe.throw(_("Das Bezugsdokument fuer das SEPA-Mandat ist ungueltig."))
    if doc.iban and not validate_iban(doc.iban):
        frappe.throw(_("IBAN {0} ist ungueltig.").format(frappe.bold(mask_iban(doc.iban))))


def lock_mandate_references(doc) -> None:
    references = {
        (doctype, name)
        for doctype, name in [
            (get_value_before_save(doc, "bezugs_doctype"), get_value_before_save(doc, "bezugs_name")),
            (doc.bezugs_doctype, doc.bezugs_name),
        ]
        if doctype in {"Mitglied", "Mieter"} and name
    }
    for doctype, name in sorted(references):
        frappe.db.get_value(doctype, name, "name", for_update=True)


def set_customer_from_reference(doc) -> None:
    previous_customer = get_value_before_save(doc, "customer")
    customer = get_customer_from_reference(doc.bezugs_doctype, doc.bezugs_name)
    doc.customer = customer or None
    if previous_customer and previous_customer != doc.customer:
        doc.bank_account = None


def get_customer_from_reference(doctype: str | None, name: str | None) -> str | None:
    if doctype in {"Mitglied", "Mieter"} and name:
        return frappe.db.get_value(doctype, name, "customer")
    return None


def validate_unique_mandate_reference(doc) -> None:
    if not doc.mandatsreferenz:
        return
    if frappe.db.get_value("SEPA Mandat", {"mandatsreferenz": doc.mandatsreferenz, "name": ("!=", doc.name)}, "name"):
        frappe.throw(_("Die Mandatsreferenz ist bereits bei einem anderen SEPA-Mandat eingetragen."))


def validate_active_mandate(doc) -> None:
    if doc.status != MANDATE_STATUS_ACTIVE:
        return
    required_fields = {
        "mandatsreferenz": _("Mandatsreferenz"),
        "mandatsdatum": _("Mandatsdatum"),
        "kontoinhaber": _("Kontoinhaber"),
        "iban": _("IBAN"),
        "bank": _("Bank"),
        "customer": _("Kunde"),
    }
    missing = [label for fieldname, label in required_fields.items() if not doc.get(fieldname)]
    if missing:
        frappe.throw(_("Fuer aktive SEPA-Mandate fehlen: {0}").format(", ".join(missing)))

    other_active = get_other_active_mandate(doc)
    replacement_for = doc.flags.get("erpverein_replacement_for")
    if other_active and other_active != replacement_for:
        frappe.throw(_("Fuer das Bezugsdokument existiert bereits ein aktives SEPA-Mandat."))


def get_other_active_mandate(doc) -> str | None:
    return frappe.db.get_value(
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


def validate_active_reference_is_not_orphaned(doc) -> None:
    previous_status = get_value_before_save(doc, "status")
    previous_doctype = get_value_before_save(doc, "bezugs_doctype")
    previous_name = get_value_before_save(doc, "bezugs_name")
    loses_active_reference = previous_status == MANDATE_STATUS_ACTIVE and (
        doc.status != MANDATE_STATUS_ACTIVE
        or previous_doctype != doc.bezugs_doctype
        or previous_name != doc.bezugs_name
    )
    if loses_active_reference:
        assert_reference_will_not_be_orphaned(previous_doctype, previous_name, doc.name)


def assert_reference_will_not_be_orphaned(doctype: str | None, name: str | None, mandate: str | None) -> None:
    if doctype not in {"Mitglied", "Mieter"} or not name or not frappe.db.exists(doctype, name):
        return
    if frappe.db.get_value(doctype, name, "abrechnungsart") != "Lastschrift":
        return
    replacement = frappe.db.get_value(
        "SEPA Mandat",
        {
            "bezugs_doctype": doctype,
            "bezugs_name": name,
            "status": MANDATE_STATUS_ACTIVE,
            "name": ("!=", mandate),
        },
        "name",
    )
    if not replacement:
        frappe.throw(_("Ein Lastschrift-Datensatz darf nicht ohne aktives SEPA-Mandat bleiben."))


def prepare_bank_account_for_mandat(doc) -> None:
    if doc.status != MANDATE_STATUS_ACTIVE:
        previous_status = get_value_before_save(doc, "status")
        previous_bank_account = get_value_before_save(doc, "bank_account")
        doc.bank_account = previous_bank_account or None
        if previous_status == MANDATE_STATUS_ACTIVE and previous_bank_account:
            disable_unused_managed_bank_account(previous_bank_account, excluding_mandate=doc.name, source_name=doc.name)
        return

    frappe.has_permission("Bank Account", ptype="read", throw=True)
    bank_account_name = get_existing_bank_account_for_mandat(doc)
    if bank_account_name:
        managed = bool(frappe.db.get_value("Bank Account", bank_account_name, BANK_ACCOUNT_MANAGED_FIELDNAME))
        if not managed:
            bank_account = frappe.get_doc("Bank Account", bank_account_name)
            ensure_bank_account_is_compatible(doc, bank_account)
            doc.bank_account = bank_account.name
            return
        frappe.db.get_value("Bank Account", bank_account_name, "name", for_update=True)
        bank_account = frappe.get_doc("Bank Account", bank_account_name)
        bank_account.check_permission("write")
        desired = get_desired_bank_account_fields(doc)
        preserve_shared_managed_account_values(doc, bank_account, desired)
        apply_managed_bank_account_fields(bank_account, desired, doc.name)
        save_managed_bank_account(bank_account)
        doc.bank_account = bank_account.name
        return

    frappe.has_permission("Bank Account", ptype="create", throw=True)
    bank_account = frappe.new_doc("Bank Account")
    desired = get_desired_bank_account_fields(doc)
    for fieldname, value in desired.items():
        if bank_account.meta.has_field(fieldname):
            bank_account.set(fieldname, value)
    state = new_bank_account_state(doc.name, desired)
    if bank_account.meta.has_field(BANK_ACCOUNT_SYNC_STATE_FIELDNAME):
        bank_account.set(BANK_ACCOUNT_SYNC_STATE_FIELDNAME, dump_bank_account_state(state))
    bank_account.flags.erpverein_sync = True
    bank_account.insert()
    doc.bank_account = bank_account.name


def get_existing_bank_account_for_mandat(doc) -> str | None:
    reference_changed = any(
        get_value_before_save(doc, fieldname) not in {None, doc.get(fieldname)}
        for fieldname in ["bezugs_doctype", "bezugs_name", "customer"]
    )
    previous = doc.get_doc_before_save()
    bank_identity_changed = bool(
        previous
        and any(
            normalize_bank_value(fieldname, previous.get(source_field))
            != normalize_bank_value(fieldname, doc.get(source_field))
            for source_field, fieldname in [("iban", "iban"), ("bic", "branch_code"), ("bank", "bank")]
        )
    )
    if (
        doc.bank_account
        and not reference_changed
        and not bank_identity_changed
        and frappe.db.exists("Bank Account", doc.bank_account)
    ):
        bank_account = frappe.get_doc("Bank Account", doc.bank_account)
        bank_account.check_permission("read")
        if bank_account.get(BANK_ACCOUNT_MANAGED_FIELDNAME):
            if not managed_bank_account_is_shared(doc, bank_account) or bank_account_values_are_compatible(
                doc, bank_account
            ):
                return bank_account.name
        if bank_account_values_are_compatible(doc, bank_account):
            return bank_account.name

    names = frappe.db.get_list(
        "Bank Account",
        filters={"party_type": "Customer", "party": doc.customer, "is_company_account": 0},
        pluck="name",
    )
    compatible = []
    for name in names:
        bank_account = frappe.get_doc("Bank Account", name)
        if bank_account_values_are_compatible(doc, bank_account):
            compatible.append(name)
    if len(compatible) > 1:
        frappe.throw(_("Mehrere kompatible Bankkonten fuer IBAN {0} gefunden.").format(frappe.bold(mask_iban(doc.iban))))
    if compatible:
        return compatible[0]
    return None


def managed_bank_account_is_shared(doc, bank_account) -> bool:
    return bool(
        frappe.db.get_value(
            "SEPA Mandat",
            {
                "bank_account": bank_account.name,
                "status": MANDATE_STATUS_ACTIVE,
                "name": ("!=", doc.name),
            },
            "name",
        )
    )


def bank_account_values_are_compatible(doc, bank_account) -> bool:
    expected = {
        "party_type": "Customer",
        "party": normalize_text(doc.customer),
        "iban": normalize_iban(doc.iban),
        "bank": normalize_text(doc.bank),
        "branch_code": normalize_bic(doc.bic),
    }
    current = {
        "party_type": normalize_text(bank_account.party_type),
        "party": normalize_text(bank_account.party),
        "iban": normalize_iban(bank_account.iban),
        "bank": normalize_text(bank_account.bank),
        "branch_code": normalize_bic(bank_account.branch_code),
    }
    return current == expected


def ensure_bank_account_is_compatible(doc, bank_account) -> None:
    if not bank_account_values_are_compatible(doc, bank_account):
        frappe.throw(
            _("Das ausgewaehlte Bankkonto ist nicht mit Kunde, IBAN, Bank und BIC des Mandats kompatibel.")
        )


def get_desired_bank_account_fields(doc) -> dict:
    account_identity = "|".join(
        cstr(value)
        for value in (
            doc.mandatsreferenz,
            doc.customer,
            normalize_iban(doc.iban),
            doc.bank,
            normalize_bic(doc.bic),
        )
    )
    account_suffix = hashlib.sha256(account_identity.encode("utf-8")).hexdigest()[:16]
    account_holder = cstr(doc.kontoinhaber or doc.customer)[:80]
    return {
        "account_name": f"{account_holder} ({account_suffix})",
        "bank": doc.bank,
        "party_type": "Customer",
        "party": doc.customer,
        "iban": doc.iban,
        "branch_code": doc.bic,
        "is_company_account": 0,
        "disabled": 0,
        BANK_ACCOUNT_MANAGED_FIELDNAME: 1,
    }


def preserve_shared_managed_account_values(doc, bank_account, desired: dict) -> None:
    if not frappe.db.get_value(
        "SEPA Mandat",
        {
            "bank_account": bank_account.name,
            "status": MANDATE_STATUS_ACTIVE,
            "name": ("!=", doc.name),
        },
        "name",
    ):
        return

    snapshots = load_bank_account_sync_state(bank_account).get("fields") or {}
    for fieldname, target in desired.items():
        if not bank_account.meta.has_field(fieldname):
            continue
        current = normalize_bank_value(fieldname, bank_account.get(fieldname))
        target = normalize_bank_value(fieldname, target)
        previous = normalize_bank_value(fieldname, snapshots.get(fieldname))
        if fieldname in snapshots and current != previous:
            frappe.throw(_("Ein gemeinsam genutztes Bankkonto wurde manuell geaendert und kann nicht synchronisiert werden."))
        if current != target:
            desired[fieldname] = bank_account.get(fieldname)


def apply_managed_bank_account_fields(bank_account, desired: dict, source_name: str | None) -> None:
    if not bank_account.get(BANK_ACCOUNT_MANAGED_FIELDNAME):
        frappe.throw(_("Ein nicht verwaltetes Bankkonto darf nicht aktualisiert werden."))
    state = load_bank_account_sync_state(bank_account)
    snapshots = state.setdefault("fields", {})
    for fieldname, value in desired.items():
        if not bank_account.meta.has_field(fieldname):
            continue
        current = normalize_bank_value(fieldname, bank_account.get(fieldname))
        target = normalize_bank_value(fieldname, value)
        has_snapshot = fieldname in snapshots
        previous = normalize_bank_value(fieldname, snapshots.get(fieldname))
        if has_snapshot and current != previous and current != target:
            frappe.throw(_("Das verwaltete Bankkonto wurde manuell geaendert und kann nicht synchronisiert werden."))
        if not has_snapshot and current not in {None, ""} and current != target:
            frappe.throw(_("Das verwaltete Bankkonto hat keinen sicheren Snapshot fuer diese Aenderung."))
        bank_account.set(fieldname, value)
        snapshots[fieldname] = value
    state["schema_version"] = BANK_ACCOUNT_STATE_SCHEMA_VERSION
    state["managed"] = True
    if source_name:
        state.setdefault("sources", {})[source_name] = {"doctype": "SEPA Mandat", "name": source_name}
    if bank_account.meta.has_field(BANK_ACCOUNT_SYNC_STATE_FIELDNAME):
        bank_account.set(BANK_ACCOUNT_SYNC_STATE_FIELDNAME, dump_bank_account_state(state))


def normalize_bank_value(fieldname: str, value):
    if fieldname == "iban":
        return normalize_iban(value)
    if fieldname == "branch_code":
        return normalize_bic(value)
    if fieldname in {"disabled", "is_company_account", BANK_ACCOUNT_MANAGED_FIELDNAME}:
        return int(value or 0)
    return normalize_text(value)


def new_bank_account_state(source_name: str, desired: dict) -> dict:
    return {
        "schema_version": BANK_ACCOUNT_STATE_SCHEMA_VERSION,
        "managed": True,
        "sources": {source_name: {"doctype": "SEPA Mandat", "name": source_name}},
        "fields": desired.copy(),
    }


def load_bank_account_sync_state(bank_account) -> dict:
    if not bank_account.meta.has_field(BANK_ACCOUNT_SYNC_STATE_FIELDNAME):
        return {"schema_version": BANK_ACCOUNT_STATE_SCHEMA_VERSION, "managed": True, "sources": {}, "fields": {}}
    raw_state = bank_account.get(BANK_ACCOUNT_SYNC_STATE_FIELDNAME)
    if not raw_state:
        return {"schema_version": BANK_ACCOUNT_STATE_SCHEMA_VERSION, "managed": True, "sources": {}, "fields": {}}
    try:
        state = json.loads(raw_state)
    except (TypeError, ValueError):
        frappe.throw(_("Der ERPverein Sync-Status des Bankkontos ist ungueltig."))
    if not isinstance(state, dict):
        frappe.throw(_("Der ERPverein Sync-Status des Bankkontos ist ungueltig."))
    if "fields" not in state:
        state = {
            "schema_version": BANK_ACCOUNT_STATE_SCHEMA_VERSION,
            "managed": True,
            "sources": {},
            "fields": state,
        }
    return state


def dump_bank_account_state(state: dict) -> str:
    return json.dumps(state, sort_keys=True, separators=(",", ":"))


def disable_unused_managed_bank_account(
    bank_account_name: str | None,
    *,
    excluding_mandate: str | None,
    source_name: str | None,
) -> None:
    if not bank_account_name or not frappe.db.exists("Bank Account", bank_account_name):
        return
    if not frappe.db.get_value("Bank Account", bank_account_name, BANK_ACCOUNT_MANAGED_FIELDNAME):
        return

    frappe.db.get_value("Bank Account", bank_account_name, "name", for_update=True)
    bank_account = frappe.get_doc("Bank Account", bank_account_name)
    if not bank_account.get(BANK_ACCOUNT_MANAGED_FIELDNAME):
        return
    bank_account.check_permission("write")
    state = load_bank_account_sync_state(bank_account)
    if source_name and source_name not in (state.get("sources") or {}):
        return
    in_use = frappe.db.get_value(
        "SEPA Mandat",
        {
            "bank_account": bank_account_name,
            "status": MANDATE_STATUS_ACTIVE,
            "name": ("!=", excluding_mandate),
        },
        "name",
    )
    desired = {} if in_use else {"disabled": 1}
    apply_managed_bank_account_fields(bank_account, desired, None)
    if source_name:
        state = load_bank_account_sync_state(bank_account)
        state.setdefault("sources", {}).pop(source_name, None)
        if bank_account.meta.has_field(BANK_ACCOUNT_SYNC_STATE_FIELDNAME):
            bank_account.set(BANK_ACCOUNT_SYNC_STATE_FIELDNAME, dump_bank_account_state(state))
    save_managed_bank_account(bank_account)


def sync_sepa_mandat(doc) -> None:
    sync_mandat_link_to_reference(doc)
    replacement_for = doc.flags.get("erpverein_replacement_for")
    if replacement_for:
        replaced = frappe.get_doc("SEPA Mandat", replacement_for)
        replaced.check_permission("write")
        replaced.flags.erpverein_replacement_transition = True
        replaced.status = MANDATE_STATUS_REPLACED
        replaced.save()
    clear_previous_reference_link(doc)

    previous_bank_account = get_value_before_save(doc, "bank_account")
    if previous_bank_account and previous_bank_account != doc.bank_account:
        disable_unused_managed_bank_account(
            previous_bank_account,
            excluding_mandate=doc.name,
            source_name=doc.name,
        )


def activate_replacement_mandate(mandate_name: str) -> dict:
    replacement = frappe.get_doc("SEPA Mandat", mandate_name)
    replacement.check_permission("write")
    if replacement.status != MANDATE_STATUS_DRAFT:
        frappe.throw(_("Nur ein SEPA-Mandat im Status Entwurf kann als Ersatz aktiviert werden."))

    lock_mandate_references(replacement)
    active_name = get_other_active_mandate(replacement)
    if not active_name:
        frappe.throw(_("Es existiert kein aktives SEPA-Mandat, das ersetzt werden kann."))
    if not frappe.has_permission("SEPA Mandat", "write", doc=active_name):
        frappe.throw(_("Das bestehende aktive SEPA-Mandat darf nicht ersetzt werden."), frappe.PermissionError)

    replacement.flags.erpverein_replacement_for = active_name
    replacement.status = MANDATE_STATUS_ACTIVE
    replacement.save()
    return {"mandate": replacement.name, "replaced": active_name}


def clear_previous_reference_link(doc) -> None:
    previous_doctype = get_value_before_save(doc, "bezugs_doctype")
    previous_name = get_value_before_save(doc, "bezugs_name")
    if previous_doctype and previous_name and (previous_doctype != doc.bezugs_doctype or previous_name != doc.bezugs_name):
        clear_reference_link(previous_doctype, previous_name, doc.name)


def sync_mandat_link_to_reference(doc) -> None:
    if doc.bezugs_doctype not in {"Mitglied", "Mieter"} or not doc.bezugs_name:
        return
    if doc.status == MANDATE_STATUS_ACTIVE:
        assert_reference_write_permission(doc.bezugs_doctype, doc.bezugs_name)
        set_value_if_changed(doc.bezugs_doctype, doc.bezugs_name, "sepa_mandat", doc.name)
    else:
        clear_reference_link(doc.bezugs_doctype, doc.bezugs_name, doc.name)


def clear_reference_link(doctype: str | None, name: str | None, mandate: str | None) -> None:
    if doctype not in {"Mitglied", "Mieter"} or not name or not mandate or not frappe.db.exists(doctype, name):
        return
    if frappe.db.get_value(doctype, name, "sepa_mandat") == mandate:
        assert_reference_write_permission(doctype, name)
        set_value_if_changed(doctype, name, "sepa_mandat", None)


def delete_sepa_mandat(doc) -> None:
    lock_mandate_references(doc)
    if doc.status == MANDATE_STATUS_ACTIVE:
        assert_reference_will_not_be_orphaned(doc.bezugs_doctype, doc.bezugs_name, doc.name)
    clear_reference_link(doc.bezugs_doctype, doc.bezugs_name, doc.name)
    disable_unused_managed_bank_account(doc.bank_account, excluding_mandate=doc.name, source_name=doc.name)


def set_value_if_changed(doctype: str, name: str, fieldname: str, value: str | None) -> None:
    current_value = frappe.db.get_value(doctype, name, fieldname) or None
    value = value or None
    if current_value != value:
        frappe.db.set_value(doctype, name, fieldname, value)


def save_managed_bank_account(bank_account) -> None:
    bank_account.flags.erpverein_sync = True
    bank_account.save()


def validate_bank_account_provenance(doc, method: str | None = None) -> None:
    if getattr(doc.flags, "erpverein_sync", False):
        return
    previous = doc.get_doc_before_save()
    protected_fields = (BANK_ACCOUNT_MANAGED_FIELDNAME, BANK_ACCOUNT_SYNC_STATE_FIELDNAME)
    if not previous:
        if any(doc.get(fieldname) for fieldname in protected_fields):
            frappe.throw(_("ERPverein-Bankkonto-Provenienz darf nicht manuell gesetzt werden."))
        return
    if any(previous.get(fieldname) != doc.get(fieldname) for fieldname in protected_fields):
        frappe.throw(_("ERPverein-Bankkonto-Provenienz darf nicht manuell geaendert werden."))


def assert_reference_write_permission(doctype: str | None, name: str | None) -> None:
    if doctype not in {"Mitglied", "Mieter"} or not name or skip_cross_link_permission_check():
        return
    frappe.get_doc(doctype, name).check_permission("write")


def skip_cross_link_permission_check() -> bool:
    return bool(frappe.flags.in_install or frappe.flags.in_patch or frappe.flags.in_migrate)


def get_value_before_save(doc, fieldname: str):
    if hasattr(doc, "get_value_before_save"):
        return doc.get_value_before_save(fieldname)
    previous = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
    return previous.get(fieldname) if previous else None
