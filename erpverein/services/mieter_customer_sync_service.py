import frappe

from erpverein.custom_fields import CUSTOMER_MIETER_FIELDNAME
from erpverein.services.customer_sync_service import (
    CUSTOMER_GROUP_INDIVIDUAL,
    CUSTOMER_TYPE_INDIVIDUAL,
    DEFAULT_COUNTRY,
    DEFAULT_CURRENCY,
    DEFAULT_TERRITORY,
    get_contact_salutation,
    get_existing_name,
    mark_customer_source_owned,
    normalize_sync_value,
    set_if_field_exists,
    sync_customer_records,
)


MIETER_CUSTOMER_SYNC_FIELDS = {"customer_name": lambda mieter: mieter.mieter_name}


def create_or_sync_customer_for_mieter(mieter_name: str) -> dict:
    mieter = frappe.get_doc("Mieter", mieter_name)
    mieter.check_permission("write")

    created = not mieter.customer
    if created:
        customer = make_customer_from_mieter(mieter)
        mark_customer_source_owned(customer, "Mieter", mieter.name, MIETER_CUSTOMER_SYNC_FIELDS, mieter)
        customer.insert()
    else:
        customer = frappe.get_doc("Customer", mieter.customer)
        customer.check_permission("write")

    sync_customer_from_mieter(mieter, customer=customer)
    mieter.reload()
    return {"mieter": mieter.name, "customer": mieter.customer or customer.name, "created": created}


def make_customer_from_mieter(mieter):
    customer = frappe.new_doc("Customer")
    customer_meta = frappe.get_meta("Customer")
    set_if_field_exists(customer, customer_meta, CUSTOMER_MIETER_FIELDNAME, mieter.name)
    set_if_field_exists(customer, customer_meta, "customer_name", mieter.mieter_name)
    set_if_field_exists(customer, customer_meta, "customer_type", CUSTOMER_TYPE_INDIVIDUAL)
    set_if_field_exists(customer, customer_meta, "customer_group", get_existing_name("Customer Group", CUSTOMER_GROUP_INDIVIDUAL))
    set_if_field_exists(customer, customer_meta, "territory", get_existing_name("Territory", DEFAULT_TERRITORY))
    set_if_field_exists(customer, customer_meta, "default_currency", get_existing_name("Currency", DEFAULT_CURRENCY))
    return customer


def sync_customer_from_mieter(mieter, customer=None, force_initial: bool = False) -> None:
    sync_customer_records(
        source=mieter,
        source_doctype="Mieter",
        customer=customer,
        customer_name=mieter.customer,
        customer_fields=MIETER_CUSTOMER_SYNC_FIELDS,
        desired_address=get_desired_address_fields(mieter),
        desired_contact_factory=lambda address: get_desired_contact_fields(mieter, address),
    )


def get_desired_address_fields(mieter) -> dict:
    return {
        "address_line1": normalize_sync_value(mieter.anschrift),
        "pincode": normalize_sync_value(mieter.plz),
        "city": normalize_sync_value(mieter.stadt),
        "country": normalize_sync_value(mieter.land) or DEFAULT_COUNTRY,
    }


def get_desired_contact_fields(mieter, address_name: str = "") -> dict:
    return {
        "first_name": normalize_sync_value(mieter.vorname),
        "middle_name": "",
        "last_name": normalize_sync_value(mieter.nachname),
        "salutation": get_contact_salutation(mieter.anrede),
        "email_id": normalize_sync_value(mieter.email),
        "mobile_no": normalize_sync_value(mieter.telefon),
        "address": normalize_sync_value(address_name),
        "is_billing_contact": "1",
    }
