import frappe

from verein_erp.services.mieter_customer_sync_service import create_or_sync_customer_for_mieter


@frappe.whitelist()
def sync_customer(mieter: str) -> dict:
    return create_or_sync_customer_for_mieter(mieter)
