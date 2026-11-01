import frappe

from erpverein.services.payment_schedule_service import (
    apply_schedule_backfill,
    apply_schedule_regeneration,
    get_schedule_editor,
    preview_schedule_backfill,
    preview_schedule_regeneration,
    update_submitted_schedule,
)
from erpverein.services.prenotification_service import confirm_prenotification_manually


@frappe.whitelist()
def get_editor(invoice: str) -> dict:
    return get_schedule_editor(invoice)


@frappe.whitelist()
def update_schedule(invoice: str, rows: str, modified: str | None = None) -> dict:
    return update_submitted_schedule(invoice, rows, modified)


@frappe.whitelist()
def preview_regeneration(invoice: str) -> dict:
    return preview_schedule_regeneration(invoice)


@frappe.whitelist()
def apply_regeneration(invoice: str, preview_hash: str) -> dict:
    return apply_schedule_regeneration(invoice, preview_hash)


@frappe.whitelist()
def preview_backfill() -> dict:
    return preview_schedule_backfill()


@frappe.whitelist()
def apply_backfill(preview_hash: str) -> dict:
    return apply_schedule_backfill(preview_hash)


@frappe.whitelist()
def confirm_prenotification(invoice: str) -> dict:
    return confirm_prenotification_manually(invoice)
