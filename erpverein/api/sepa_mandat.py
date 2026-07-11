import frappe

from erpverein.services.sepa_mandat_service import activate_replacement_mandate


@frappe.whitelist()
def activate_replacement(mandate: str) -> dict:
    return activate_replacement_mandate(mandate)
