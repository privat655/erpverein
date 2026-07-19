import json
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, getdate, nowdate

from erpverein.services.billing_common import (
    BILLING_KIND_RENTAL,
    RUN_STATUS_DRAFT,
    RUN_STATUS_EXECUTED,
    RUN_STATUS_PARTIAL,
    RUN_STATUS_PREVIEW,
    RUN_STATUS_PREVIEW_ERRORS,
    BillingConflict,
    BillingRowError,
    assert_plan_contracts_current,
    classify_subscription,
    create_managed_subscription,
    ensure_preview_editable,
    generation_key,
    generation_payload,
    make_preview_hash,
    plan_contract,
    parse_selection_json,
    periods_overlap,
    validate_plan_interval,
    validate_plan_currency,
)
from erpverein.services.mieter_service import BILLING_TYPE_DIRECT_DEBIT, BILLING_TYPE_INVOICE
from erpverein.services.subscription_generation_service import (
    DEFAULT_GENERATE_INVOICE_AT,
    INVOICE_AT_DAYS_BEFORE,
    INVOICE_AT_PERIOD_END,
    INVOICE_AT_PERIOD_START,
    get_default_company,
    get_default_cost_center,
    get_erpnext_generate_invoice_at,
    get_active_source_mandate,
    normalize_generate_invoice_at,
    subscription_settings_payload,
)


RUN_DOCTYPE = "Mietabrechnung"
SCOPE_SINGLE = "Einzelner Mieter"
SCOPE_SELECTED = "Ausgewaehlte Mieter"
SCOPE_ALL = "Alle Mieter"
ACTION_CREATE = "Create"
ACTION_CONFLICT = "Conflict"
ACTION_ERROR = "Error"
ACTION_CREATED = "Created"
ACTION_SKIPPED = "Skipped"
ACTION_NO_OVERLAP = "No Overlap"
PAYING_BILLING_TYPES = {BILLING_TYPE_INVOICE, BILLING_TYPE_DIRECT_DEBIT}
RUN_INPUT_FIELDS = [
    "scope",
    "mieter",
    "selected_mieter_json",
    "company",
    "cost_center",
    "submit_invoice",
    "generate_new_invoices_past_due_date",
    "generate_invoice_at",
    "number_of_days",
    "days_until_due",
]
PREVIEW_HASH_FIELDS = [
    "mieter",
    "customer",
    "period_from",
    "period_to",
    "subscription_plan",
    "plans_json",
    "action",
    "generation_key",
    "generation_payload",
]


@dataclass
class TenantBillingInfo:
    name: str
    customer: str | None
    abrechnungsart: str
    mietbeginn: str
    mietende: str | None
    sepa_mandat: str | None


def set_rental_run_defaults(doc) -> None:
    doc.status = doc.status or RUN_STATUS_DRAFT
    doc.scope = doc.scope or SCOPE_ALL
    doc.company = doc.company or get_default_company()
    doc.cost_center = doc.cost_center or get_default_cost_center(doc.company)
    doc.submit_invoice = 1 if doc.submit_invoice is None else doc.submit_invoice
    doc.generate_new_invoices_past_due_date = 1 if doc.generate_new_invoices_past_due_date is None else doc.generate_new_invoices_past_due_date
    doc.generate_invoice_at = doc.generate_invoice_at or DEFAULT_GENERATE_INVOICE_AT
    doc.days_until_due = cint(doc.days_until_due)
    doc.number_of_days = cint(doc.number_of_days)

    if not doc.get("periods"):
        add_default_periods(doc)


def add_default_periods(doc) -> None:
    if doc.get("mieter"):
        tenant = frappe.db.get_value("Mieter", doc.mieter, ["mietbeginn", "mietende"], as_dict=True)
        if tenant and tenant.mietbeginn:
            doc.append("periods", {"from_date": tenant.mietbeginn, "to_date": tenant.mietende})
            return

    today = getdate(nowdate())
    doc.append("periods", {"from_date": f"{today.year}-01-01", "to_date": f"{today.year}-12-31"})


def create_rental_subscription_run(scope: str = SCOPE_ALL, mieter: str | None = None, mieter_names: list[str] | None = None):
    run = frappe.new_doc(RUN_DOCTYPE)
    run.scope = scope
    run.mieter = mieter
    if mieter_names:
        run.selected_mieter_json = json.dumps(sorted(set(mieter_names)))
    set_rental_run_defaults(run)
    run.insert()
    return run


def create_run_for_mieter(mieter: str):
    return create_rental_subscription_run(scope=SCOPE_SINGLE, mieter=mieter)


def create_run_for_selection(mieter_names: list[str] | None = None):
    mieter_names = mieter_names or []
    return create_rental_subscription_run(scope=SCOPE_SELECTED if mieter_names else SCOPE_ALL, mieter_names=mieter_names)


def build_rental_subscription_preview(run_name: str) -> dict:
    run = frappe.get_doc(RUN_DOCTYPE, run_name)
    run.check_permission("write")
    ensure_preview_editable(run)
    set_rental_run_defaults(run)
    run.set("preview_rows", [])

    rows = make_preview_rows(run)
    for row in rows:
        run.append("preview_rows", row)

    errors = sum(1 for row in rows if row["action"] in {ACTION_ERROR, ACTION_CONFLICT})
    run.status = RUN_STATUS_PREVIEW if not errors else RUN_STATUS_PREVIEW_ERRORS
    run.result_summary = json.dumps({"total": len(rows), "errors": errors}, sort_keys=True)
    run.preview_hash = compute_rental_preview_hash(run)
    run.flags.erpverein_worker_update = True
    run.save()
    return {"run": run.name, "total": len(rows), "errors": errors, "preview_hash": run.preview_hash}


def compute_rental_preview_hash(run) -> str:
    return make_preview_hash(run, RUN_INPUT_FIELDS, PREVIEW_HASH_FIELDS)


def make_preview_rows(run) -> list[dict]:
    rows = []
    for tenant in get_tenants_for_run(run).values():
        rows.extend(make_preview_rows_for_tenant(run, tenant))
    return rows


def get_tenants_for_run(run) -> dict[str, TenantBillingInfo]:
    selected_names = get_selected_tenant_names(run)
    if run.scope == SCOPE_ALL:
        rows = frappe.db.get_list(
            "Mieter",
            fields=["name", "customer", "abrechnungsart", "mietbeginn", "mietende", "sepa_mandat"],
            order_by="name asc",
        )
        return {row.name: make_tenant_info(row) for row in rows if row.abrechnungsart in PAYING_BILLING_TYPES}

    return get_tenant_rows(selected_names)


def get_selected_tenant_names(run) -> list[str]:
    if run.scope == SCOPE_SINGLE:
        return [run.mieter] if run.mieter else []
    if run.scope == SCOPE_SELECTED:
        return parse_selection_json(run.selected_mieter_json, _("Ausgewaehlte Mieter"))
    return []


def get_tenant_rows(names: list[str]) -> dict[str, TenantBillingInfo]:
    if not names:
        return {}
    rows = frappe.db.get_list(
        "Mieter",
        filters={"name": ["in", names]},
        fields=["name", "customer", "abrechnungsart", "mietbeginn", "mietende", "sepa_mandat"],
        order_by="name asc",
    )
    return {row.name: make_tenant_info(row) for row in rows}


def make_tenant_info(row) -> TenantBillingInfo:
    return TenantBillingInfo(
        row.name,
        row.customer,
        row.abrechnungsart,
        str(row.mietbeginn),
        str(row.mietende) if row.mietende else None,
        row.sepa_mandat,
    )


def make_preview_rows_for_tenant(run, tenant: TenantBillingInfo) -> list[dict]:
    if not tenant.customer:
        return [make_error_row(tenant, _("Mieter hat keinen Kunden."))]
    if tenant.abrechnungsart == BILLING_TYPE_DIRECT_DEBIT and not get_active_source_mandate(
        "Mieter", tenant.name, tenant.sepa_mandat
    ):
        return [make_error_row(tenant, _("Mieter hat kein gueltiges aktives SEPA-Mandat."))]

    rows = []
    for period in run.periods:
        rows.append(make_preview_row_for_period(run, tenant, period))
    return rows


def make_preview_row_for_period(run, tenant: TenantBillingInfo, period) -> dict:
    if not period.subscription_plan:
        return make_error_row(tenant, _("Mietplan fehlt fuer Periode {0}.").format(frappe.bold(period.from_date)), period_from=period.from_date, period_to=period.to_date)
    try:
        validate_plan_currency(period.subscription_plan, tenant.customer, run.company)
    except frappe.ValidationError as exc:
        return make_error_row(tenant, str(exc), period_from=period.from_date, period_to=period.to_date)

    effective_from, effective_to = get_effective_period(tenant, period)
    if not effective_from:
        return make_no_overlap_row(tenant, period)

    if effective_to and getdate(effective_to) <= getdate(add_days(add_to_date(effective_from, months=1), -1)):
        return make_error_row(
            tenant,
            _("Effektives Mietende muss nach dem Ende des ersten Abrechnungszyklus liegen."),
            period_from=effective_from,
            period_to=effective_to,
        )
    key = generation_key(BILLING_KIND_RENTAL, tenant.name, effective_from, effective_to)
    sources = [{"source_doctype": "Mieter", "source_name": tenant.name, "source_role": "Mieter"}]
    payload = generation_payload(
        {
            "kind": BILLING_KIND_RENTAL,
            "mieter": tenant.name,
            "customer": tenant.customer,
            "period": {"from": str(getdate(effective_from)), "to": str(getdate(effective_to)) if effective_to else None},
            "plans": [{"plan": period.subscription_plan, "qty": 1}],
            "plan_contracts": [dict(plan_contract(period.subscription_plan))],
            "sources": sources,
            "source_contracts": [rental_source_contract(tenant)],
            "sepa_mandat": tenant.sepa_mandat if tenant.abrechnungsart == BILLING_TYPE_DIRECT_DEBIT else None,
            "settings": subscription_settings_payload(run),
        }
    )
    decision, existing_subscription, conflict_message = classify_subscription(
        key, payload, tenant.customer, effective_from, effective_to, BILLING_KIND_RENTAL
    )
    action = ACTION_CREATE if decision == "create" else ACTION_SKIPPED if decision == "skip" else ACTION_CONFLICT
    return {
        "mieter": tenant.name,
        "customer": tenant.customer,
        "billing_type": tenant.abrechnungsart,
        "mietbeginn": tenant.mietbeginn,
        "mietende": tenant.mietende,
        "period_from": effective_from,
        "period_to": effective_to,
        "subscription_plan": period.subscription_plan,
        "plans_json": json.dumps([{"plan": period.subscription_plan, "qty": 1}], sort_keys=True),
        "generation_key": key,
        "generation_payload": payload,
        "existing_subscription": existing_subscription,
        "estimated_invoice_count": estimate_invoice_count(
            frappe._dict({"from_date": effective_from, "to_date": effective_to}),
            run.generate_invoice_at,
            run.number_of_days,
            bool(run.generate_new_invoices_past_due_date),
        ),
        "action": action,
        "message": conflict_message,
    }


def get_effective_period(tenant: TenantBillingInfo, period) -> tuple[str | None, str | None]:
    tenant_start = getdate(tenant.mietbeginn)
    tenant_end = getdate(tenant.mietende) if tenant.mietende else None
    period_start = getdate(period.from_date)
    period_end = getdate(period.to_date) if period.to_date else None

    effective_start = max(tenant_start, period_start)
    effective_end = min(date for date in [tenant_end, period_end] if date) if tenant_end or period_end else None
    if effective_end and effective_end < effective_start:
        return None, None
    return str(effective_start), str(effective_end) if effective_end else None


def make_no_overlap_row(tenant: TenantBillingInfo, period) -> dict:
    return {
        "mieter": tenant.name,
        "customer": tenant.customer,
        "billing_type": tenant.abrechnungsart,
        "mietbeginn": tenant.mietbeginn,
        "mietende": tenant.mietende,
        "period_from": period.from_date,
        "period_to": period.to_date,
        "estimated_invoice_count": 0,
        "action": ACTION_NO_OVERLAP,
        "message": _("Keine Ueberschneidung mit Mietzeitraum."),
    }


def make_error_row(tenant: TenantBillingInfo, message: str, **overrides) -> dict:
    row = {
        "mieter": tenant.name,
        "customer": tenant.customer,
        "billing_type": tenant.abrechnungsart,
        "mietbeginn": tenant.mietbeginn,
        "mietende": tenant.mietende,
        "estimated_invoice_count": 0,
        "action": ACTION_ERROR,
        "message": message,
    }
    row.update(overrides)
    return row


def estimate_invoice_count(
    period,
    generate_invoice_at: str,
    number_of_days: int = 0,
    generate_new_invoices_past_due_date: bool = True,
) -> int:
    if not period.from_date:
        return 0
    today = getdate(nowdate())
    current_start = getdate(period.from_date)
    if current_start > today:
        return 0
    end_date = getdate(period.to_date) if period.to_date else None
    count = 0

    for _ in range(600):
        current_end = add_days(add_to_date(current_start, months=1), -1)
        if end_date and getdate(current_end) > end_date:
            current_end = end_date
        if getdate(get_invoice_trigger_date(current_start, current_end, generate_invoice_at, number_of_days)) <= today:
            count += 1
            if not generate_new_invoices_past_due_date:
                break
        else:
            break
        next_start = add_days(current_end, 1)
        if end_date and getdate(next_start) > end_date:
            break
        current_start = getdate(next_start)
    return count


def get_invoice_trigger_date(period_start, period_end, generate_invoice_at: str, number_of_days: int = 0):
    invoice_timing = normalize_generate_invoice_at(generate_invoice_at)
    if invoice_timing == INVOICE_AT_PERIOD_START:
        return period_start
    if invoice_timing == INVOICE_AT_DAYS_BEFORE:
        return add_days(period_start, -cint(number_of_days))
    if invoice_timing == INVOICE_AT_PERIOD_END:
        return period_end
    return period_start


def create_subscriptions_from_preview(run_name: str) -> dict:
    run = frappe.get_doc(RUN_DOCTYPE, run_name)
    run.check_permission("write")
    if not run.preview_rows:
        frappe.throw(_("Bitte zuerst eine Vorschau anzeigen."))

    created = skipped = errors = 0
    for row in run.preview_rows:
        if row.created_subscription or row.action in {ACTION_CREATED, ACTION_SKIPPED, ACTION_NO_OVERLAP}:
            skipped += 1
            continue
        if row.action != ACTION_CREATE:
            errors += 1
            continue
        try:
            subscription, existing, message = create_subscription_for_preview_row(run, row)
            row.created_subscription = subscription.name if subscription else existing
            row.action = ACTION_CREATED if subscription else ACTION_SKIPPED
            row.message = message
            created += int(bool(subscription))
            skipped += int(not subscription)
        except BillingConflict as exc:
            row.existing_subscription = exc.subscription
            row.action = ACTION_CONFLICT
            row.message = str(exc)
            errors += 1
        except BillingRowError as exc:
            row.action = ACTION_ERROR
            row.message = str(exc)
            errors += 1

    run.flags.erpverein_worker_update = True
    run.status = RUN_STATUS_EXECUTED if errors == 0 else RUN_STATUS_PARTIAL
    run.result_summary = json.dumps({"created": created, "skipped": skipped, "errors": errors}, sort_keys=True)
    run.finished_at = frappe.utils.now_datetime()
    run.save()
    return {"run": run.name, "created": created, "skipped": skipped, "errors": errors}


def create_subscription_for_preview_row(run, row):
    payload = json.loads(row.generation_payload)
    if row.billing_type == BILLING_TYPE_DIRECT_DEBIT and not get_active_source_mandate(
        "Mieter", row.mieter, payload.get("sepa_mandat")
    ):
        raise BillingRowError(_("Das aktive SEPA-Mandat hat sich seit der Vorschau geaendert."))

    def build_subscription():
        subscription = frappe.new_doc("Subscription")
        subscription.party_type = "Customer"
        subscription.party = row.customer
        subscription.company = run.company
        subscription.cost_center = run.cost_center
        subscription.start_date = row.period_from
        subscription.end_date = row.period_to
        subscription.generate_new_invoices_past_due_date = run.generate_new_invoices_past_due_date
        subscription.submit_invoice = run.submit_invoice
        subscription.generate_invoice_at = get_erpnext_generate_invoice_at(run.generate_invoice_at)
        subscription.number_of_days = run.number_of_days
        subscription.days_until_due = run.days_until_due
        for plan_line in load_plan_lines(row):
            subscription.append("plans", plan_line)
        return subscription

    return create_managed_subscription(
        run,
        row,
        kind=BILLING_KIND_RENTAL,
        source_doctype="Mieter",
        source_name=row.mieter,
        build_subscription=build_subscription,
        validate_current=lambda: validate_rental_payload_current(run, row, payload),
    )


def load_plan_lines(row) -> list[dict]:
    return json.loads(row.plans_json) if row.plans_json else [{"plan": row.subscription_plan, "qty": 1}]


def rental_source_contract(tenant: TenantBillingInfo) -> dict:
    return {
        "name": tenant.name,
        "customer": tenant.customer,
        "abrechnungsart": tenant.abrechnungsart,
        "mietbeginn": tenant.mietbeginn,
        "mietende": tenant.mietende,
        "sepa_mandat": tenant.sepa_mandat,
    }


def validate_rental_payload_current(run, row, payload: dict) -> None:
    current = get_tenant_rows([row.mieter]).get(row.mieter)
    expected = (payload.get("source_contracts") or [None])[0]
    if not current or generation_payload(rental_source_contract(current)) != generation_payload(expected):
        raise BillingRowError(_("Mieter- oder Mietdaten haben sich seit der Vorschau geaendert."))
    assert_plan_contracts_current(payload.get("plan_contracts", []))
    validate_plan_currency(row.subscription_plan, row.customer, run.company)


def validate_rental_periods(doc) -> None:
    ranges = []
    for period in doc.get("periods") or []:
        if not period.from_date:
            frappe.throw(_("Von-Datum ist fuer jede Mietperiode erforderlich."))
        start = getdate(period.from_date)
        end = getdate(period.to_date) if period.to_date else None
        if end and end < start:
            frappe.throw(_("Bis-Datum darf nicht vor dem Von-Datum liegen."))
        if period.subscription_plan:
            validate_plan_interval(period.subscription_plan, "Month", _("Mietplan"))
        for other_start, other_end in ranges:
            if periods_overlap(start, end, other_start, other_end):
                frappe.throw(_("Mietperioden duerfen sich nicht ueberschneiden oder doppelt vorkommen."))
        ranges.append((start, end))
