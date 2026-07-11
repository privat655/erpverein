import hashlib
import json
from datetime import date, datetime

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from erpverein.custom_fields import (
    SUBSCRIPTION_BILLING_KIND_FIELDNAME,
    SUBSCRIPTION_GENERATION_KEY_FIELDNAME,
    SUBSCRIPTION_GENERATION_PAYLOAD_FIELDNAME,
    SUBSCRIPTION_MANAGED_FIELDNAME,
    SUBSCRIPTION_RUN_DOCTYPE_FIELDNAME,
    SUBSCRIPTION_RUN_FIELDNAME,
SUBSCRIPTION_SOURCES_FIELDNAME,
)


BILLING_KIND_MEMBERSHIP = "Mitgliedsbeitrag"
BILLING_KIND_RENTAL = "Miete"
EXECUTION_ACTION = "create_subscriptions"
PREVIEW_CONTRACT_VERSION = 1
MAX_SELECTION_ITEMS = 1000
MAX_SELECTION_JSON_LENGTH = 200_000
MAX_SELECTION_VALUE_LENGTH = 140

RUN_STATUS_DRAFT = "Entwurf"
RUN_STATUS_PREVIEW = "Vorschau erstellt"
RUN_STATUS_PREVIEW_ERRORS = "Vorschau mit Fehlern"
RUN_STATUS_QUEUED = "Eingereiht"
RUN_STATUS_RUNNING = "In Bearbeitung"
RUN_STATUS_EXECUTED = "Ausgefuehrt"
RUN_STATUS_PARTIAL = "Teilweise fehlgeschlagen"
RUN_STATUS_FAILED = "Fehlgeschlagen"
RUN_STATUS_CANCELLED = "Abgebrochen"

FROZEN_RUN_STATUSES = {
    RUN_STATUS_QUEUED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_EXECUTED,
    RUN_STATUS_PARTIAL,
    RUN_STATUS_FAILED,
    RUN_STATUS_CANCELLED,
}
SUBSCRIPTION_PROVENANCE_FIELDS = (
    SUBSCRIPTION_MANAGED_FIELDNAME,
    SUBSCRIPTION_BILLING_KIND_FIELDNAME,
    SUBSCRIPTION_GENERATION_KEY_FIELDNAME,
    SUBSCRIPTION_GENERATION_PAYLOAD_FIELDNAME,
    SUBSCRIPTION_RUN_DOCTYPE_FIELDNAME,
    SUBSCRIPTION_RUN_FIELDNAME,
    SUBSCRIPTION_SOURCES_FIELDNAME,
)


class BillingConflict(Exception):
    def __init__(self, message: str, subscription: str | None = None):
        super().__init__(message)
        self.subscription = subscription


class BillingRowError(Exception):
    pass


def validate_subscription_provenance(doc, method: str | None = None) -> None:
    if getattr(doc.flags, "erpverein_generation", False):
        return
    previous = doc.get_doc_before_save()
    if not previous:
        if any(_document_value(doc.get(fieldname)) not in (None, "", [], 0) for fieldname in SUBSCRIPTION_PROVENANCE_FIELDS):
            frappe.throw(_("ERPverein-Provenienz darf nur durch einen ERPverein-Abrechnungslauf gesetzt werden."))
        return
    if any(
        _document_value(previous.get(fieldname)) != _document_value(doc.get(fieldname))
        for fieldname in SUBSCRIPTION_PROVENANCE_FIELDS
    ):
        frappe.throw(_("ERPverein-Provenienz eines Abonnements darf nicht manuell geaendert werden."))


def canonical_json(value) -> str:
    return json.dumps(_json_value(value), ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def stable_hash(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalized_period(from_date, to_date=None) -> dict[str, str | None]:
    return {
        "from": str(getdate(from_date)),
        "to": str(getdate(to_date)) if to_date else None,
    }


def generation_key(kind: str, source_name: str, from_date, to_date=None) -> str:
    return stable_hash({"kind": kind, "source": source_name, "period": normalized_period(from_date, to_date)})


def generation_payload(data: dict) -> str:
    return canonical_json(data)


def parse_selection_json(raw: str | None, label: str) -> list[str]:
    if not raw:
        return []
    if not isinstance(raw, str) or len(raw) > MAX_SELECTION_JSON_LENGTH:
        frappe.throw(_("{0} ist zu gross.").format(label))
    try:
        values = json.loads(raw)
    except (TypeError, ValueError):
        frappe.throw(_("{0} JSON ist ungueltig.").format(label))
    if not isinstance(values, list) or len(values) > MAX_SELECTION_ITEMS:
        frappe.throw(_("{0} muss eine Liste mit hoechstens {1} Eintraegen sein.").format(label, MAX_SELECTION_ITEMS))
    if any(not isinstance(value, str) or not value.strip() or len(value) > MAX_SELECTION_VALUE_LENGTH for value in values):
        frappe.throw(_("{0} darf nur nicht-leere Textwerte enthalten.").format(label))
    return sorted(set(values))


def make_preview_hash(run, input_fields: list[str], preview_fields: list[str]) -> str:
    return stable_hash(
        {
            "contract_version": PREVIEW_CONTRACT_VERSION,
            "action": EXECUTION_ACTION,
            "doctype": run.doctype,
            "name": run.name,
            "inputs": {field: _document_value(run.get(field)) for field in input_fields},
            "periods": [_document_value(row) for row in (run.get("periods") or [])],
            "preview": [
                {field: _document_value(row.get(field)) for field in preview_fields}
                for row in (run.get("preview_rows") or [])
            ],
        }
    )


def lock_source(doctype: str, name: str) -> None:
    if not frappe.db.get_value(doctype, name, "name", for_update=True):
        raise BillingConflict(_("Abrechnungsquelle wurde nicht gefunden."))


def ensure_preview_editable(run) -> None:
    if run.status in FROZEN_RUN_STATUSES:
        frappe.throw(_("Fuer einen eingereihten, laufenden oder abgeschlossenen Lauf kann keine neue Vorschau erstellt werden."))


def classify_subscription(key: str, payload: str, party: str, period_from, period_to, kind: str) -> tuple[str, str | None, str]:
    existing_rows = frappe.db.get_list(
        "Subscription",
        filters={SUBSCRIPTION_GENERATION_KEY_FIELDNAME: key},
        fields=["name", SUBSCRIPTION_GENERATION_PAYLOAD_FIELDNAME],
        limit_page_length=1,
    )
    existing = existing_rows[0] if existing_rows else None
    if existing:
        if existing.get(SUBSCRIPTION_GENERATION_PAYLOAD_FIELDNAME) == payload:
            return "skip", existing.name, _("Identisches Abo wurde bereits erstellt.")
        return "conflict", existing.name, _("Generierungsschluessel ist bereits mit anderen Daten belegt.")

    fields = [
        "name",
        "start_date",
        "end_date",
        SUBSCRIPTION_MANAGED_FIELDNAME,
        SUBSCRIPTION_BILLING_KIND_FIELDNAME,
    ]
    subscriptions = frappe.db.get_list(
        "Subscription",
        filters={"party_type": "Customer", "party": party, "status": ["!=", "Cancelled"]},
        fields=fields,
        order_by="start_date asc, name asc",
    )
    for subscription in subscriptions:
        if not periods_overlap(period_from, period_to, subscription.start_date, subscription.end_date):
            continue
        managed = cint(subscription.get(SUBSCRIPTION_MANAGED_FIELDNAME))
        existing_kind = subscription.get(SUBSCRIPTION_BILLING_KIND_FIELDNAME)
        if not managed:
            return "conflict", subscription.name, _("Nicht verwaltetes Abo ueberschneidet den Zeitraum.")
        if existing_kind == kind:
            return "conflict", subscription.name, _("Verwaltetes Abo gleicher Art ueberschneidet den Zeitraum.")
        if existing_kind not in {BILLING_KIND_MEMBERSHIP, BILLING_KIND_RENTAL}:
            return "conflict", subscription.name, _("Verwaltetes Abo ohne eindeutige Abrechnungsart ueberschneidet den Zeitraum.")
    return "create", None, ""


def periods_overlap(first_from, first_to, second_from, second_to) -> bool:
    first_start = getdate(first_from)
    first_end = getdate(first_to) if first_to else date.max
    second_start = getdate(second_from)
    second_end = getdate(second_to) if second_to else date.max
    return first_start <= second_end and second_start <= first_end


def create_managed_subscription(
    run,
    row,
    *,
    kind: str,
    source_doctype: str,
    source_name: str,
    build_subscription,
    validate_current=None,
):
    key = row.generation_key
    payload = row.generation_payload
    sources = json.loads(payload).get("sources", [])
    plan_contracts = json.loads(payload).get("plan_contracts", [])
    source_keys = {
        (source.get("source_doctype"), source.get("source_name"))
        for source in sources
        if source.get("source_doctype") and source.get("source_name")
    }
    source_keys.add((source_doctype, source_name))
    for locked_doctype, locked_name in sorted(source_keys):
        lock_source(locked_doctype, locked_name)
    for plan_name in sorted({contract.get("name") for contract in plan_contracts if contract.get("name")}):
        if not frappe.db.get_value("Subscription Plan", plan_name, "name", for_update=True):
            raise BillingRowError(_("Ein Subscription Plan wurde seit der Vorschau entfernt."))
    if validate_current:
        validate_current()
    decision, existing, message = classify_subscription(key, payload, row.customer, row.period_from, row.period_to, kind)
    if decision == "skip":
        return None, existing, message
    if decision == "conflict":
        raise BillingConflict(message, existing)

    savepoint = f"erpverein_billing_{frappe.generate_hash(length=12)}"
    frappe.db.savepoint(savepoint)
    try:
        subscription = build_subscription()
        subscription.set(SUBSCRIPTION_MANAGED_FIELDNAME, 1)
        subscription.set(SUBSCRIPTION_BILLING_KIND_FIELDNAME, kind)
        subscription.set(SUBSCRIPTION_GENERATION_KEY_FIELDNAME, key)
        subscription.set(SUBSCRIPTION_GENERATION_PAYLOAD_FIELDNAME, payload)
        subscription.set(SUBSCRIPTION_RUN_DOCTYPE_FIELDNAME, run.doctype)
        subscription.set(SUBSCRIPTION_RUN_FIELDNAME, run.name)
        for source in json.loads(payload).get("sources", []):
            subscription.append(SUBSCRIPTION_SOURCES_FIELDNAME, source)
        subscription.flags.erpverein_generation = True
        try:
            subscription.insert()
        finally:
            subscription.flags.pop("erpverein_generation", None)
        frappe.db.release_savepoint(savepoint)
        return subscription, None, _("Abo erstellt.")
    except Exception as exc:
        if is_retryable_error(exc):
            raise
        frappe.db.rollback(save_point=savepoint)
        frappe.db.release_savepoint(savepoint)
        if _is_unique_error(exc):
            decision, existing, message = classify_subscription(key, payload, row.customer, row.period_from, row.period_to, kind)
            if decision == "skip":
                return None, existing, message
            if decision == "conflict":
                raise BillingConflict(message, existing) from exc
        frappe.log_error(title=f"ERPverein billing row failed: {run.doctype} {run.name}", message=frappe.get_traceback())
        raise BillingRowError(_("Abo konnte nicht erstellt werden. Details wurden protokolliert.")) from exc


def validate_plan_interval(plan: str | None, interval: str, label: str) -> dict:
    if not plan:
        frappe.throw(_("{0} fehlt.").format(label))
    values = plan_contract(plan)
    if not values or values.billing_interval != interval or cint(values.billing_interval_count) != 1:
        frappe.throw(_("{0} {1} muss Intervall {2} mit Anzahl 1 verwenden.").format(label, frappe.bold(plan), interval))
    if values.price_determination != "Fixed Rate":
        frappe.throw(_("{0} {1} muss die Preisermittlung Fixed Rate verwenden.").format(label, frappe.bold(plan)))
    return values


def plan_contract(plan: str) -> dict:
    rows = frappe.db.get_list(
        "Subscription Plan",
        filters={"name": plan},
        fields=[
            "name",
            "billing_interval",
            "billing_interval_count",
            "currency",
            "item",
            "price_determination",
            "cost",
            "cost_center",
        ],
        limit_page_length=1,
    )
    if not rows:
        frappe.throw(_("Subscription Plan {0} wurde nicht gefunden oder darf nicht gelesen werden.").format(frappe.bold(plan)))
    row = rows[0]
    effective_rate = flt(row.cost)
    try:
        from erpnext.accounts.doctype.subscription_plan.subscription_plan import get_plan_rate

        effective_rate = flt(get_plan_rate(plan, quantity=1))
    except Exception:
        if row.price_determination != "Fixed Rate":
            raise
    return frappe._dict(
        {
            "name": row.name,
            "billing_interval": row.billing_interval,
            "billing_interval_count": cint(row.billing_interval_count),
            "currency": row.currency,
            "item": row.item,
            "price_determination": row.price_determination,
            "cost": flt(row.cost),
            "effective_rate": effective_rate,
            "cost_center": row.cost_center,
        }
    )


def validate_plan_currency(plan: str, customer: str, company: str) -> None:
    contract = plan_contract(plan)
    customer_rows = frappe.db.get_list(
        "Customer", filters={"name": customer}, fields=["default_currency"], limit_page_length=1
    )
    company_rows = frappe.db.get_list(
        "Company", filters={"name": company}, fields=["default_currency"], limit_page_length=1
    )
    expected = (customer_rows[0].default_currency if customer_rows else None) or (
        company_rows[0].default_currency if company_rows else None
    )
    if expected and contract.currency != expected:
        frappe.throw(
            _("Subscription Plan {0} verwendet {1}, erwartet wird {2} fuer Kunde/Unternehmen.").format(
                frappe.bold(plan), frappe.bold(contract.currency), frappe.bold(expected)
            )
        )


def assert_plan_contracts_current(expected_contracts: list[dict]) -> None:
    current = [dict(plan_contract(contract["name"])) for contract in expected_contracts]
    if canonical_json(current) != canonical_json(expected_contracts):
        raise BillingRowError(_("Ein Subscription Plan hat sich seit der Vorschau geaendert."))


def _is_unique_error(exc: Exception) -> bool:
    names = {cls.__name__ for cls in type(exc).mro()}
    if names & {"UniqueValidationError", "DuplicateEntryError"}:
        return True
    args = getattr(exc, "args", ())
    return bool(args and args[0] == 1062)


def is_retryable_error(exc: Exception) -> bool:
    names = {cls.__name__ for cls in type(exc).mro()}
    return bool(
        names
        & {
            "RetryBackgroundJobError",
            "QueryDeadlockError",
            "QueryTimeoutError",
            "DeadlockError",
            "InternalError",
        }
    )


def _document_value(value):
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        value = as_dict(no_nulls=False)
        for field in ("name", "owner", "creation", "modified", "modified_by", "parent", "parentfield", "parenttype", "doctype", "idx", "docstatus"):
            value.pop(field, None)
    return _json_value(value)


def _json_value(value):
    as_dict = getattr(value, "as_dict", None)
    if callable(as_dict):
        value = as_dict(no_nulls=False)
        for field in (
            "name",
            "owner",
            "creation",
            "modified",
            "modified_by",
            "parent",
            "parentfield",
            "parenttype",
            "doctype",
            "idx",
            "docstatus",
        ):
            value.pop(field, None)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return str(value)
    return value
