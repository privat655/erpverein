import json

import frappe
from frappe import _

from verein_erp.services.rental_subscription_generation_service import (
    RUN_STATUS_RUNNING,
    build_rental_subscription_preview,
    create_run_for_mieter,
    create_run_for_selection,
)


def _enqueue_subscription_creation(run: str) -> dict:
    job = frappe.enqueue(
        "verein_erp.services.rental_subscription_generation_service.create_subscriptions_from_preview",
        queue="long",
        timeout=7200,
        enqueue_after_commit=True,
        job_name=f"Mietabrechnung {run}",
        run_name=run,
    )
    return {"run": run, "job_id": getattr(job, "id", None)}


@frappe.whitelist()
def create_run(mieter: str | None = None, mieter_json: str | None = None) -> dict:
    if mieter:
        run = create_run_for_mieter(mieter)
    else:
        selected_mieter = json.loads(mieter_json) if mieter_json else []
        run = create_run_for_selection(selected_mieter)
    return {"run": run.name}


@frappe.whitelist()
def generate_preview(run: str) -> dict:
    return build_rental_subscription_preview(run)


@frappe.whitelist()
def create_subscriptions(run: str) -> dict:
    doc = frappe.get_doc("Mietabrechnung", run)
    doc.check_permission("write")
    if doc.status == RUN_STATUS_RUNNING:
        return {"run": run, "already_running": True}
    if not doc.preview_rows:
        frappe.throw(_("Bitte zuerst eine Vorschau anzeigen."))
    doc.status = RUN_STATUS_RUNNING
    doc.result_summary = json.dumps({"status": "queued"}, sort_keys=True)
    doc.save()
    return _enqueue_subscription_creation(run)
