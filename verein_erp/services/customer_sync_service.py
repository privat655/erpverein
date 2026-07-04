import json
from collections.abc import Callable

import frappe
from frappe import _
from frappe.utils import cstr

from verein_erp.custom_fields import CUSTOMER_MITGLIED_FIELDNAME, CUSTOMER_SYNC_STATE_FIELDNAME


CUSTOMER_GROUP_INDIVIDUAL = "Individual"
CUSTOMER_TYPE_INDIVIDUAL = "Individual"
DEFAULT_COUNTRY = "Germany"
DEFAULT_CURRENCY = "EUR"
DEFAULT_GENDER = "Male"
DEFAULT_TERRITORY = "Germany"

CUSTOMER_SYNC_FIELDS: dict[str, Callable] = {
    "customer_name": lambda mitglied: mitglied.mitglied_name,
    "image": lambda mitglied: mitglied.picture_url,
    "email_id": lambda mitglied: mitglied.email,
    "mobile_no": lambda mitglied: mitglied.telefon,
    "first_name": lambda mitglied: mitglied.vorname,
    "last_name": lambda mitglied: mitglied.nachname,
}


def create_or_sync_customer_for_mitglied(mitglied_name: str) -> dict:
    mitglied = frappe.get_doc("Mitglied", mitglied_name)
    mitglied.check_permission("write")

    customer_name = mitglied.customer
    created = False
    if not customer_name:
        customer = make_customer_from_mitglied(mitglied)
        customer.insert()
        customer_name = customer.name
        created = True
    else:
        customer = frappe.get_doc("Customer", customer_name)
        customer.check_permission("write")

    sync_customer_from_mitglied(mitglied, customer=customer, force_initial=created)
    mitglied.reload()

    return {"mitglied": mitglied.name, "customer": mitglied.customer or customer_name, "created": created}


def sync_unlinked_mitglieder() -> dict:
    names = get_unlinked_mitglied_names()
    result = {"total": len(names), "created": 0, "synced": 0, "errors": []}

    for name in names:
        try:
            sync_result = create_or_sync_customer_for_mitglied(name)
            if sync_result["created"]:
                result["created"] += 1
            else:
                result["synced"] += 1
        except Exception as exc:
            result["errors"].append({"mitglied": name, "error": cstr(exc)})

    return result


def get_unlinked_mitglied_names() -> list[str]:
    names = set(
        frappe.db.get_list(
            "Mitglied",
            filters={"customer": ["is", "not set"]},
            pluck="name",
            order_by="creation asc",
        )
    )
    names.update(
        frappe.db.get_list(
            "Mitglied",
            filters={"customer": ""},
            pluck="name",
            order_by="creation asc",
        )
    )
    return sorted(names)


def make_customer_from_mitglied(mitglied):
    customer = frappe.new_doc("Customer")
    customer_meta = frappe.get_meta("Customer")

    set_if_field_exists(customer, customer_meta, CUSTOMER_MITGLIED_FIELDNAME, mitglied.name)
    set_if_field_exists(customer, customer_meta, "customer_name", mitglied.mitglied_name)
    set_if_field_exists(customer, customer_meta, "customer_type", CUSTOMER_TYPE_INDIVIDUAL)
    set_if_field_exists(customer, customer_meta, "customer_group", get_existing_name("Customer Group", CUSTOMER_GROUP_INDIVIDUAL))
    set_if_field_exists(customer, customer_meta, "territory", get_existing_name("Territory", DEFAULT_TERRITORY))
    set_if_field_exists(customer, customer_meta, "gender", DEFAULT_GENDER)
    set_if_field_exists(customer, customer_meta, "default_currency", get_existing_name("Currency", DEFAULT_CURRENCY))

    for fieldname, getter in CUSTOMER_SYNC_FIELDS.items():
        set_if_field_exists(customer, customer_meta, fieldname, normalize_sync_value(getter(mitglied)))

    return customer


def sync_customer_from_mitglied(mitglied, customer=None, force_initial: bool = False) -> None:
    customer_name = customer.name if customer else mitglied.customer
    if not customer_name:
        return

    customer = customer or frappe.get_doc("Customer", customer_name)
    customer.check_permission("write")

    state = load_sync_state(customer)
    initial_customer_sync = force_initial or not state.get("customer_fields")
    changed = sync_customer_fields(mitglied, customer, state, force_initial=initial_customer_sync)
    changed = sync_customer_address(mitglied, customer, state, force_initial=force_initial) or changed

    if changed:
        customer.set(CUSTOMER_SYNC_STATE_FIELDNAME, dump_sync_state(state))
        customer.save()


def sync_customer_fields(mitglied, customer, state: dict, force_initial: bool = False) -> bool:
    customer_meta = frappe.get_meta("Customer")
    previous_values = state.setdefault("customer_fields", {})
    changed = False

    for fieldname, getter in CUSTOMER_SYNC_FIELDS.items():
        if not customer_meta.has_field(fieldname):
            continue

        desired = normalize_sync_value(getter(mitglied))
        current = normalize_sync_value(customer.get(fieldname))
        previous = normalize_sync_value(previous_values.get(fieldname))

        if force_initial or current == previous:
            if current != desired:
                customer.set(fieldname, desired)
                changed = True
            if force_initial and fieldname not in previous_values:
                previous_values[fieldname] = desired
                changed = True
            elif previous != desired:
                previous_values[fieldname] = desired
                changed = True

    return changed


def sync_customer_address(mitglied, customer, state: dict, force_initial: bool = False) -> bool:
    desired = get_desired_address_fields(mitglied)
    if not has_required_address_data(desired):
        return False

    address_state = state.setdefault("address", {})
    address_name = address_state.get("name") if frappe.db.exists("Address", address_state.get("name")) else None
    if not address_name:
        address_name = get_customer_primary_address(customer) or get_first_linked_customer_address(customer.name)

    if not address_name:
        address = create_customer_address(customer, desired)
        address_state["name"] = address.name
        address_state["fields"] = desired
        return set_customer_primary_address_if_allowed(customer, address.name, address_state, force_initial=True) or True

    address = frappe.get_doc("Address", address_name)
    address.check_permission("write")
    ensure_address_links_customer(address, customer.name)

    previous_fields = address_state.get("fields") or {}
    current_fields = get_address_sync_fields(address)
    can_update_address = force_initial or not previous_fields or current_fields == previous_fields
    changed = False

    if can_update_address:
        for fieldname, value in desired.items():
            if normalize_sync_value(address.get(fieldname)) != value:
                address.set(fieldname, value)
                changed = True
        if changed:
            address.save()
        if previous_fields != desired:
            address_state["fields"] = desired
            changed = True

    address_state["name"] = address.name
    changed = set_customer_primary_address_if_allowed(customer, address.name, address_state, force_initial=force_initial) or changed
    return changed


def create_customer_address(customer, desired: dict):
    address = frappe.new_doc("Address")
    address.address_title = customer.customer_name
    address.address_type = "Billing"
    for fieldname, value in desired.items():
        address.set(fieldname, value)
    address.append("links", {"link_doctype": "Customer", "link_name": customer.name})
    address.insert()
    return address


def ensure_address_links_customer(address, customer_name: str) -> None:
    for link in address.get("links") or []:
        if link.link_doctype == "Customer" and link.link_name == customer_name:
            return
    address.append("links", {"link_doctype": "Customer", "link_name": customer_name})
    address.save()


def set_customer_primary_address_if_allowed(customer, address_name: str, address_state: dict, force_initial: bool = False) -> bool:
    if not frappe.get_meta("Customer").has_field("customer_primary_address"):
        return False

    previous_primary = normalize_sync_value(address_state.get("customer_primary_address"))
    current_primary = normalize_sync_value(customer.get("customer_primary_address"))
    if not force_initial and previous_primary and current_primary != previous_primary:
        return False

    if current_primary != address_name:
        customer.set("customer_primary_address", address_name)
        address_state["customer_primary_address"] = address_name
        return True

    if previous_primary != address_name:
        address_state["customer_primary_address"] = address_name
        return True

    return False


def get_desired_address_fields(mitglied) -> dict:
    return {
        "address_line1": normalize_sync_value(mitglied.strasse),
        "pincode": normalize_sync_value(mitglied.plz),
        "city": normalize_sync_value(mitglied.ort),
        "country": normalize_sync_value(mitglied.land) or DEFAULT_COUNTRY,
    }


def has_required_address_data(address_fields: dict) -> bool:
    return bool(address_fields.get("address_line1") and address_fields.get("city") and address_fields.get("country"))


def get_address_sync_fields(address) -> dict:
    return {
        "address_line1": normalize_sync_value(address.address_line1),
        "pincode": normalize_sync_value(address.pincode),
        "city": normalize_sync_value(address.city),
        "country": normalize_sync_value(address.country),
    }


def get_customer_primary_address(customer) -> str | None:
    if frappe.get_meta("Customer").has_field("customer_primary_address"):
        return customer.get("customer_primary_address") or None
    return None


def get_first_linked_customer_address(customer_name: str) -> str | None:
    return frappe.db.get_value(
        "Dynamic Link",
        {
            "parenttype": "Address",
            "link_doctype": "Customer",
            "link_name": customer_name,
        },
        "parent",
    )


def load_sync_state(customer) -> dict:
    if not frappe.get_meta("Customer", cached=False).has_field(CUSTOMER_SYNC_STATE_FIELDNAME):
        return {}

    raw_state = customer.get(CUSTOMER_SYNC_STATE_FIELDNAME)
    if not raw_state:
        return {}

    try:
        state = json.loads(raw_state)
    except (TypeError, ValueError):
        frappe.throw(_("Customer {0} hat einen ungueltigen Verein ERP Sync State.").format(frappe.bold(customer.name)))

    return state if isinstance(state, dict) else {}


def dump_sync_state(state: dict) -> str:
    return json.dumps(state, sort_keys=True, separators=(",", ":"))


def set_if_field_exists(doc, meta, fieldname: str, value) -> None:
    if meta.has_field(fieldname):
        doc.set(fieldname, value)


def get_existing_name(doctype: str, preferred_name: str) -> str:
    if frappe.db.exists(doctype, preferred_name):
        return preferred_name
    return preferred_name


def normalize_sync_value(value) -> str:
    return cstr(value).strip()
