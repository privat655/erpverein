import json
from collections import defaultdict
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, flt, getdate, nowdate

from erpverein.services.billing_common import (
    BILLING_KIND_MEMBERSHIP,
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
from erpverein.services.mitglied_service import (
    BILLING_TYPE_COVERED,
    BILLING_TYPE_DIRECT_DEBIT,
    BILLING_TYPE_FREE,
    BILLING_TYPE_INVOICE,
)


RUN_DOCTYPE = "Beitragsabrechnung"
SCOPE_SINGLE = "Einzelnes Mitglied"
SCOPE_SELECTED = "Ausgewaehlte Mitglieder"
SCOPE_ALL = "Alle Mitglieder"
ACTION_CREATE = "Create"
ACTION_CONFLICT = "Conflict"
ACTION_ERROR = "Error"
ACTION_CREATED = "Created"
ACTION_SKIPPED = "Skipped"
INVOICE_AT_PERIOD_END = "Periodenende"
INVOICE_AT_PERIOD_START = "Periodenbeginn"
INVOICE_AT_DAYS_BEFORE = "Tage vor Periodenbeginn"
ERP_INVOICE_AT_PERIOD_END = "End of the current subscription period"
ERP_INVOICE_AT_PERIOD_START = "Beginning of the current subscription period"
ERP_INVOICE_AT_DAYS_BEFORE = "Days before the current subscription period"
DEFAULT_GENERATE_INVOICE_AT = INVOICE_AT_PERIOD_START
ERP_GENERATE_INVOICE_AT_VALUES = {
    INVOICE_AT_PERIOD_END: ERP_INVOICE_AT_PERIOD_END,
    INVOICE_AT_PERIOD_START: ERP_INVOICE_AT_PERIOD_START,
    INVOICE_AT_DAYS_BEFORE: ERP_INVOICE_AT_DAYS_BEFORE,
    ERP_INVOICE_AT_PERIOD_END: ERP_INVOICE_AT_PERIOD_END,
    ERP_INVOICE_AT_PERIOD_START: ERP_INVOICE_AT_PERIOD_START,
    ERP_INVOICE_AT_DAYS_BEFORE: ERP_INVOICE_AT_DAYS_BEFORE,
}
HISTORICAL_RATE_CUTOFF = "2023-01-01"
PAYING_BILLING_TYPES = {BILLING_TYPE_INVOICE, BILLING_TYPE_DIRECT_DEBIT}
RUN_INPUT_FIELDS = [
    "scope",
    "mitglied",
    "selected_mitglieder_json",
    "company",
    "cost_center",
    "submit_invoice",
    "generate_new_invoices_past_due_date",
    "generate_invoice_at",
    "number_of_days",
    "days_until_due",
]
PREVIEW_HASH_FIELDS = [
    "payer_mitglied",
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
class MemberBillingInfo:
    name: str
    customer: str | None
    abrechnungsart: str
    jahresbeitrag: float
    beitragszahler: str | None
    sepa_mandat: str | None


def set_subscription_run_defaults(doc) -> None:
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


def get_default_company() -> str | None:
    try:
        from erpnext import get_default_company as erpnext_get_default_company

        company = erpnext_get_default_company()
    except Exception:
        company = None

    return company or frappe.defaults.get_user_default("Company") or frappe.db.get_value("Company", {}, "name")


def get_default_cost_center(company: str | None = None) -> str | None:
    filters = {"is_group": 0}
    if company and frappe.get_meta("Cost Center").has_field("company"):
        filters["company"] = company
    return frappe.db.get_value("Cost Center", filters, "name", order_by="lft asc")


def add_default_periods(doc) -> None:
    suggestions = suggest_subscription_plans()
    doc.append("periods", {"from_date": "2016-01-01", "to_date": "2022-12-31", "subscription_plan": suggestions.get(250)})
    doc.append("periods", {"from_date": "2023-01-01", "subscription_plan": suggestions.get(350)})


def suggest_subscription_plans() -> dict[float, str]:
    suggestions = {}
    plans = frappe.db.get_list(
        "Subscription Plan",
        filters={"billing_interval": "Year", "billing_interval_count": 1},
        fields=["name", "price_determination", "cost"],
        order_by="creation asc",
    )
    for plan in plans:
        amount = get_subscription_plan_amount(plan.name, plan.price_determination, plan.cost)
        if amount and amount not in suggestions:
            suggestions[amount] = plan.name
    return suggestions


def get_subscription_plan_amount(plan: str, price_determination: str | None = None, cost=None) -> float | None:
    try:
        if price_determination == "Fixed Rate":
            return flt(cost)
        from erpnext.accounts.doctype.subscription_plan.subscription_plan import get_plan_rate

        return flt(get_plan_rate(plan, quantity=1))
    except Exception:
        return None


def create_subscription_run(scope: str = SCOPE_ALL, mitglied: str | None = None, mitglieder: list[str] | None = None):
    run = frappe.new_doc(RUN_DOCTYPE)
    run.scope = scope
    run.mitglied = mitglied
    if mitglieder:
        run.selected_mitglieder_json = json.dumps(sorted(set(mitglieder)))
    set_subscription_run_defaults(run)
    run.insert()
    return run


def create_run_for_mitglied(mitglied: str):
    return create_subscription_run(scope=SCOPE_SINGLE, mitglied=mitglied)


def create_run_for_selection(mitglieder: list[str] | None = None):
    mitglieder = mitglieder or []
    return create_subscription_run(scope=SCOPE_SELECTED if mitglieder else SCOPE_ALL, mitglieder=mitglieder)


def build_subscription_preview(run_name: str) -> dict:
    run = frappe.get_doc(RUN_DOCTYPE, run_name)
    run.check_permission("write")
    ensure_preview_editable(run)
    set_subscription_run_defaults(run)
    run.set("preview_rows", [])

    rows = make_preview_rows(run)
    for row in rows:
        run.append("preview_rows", row)

    errors = sum(1 for row in rows if row["action"] in {ACTION_ERROR, ACTION_CONFLICT})
    run.status = RUN_STATUS_PREVIEW if not errors else RUN_STATUS_PREVIEW_ERRORS
    run.result_summary = json.dumps({"total": len(rows), "errors": errors}, sort_keys=True)
    run.preview_hash = compute_subscription_preview_hash(run)
    run.flags.erpverein_worker_update = True
    run.save()
    return {"run": run.name, "total": len(rows), "errors": errors, "preview_hash": run.preview_hash}


def compute_subscription_preview_hash(run) -> str:
    return make_preview_hash(run, RUN_INPUT_FIELDS, PREVIEW_HASH_FIELDS)


def make_preview_rows(run) -> list[dict]:
    groups = build_payer_groups(get_members_for_run(run))
    rows = []
    for payer_name in sorted(groups):
        rows.extend(make_preview_rows_for_payer(run, groups[payer_name]["payer"], groups[payer_name]["members"]))
    return rows


def get_members_for_run(run) -> dict[str, MemberBillingInfo]:
    selected_names = get_selected_member_names(run)
    if run.scope == SCOPE_ALL:
        rows = frappe.db.get_list(
            "Mitglied",
            fields=["name", "customer", "abrechnungsart", "jahresbeitrag", "beitragszahler", "sepa_mandat"],
            order_by="name asc",
        )
        return {row.name: make_member_info(row) for row in rows}

    selected = get_member_rows(selected_names)
    payer_names = {row.name for row in selected.values() if row.abrechnungsart in PAYING_BILLING_TYPES}
    payer_names.update(row.beitragszahler for row in selected.values() if row.abrechnungsart == BILLING_TYPE_COVERED and row.beitragszahler)

    names = set(selected_names) | payer_names
    if payer_names:
        covered_rows = frappe.db.get_list(
            "Mitglied",
            filters={"beitragszahler": ["in", sorted(payer_names)]},
            fields=["name"],
        )
        names.update(row.name for row in covered_rows)
    return get_member_rows(sorted(name for name in names if name))


def get_selected_member_names(run) -> list[str]:
    if run.scope == SCOPE_SINGLE:
        return [run.mitglied] if run.mitglied else []
    if run.scope == SCOPE_SELECTED:
        return parse_selection_json(run.selected_mitglieder_json, _("Ausgewaehlte Mitglieder"))
    return []


def get_member_rows(names: list[str]) -> dict[str, MemberBillingInfo]:
    if not names:
        return {}
    rows = frappe.db.get_list(
        "Mitglied",
        filters={"name": ["in", names]},
        fields=["name", "customer", "abrechnungsart", "jahresbeitrag", "beitragszahler", "sepa_mandat"],
        order_by="name asc",
    )
    return {row.name: make_member_info(row) for row in rows}


def make_member_info(row) -> MemberBillingInfo:
    return MemberBillingInfo(row.name, row.customer, row.abrechnungsart, flt(row.jahresbeitrag), row.beitragszahler, row.sepa_mandat)


def build_payer_groups(members: dict[str, MemberBillingInfo]) -> dict[str, dict]:
    groups = {}
    for member in list(members.values()):
        if member.abrechnungsart in PAYING_BILLING_TYPES:
            groups.setdefault(member.name, {"payer": member, "members": []})
        elif member.abrechnungsart == BILLING_TYPE_COVERED and member.beitragszahler:
            payer = members.get(member.beitragszahler) or get_member_rows([member.beitragszahler]).get(member.beitragszahler)
            if payer:
                members[payer.name] = payer
                groups.setdefault(payer.name, {"payer": payer, "members": []})

    for payer_name, group in groups.items():
        if group["payer"].abrechnungsart in PAYING_BILLING_TYPES:
            group["members"].append(group["payer"])
    for member in members.values():
        if member.abrechnungsart == BILLING_TYPE_COVERED and member.beitragszahler in groups:
            groups[member.beitragszahler]["members"].append(member)
    return groups


def make_preview_rows_for_payer(run, payer: MemberBillingInfo, members: list[MemberBillingInfo]) -> list[dict]:
    if payer.abrechnungsart == BILLING_TYPE_FREE:
        return []
    if not payer.customer:
        return [make_error_row(payer, _("Beitragszahler hat keinen Kunden."))]
    if payer.abrechnungsart == BILLING_TYPE_DIRECT_DEBIT and not get_active_source_mandate(
        "Mitglied", payer.name, "Mitgliedsbeitrag", payer.sepa_mandat
    ):
        return [make_error_row(payer, _("Beitragszahler hat kein gueltiges aktives SEPA-Mandat."))]

    rows = []
    periods_by_range = defaultdict(list)
    for period in run.periods:
        periods_by_range[(str(period.from_date), str(period.to_date or ""))].append(period)

    for period_key, periods in sorted(periods_by_range.items()):
        plan_lines, errors = build_plan_lines_for_period(members, periods, period_key)
        for plan_line in plan_lines:
            try:
                validate_plan_currency(plan_line["plan"], payer.customer, run.company)
            except frappe.ValidationError as exc:
                errors.append(str(exc))
        rows.extend(make_error_row(payer, message, period_from=period_key[0], period_to=period_key[1] or None) for message in errors)
        if plan_lines:
            rows.append(make_preview_row(run, payer, members, period_key[0], period_key[1] or None, plan_lines))
    return rows


def build_plan_lines_for_period(members: list[MemberBillingInfo], periods, period_key) -> tuple[list[dict], list[str]]:
    if is_historical_period(period_key[1]):
        period = periods[0] if periods else None
        if not period or not period.subscription_plan:
            return [], [_("Beitragsplan fehlt fuer historische Periode {0}.").format(frappe.bold(period_key[0]))]
        return [{"plan": period.subscription_plan, "qty": len(members)}], []

    plan_amounts = []
    errors = []
    for period in periods:
        if not period.subscription_plan:
            errors.append(_("Beitragsplan fehlt fuer Periode {0}.").format(frappe.bold(period_key[0])))
            continue
        amount = get_subscription_plan_amount(period.subscription_plan)
        if not amount:
            errors.append(_("Betrag fuer Beitragsplan {0} konnte nicht ermittelt werden.").format(frappe.bold(period.subscription_plan)))
            continue
        plan_amounts.append((period.subscription_plan, amount))

    plan_lines = []
    matched = set()
    for plan, amount in plan_amounts:
        matching = [member for member in members if flt(member.jahresbeitrag) == flt(amount)]
        if matching:
            matched.update(member.name for member in matching)
            plan_lines.append({"plan": plan, "qty": len(matching)})

    for member in members:
        if member.name not in matched:
            errors.append(
                _("Kein Beitragsplan fuer Jahresbeitrag {0} in Periode {1} gefunden.").format(
                    frappe.bold(member.jahresbeitrag), frappe.bold(period_key[0])
                )
            )
    return plan_lines, errors


def is_historical_period(period_to: str | None) -> bool:
    return bool(period_to and getdate(period_to) < getdate(HISTORICAL_RATE_CUTOFF))


def make_preview_row(run, payer: MemberBillingInfo, members: list[MemberBillingInfo], period_from, period_to, plan_lines: list[dict]) -> dict:
    sources = [{"source_doctype": "Mitglied", "source_name": payer.name, "source_role": "Beitragszahler"}]
    sources.extend(
        {"source_doctype": "Mitglied", "source_name": member.name, "source_role": "Uebernommenes Mitglied"}
        for member in sorted(members, key=lambda member: member.name)
        if member.name != payer.name
    )
    key = generation_key(BILLING_KIND_MEMBERSHIP, payer.name, period_from, period_to)
    payload = generation_payload(
        {
            "kind": BILLING_KIND_MEMBERSHIP,
            "payer": payer.name,
            "customer": payer.customer,
            "period": {"from": str(getdate(period_from)), "to": str(getdate(period_to)) if period_to else None},
            "plans": sorted(plan_lines, key=lambda line: (line["plan"], cint(line["qty"]))),
            "plan_contracts": [dict(plan_contract(line["plan"])) for line in sorted(plan_lines, key=lambda line: line["plan"])],
            "sources": sources,
            "source_contracts": membership_source_contracts(members),
            "sepa_mandat": payer.sepa_mandat if payer.abrechnungsart == BILLING_TYPE_DIRECT_DEBIT else None,
            "settings": subscription_settings_payload(run),
        }
    )
    decision, existing_subscription, conflict_message = classify_subscription(
        key, payload, payer.customer, period_from, period_to, BILLING_KIND_MEMBERSHIP
    )
    total_qty = sum(cint(line["qty"]) for line in plan_lines)
    action = ACTION_CREATE if decision == "create" else ACTION_SKIPPED if decision == "skip" else ACTION_CONFLICT
    return {
        "payer_mitglied": payer.name,
        "customer": payer.customer,
        "billing_type": payer.abrechnungsart,
        "covered_count": max(total_qty - 1, 0),
        "total_qty": total_qty,
        "period_from": period_from,
        "period_to": period_to,
        "subscription_plan": plan_lines[0]["plan"],
        "plans_json": json.dumps(plan_lines, sort_keys=True),
        "generation_key": key,
        "generation_payload": payload,
        "existing_subscription": existing_subscription,
        "estimated_invoice_count": estimate_invoice_count(
            frappe._dict({"from_date": period_from, "to_date": period_to}),
            run.generate_invoice_at,
            run.number_of_days,
            bool(run.generate_new_invoices_past_due_date),
        ),
        "action": action,
        "message": conflict_message,
    }


def make_error_row(payer: MemberBillingInfo, message: str, **overrides) -> dict:
    row = {
        "payer_mitglied": payer.name,
        "customer": payer.customer,
        "billing_type": payer.abrechnungsart,
        "covered_count": 0,
        "total_qty": 0,
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

    for _ in range(300):
        current_end = add_days(add_to_date(current_start, years=1), -1)
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
    return period_end


def normalize_generate_invoice_at(generate_invoice_at: str | None) -> str:
    if generate_invoice_at == ERP_INVOICE_AT_PERIOD_END:
        return INVOICE_AT_PERIOD_END
    if generate_invoice_at == ERP_INVOICE_AT_PERIOD_START:
        return INVOICE_AT_PERIOD_START
    if generate_invoice_at == ERP_INVOICE_AT_DAYS_BEFORE:
        return INVOICE_AT_DAYS_BEFORE
    return generate_invoice_at or INVOICE_AT_PERIOD_START


def get_erpnext_generate_invoice_at(generate_invoice_at: str | None) -> str:
    return ERP_GENERATE_INVOICE_AT_VALUES.get(generate_invoice_at or INVOICE_AT_PERIOD_START, ERP_INVOICE_AT_PERIOD_START)


def create_subscriptions_from_preview(run_name: str) -> dict:
    run = frappe.get_doc(RUN_DOCTYPE, run_name)
    run.check_permission("write")
    if not run.preview_rows:
        frappe.throw(_("Bitte zuerst eine Vorschau anzeigen."))

    created = skipped = errors = 0
    for row in run.preview_rows:
        if row.created_subscription or row.action in {ACTION_CREATED, ACTION_SKIPPED}:
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
        "Mitglied", row.payer_mitglied, "Mitgliedsbeitrag", payload.get("sepa_mandat")
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
        kind=BILLING_KIND_MEMBERSHIP,
        source_doctype="Mitglied",
        source_name=row.payer_mitglied,
        build_subscription=build_subscription,
        validate_current=lambda: validate_membership_payload_current(run, row, payload),
    )


def load_plan_lines(row) -> list[dict]:
    return json.loads(row.plans_json) if row.plans_json else [{"plan": row.subscription_plan, "qty": row.total_qty}]


def subscription_settings_payload(run) -> dict:
    return {
        "company": run.company,
        "cost_center": run.cost_center,
        "submit_invoice": cint(run.submit_invoice),
        "generate_new_invoices_past_due_date": cint(run.generate_new_invoices_past_due_date),
        "generate_invoice_at": get_erpnext_generate_invoice_at(run.generate_invoice_at),
        "number_of_days": cint(run.number_of_days),
        "days_until_due": cint(run.days_until_due),
    }


def membership_source_contracts(members: list[MemberBillingInfo]) -> list[dict]:
    return [
        {
            "name": member.name,
            "customer": member.customer,
            "abrechnungsart": member.abrechnungsart,
            "jahresbeitrag": flt(member.jahresbeitrag),
            "beitragszahler": member.beitragszahler,
            "sepa_mandat": member.sepa_mandat,
        }
        for member in sorted(members, key=lambda member: member.name)
    ]


def validate_membership_payload_current(run, row, payload: dict) -> None:
    names = [contract["name"] for contract in payload.get("source_contracts", [])]
    current = get_member_rows(names)
    current_covered = set(
        frappe.db.get_list(
            "Mitglied",
            filters={"beitragszahler": row.payer_mitglied},
            pluck="name",
        )
    )
    expected_covered = {
        source["source_name"]
        for source in payload.get("sources", [])
        if source.get("source_role") == "Uebernommenes Mitglied"
    }
    if current_covered != expected_covered:
        raise BillingRowError(_("Die Zuordnung uebernommener Mitglieder hat sich seit der Vorschau geaendert."))
    if canonical_source_contracts := payload.get("source_contracts"):
        if generation_payload(membership_source_contracts(list(current.values()))) != generation_payload(canonical_source_contracts):
            raise BillingRowError(_("Mitglieds- oder Beitragsdaten haben sich seit der Vorschau geaendert."))
    assert_plan_contracts_current(payload.get("plan_contracts", []))
    for plan_line in payload.get("plans", []):
        validate_plan_currency(plan_line["plan"], row.customer, run.company)


def validate_membership_periods(doc) -> None:
    groups: dict[tuple[str, str | None], list] = defaultdict(list)
    ranges = []
    for period in doc.get("periods") or []:
        if not period.from_date:
            frappe.throw(_("Von-Datum ist fuer jede Beitragsperiode erforderlich."))
        start = getdate(period.from_date)
        end = getdate(period.to_date) if period.to_date else None
        if end and end < start:
            frappe.throw(_("Bis-Datum darf nicht vor dem Von-Datum liegen."))
        if end and end <= getdate(add_days(add_to_date(start, years=1), -1)):
            frappe.throw(_("Eine endliche Beitragsperiode muss nach dem ersten vollstaendigen Jahreszyklus enden."))
        if period.subscription_plan:
            validate_plan_interval(period.subscription_plan, "Year", _("Beitragsplan"))
        key = (str(start), str(end) if end else None)
        groups[key].append(period)
        ranges.append((key, start, end))

    seen_ranges = []
    for key, start, end in ranges:
        if key in seen_ranges:
            continue
        for other_key, other_start, other_end in ranges:
            if key == other_key or other_key in seen_ranges:
                continue
            if periods_overlap(start, end, other_start, other_end):
                frappe.throw(_("Beitragsperioden duerfen sich nur bei identischen Zeitraeumen ueberschneiden."))
        seen_ranges.append(key)

    for periods in groups.values():
        if periods and is_historical_period(str(periods[0].to_date) if periods[0].to_date else None) and len(periods) != 1:
            frappe.throw(_("Historische Beitragsperioden duerfen genau einen Beitragsplan enthalten."))
        plans = [period.subscription_plan for period in periods if period.subscription_plan]
        if len(plans) != len(set(plans)):
            frappe.throw(_("Derselbe Beitragsplan darf je Zeitraum nur einmal vorkommen."))
        amounts = [get_subscription_plan_amount(plan) for plan in plans]
        if None in amounts or len(amounts) != len(set(amounts)):
            frappe.throw(_("Beitragsplaene desselben Zeitraums muessen unterschiedliche, ermittelbare Betraege haben."))


def get_active_source_mandate(doctype: str, source_name: str, category: str, mandate_name: str | None) -> str | None:
    if not mandate_name:
        return None
    matches = frappe.db.get_list(
        "SEPA Mandat",
        filters={
            "name": mandate_name,
            "status": "Aktiv",
            "mandatskategorie": category,
            "bezugs_doctype": doctype,
            "bezugs_name": source_name,
        },
        pluck="name",
        limit_page_length=1,
    )
    return matches[0] if matches else None
