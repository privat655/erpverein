import frappe

from erpverein.services.sepa_collection_export_service import export_collection_run
from erpverein.services.sepa_collection_service import (
    build_collection_preview,
    cancel_collection_position,
    cancel_collection_positions,
    cancel_collection_run,
    check_collection_run,
    create_collection_runs,
    set_collection_bank_reference,
)
from erpverein.services.sepa_settlement_service import mark_position_done, start_run_reconciliation


@frappe.whitelist()
def get_preview(cutoff_date: str, company: str | None = None, batch_booking: int | None = None) -> dict:
    return build_collection_preview(company, cutoff_date, batch_booking)


@frappe.whitelist(methods=["POST"])
def create_runs(
    cutoff_date: str,
    preview_hash: str,
    company: str | None = None,
    batch_booking: int | None = None,
    selected_rows: str | None = None,
) -> dict:
    return create_collection_runs(company, cutoff_date, preview_hash, batch_booking, selected_rows)


@frappe.whitelist(methods=["POST"])
def check_run(run: str) -> dict:
    return check_collection_run(run)


@frappe.whitelist(methods=["POST"])
def export_run(run: str) -> dict:
    return export_collection_run(run)


@frappe.whitelist(methods=["POST"])
def cancel_positions(run: str, reason: str, positions: str | None = None, only_errors: int = 0) -> dict:
    return cancel_collection_positions(run, positions, reason, only_errors)


@frappe.whitelist(methods=["POST"])
def cancel_position(position: str, reason: str) -> dict:
    return cancel_collection_position(position, reason)


@frappe.whitelist(methods=["POST"])
def cancel_run(run: str, reason: str, confirm_not_submitted: int = 0) -> dict:
    return cancel_collection_run(run, reason, confirm_not_submitted)


@frappe.whitelist(methods=["POST"])
def set_bank_reference(run: str, reference: str) -> dict:
    return set_collection_bank_reference(run, reference)


@frappe.whitelist(methods=["POST"])
def start_reconciliation(run: str) -> dict:
    return start_run_reconciliation(run)


@frappe.whitelist(methods=["POST"])
def mark_done(position: str, reason: str) -> dict:
    return mark_position_done(position, reason)
