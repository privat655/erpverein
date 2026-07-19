import frappe
from frappe import _
from frappe.utils import cstr, getdate

from erpverein.custom_fields import CUSTOMER_MIETER_FIELDNAME
from erpverein.services.sepa_mandat_service import MANDATE_STATUS_ACTIVE


BILLING_TYPE_INVOICE = "Rechnung"
BILLING_TYPE_DIRECT_DEBIT = "Lastschrift"
MIETER_NAMING_SERIES = "MIE-.YYYY.-.#####"
BILLING_TYPES = {BILLING_TYPE_INVOICE, BILLING_TYPE_DIRECT_DEBIT}
SALUTATIONS = {"Herr", "Frau"}


def normalize_text(value: object) -> str | None:
    value = " ".join(cstr(value).strip().split())
    return value or None


def normalize_email(value: object) -> str | None:
    value = normalize_text(value)
    return value.lower() if value else None


def get_mieter_display_name(vorname: object, nachname: object) -> str:
    return " ".join(part for part in [normalize_text(vorname), normalize_text(nachname)] if part)


def validate_mieter(doc) -> None:
    normalize_mieter(doc)
    validate_mieter_dates(doc)
    validate_mieter_billing(doc)
    validate_sepa_mandat_link_if_set(doc)
    validate_mieter_customer_link(doc)


def normalize_mieter(doc) -> None:
    doc.fremd_id = normalize_text(doc.get("fremd_id"))
    doc.anrede = normalize_text(doc.get("anrede"))
    doc.vorname = normalize_text(doc.vorname)
    doc.nachname = normalize_text(doc.nachname)
    doc.anschrift = normalize_text(doc.get("anschrift"))
    doc.plz = normalize_text(doc.get("plz"))
    doc.stadt = normalize_text(doc.get("stadt"))
    doc.land = normalize_text(doc.get("land"))
    doc.email = normalize_email(doc.get("email"))
    doc.telefon = normalize_text(doc.get("telefon"))
    doc.abrechnungsart = normalize_text(doc.abrechnungsart) or BILLING_TYPE_INVOICE
    doc.sepa_mandat = normalize_text(doc.get("sepa_mandat"))
    doc.mieter_name = get_mieter_display_name(doc.vorname, doc.nachname)

    if doc.name and not doc.name.startswith("New "):
        doc.mieter_id = doc.name


def validate_mieter_dates(doc) -> None:
    if doc.mietbeginn and doc.mietende and getdate(doc.mietende) < getdate(doc.mietbeginn):
        frappe.throw(_("Mietende darf nicht vor dem Mietbeginn liegen."))


def validate_mieter_billing(doc) -> None:
    if doc.anrede and doc.anrede not in SALUTATIONS:
        frappe.throw(_("Ungueltige Anrede: {0}").format(frappe.bold(doc.anrede)))

    if doc.abrechnungsart not in BILLING_TYPES:
        frappe.throw(_("Ungueltige Abrechnungsart: {0}").format(frappe.bold(doc.abrechnungsart)))


def validate_sepa_mandat_link_if_set(doc) -> None:
    active_mandat = get_active_sepa_mandat_for_mieter(doc.name)
    if active_mandat and not doc.sepa_mandat:
        doc.sepa_mandat = active_mandat

    if not doc.sepa_mandat:
        if doc.abrechnungsart == BILLING_TYPE_DIRECT_DEBIT:
            frappe.throw(_("Ein aktives SEPA-Mandat ist fuer Lastschrift erforderlich."))
        return

    mandate = frappe.db.get_value(
        "SEPA Mandat",
        doc.sepa_mandat,
        ["status", "bezugs_doctype", "bezugs_name"],
        as_dict=True,
    )
    if not mandate:
        frappe.throw(_("SEPA-Mandat {0} existiert nicht.").format(frappe.bold(doc.sepa_mandat)))

    if (
        mandate.status != MANDATE_STATUS_ACTIVE
        or mandate.bezugs_doctype != "Mieter"
        or mandate.bezugs_name != doc.name
    ):
        frappe.throw(_("SEPA-Mandat {0} ist kein aktives Mandat fuer diesen Mieter.").format(frappe.bold(doc.sepa_mandat)))


def get_active_sepa_mandat_for_mieter(mieter: str | None) -> str | None:
    if not mieter:
        return None

    return frappe.db.get_value(
        "SEPA Mandat",
        {
            "bezugs_doctype": "Mieter",
            "bezugs_name": mieter,
            "status": MANDATE_STATUS_ACTIVE,
        },
        "name",
    )


def validate_mieter_customer_link(doc) -> None:
    previous_customer = get_value_before_save(doc, "customer")
    current_customer = doc.customer or None

    if previous_customer == current_customer:
        if current_customer and source_sync_fields_changed(
            doc,
            ["vorname", "nachname", "anschrift", "plz", "stadt", "land", "email", "telefon", "anrede"],
        ):
            from erpverein.services.mieter_customer_sync_service import sync_customer_from_mieter

            sync_customer_from_mieter(doc)
        return

    if get_active_sepa_mandat_for_mieter(doc.name):
        frappe.throw(_("Der Kunde kann bei einem aktiven SEPA-Mandat nicht geaendert werden."))

    if previous_customer:
        assert_customer_write_permission(previous_customer)

    if not current_customer:
        return

    assert_customer_write_permission(current_customer)

    other_mieter = frappe.db.get_value(
        "Mieter",
        {"customer": current_customer, "name": ("!=", doc.name)},
        "name",
    )
    if other_mieter:
        frappe.throw(_("Der ausgewaehlte Kunde ist bereits mit einem anderen Mieter verknuepft."))

    if not customer_has_mieter_field():
        return

    linked_mieter = frappe.db.get_value("Customer", current_customer, CUSTOMER_MIETER_FIELDNAME)
    if linked_mieter and linked_mieter != doc.name:
        frappe.throw(_("Der ausgewaehlte Kunde verweist bereits auf einen anderen Mieter."))


def validate_customer_rental_link(doc, method: str | None = None) -> None:
    if not customer_has_mieter_field():
        return

    mieter = doc.get(CUSTOMER_MIETER_FIELDNAME)
    previous_mieter = get_value_before_save(doc, CUSTOMER_MIETER_FIELDNAME)
    lock_mieter(previous_mieter, mieter)

    if previous_mieter == mieter:
        return

    if previous_mieter and get_active_sepa_mandat_for_mieter(previous_mieter):
        frappe.throw(_("Die Kundenverknuepfung kann bei einem aktiven SEPA-Mandat nicht geaendert werden."))

    if previous_mieter:
        assert_mieter_write_permission(previous_mieter)

    if not mieter:
        return

    assert_mieter_write_permission(mieter)

    linked_customer = frappe.db.get_value("Mieter", mieter, "customer")
    if linked_customer and linked_customer != doc.name:
        frappe.throw(_("Der ausgewaehlte Mieter ist bereits mit einem anderen Kunden verknuepft."))

    other_customer = frappe.db.get_value(
        "Customer",
        {CUSTOMER_MIETER_FIELDNAME: mieter, "name": ("!=", doc.name)},
        "name",
    )
    if other_customer:
        frappe.throw(_("Der ausgewaehlte Mieter ist bereits bei einem anderen Kunden eingetragen."))


def sync_mieter_to_customer(doc) -> None:
    if not customer_has_mieter_field():
        return

    previous_customer = get_value_before_save(doc, "customer")
    current_customer = doc.customer or None

    if previous_customer == current_customer:
        return

    if previous_customer and previous_customer != current_customer:
        clear_customer_mieter_if_current(previous_customer, doc.name)

    if current_customer:
        set_value_if_changed("Customer", current_customer, CUSTOMER_MIETER_FIELDNAME, doc.name)
        from erpverein.services.mieter_customer_sync_service import sync_customer_from_mieter

        sync_customer_from_mieter(doc)


def sync_customer_to_mieter(doc, method: str | None = None) -> None:
    if not customer_has_mieter_field():
        return

    previous_mieter = get_value_before_save(doc, CUSTOMER_MIETER_FIELDNAME)
    current_mieter = doc.get(CUSTOMER_MIETER_FIELDNAME) or None

    if previous_mieter == current_mieter:
        return

    if previous_mieter and previous_mieter != current_mieter:
        clear_mieter_customer_if_current(previous_mieter, doc.name)

    if current_mieter:
        set_value_if_changed("Mieter", current_mieter, "customer", doc.name)


def clear_mieter_link_from_customer(doc) -> None:
    if not customer_has_mieter_field():
        return

    customer = doc.customer or frappe.db.get_value("Customer", {CUSTOMER_MIETER_FIELDNAME: doc.name}, "name")
    if customer and frappe.db.get_value("Customer", customer, CUSTOMER_MIETER_FIELDNAME) == doc.name:
        assert_customer_write_permission(customer)
        clear_customer_mieter_if_current(customer, doc.name)


def clear_customer_link_from_mieter(doc, method: str | None = None) -> None:
    if not customer_has_mieter_field():
        return

    mieter = doc.get(CUSTOMER_MIETER_FIELDNAME)
    lock_mieter(mieter)
    if mieter and frappe.db.get_value("Mieter", mieter, "customer") == doc.name:
        if get_active_sepa_mandat_for_mieter(mieter):
            frappe.throw(_("Ein Kunde mit aktivem SEPA-Mandat kann nicht geloescht werden."))
        assert_mieter_write_permission(mieter)
        clear_mieter_customer_if_current(mieter, doc.name)


def clear_customer_mieter_if_current(customer: str, mieter: str) -> None:
    if not customer or not mieter or not frappe.db.exists("Customer", customer):
        return

    current_mieter = frappe.db.get_value("Customer", customer, CUSTOMER_MIETER_FIELDNAME)
    if current_mieter == mieter:
        set_value_if_changed("Customer", customer, CUSTOMER_MIETER_FIELDNAME, None)


def clear_mieter_customer_if_current(mieter: str, customer: str) -> None:
    if not customer or not mieter or not frappe.db.exists("Mieter", mieter):
        return

    current_customer = frappe.db.get_value("Mieter", mieter, "customer")
    if current_customer == customer:
        set_value_if_changed("Mieter", mieter, "customer", None)


def set_value_if_changed(doctype: str, name: str, fieldname: str, value: str | None) -> None:
    current_value = frappe.db.get_value(doctype, name, fieldname) or None
    value = value or None
    if current_value != value:
        frappe.db.set_value(doctype, name, fieldname, value)


def assert_customer_write_permission(customer: str) -> None:
    if skip_cross_link_permission_check():
        return

    frappe.get_doc("Customer", customer).check_permission("write")


def assert_mieter_write_permission(mieter: str) -> None:
    if skip_cross_link_permission_check():
        return

    frappe.get_doc("Mieter", mieter).check_permission("write")


def skip_cross_link_permission_check() -> bool:
    return bool(frappe.flags.in_install or frappe.flags.in_patch or frappe.flags.in_migrate)


def get_value_before_save(doc, fieldname: str):
    if hasattr(doc, "get_value_before_save"):
        return doc.get_value_before_save(fieldname)

    previous = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
    return previous.get(fieldname) if previous else None


def customer_has_mieter_field() -> bool:
    return bool(frappe.get_meta("Customer", cached=False).has_field(CUSTOMER_MIETER_FIELDNAME))


def lock_mieter(*names: str | None) -> None:
    for name in sorted({name for name in names if name}):
        frappe.db.get_value("Mieter", name, "name", for_update=True)


def source_sync_fields_changed(doc, fieldnames: list[str]) -> bool:
    previous = doc.get_doc_before_save()
    return bool(previous and any(previous.get(fieldname) != doc.get(fieldname) for fieldname in fieldnames))
