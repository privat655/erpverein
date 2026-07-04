import json
from collections import defaultdict
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, flt, getdate, nowdate

from verein_erp.services.mitglied_service import (
    BILLING_TYPE_COVERED,
    BILLING_TYPE_DIRECT_DEBIT,
    BILLING_TYPE_FREE,
    BILLING_TYPE_INVOICE,
)


RUN_STATUS_DRAFT = "Entwurf"
RUN_STATUS_PREVIEW = "Vorschau erstellt"
RUN_STATUS_EXECUTED = "Ausgefuehrt"
RUN_STATUS_PARTIAL = "Teilweise fehlgeschlagen"
SCOPE_SINGLE = "Einzelnes Mitglied"
SCOPE_SELECTED = "Ausgewaehlte Mitglieder"
SCOPE_ALL = "Alle Mitglieder"
ACTION_CREATE = "Create"
ACTION_CONFLICT = "Conflict"
ACTION_ERROR = "Error"
ACTION_CREATED = "Created"
ACTION_SKIPPED = "Skipped"
DEFAULT_GENERATE_INVOICE_AT = "Beginning of the current subscription period"
HISTORICAL_RATE_CUTOFF = "2023-01-01"
PAYING_BILLING_TYPES = {BILLING_TYPE_INVOICE, BILLING_TYPE_DIRECT_DEBIT}


@dataclass
class MemberBillingInfo:
    name: str
    customer: str | None
    abrechnungsart: str
    jahresbeitrag: float
    beitragszahler: str | None


def set_subscription_run_defaults(doc) -> None:
    doc.status = doc.status or RUN_STATUS_DRAFT
    doc.scope = doc.scope or SCOPE_ALL
    doc.company = doc.company or get_default_company()
    doc.cost_center = doc.cost_center or get_default_cost_center(doc.company)
    doc.submit_invoice = 1
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
    run = frappe.new_doc("Mitglied Subscription Lauf")
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


def create_subscription_run(scope: str = SCOPE_ALL, mitglied: str | None = None, mitglieder: list[str] | None = None):
    run = frappe.new_doc("Mitglied Subscription Lauf")
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
    run = frappe.get_doc("Mitglied Subscription Lauf", run_name)
    run.check_permission("write")
    set_subscription_run_defaults(run)
    run.set("preview_rows", [])

    rows = make_preview_rows(run)
    for row in rows:
        run.append("preview_rows", row)

    errors = sum(1 for row in rows if row["action"] in {ACTION_ERROR, ACTION_CONFLICT})
    run.status = RUN_STATUS_PREVIEW if not errors else RUN_STATUS_PARTIAL
    run.result_summary = json.dumps({"total": len(rows), "errors": errors}, sort_keys=True)
    run.save()
    return {"run": run.name, "total": len(rows), "errors": errors}


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
            fields=["name", "customer", "abrechnungsart", "jahresbeitrag", "beitragszahler"],
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
    if run.scope == SCOPE_SELECTED and run.selected_mitglieder_json:
        try:
            selected = json.loads(run.selected_mitglieder_json)
        except ValueError:
            frappe.throw(_("Ausgewaehlte Mitglieder JSON ist ungueltig."))
        return sorted({name for name in selected if name})
    return []


def get_member_rows(names: list[str]) -> dict[str, MemberBillingInfo]:
    if not names:
        return {}
    rows = frappe.db.get_list(
        "Mitglied",
        filters={"name": ["in", names]},
        fields=["name", "customer", "abrechnungsart", "jahresbeitrag", "beitragszahler"],
        order_by="name asc",
    )
    return {row.name: make_member_info(row) for row in rows}


def make_member_info(row) -> MemberBillingInfo:
    return MemberBillingInfo(row.name, row.customer, row.abrechnungsart, flt(row.jahresbeitrag), row.beitragszahler)


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
        return [make_error_row(payer, _("Beitragszahler hat keinen Customer."))]

    rows = []
    periods_by_range = defaultdict(list)
    for period in run.periods:
        periods_by_range[(str(period.from_date), str(period.to_date or ""))].append(period)

    for period_key, periods in sorted(periods_by_range.items()):
        plan_lines, errors = build_plan_lines_for_period(members, periods, period_key)
        rows.extend(make_error_row(payer, message, period_from=period_key[0], period_to=period_key[1] or None) for message in errors)
        if plan_lines:
            rows.append(make_preview_row(run, payer, period_key[0], period_key[1] or None, plan_lines))
    return rows


def build_plan_lines_for_period(members: list[MemberBillingInfo], periods, period_key) -> tuple[list[dict], list[str]]:
    if is_historical_period(period_key[1]):
        period = periods[0] if periods else None
        if not period or not period.subscription_plan:
            return [], [_("Subscription Plan fehlt fuer historische Periode {0}.").format(frappe.bold(period_key[0]))]
        return [{"plan": period.subscription_plan, "qty": len(members)}], []

    plan_amounts = []
    errors = []
    for period in periods:
        if not period.subscription_plan:
            errors.append(_("Subscription Plan fehlt fuer Periode {0}.").format(frappe.bold(period_key[0])))
            continue
        amount = get_subscription_plan_amount(period.subscription_plan)
        if not amount:
            errors.append(_("Betrag fuer Subscription Plan {0} konnte nicht ermittelt werden.").format(frappe.bold(period.subscription_plan)))
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
                _("Kein Subscription Plan fuer Jahresbeitrag {0} in Periode {1} gefunden.").format(
                    frappe.bold(member.jahresbeitrag), frappe.bold(period_key[0])
                )
            )
    return plan_lines, errors


def is_historical_period(period_to: str | None) -> bool:
    return bool(period_to and getdate(period_to) < getdate(HISTORICAL_RATE_CUTOFF))


def make_preview_row(run, payer: MemberBillingInfo, period_from, period_to, plan_lines: list[dict]) -> dict:
    existing_subscription = find_existing_subscription(payer.customer, period_from, period_to)
    total_qty = sum(cint(line["qty"]) for line in plan_lines)
    action = ACTION_CONFLICT if existing_subscription else ACTION_CREATE
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
        "existing_subscription": existing_subscription,
        "estimated_invoice_count": estimate_invoice_count(frappe._dict({"from_date": period_from, "to_date": period_to}), run.generate_invoice_at, run.number_of_days),
        "action": action,
        "message": _("Vorhandene Subscription fuer Customer/Zeitraum gefunden.") if existing_subscription else "",
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


def find_existing_subscription(customer: str, period_from, period_to) -> str | None:
    filters = {"party_type": "Customer", "party": customer, "start_date": period_from, "status": ["!=", "Cancelled"]}
    filters["end_date"] = period_to if period_to else ["is", "not set"]
    return frappe.db.get_value("Subscription", filters, "name")


def estimate_invoice_count(period, generate_invoice_at: str, number_of_days: int = 0) -> int:
    if not period.from_date:
        return 0
    today = getdate(nowdate())
    current_start = getdate(period.from_date)
    end_date = getdate(period.to_date) if period.to_date else None
    count = 0

    for _ in range(300):
        current_end = add_days(add_to_date(current_start, years=1), -1)
        if end_date and getdate(current_end) > end_date:
            current_end = end_date
        if getdate(get_invoice_trigger_date(current_start, current_end, generate_invoice_at, number_of_days)) <= today:
            count += 1
        else:
            break
        next_start = add_days(current_end, 1)
        if end_date and getdate(next_start) > end_date:
            break
        current_start = getdate(next_start)
    return count


def get_invoice_trigger_date(period_start, period_end, generate_invoice_at: str, number_of_days: int = 0):
    if generate_invoice_at == "Beginning of the current subscription period":
        return period_start
    if generate_invoice_at == "Days before the current subscription period":
        return add_days(period_start, -cint(number_of_days))
    return period_end


def create_subscriptions_from_preview(run_name: str) -> dict:
    run = frappe.get_doc("Mitglied Subscription Lauf", run_name)
    run.check_permission("write")
    if not run.preview_rows:
        frappe.throw(_("Bitte zuerst eine Vorschau erzeugen."))

    created = skipped = errors = 0
    for row in run.preview_rows:
        if row.created_subscription or row.action in {ACTION_CREATED, ACTION_SKIPPED}:
            skipped += 1
            continue
        if row.action != ACTION_CREATE:
            errors += 1
            continue
        try:
            subscription = create_subscription_for_preview_row(run, row)
            row.created_subscription = subscription.name
            row.action = ACTION_CREATED
            row.message = _("Subscription erstellt.")
            created += 1
        except Exception as exc:
            row.action = ACTION_ERROR
            row.message = str(exc)
            errors += 1

    run.status = RUN_STATUS_EXECUTED if errors == 0 else RUN_STATUS_PARTIAL
    run.result_summary = json.dumps({"created": created, "skipped": skipped, "errors": errors}, sort_keys=True)
    run.save()
    return {"run": run.name, "created": created, "skipped": skipped, "errors": errors}


def create_subscription_for_preview_row(run, row):
    subscription = frappe.new_doc("Subscription")
    subscription.party_type = "Customer"
    subscription.party = row.customer
    subscription.company = run.company
    subscription.cost_center = run.cost_center
    subscription.start_date = row.period_from
    subscription.end_date = row.period_to
    subscription.generate_new_invoices_past_due_date = run.generate_new_invoices_past_due_date
    subscription.submit_invoice = 1
    subscription.generate_invoice_at = run.generate_invoice_at
    subscription.number_of_days = run.number_of_days
    subscription.days_until_due = run.days_until_due
    for plan_line in load_plan_lines(row):
        subscription.append("plans", plan_line)
    subscription.insert()
    return subscription


def load_plan_lines(row) -> list[dict]:
    return json.loads(row.plans_json) if row.plans_json else [{"plan": row.subscription_plan, "qty": row.total_qty}]
