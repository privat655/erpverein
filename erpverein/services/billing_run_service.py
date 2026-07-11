import frappe
from frappe import _

from erpverein.services.billing_common import (
    FROZEN_RUN_STATUSES,
    RUN_STATUS_DRAFT,
    RUN_STATUS_PREVIEW,
    RUN_STATUS_PREVIEW_ERRORS,
)


TRACKING_FIELDS = [
    "status",
    "preview_hash",
    "launcher_job_id",
    "worker_job_id",
    "queued_at",
    "started_at",
    "finished_at",
    "failure_message",
    "result_summary",
]


def validate_run_freeze(doc, input_fields: list[str]) -> None:
    old = doc.get_doc_before_save()
    if not old:
        return
    inputs_changed = any(_value(old.get(field)) != _value(doc.get(field)) for field in input_fields + ["periods"])
    preview_changed = _value(old.get("preview_rows")) != _value(doc.get("preview_rows"))
    tracking_changed = any(_value(old.get(field)) != _value(doc.get(field)) for field in TRACKING_FIELDS)

    if old.status in FROZEN_RUN_STATUSES:
        if inputs_changed:
            frappe.throw(_("Eingaben eines eingereihten, laufenden oder abgeschlossenen Laufs sind gesperrt."))
        if (preview_changed or tracking_changed) and not getattr(doc.flags, "erpverein_worker_update", False):
            frappe.throw(_("Ausgaben dieses Abrechnungslaufs duerfen nur vom Hintergrundauftrag geaendert werden."))
        return

    if inputs_changed and (old.preview_hash or old.status in {RUN_STATUS_PREVIEW, RUN_STATUS_PREVIEW_ERRORS}):
        doc.set("preview_rows", [])
        doc.preview_hash = None
        doc.result_summary = None
        doc.status = RUN_STATUS_DRAFT
        return

    if (preview_changed or tracking_changed) and not getattr(doc.flags, "erpverein_worker_update", False):
        frappe.throw(_("Status und Ausgaben eines Abrechnungslaufs duerfen nicht direkt geaendert werden."))


def prevent_frozen_run_deletion(doc) -> None:
    if doc.status in FROZEN_RUN_STATUSES:
        frappe.throw(_("Eingereihte, laufende oder abgeschlossene Abrechnungslaufe koennen nicht geloescht werden."))


def _value(value):
    if hasattr(value, "as_dict"):
        value = value.as_dict(no_nulls=False)
        for field in ("name", "owner", "creation", "modified", "modified_by", "parent", "parentfield", "parenttype", "doctype", "idx", "docstatus"):
            value.pop(field, None)
    if isinstance(value, list):
        return [_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _value(item) for key, item in value.items()}
    return str(value) if value is not None else None
