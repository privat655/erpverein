import re

import frappe
from frappe import _
from frappe.utils import cint, cstr


DEFAULT_PRENOTIFICATION_DAYS = 14
CREDITOR_IDENTIFIER_PATTERN = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{3}[A-Z0-9]{1,28}$")
VERSIONED_FIELDS = (
    "company",
    "bank_account",
    "creditor_identifier",
    "scheme",
    "export_format",
    "currency",
    "go_live_date",
    "prenotification_days",
    "lead_time_business_days",
    "batch_booking_default",
    "settlement_tolerance_days",
    "max_positions_per_file",
    "default_purpose_template",
    "default_fee_account",
)
MINIMUM_VALUES = (
    ("prenotification_days", 1, "Frist der Vorabankuendigung"),
    ("lead_time_business_days", 1, "Vorlauf"),
    ("max_positions_per_file", 1, "Hoechstzahl Positionen je Datei"),
)
RECOMMENDED_ACCOUNTS_SETTINGS = (
    ("enable_party_matching", 1, "Partei ueber IBAN erkennen (enable_party_matching) muss eingeschaltet sein."),
    ("enable_fuzzy_matching", 0, "Unscharfer Partei-Abgleich (enable_fuzzy_matching) muss ausgeschaltet sein."),
    (
        "automatically_run_rules_on_unreconciled_transactions",
        1,
        "Regeln fuer offene Bankumsaetze automatisch ausfuehren "
        "(automatically_run_rules_on_unreconciled_transactions) muss eingeschaltet sein.",
    ),
    ("auto_reconcile_payments", 0, "Zahlungen automatisch verteilen (auto_reconcile_payments) muss ausgeschaltet sein."),
)


def normalize_creditor_identifier(value: object) -> str | None:
    value = "".join(cstr(value).split()).upper()
    return value or None


def is_valid_creditor_identifier(value: object) -> bool:
    value = normalize_creditor_identifier(value)
    if not value or not CREDITOR_IDENTIFIER_PATTERN.match(value):
        return False
    rearranged = value[7:] + value[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1


def validate_sepa_export_profile(doc) -> None:
    doc.creditor_identifier = normalize_creditor_identifier(doc.creditor_identifier)
    if not is_valid_creditor_identifier(doc.creditor_identifier):
        frappe.throw(_("Die Glaeubiger-ID ist ungueltig."))
    for fieldname, minimum, label in MINIMUM_VALUES:
        if cint(doc.get(fieldname)) < minimum:
            frappe.throw(_("{0} muss mindestens {1} sein.").format(_(label), minimum))
    validate_company_bank_account(doc)
    validate_single_active_profile(doc)
    set_profile_version(doc)


def validate_company_bank_account(doc) -> None:
    values = frappe.db.get_value(
        "Bank Account", doc.bank_account, ["is_company_account", "company", "account", "disabled"], as_dict=True
    )
    if not values or not cint(values.is_company_account):
        frappe.throw(_("Das Vereinskonto muss ein Firmen-Bankkonto sein."))
    if values.company != doc.company:
        frappe.throw(_("Das Vereinskonto gehoert nicht zum gewaehlten Unternehmen."))
    if not values.account:
        frappe.throw(_("Das Vereinskonto braucht ein Hauptbuchkonto."))
    if cint(values.disabled):
        frappe.throw(_("Das Vereinskonto ist deaktiviert."))


def validate_single_active_profile(doc) -> None:
    if not cint(doc.active):
        return
    other = frappe.db.get_value(
        "SEPA Exportprofil", {"company": doc.company, "active": 1, "name": ("!=", doc.name)}, "name"
    )
    if other:
        frappe.throw(_("Fuer dieses Unternehmen ist bereits das Exportprofil {0} aktiv.").format(frappe.bold(other)))


def set_profile_version(doc) -> None:
    previous = doc.get_doc_before_save()
    if not previous:
        doc.profile_version = cint(doc.profile_version) or 1
        return
    if any(previous.get(fieldname) != doc.get(fieldname) for fieldname in VERSIONED_FIELDS):
        doc.profile_version = cint(previous.profile_version) + 1


def get_active_export_profile(company: str | None) -> frappe._dict | None:
    if not company:
        return None
    rows = frappe.get_all(
        "SEPA Exportprofil",
        filters={"company": company, "active": 1},
        fields=["name", "creditor_identifier", "bank_account", "prenotification_days", "go_live_date"],
        limit_page_length=1,
    )
    return rows[0] if rows else None


def get_prenotification_days(company: str | None) -> int:
    profile = get_active_export_profile(company)
    return cint(profile.prenotification_days) if profile else DEFAULT_PRENOTIFICATION_DAYS


def get_accounts_settings_warnings() -> list[str]:
    return [
        _(message)
        for fieldname, expected, message in RECOMMENDED_ACCOUNTS_SETTINGS
        if cint(frappe.db.get_single_value("Accounts Settings", fieldname)) != expected
    ]
