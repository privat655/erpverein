import frappe
from frappe import _
from frappe.utils import nowdate

from verein_erp.custom_fields import (
    BANK_ACCOUNT_MANAGED_FIELDNAME,
    CUSTOMER_MIETER_FIELDNAME,
    CUSTOMER_SYNC_STATE_FIELDNAME,
)
from verein_erp.services.customer_sync_service import (
    CUSTOMER_GROUP_INDIVIDUAL,
    CUSTOMER_TYPE_INDIVIDUAL,
    DEFAULT_COUNTRY,
    DEFAULT_CURRENCY,
    DEFAULT_TERRITORY,
    apply_contact_fields,
    create_customer_address,
    create_customer_contact,
    dump_sync_state,
    ensure_address_links_customer,
    ensure_contact_links_customer,
    get_customer_primary_address,
    get_customer_primary_contact,
    get_existing_name,
    get_first_linked_customer_address,
    get_supported_contact_fields,
    has_required_address_data,
    has_required_contact_data,
    load_sync_state,
    normalize_sync_value,
    set_customer_primary_address_if_allowed,
    set_customer_primary_contact_if_allowed,
    set_if_field_exists,
)
from verein_erp.services.mieter_service import BILLING_TYPE_DIRECT_DEBIT
from verein_erp.services.sepa_mandat_service import (
    MANDATE_CATEGORY_RENT,
    MANDATE_MODE_YEARLY,
    MANDATE_STATUS_ACTIVE,
)


def create_or_sync_customer_for_mieter(mieter_name: str) -> dict:
    mieter = frappe.get_doc("Mieter", mieter_name)
    mieter.check_permission("write")

    customer_name = mieter.customer
    created = False
    if not customer_name:
        customer = make_customer_from_mieter(mieter)
        customer.insert()
        customer_name = customer.name
        created = True
    else:
        customer = frappe.get_doc("Customer", customer_name)
        customer.check_permission("write")

    sync_customer_from_mieter(mieter, customer=customer, force_initial=created)
    sync_bank_details_from_mieter(mieter)
    mieter.reload()

    return {"mieter": mieter.name, "customer": mieter.customer or customer_name, "created": created}


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
    customer_name = customer.name if customer else mieter.customer
    if not customer_name:
        return

    customer = customer or frappe.get_doc("Customer", customer_name)
    customer.check_permission("write")

    state = load_sync_state(customer)
    changed = sync_customer_link_field(mieter, customer)
    changed = sync_customer_fields(mieter, customer, state, force_initial=force_initial) or changed
    changed = sync_customer_address(mieter, customer, state, force_initial=force_initial) or changed
    changed = sync_customer_contact(mieter, customer, state, force_initial=force_initial) or changed

    if changed:
        customer.set(CUSTOMER_SYNC_STATE_FIELDNAME, dump_sync_state(state))
        customer.save()

    if mieter.customer != customer.name:
        frappe.db.set_value("Mieter", mieter.name, "customer", customer.name, update_modified=False)
        mieter.customer = customer.name


def sync_customer_link_field(mieter, customer) -> bool:
    if not customer.meta.has_field(CUSTOMER_MIETER_FIELDNAME):
        return False
    if customer.get(CUSTOMER_MIETER_FIELDNAME) == mieter.name:
        return False
    customer.set(CUSTOMER_MIETER_FIELDNAME, mieter.name)
    return True


def sync_customer_fields(mieter, customer, state: dict, force_initial: bool = False) -> bool:
    customer_meta = frappe.get_meta("Customer")
    previous_values = state.setdefault("mieter_customer_fields", {})
    changed = False
    desired_values = {"customer_name": normalize_sync_value(mieter.mieter_name)}

    for fieldname, desired in desired_values.items():
        if not customer_meta.has_field(fieldname):
            continue

        current = normalize_sync_value(customer.get(fieldname))
        previous = normalize_sync_value(previous_values.get(fieldname))

        if force_initial or current == previous:
            if current != desired:
                customer.set(fieldname, desired)
                changed = True
            if previous != desired:
                previous_values[fieldname] = desired
                changed = True

    return changed


def sync_customer_address(mieter, customer, state: dict, force_initial: bool = False) -> bool:
    desired = get_desired_address_fields(mieter)
    if not has_required_address_data(desired):
        return False

    address_state = state.setdefault("mieter_address", {})
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


def sync_customer_contact(mieter, customer, state: dict, force_initial: bool = False) -> bool:
    address_name = (state.get("mieter_address") or {}).get("name") or get_customer_primary_address(customer) or ""
    desired = get_supported_contact_fields(get_desired_contact_fields(mieter, address_name))
    if not has_required_contact_data(desired):
        return False

    contact_state = state.setdefault("mieter_contact", {})
    contact_name = contact_state.get("name") if frappe.db.exists("Contact", contact_state.get("name")) else None
    if not contact_name:
        contact_name = get_customer_primary_contact(customer)

    if not contact_name:
        contact = create_customer_contact(customer, desired)
        contact_state["name"] = contact.name
        contact_state["fields"] = desired
        return set_customer_primary_contact_if_allowed(customer, contact.name, contact_state, force_initial=True) or True

    contact = frappe.get_doc("Contact", contact_name)
    contact.check_permission("write")
    ensure_contact_links_customer(contact, customer.name)

    previous_fields = contact_state.get("fields") or {}
    current_fields = get_contact_sync_fields(contact)
    can_update_contact = force_initial or bool(previous_fields and current_fields == previous_fields)
    changed = False

    if can_update_contact:
        changed = apply_contact_fields(contact, desired)
        if changed:
            contact.save()
        if previous_fields != desired:
            contact_state["fields"] = desired
            changed = True

    contact_state["name"] = contact.name
    changed = set_customer_primary_contact_if_allowed(customer, contact.name, contact_state, force_initial=force_initial) or changed
    return changed


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


def get_contact_salutation(anrede: object) -> str:
    salutation = {"herr": "Mr", "frau": "Ms"}.get(normalize_sync_value(anrede).lower(), "")
    return salutation if salutation and frappe.db.exists("Salutation", salutation) else ""


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


def sync_bank_details_from_mieter(mieter) -> None:
    if not mieter.customer or not mieter.iban or not mieter.bank_name:
        return

    bank = get_or_create_bank(mieter.bank_name)
    bank_account = get_or_create_bank_account(mieter, bank)
    if mieter.abrechnungsart == BILLING_TYPE_DIRECT_DEBIT:
        sync_rent_sepa_mandat(mieter, bank, bank_account)


def get_or_create_bank(bank_name: str):
    existing = frappe.db.get_value("Bank", {"bank_name": bank_name}, "name") or frappe.db.get_value("Bank", bank_name, "name")
    if existing:
        return frappe.get_doc("Bank", existing)

    return frappe.get_doc({"doctype": "Bank", "bank_name": bank_name}).insert()


def get_or_create_bank_account(mieter, bank):
    matches = frappe.db.get_list(
        "Bank Account",
        filters={"party_type": "Customer", "party": mieter.customer, "iban": mieter.iban, "is_company_account": 0},
        pluck="name",
    )
    if len(matches) > 1:
        frappe.throw(_("Mehrere Bankkonten fuer Kunde {0} und IBAN {1} gefunden.").format(frappe.bold(mieter.customer), frappe.bold(mieter.iban)))

    bank_account = frappe.get_doc("Bank Account", matches[0]) if matches else frappe.new_doc("Bank Account")
    if matches:
        bank_account.check_permission("write")

    desired = {
        "account_name": mieter.kontoinhaber or mieter.mieter_name,
        "bank": bank.name,
        "party_type": "Customer",
        "party": mieter.customer,
        "iban": mieter.iban,
        "branch_code": mieter.bic,
        "is_company_account": 0,
        "disabled": 0,
        BANK_ACCOUNT_MANAGED_FIELDNAME: 1,
    }
    for fieldname, value in desired.items():
        if bank_account.meta.has_field(fieldname):
            bank_account.set(fieldname, value)

    if bank_account.name:
        bank_account.save()
    else:
        bank_account.insert()
    return bank_account


def sync_rent_sepa_mandat(mieter, bank, bank_account) -> None:
    mandate_reference = f"Miete-{mieter.name}"
    mandate_name = (
        mieter.sepa_mandat
        or frappe.db.get_value(
            "SEPA Mandat",
            {"mandatskategorie": MANDATE_CATEGORY_RENT, "bezugs_doctype": "Mieter", "bezugs_name": mieter.name, "status": MANDATE_STATUS_ACTIVE},
            "name",
        )
        or frappe.db.get_value("SEPA Mandat", {"mandatsreferenz": mandate_reference}, "name")
    )
    mandate = frappe.get_doc("SEPA Mandat", mandate_name) if mandate_name else frappe.new_doc("SEPA Mandat")
    if mandate.name:
        mandate.check_permission("write")

    mandate.mandatsreferenz = mandate.mandatsreferenz or mandate_reference
    mandate.mandatskategorie = MANDATE_CATEGORY_RENT
    mandate.status = MANDATE_STATUS_ACTIVE
    mandate.bezugs_doctype = "Mieter"
    mandate.bezugs_name = mieter.name
    mandate.customer = mieter.customer
    mandate.mandatsdatum = mandate.mandatsdatum or mieter.mietbeginn or nowdate()
    mandate.einzugsmodus = mandate.einzugsmodus or MANDATE_MODE_YEARLY
    mandate.kontoinhaber = mieter.kontoinhaber or mieter.mieter_name
    mandate.iban = mieter.iban
    mandate.bic = mieter.bic
    mandate.bank = bank.name
    mandate.bank_name_freitext = mieter.bank_name
    mandate.bank_account = bank_account.name

    if mandate.name:
        mandate.save()
    else:
        mandate.insert()

    if mieter.sepa_mandat != mandate.name:
        frappe.db.set_value("Mieter", mieter.name, "sepa_mandat", mandate.name, update_modified=False)
        mieter.sepa_mandat = mandate.name
