import frappe

from erpverein.services.billing_common import parse_selection_json
from erpverein.services.billing_job_service import queue_billing_run
from erpverein.services.rental_subscription_generation_service import (
    build_rental_subscription_preview,
    create_run_for_mieter,
    create_run_for_selection,
)


@frappe.whitelist()
def create_run(mieter: str | None = None, mieter_json: str | None = None) -> dict:
    if mieter:
        run = create_run_for_mieter(mieter)
    else:
        selected_mieter = parse_selection_json(mieter_json, "Ausgewaehlte Mieter")
        run = create_run_for_selection(selected_mieter)
    return {"run": run.name}


@frappe.whitelist()
def generate_preview(run: str) -> dict:
    return build_rental_subscription_preview(run)


@frappe.whitelist()
def create_subscriptions(run: str, preview_hash: str, action: str) -> dict:
    return queue_billing_run("Mietabrechnung", run, preview_hash, action)
