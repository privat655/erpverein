import frappe
from frappe import _
from frappe.utils import cstr, flt, getdate

from verein_erp.services.sepa_mandat_service import (
    MANDATE_CATEGORY_MEMBERSHIP,
    MANDATE_STATUS_ACTIVE,
)


BILLING_TYPE_INVOICE = "Rechnung"
BILLING_TYPE_DIRECT_DEBIT = "Lastschrift"
BILLING_TYPE_COVERED = "Beitrag wird uebernommen"
BILLING_TYPE_FREE = "Beitragsfrei"
DEFAULT_ANNUAL_FEE = 350
CUSTOMER_MITGLIED_FIELDNAME = "mitglied"
MITGLIED_NAMING_SERIES = "MIT-.YYYY.-.#####"
BILLING_TYPES = {
    BILLING_TYPE_INVOICE,
    BILLING_TYPE_DIRECT_DEBIT,
    BILLING_TYPE_COVERED,
    BILLING_TYPE_FREE,
}


def normalize_text(value: object) -> str | None:
    value = " ".join(cstr(value).strip().split())
    return value or None


def normalize_email(value: object) -> str | None:
    value = normalize_text(value)
    return value.lower() if value else None


def get_mitglied_display_name(vorname: object, nachname: object) -> str:
    return " ".join(part for part in [normalize_text(vorname), normalize_text(nachname)] if part)


def validate_mitglied(doc) -> None:
    normalize_mitglied(doc)
    validate_mitglied_dates(doc)
    validate_lastschrift_mandate(doc)
    validate_billing(doc)
    validate_mitglied_customer_link(doc)


def normalize_mitglied(doc) -> None:
    doc.vorname = normalize_text(doc.vorname)
    doc.nachname = normalize_text(doc.nachname)
    doc.strasse = normalize_text(doc.strasse)
    doc.plz = normalize_text(doc.plz)
    doc.ort = normalize_text(doc.ort)
    doc.land = normalize_text(doc.land)
    doc.email = normalize_email(doc.email)
    doc.telefon = normalize_text(doc.telefon)
    doc.sepa_mandat = normalize_text(doc.get("sepa_mandat"))
    doc.beitragszahler = normalize_text(doc.get("beitragszahler"))
    doc.abrechnungsart = normalize_text(doc.abrechnungsart) or BILLING_TYPE_INVOICE
    doc.mitglied_name = get_mitglied_display_name(doc.vorname, doc.nachname)
    normalize_annual_fee(doc)

    if doc.name and not doc.name.startswith("New "):
        doc.mitglied_id = doc.name


def normalize_annual_fee(doc) -> None:
    if doc.abrechnungsart == BILLING_TYPE_FREE:
        doc.jahresbeitrag = 0
        return

    if doc.get("jahresbeitrag") in {None, ""}:
        doc.jahresbeitrag = DEFAULT_ANNUAL_FEE


def validate_mitglied_dates(doc) -> None:
    if doc.geburtsdatum and doc.eintrittsdatum and getdate(doc.geburtsdatum) > getdate(doc.eintrittsdatum):
        frappe.throw(_("Geburtsdatum darf nicht nach dem Eintrittsdatum liegen."))

    if doc.eintrittsdatum and doc.austrittsdatum and getdate(doc.austrittsdatum) < getdate(doc.eintrittsdatum):
        frappe.throw(_("Austrittsdatum darf nicht vor dem Eintrittsdatum liegen."))


def validate_billing(doc) -> None:
    if doc.abrechnungsart not in BILLING_TYPES:
        frappe.throw(_("Ungueltige Abrechnungsart: {0}").format(frappe.bold(doc.abrechnungsart)))

    annual_fee = flt(doc.jahresbeitrag)
    if annual_fee < 0:
        frappe.throw(_("Jahresbeitrag darf nicht negativ sein."))

    if doc.abrechnungsart != BILLING_TYPE_FREE and annual_fee <= 0:
        frappe.throw(_("Jahresbeitrag ist fuer diese Abrechnungsart erforderlich."))

    if doc.abrechnungsart == BILLING_TYPE_FREE:
        if doc.sepa_mandat or doc.beitragszahler:
            frappe.throw(_("Beitragsfreie Mitglieder duerfen kein SEPA Mandat und keinen Beitragszahler haben."))
        return

    if doc.abrechnungsart == BILLING_TYPE_COVERED:
        validate_contributing_member(doc)
    elif doc.beitragszahler:
        frappe.throw(_("Beitragszahler darf nur bei 'Beitrag wird uebernommen' gesetzt sein."))


def validate_contributing_member(doc) -> None:
    if not doc.beitragszahler:
        frappe.throw(_("Beitragszahler ist erforderlich, wenn der Beitrag uebernommen wird."))

    if doc.beitragszahler == doc.name:
        frappe.throw(_("Ein Mitglied kann nicht sein eigener Beitragszahler sein."))

    payer = frappe.db.get_value("Mitglied", doc.beitragszahler, ["abrechnungsart"], as_dict=True)
    if not payer:
        frappe.throw(_("Beitragszahler {0} existiert nicht.").format(frappe.bold(doc.beitragszahler)))

    if payer.abrechnungsart == BILLING_TYPE_COVERED:
        frappe.throw(_("Beitragszahler darf nicht selbst 'Beitrag wird uebernommen' haben."))

    if payer.abrechnungsart == BILLING_TYPE_FREE:
        frappe.throw(_("Beitragszahler darf nicht beitragsfrei sein."))


def validate_lastschrift_mandate(doc) -> None:
    active_mandat = get_active_sepa_mandat_for_mitglied(doc.name)
    if active_mandat and not doc.sepa_mandat:
        doc.sepa_mandat = active_mandat

    if doc.sepa_mandat:
        validate_sepa_mandat_link(doc, doc.sepa_mandat)

    if doc.abrechnungsart != BILLING_TYPE_DIRECT_DEBIT:
        return

    if not doc.sepa_mandat:
        frappe.throw(_("Ein aktives SEPA Mandat ist fuer Lastschrift erforderlich."))


def get_active_sepa_mandat_for_mitglied(mitglied: str | None) -> str | None:
    if not mitglied:
        return None

    return frappe.db.get_value(
        "SEPA Mandat",
        {
            "mandatskategorie": MANDATE_CATEGORY_MEMBERSHIP,
            "bezugs_doctype": "Mitglied",
            "bezugs_name": mitglied,
            "status": MANDATE_STATUS_ACTIVE,
        },
        "name",
    )


def validate_sepa_mandat_link(doc, sepa_mandat: str) -> None:
    mandate = frappe.db.get_value(
        "SEPA Mandat",
        sepa_mandat,
        ["status", "mandatskategorie", "bezugs_doctype", "bezugs_name"],
        as_dict=True,
    )
    if not mandate:
        frappe.throw(_("SEPA Mandat {0} existiert nicht.").format(frappe.bold(sepa_mandat)))

    if (
        mandate.status != MANDATE_STATUS_ACTIVE
        or mandate.mandatskategorie != MANDATE_CATEGORY_MEMBERSHIP
        or mandate.bezugs_doctype != "Mitglied"
        or mandate.bezugs_name != doc.name
    ):
        frappe.throw(_("SEPA Mandat {0} ist kein aktives Mandat fuer dieses Mitglied.").format(frappe.bold(sepa_mandat)))


def validate_mitglied_customer_link(doc) -> None:
    previous_customer = get_value_before_save(doc, "customer")
    current_customer = doc.customer or None

    if previous_customer and previous_customer != current_customer:
        assert_customer_write_permission(previous_customer)

    if not current_customer:
        return

    assert_customer_write_permission(current_customer)

    other_mitglied = frappe.db.get_value(
        "Mitglied",
        {"customer": current_customer, "name": ("!=", doc.name)},
        "name",
    )
    if other_mitglied:
        frappe.throw(
            _("Customer {0} ist bereits mit Mitglied {1} verknuepft.").format(
                frappe.bold(current_customer), frappe.bold(other_mitglied)
            )
        )

    if not customer_has_mitglied_field():
        return

    linked_mitglied = frappe.db.get_value("Customer", current_customer, CUSTOMER_MITGLIED_FIELDNAME)
    if linked_mitglied and linked_mitglied != doc.name:
        frappe.throw(
            _("Customer {0} verweist bereits auf Mitglied {1}.").format(
                frappe.bold(current_customer), frappe.bold(linked_mitglied)
            )
        )


def validate_customer_membership_link(doc, method: str | None = None) -> None:
    if not customer_has_mitglied_field():
        return

    mitglied = doc.get(CUSTOMER_MITGLIED_FIELDNAME)
    previous_mitglied = get_value_before_save(doc, CUSTOMER_MITGLIED_FIELDNAME)

    if previous_mitglied and previous_mitglied != mitglied:
        assert_mitglied_write_permission(previous_mitglied)

    if not mitglied:
        return

    assert_mitglied_write_permission(mitglied)

    linked_customer = frappe.db.get_value("Mitglied", mitglied, "customer")
    if linked_customer and linked_customer != doc.name:
        frappe.throw(
            _("Mitglied {0} ist bereits mit Customer {1} verknuepft.").format(
                frappe.bold(mitglied), frappe.bold(linked_customer)
            )
        )

    other_customer = frappe.db.get_value(
        "Customer",
        {CUSTOMER_MITGLIED_FIELDNAME: mitglied, "name": ("!=", doc.name)},
        "name",
    )
    if other_customer:
        frappe.throw(
            _("Mitglied {0} ist bereits bei Customer {1} eingetragen.").format(
                frappe.bold(mitglied), frappe.bold(other_customer)
            )
        )


def sync_mitglied_to_customer(doc) -> None:
    if not customer_has_mitglied_field():
        return

    previous_customer = get_value_before_save(doc, "customer")
    current_customer = doc.customer or None

    if previous_customer and previous_customer != current_customer:
        clear_customer_mitglied_if_current(previous_customer, doc.name)

    if current_customer:
        set_value_if_changed("Customer", current_customer, CUSTOMER_MITGLIED_FIELDNAME, doc.name)
        from verein_erp.services.customer_sync_service import sync_customer_from_mitglied

        sync_customer_from_mitglied(doc)


def sync_customer_to_mitglied(doc, method: str | None = None) -> None:
    if not customer_has_mitglied_field():
        return

    previous_mitglied = get_value_before_save(doc, CUSTOMER_MITGLIED_FIELDNAME)
    current_mitglied = doc.get(CUSTOMER_MITGLIED_FIELDNAME) or None

    if previous_mitglied and previous_mitglied != current_mitglied:
        clear_mitglied_customer_if_current(previous_mitglied, doc.name)

    if current_mitglied:
        set_value_if_changed("Mitglied", current_mitglied, "customer", doc.name)


def clear_mitglied_link_from_customer(doc) -> None:
    if not customer_has_mitglied_field():
        return

    customer = doc.customer or frappe.db.get_value("Customer", {CUSTOMER_MITGLIED_FIELDNAME: doc.name}, "name")
    if customer:
        assert_customer_write_permission(customer)
        clear_customer_mitglied_if_current(customer, doc.name)


def clear_customer_link_from_mitglied(doc, method: str | None = None) -> None:
    if not customer_has_mitglied_field():
        return

    mitglied = doc.get(CUSTOMER_MITGLIED_FIELDNAME)
    if mitglied:
        assert_mitglied_write_permission(mitglied)
        clear_mitglied_customer_if_current(mitglied, doc.name)


def clear_customer_mitglied_if_current(customer: str, mitglied: str) -> None:
    if not customer or not mitglied or not frappe.db.exists("Customer", customer):
        return

    current_mitglied = frappe.db.get_value("Customer", customer, CUSTOMER_MITGLIED_FIELDNAME)
    if current_mitglied == mitglied:
        set_value_if_changed("Customer", customer, CUSTOMER_MITGLIED_FIELDNAME, None)


def clear_mitglied_customer_if_current(mitglied: str, customer: str) -> None:
    if not customer or not mitglied or not frappe.db.exists("Mitglied", mitglied):
        return

    current_customer = frappe.db.get_value("Mitglied", mitglied, "customer")
    if current_customer == customer:
        set_value_if_changed("Mitglied", mitglied, "customer", None)


def set_value_if_changed(doctype: str, name: str, fieldname: str, value: str | None) -> None:
    current_value = frappe.db.get_value(doctype, name, fieldname) or None
    value = value or None
    if current_value != value:
        frappe.db.set_value(doctype, name, fieldname, value)


def assert_customer_write_permission(customer: str) -> None:
    if skip_cross_link_permission_check():
        return

    frappe.get_doc("Customer", customer).check_permission("write")


def assert_mitglied_write_permission(mitglied: str) -> None:
    if skip_cross_link_permission_check():
        return

    frappe.get_doc("Mitglied", mitglied).check_permission("write")


def skip_cross_link_permission_check() -> bool:
    return bool(frappe.flags.in_install or frappe.flags.in_patch or frappe.flags.in_migrate)


def get_value_before_save(doc, fieldname: str):
    if hasattr(doc, "get_value_before_save"):
        return doc.get_value_before_save(fieldname)

    previous = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
    return previous.get(fieldname) if previous else None


def customer_has_mitglied_field() -> bool:
    return bool(frappe.get_meta("Customer", cached=False).has_field(CUSTOMER_MITGLIED_FIELDNAME))
