import json
from collections.abc import Callable

import frappe
from frappe import _
from frappe.utils import cstr

from erpverein.custom_fields import CUSTOMER_MITGLIED_FIELDNAME, CUSTOMER_SYNC_STATE_FIELDNAME


CUSTOMER_GROUP_INDIVIDUAL = "Individual"
CUSTOMER_TYPE_INDIVIDUAL = "Individual"
DEFAULT_COUNTRY = "Germany"
DEFAULT_CURRENCY = "EUR"
DEFAULT_GENDER = "Male"
DEFAULT_TERRITORY = "Germany"
SYNC_STATE_SCHEMA_VERSION = 2

CUSTOMER_SYNC_FIELDS: dict[str, Callable] = {
    "customer_name": lambda mitglied: mitglied.mitglied_name,
    "image": lambda mitglied: mitglied.picture_url,
}


def create_or_sync_customer_for_mitglied(mitglied_name: str) -> dict:
    mitglied = frappe.get_doc("Mitglied", mitglied_name)
    mitglied.check_permission("write")

    created = not mitglied.customer
    if created:
        customer = make_customer_from_mitglied(mitglied)
        mark_customer_source_owned(customer, "Mitglied", mitglied.name, CUSTOMER_SYNC_FIELDS, mitglied)
        customer.insert()
    else:
        customer = frappe.get_doc("Customer", mitglied.customer)
        customer.check_permission("write")

    sync_customer_from_mitglied(mitglied, customer=customer)
    mitglied.reload()
    return {"mitglied": mitglied.name, "customer": mitglied.customer or customer.name, "created": created}


def sync_unlinked_mitglieder() -> dict:
    names = get_unlinked_mitglied_names()
    result = {"total": len(names), "created": 0, "synced": 0, "errors": []}

    for name in names:
        savepoint = f"erpverein_member_sync_{frappe.generate_hash(length=10)}"
        frappe.db.savepoint(savepoint)
        try:
            sync_result = create_or_sync_customer_for_mitglied(name)
        except Exception:
            frappe.db.rollback(save_point=savepoint)
            frappe.db.release_savepoint(savepoint)
            result["errors"].append({"mitglied": name, "error": _("Synchronisierung fehlgeschlagen.")})
            continue

        frappe.db.release_savepoint(savepoint)
        result["created" if sync_result["created"] else "synced"] += 1

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
    sync_customer_records(
        source=mitglied,
        source_doctype="Mitglied",
        customer=customer,
        customer_name=mitglied.customer,
        customer_fields=CUSTOMER_SYNC_FIELDS,
        desired_address=get_desired_address_fields(mitglied),
        desired_contact_factory=lambda address: get_desired_contact_fields(mitglied, address),
    )


def sync_customer_records(
    *,
    source,
    source_doctype: str,
    customer,
    customer_name: str | None,
    customer_fields: dict[str, Callable],
    desired_address: dict,
    desired_contact_factory: Callable[[str], dict],
) -> None:
    customer_name = customer.name if customer else customer_name
    if not customer_name:
        return

    customer = customer or frappe.get_doc("Customer", customer_name)
    customer.check_permission("write")
    state = load_sync_state(customer)
    source_state = get_source_state(state, source_doctype, source.name)

    changed = sync_owned_customer_fields(source, customer, source_state, customer_fields)
    address_changed, address_name = sync_owned_address(customer, source_state, desired_address)
    desired_contact = get_supported_contact_fields(desired_contact_factory(address_name or ""))
    contact_changed = sync_owned_contact(customer, source_state, desired_contact)

    pending_deletes = source_state.pop("_pending_deletes", [])
    if changed or address_changed or contact_changed or pending_deletes:
        customer.set(CUSTOMER_SYNC_STATE_FIELDNAME, dump_sync_state(state))
        customer.save()
        for doctype, name in sorted(pending_deletes, key=lambda item: 0 if item[0] == "Contact" else 1):
            frappe.delete_doc(doctype, name)


def mark_customer_source_owned(customer, source_doctype: str, source_name: str, fields: dict[str, Callable], source) -> None:
    if not frappe.get_meta("Customer", cached=False).has_field(CUSTOMER_SYNC_STATE_FIELDNAME):
        return
    state = load_sync_state(customer)
    source_state = get_source_state(state, source_doctype, source_name)
    source_state["customer"] = {
        "managed": True,
        "source": source_marker(source_doctype, source_name),
        "fields": {
            fieldname: normalize_sync_value(getter(source))
            for fieldname, getter in fields.items()
            if frappe.get_meta("Customer").has_field(fieldname)
        },
    }
    customer.set(CUSTOMER_SYNC_STATE_FIELDNAME, dump_sync_state(state))


def sync_owned_customer_fields(source, customer, source_state: dict, fields: dict[str, Callable]) -> bool:
    managed_state = source_state.get("customer") or {}
    if not is_source_owned(managed_state, source.doctype, source.name):
        return False

    snapshots = managed_state.setdefault("fields", {})
    changed = False
    for fieldname, getter in fields.items():
        if not customer.meta.has_field(fieldname):
            continue
        desired = normalize_sync_value(getter(source))
        current = normalize_sync_value(customer.get(fieldname))
        previous = normalize_sync_value(snapshots.get(fieldname))
        if current != previous:
            continue
        if current != desired:
            customer.set(fieldname, desired)
            changed = True
        if previous != desired:
            snapshots[fieldname] = desired
            changed = True
    return changed


def sync_owned_address(customer, source_state: dict, desired: dict) -> tuple[bool, str | None]:
    address_state = source_state.get("address") or {}
    if not has_required_address_data(desired):
        return remove_owned_record(customer, source_state, "address", "Address", address_state, get_address_sync_fields), None

    address_name = get_owned_record_name("Address", address_state, source_state["source"])
    if not address_name:
        previous_primary = get_customer_primary_address(customer)
        address = create_customer_address(customer, desired)
        source_state["address"] = make_record_state(source_state["source"], address.name, desired)
        changed = set_managed_primary(customer, "customer_primary_address", address.name, previous_primary, None)
        source_state["address"]["primary"] = address.name if changed else None
        return True, address.name

    address = frappe.get_doc("Address", address_name)
    address.check_permission("write")
    if record_has_other_links(address, customer.name):
        source_state.pop("address", None)
        return True, address.name
    snapshots = address_state.get("fields") or {}
    if get_address_sync_fields(address) != snapshots:
        return False, address.name

    changed = apply_simple_fields(address, desired)
    if changed:
        address.save()
    if snapshots != desired:
        address_state["fields"] = desired
        changed = True
    changed = sync_managed_primary(customer, "customer_primary_address", address.name, address_state) or changed
    return changed, address.name


def sync_owned_contact(customer, source_state: dict, desired: dict) -> bool:
    contact_state = source_state.get("contact") or {}
    if not has_required_contact_data(desired):
        return remove_owned_record(customer, source_state, "contact", "Contact", contact_state, get_contact_sync_fields)

    contact_name = get_owned_record_name("Contact", contact_state, source_state["source"])
    if not contact_name:
        previous_primary = get_customer_primary_contact(customer)
        contact = create_customer_contact(customer, desired, make_primary=not previous_primary)
        source_state["contact"] = make_record_state(source_state["source"], contact.name, desired)
        changed = set_managed_primary(customer, "customer_primary_contact", contact.name, previous_primary, None)
        source_state["contact"]["primary"] = contact.name if changed else None
        return True

    contact = frappe.get_doc("Contact", contact_name)
    contact.check_permission("write")
    if record_has_other_links(contact, customer.name):
        source_state.pop("contact", None)
        return True
    snapshots = contact_state.get("fields") or {}
    if get_contact_sync_fields(contact) != snapshots:
        return False

    changed = apply_contact_fields(contact, desired)
    if changed:
        contact.save()
    if snapshots != desired:
        contact_state["fields"] = desired
        changed = True
    return sync_managed_primary(customer, "customer_primary_contact", contact.name, contact_state) or changed


def create_customer_address(customer, desired: dict):
    address = frappe.new_doc("Address")
    address.address_title = customer.customer_name
    address.address_type = "Billing"
    for fieldname, value in desired.items():
        address.set(fieldname, value)
    address.append("links", {"link_doctype": "Customer", "link_name": customer.name})
    address.insert()
    return address


def create_customer_contact(customer, desired: dict, make_primary: bool = True):
    contact = frappe.new_doc("Contact")
    apply_contact_fields(contact, desired)
    contact.is_primary_contact = int(make_primary)
    contact.append("links", {"link_doctype": "Customer", "link_name": customer.name})
    contact.insert()
    return contact


def apply_contact_fields(contact, desired: dict) -> bool:
    changed = False
    for fieldname in ["first_name", "middle_name", "last_name", "salutation", "address", "is_billing_contact"]:
        if fieldname in desired and normalize_sync_value(contact.get(fieldname)) != desired[fieldname]:
            contact.set(fieldname, desired[fieldname])
            changed = True
    if normalize_sync_value(contact.email_id) != desired["email_id"]:
        primary_email = next((row for row in contact.get("email_ids") or [] if row.is_primary), None)
        if primary_email and desired["email_id"]:
            primary_email.email_id = desired["email_id"]
        elif primary_email:
            contact.remove(primary_email)
        elif desired["email_id"]:
            contact.add_email(desired["email_id"], is_primary=True)
        changed = True
    if normalize_sync_value(contact.mobile_no) != desired["mobile_no"]:
        primary_phone = next((row for row in contact.get("phone_nos") or [] if row.is_primary_mobile_no), None)
        if primary_phone and desired["mobile_no"]:
            primary_phone.phone = desired["mobile_no"]
        elif primary_phone:
            contact.remove(primary_phone)
        elif desired["mobile_no"]:
            contact.add_phone(desired["mobile_no"], is_primary_mobile_no=True)
        changed = True
    return changed


def remove_owned_record(customer, source_state: dict, state_key: str, doctype: str, record_state: dict, snapshot_getter) -> bool:
    record_name = get_owned_record_name(doctype, record_state, source_state["source"])
    if not record_name:
        return False
    record = frappe.get_doc(doctype, record_name)
    record.check_permission("delete")
    snapshots = record_state.get("fields") or {}
    if snapshot_getter(record) != snapshots or record_has_manual_additions(record, customer.name):
        pending_addresses = {
            name for pending_doctype, name in source_state.get("_pending_deletes", []) if pending_doctype == "Address"
        }
        if doctype == "Contact" and record.get("address") in pending_addresses:
            record.address = None
            record.save()
        source_state.pop(state_key, None)
        return True

    primary_field = "customer_primary_address" if doctype == "Address" else "customer_primary_contact"
    if customer.meta.has_field(primary_field) and customer.get(primary_field) == record.name:
        customer.set(primary_field, None)
        if doctype == "Contact":
            for fieldname in ("email_id", "mobile_no", "first_name", "last_name"):
                if customer.meta.has_field(fieldname):
                    customer.set(fieldname, None)
    source_state.pop(state_key, None)
    source_state.setdefault("_pending_deletes", []).append((doctype, record.name))
    return True


def record_has_manual_additions(record, customer_name: str) -> bool:
    if record_has_other_links(record, customer_name):
        return True
    if record.doctype == "Contact":
        if any(not row.is_primary for row in (record.get("email_ids") or [])):
            return True
        if any(not row.is_primary_mobile_no for row in (record.get("phone_nos") or [])):
            return True
    return False


def record_has_other_links(record, customer_name: str) -> bool:
    return any(
        link.link_doctype != "Customer" or link.link_name != customer_name
        for link in (record.get("links") or [])
    )


def apply_simple_fields(doc, desired: dict) -> bool:
    changed = False
    for fieldname, value in desired.items():
        if normalize_sync_value(doc.get(fieldname)) != value:
            doc.set(fieldname, value)
            changed = True
    return changed


def set_managed_primary(customer, fieldname: str, record_name: str, current_primary: str | None, managed_primary: str | None) -> bool:
    if not customer.meta.has_field(fieldname):
        return False
    current_primary = normalize_sync_value(current_primary)
    if current_primary and current_primary != normalize_sync_value(managed_primary):
        return False
    if current_primary != record_name:
        customer.set(fieldname, record_name)
        return True
    return False


def sync_managed_primary(customer, fieldname: str, record_name: str, record_state: dict) -> bool:
    managed_primary = normalize_sync_value(record_state.get("primary"))
    current_primary = normalize_sync_value(customer.get(fieldname)) if customer.meta.has_field(fieldname) else ""
    changed = set_managed_primary(customer, fieldname, record_name, current_primary, managed_primary)
    if changed or current_primary == record_name:
        record_state["primary"] = record_name
    return changed


def make_record_state(source: dict, name: str, fields: dict) -> dict:
    return {"managed": True, "source": source, "name": name, "fields": fields}


def get_owned_record_name(doctype: str, record_state: dict | None, source: dict) -> str | None:
    if not record_state or not record_state.get("managed") or record_state.get("source") != source:
        return None
    name = record_state.get("name")
    if not name or not frappe.db.exists(doctype, name):
        return None
    return name


def get_desired_address_fields(mitglied) -> dict:
    return {
        "address_line1": normalize_sync_value(mitglied.strasse),
        "pincode": normalize_sync_value(mitglied.plz),
        "city": normalize_sync_value(mitglied.ort),
        "country": normalize_sync_value(mitglied.land) or DEFAULT_COUNTRY,
    }


def get_desired_contact_fields(mitglied, address_name: str = "") -> dict:
    return {
        "first_name": normalize_sync_value(mitglied.vorname),
        "middle_name": "",
        "last_name": normalize_sync_value(mitglied.nachname),
        "salutation": get_contact_salutation(mitglied.anrede),
        "email_id": normalize_sync_value(mitglied.email),
        "mobile_no": normalize_sync_value(mitglied.telefon),
        "address": normalize_sync_value(address_name),
        "is_billing_contact": "1",
    }


def get_contact_salutation(anrede: object) -> str:
    salutation = {"herr": "Mr", "herrn": "Mr", "frau": "Ms"}.get(normalize_sync_value(anrede).lower(), "")
    return salutation if salutation and frappe.db.exists("Salutation", salutation) else ""


def get_supported_contact_fields(fields: dict) -> dict:
    contact_meta = frappe.get_meta("Contact")
    supported_fields = {"email_id", "mobile_no"}
    for fieldname in ["first_name", "middle_name", "last_name", "salutation", "address", "is_billing_contact"]:
        if contact_meta.has_field(fieldname):
            supported_fields.add(fieldname)
    return {fieldname: value for fieldname, value in fields.items() if fieldname in supported_fields}


def has_required_address_data(address_fields: dict) -> bool:
    return bool(address_fields.get("address_line1") and address_fields.get("city") and address_fields.get("country"))


def has_required_contact_data(contact_fields: dict) -> bool:
    return bool(contact_fields.get("email_id") or contact_fields.get("mobile_no"))


def get_address_sync_fields(address) -> dict:
    return {
        "address_line1": normalize_sync_value(address.address_line1),
        "pincode": normalize_sync_value(address.pincode),
        "city": normalize_sync_value(address.city),
        "country": normalize_sync_value(address.country),
    }


def get_contact_sync_fields(contact) -> dict:
    fields = {
        "first_name": normalize_sync_value(contact.first_name),
        "middle_name": normalize_sync_value(contact.middle_name),
        "last_name": normalize_sync_value(contact.last_name),
        "email_id": normalize_sync_value(contact.email_id),
        "mobile_no": normalize_sync_value(contact.mobile_no),
    }
    contact_meta = frappe.get_meta("Contact")
    for fieldname in ["salutation", "address", "is_billing_contact"]:
        if contact_meta.has_field(fieldname):
            fields[fieldname] = normalize_sync_value(contact.get(fieldname))
    return fields


def get_customer_primary_address(customer) -> str | None:
    return customer.get("customer_primary_address") or None if customer.meta.has_field("customer_primary_address") else None


def get_customer_primary_contact(customer) -> str | None:
    return customer.get("customer_primary_contact") or None if customer.meta.has_field("customer_primary_contact") else None


def source_marker(doctype: str, name: str) -> dict:
    return {"doctype": doctype, "name": name}


def get_source_state(state: dict, doctype: str, name: str) -> dict:
    state["schema_version"] = SYNC_STATE_SCHEMA_VERSION
    sources = state.setdefault("sources", {})
    key = f"{doctype}:{name}"
    source_state = sources.setdefault(key, {})
    source_state["source"] = source_marker(doctype, name)
    return source_state


def is_source_owned(entity_state: dict, doctype: str, name: str) -> bool:
    return bool(entity_state.get("managed") and entity_state.get("source") == source_marker(doctype, name))


def load_sync_state(customer) -> dict:
    if not frappe.get_meta("Customer", cached=False).has_field(CUSTOMER_SYNC_STATE_FIELDNAME):
        return {}
    raw_state = customer.get(CUSTOMER_SYNC_STATE_FIELDNAME)
    if not raw_state:
        return {}
    try:
        state = json.loads(raw_state)
    except (TypeError, ValueError):
        frappe.throw(_("Der ERPverein Sync-Status des Kunden ist ungueltig."))
    return state if isinstance(state, dict) else {}


def dump_sync_state(state: dict) -> str:
    return json.dumps(state, sort_keys=True, separators=(",", ":"))


def set_if_field_exists(doc, meta, fieldname: str, value) -> None:
    if meta.has_field(fieldname):
        doc.set(fieldname, value)


def get_existing_name(doctype: str, preferred_name: str) -> str:
    if frappe.db.exists(doctype, preferred_name):
        return preferred_name
    frappe.throw(_("Erforderlicher Stammdatensatz {0} {1} fehlt.").format(doctype, frappe.bold(preferred_name)))


def normalize_sync_value(value) -> str:
    return cstr(value).strip()
