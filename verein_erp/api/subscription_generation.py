import json

import frappe

from verein_erp.services.subscription_generation_service import (
    build_subscription_preview,
    create_run_for_mitglied,
    create_run_for_selection,
    create_subscriptions_from_preview,
)


@frappe.whitelist()
def create_run(mitglied: str | None = None, mitglieder_json: str | None = None) -> dict:
    if mitglied:
        run = create_run_for_mitglied(mitglied)
    else:
        mitglieder = json.loads(mitglieder_json) if mitglieder_json else []
        run = create_run_for_selection(mitglieder)
    return {"run": run.name}


@frappe.whitelist()
def generate_preview(run: str) -> dict:
    return build_subscription_preview(run)


@frappe.whitelist()
def create_subscriptions(run: str) -> dict:
    return create_subscriptions_from_preview(run)
