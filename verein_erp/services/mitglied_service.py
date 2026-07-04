import frappe
from frappe import _
from frappe.utils import cstr, getdate


BILLING_TYPE_INVOICE = "Rechnung"
BILLING_TYPE_DIRECT_DEBIT = "Lastschrift"
CUSTOMER_MITGLIED_FIELDNAME = "mitglied"
MITGLIED_NAMING_SERIES = "MIT-.YYYY.-.#####"


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
    doc.mandat_id = normalize_text(doc.mandat_id)
    doc.abrechnungsart = normalize_text(doc.abrechnungsart) or BILLING_TYPE_INVOICE
    doc.mitglied_name = get_mitglied_display_name(doc.vorname, doc.nachname)

    if doc.name and not doc.name.startswith("New "):
        doc.mitglied_id = doc.name

    if doc.abrechnungsart not in {BILLING_TYPE_INVOICE, BILLING_TYPE_DIRECT_DEBIT}:
        frappe.throw(
            _("Abrechnungsart muss entweder {0} oder {1} sein.").format(
                frappe.bold(BILLING_TYPE_INVOICE), frappe.bold(BILLING_TYPE_DIRECT_DEBIT)
            )
        )


def validate_mitglied_dates(doc) -> None:
    if doc.geburtsdatum and doc.eintrittsdatum and getdate(doc.geburtsdatum) > getdate(doc.eintrittsdatum):
        frappe.throw(_("Geburtsdatum darf nicht nach dem Eintrittsdatum liegen."))

    if doc.eintrittsdatum and doc.austrittsdatum and getdate(doc.austrittsdatum) < getdate(doc.eintrittsdatum):
        frappe.throw(_("Austrittsdatum darf nicht vor dem Eintrittsdatum liegen."))


def validate_lastschrift_mandate(doc) -> None:
    if doc.mandat_id:
        other_mitglied = frappe.db.get_value(
            "Mitglied",
            {"mandat_id": doc.mandat_id, "name": ("!=", doc.name)},
            "name",
        )
        if other_mitglied:
            frappe.throw(
                _("Mandat ID {0} ist bereits bei Mitglied {1} eingetragen.").format(
                    frappe.bold(doc.mandat_id), frappe.bold(other_mitglied)
                )
            )

    if doc.abrechnungsart != BILLING_TYPE_DIRECT_DEBIT:
        return

    if not doc.mandat_id or not doc.mandatsdatum:
        frappe.throw(_("Mandat ID und Mandatsdatum sind fuer Lastschrift erforderlich."))


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
