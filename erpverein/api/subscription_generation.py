import frappe

from erpverein.services.billing_common import parse_selection_json
from erpverein.services.billing_job_service import queue_billing_run
from erpverein.services.subscription_generation_service import (
    build_subscription_preview,
    create_run_for_mitglied,
    create_run_for_selection,
)


@frappe.whitelist()
def create_run(mitglied: str | None = None, mitglieder_json: str | None = None) -> dict:
    if mitglied:
        run = create_run_for_mitglied(mitglied)
    else:
        mitglieder = parse_selection_json(mitglieder_json, "Ausgewaehlte Mitglieder")
        run = create_run_for_selection(mitglieder)
    return {"run": run.name}


@frappe.whitelist()
def generate_preview(run: str) -> dict:
    return build_subscription_preview(run)


@frappe.whitelist()
def create_subscriptions(run: str, preview_hash: str, action: str) -> dict:
    return queue_billing_run("Beitragsabrechnung", run, preview_hash, action)
