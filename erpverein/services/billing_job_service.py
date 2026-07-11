from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime
from frappe.utils.background_jobs import get_job_status

from erpverein.services.billing_common import (
    EXECUTION_ACTION,
    RUN_STATUS_FAILED,
    RUN_STATUS_PREVIEW,
    RUN_STATUS_PREVIEW_ERRORS,
    RUN_STATUS_QUEUED,
    RUN_STATUS_RUNNING,
    is_retryable_error,
)


RUN_CONFIG = {
    "Beitragsabrechnung": {
        "prefix": "membership",
        "worker": "erpverein.services.subscription_generation_service.create_subscriptions_from_preview",
        "hash": "erpverein.services.subscription_generation_service.compute_subscription_preview_hash",
    },
    "Mietabrechnung": {
        "prefix": "rental",
        "worker": "erpverein.services.rental_subscription_generation_service.create_subscriptions_from_preview",
        "hash": "erpverein.services.rental_subscription_generation_service.compute_rental_preview_hash",
    },
}


def deterministic_job_ids(doctype: str, run_name: str) -> tuple[str, str]:
    prefix = RUN_CONFIG[doctype]["prefix"]
    safe_name = run_name.lower().replace(" ", "-")
    base = f"erpverein-{prefix}-billing-{safe_name}"
    return f"{base}-launcher", f"{base}-worker"


def queue_billing_run(doctype: str, run_name: str, preview_hash: str, action: str) -> dict:
    frappe.db.get_value(doctype, run_name, "name", for_update=True)
    run = frappe.get_doc(doctype, run_name)
    run.check_permission("write")
    if run.status in {RUN_STATUS_QUEUED, RUN_STATUS_RUNNING}:
        if action != EXECUTION_ACTION or not preview_hash or preview_hash != run.preview_hash:
            frappe.throw(_("Vorschau oder Ausfuehrungsaktion ist nicht mehr gueltig."))
        return {"run": run.name, "job_id": run.launcher_job_id, "already_running": True}
    if run.status not in {RUN_STATUS_PREVIEW, RUN_STATUS_PREVIEW_ERRORS} or not run.preview_rows:
        frappe.throw(_("Bitte zuerst eine Vorschau anzeigen."))
    if action != EXECUTION_ACTION or not preview_hash or preview_hash != run.preview_hash:
        frappe.throw(_("Vorschau oder Ausfuehrungsaktion ist nicht mehr gueltig. Bitte Vorschau aktualisieren."))

    current_hash = frappe.get_attr(RUN_CONFIG[doctype]["hash"])(run)
    if current_hash != run.preview_hash:
        frappe.throw(_("Eingaben oder Vorschau wurden geaendert. Bitte Vorschau aktualisieren."))

    launcher_id, worker_id = deterministic_job_ids(doctype, run.name)
    run.flags.erpverein_worker_update = True
    run.status = RUN_STATUS_QUEUED
    run.launcher_job_id = launcher_id
    run.worker_job_id = worker_id
    run.queued_at = now_datetime()
    run.started_at = None
    run.finished_at = None
    run.failure_message = None
    run.save()
    frappe.enqueue(
        "erpverein.services.billing_job_service.launch_billing_worker",
        queue="default",
        enqueue_after_commit=True,
        job_id=launcher_id,
        deduplicate=True,
        doctype=doctype,
        run_name=run.name,
    )
    return {"run": run.name, "job_id": launcher_id}


def launch_billing_worker(doctype: str, run_name: str) -> None:
    frappe.db.get_value(doctype, run_name, "name", for_update=True)
    run = frappe.get_doc(doctype, run_name)
    if run.status == RUN_STATUS_RUNNING:
        return
    if run.status != RUN_STATUS_QUEUED:
        return
    run.flags.erpverein_worker_update = True
    run.status = RUN_STATUS_RUNNING
    run.started_at = now_datetime()
    run.save()
    frappe.enqueue(
        "erpverein.services.billing_job_service.run_billing_worker",
        queue="long",
        timeout=7200,
        enqueue_after_commit=True,
        job_id=run.worker_job_id,
        deduplicate=True,
        doctype=doctype,
        run_name=run.name,
    )


def run_billing_worker(doctype: str, run_name: str) -> None:
    try:
        status = frappe.db.get_value(doctype, run_name, "status")
        if status != RUN_STATUS_RUNNING:
            return
        frappe.get_attr(RUN_CONFIG[doctype]["worker"])(run_name)
    except Exception as exc:
        error_names = {cls.__name__ for cls in type(exc).mro()}
        if error_names & {"QueryDeadlockError", "QueryTimeoutError", "DeadlockError"}:
            raise frappe.RetryBackgroundJobError() from exc
        if is_retryable_error(exc):
            raise
        frappe.db.rollback()
        frappe.log_error(title=f"ERPverein billing job failed: {doctype} {run_name}", message=frappe.get_traceback())
        run = frappe.get_doc(doctype, run_name)
        run.flags.erpverein_worker_update = True
        run.status = RUN_STATUS_FAILED
        run.finished_at = now_datetime()
        run.failure_message = _("Abrechnung konnte nicht abgeschlossen werden. Details wurden protokolliert.")
        run.save()


def reconcile_stale_billing_jobs() -> None:
    now = now_datetime()
    for doctype in RUN_CONFIG:
        rows = frappe.db.get_list(
            doctype,
            filters={"status": ["in", [RUN_STATUS_QUEUED, RUN_STATUS_RUNNING]]},
            fields=["name", "status", "launcher_job_id", "worker_job_id", "queued_at", "started_at"],
        )
        for row in rows:
            timestamp = row.queued_at if row.status == RUN_STATUS_QUEUED else row.started_at
            maximum_age = timedelta(minutes=30) if row.status == RUN_STATUS_QUEUED else timedelta(hours=3)
            job_id = row.launcher_job_id if row.status == RUN_STATUS_QUEUED else row.worker_job_id
            status = _job_status(job_id)
            if status in {"queued", "started", "deferred", "scheduled", "unknown"}:
                continue
            if status == "missing" and timestamp and now - get_datetime(timestamp) <= maximum_age:
                continue
            _fail_stale_run(doctype, row.name, status)


def _fail_stale_run(doctype: str, run_name: str, job_status: str) -> None:
    frappe.db.get_value(doctype, run_name, "name", for_update=True)
    run = frappe.get_doc(doctype, run_name)
    if run.status not in {RUN_STATUS_QUEUED, RUN_STATUS_RUNNING}:
        return
    run.flags.erpverein_worker_update = True
    run.status = RUN_STATUS_FAILED
    run.finished_at = now_datetime()
    run.failure_message = _("Hintergrundauftrag ist nicht mehr aktiv (Status: {0}).").format(job_status or "unknown")
    run.save()


def _job_status(job_id: str | None) -> str:
    if not job_id:
        return "missing"
    try:
        status = get_job_status(job_id)
    except Exception:
        frappe.log_error(title=f"ERPverein job status lookup failed: {job_id}", message=frappe.get_traceback())
        return "unknown"
    return str(getattr(status, "value", status) or "missing").lower()
