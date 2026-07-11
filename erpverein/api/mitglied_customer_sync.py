import frappe

from erpverein.services.customer_sync_service import create_or_sync_customer_for_mitglied, sync_unlinked_mitglieder


@frappe.whitelist()
def sync_customer(mitglied: str) -> dict:
    return create_or_sync_customer_for_mitglied(mitglied)


@frappe.whitelist()
def sync_all_unlinked() -> dict:
    return sync_unlinked_mitglieder()
