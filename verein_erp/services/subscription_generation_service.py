import json
from collections import defaultdict
from dataclasses import dataclass

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, flt, getdate, nowdate

from verein_erp.custom_fields import (
    SUBSCRIPTION_GENERATION_RUN_FIELDNAME,
    SUBSCRIPTION_MANAGED_FIELDNAME,
    SUBSCRIPTION_PAYER_FIELDNAME,
    SUBSCRIPTION_SYNC_STATE_FIELDNAME,
)
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
RUN_STATUS_CANCELLED = "Abgebrochen"
SCOPE_SINGLE = "Einzelnes Mitglied"
SCOPE_SELECTED = "Ausgewaehlte Mitglieder"
SCOPE_ALL = "Alle Mitglieder"
ACTION_CREATE = "Create"
ACTION_SKIP = "Skip Existing"
ACTION_ERROR = "Error"
ACTION_CREATED = "Created"
DEFAULT_GENERATE_INVOICE_AT = "Beginning of the current subscription period"
DEFAULT_PERIODS = [
    {"from_date": "2016-01-01", "to_date": "2022-12-31", "annual_amount": 250, "apply_to_annual_fee": 350},
    {"from_date": "2023-01-01", "to_date": None, "annual_amount": 350, "apply_to_annual_fee": 350},
]
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
    plan_suggestions = suggest_subscription_plans()
    for period in DEFAULT_PERIODS:
        annual_amount = flt(period["annual_amount"])
        doc.append(
            "periods",
            {
                "from_date": period["from_date"],
                "to_date": period["to_date"],
                "annual_amount": annual_amount,
                "apply_to_annual_fee": period["apply_to_annual_fee"],
                "subscription_plan": plan_suggestions.get(annual_amount),
            },
        )


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


def build_subscription_preview(run_name: str) -> dict:
    run = frappe.get_doc("Mitglied Subscription Lauf", run_name)
    run.check_permission("write")
    set_subscription_run_defaults(run)
    run.set("preview_rows", [])

    preview_rows = make_preview_rows(run)
    for row in preview_rows:
        run.append("preview_rows", row)

    errors = sum(1 for row in preview_rows if row["action"] == ACTION_ERROR)
    run.status = RUN_STATUS_PREVIEW if not errors else RUN_STATUS_PARTIAL
    run.result_summary = json.dumps({"total": len(preview_rows), "errors": errors}, sort_keys=True)
    run.save()

    return {"run": run.name, "total": len(preview_rows), "errors": errors}


def make_preview_rows(run) -> list[dict]:
    members = get_members_for_run(run)
    groups = build_payer_groups(members)
    rows = []

    for payer_name in sorted(groups):
        payer = groups[payer_name]["payer"]
        group_members = groups[payer_name]["members"]
        rows.extend(make_preview_rows_for_payer(run, payer, group_members))

    return rows


def get_members_for_run(run) -> dict[str, MemberBillingInfo]:
    selected_names = get_selected_member_names(run)
    names_to_load = set(selected_names)

    if run.scope == SCOPE_ALL:
        rows = frappe.db.get_list(
            "Mitglied",
            fields=["name", "customer", "abrechnungsart", "jahresbeitrag", "beitragszahler"],
            order_by="name asc",
        )
        return {row.name: make_member_info(row) for row in rows}

    selected_members = get_member_rows(selected_names)
    payer_names = {row.beitragszahler for row in selected_members.values() if row.abrechnungsart == BILLING_TYPE_COVERED and row.beitragszahler}
    payer_names.update(row.name for row in selected_members.values() if row.abrechnungsart in PAYING_BILLING_TYPES)
    names_to_load.update(payer_names)

    if payer_names:
        covered_rows = frappe.db.get_list(
            "Mitglied",
            filters={"beitragszahler": ["in", sorted(payer_names)]},
            fields=["name", "customer", "abrechnungsart", "jahresbeitrag", "beitragszahler"],
            order_by="name asc",
        )
        names_to_load.update(row.name for row in covered_rows)

    return get_member_rows(sorted(names_to_load))


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
    return MemberBillingInfo(
        name=row.name,
        customer=row.customer,
        abrechnungsart=row.abrechnungsart,
        jahresbeitrag=flt(row.jahresbeitrag),
        beitragszahler=row.beitragszahler,
    )


def build_payer_groups(members: dict[str, MemberBillingInfo]) -> dict[str, dict]:
    groups = {}

    for member in members.values():
        if member.abrechnungsart in PAYING_BILLING_TYPES:
            groups.setdefault(member.name, {"payer": member, "members": []})

    for member in members.values():
        if member.abrechnungsart == BILLING_TYPE_COVERED and member.beitragszahler:
            payer = members.get(member.beitragszahler) or get_member_rows([member.beitragszahler]).get(member.beitragszahler)
            if payer:
                groups.setdefault(payer.name, {"payer": payer, "members": []})
                members[payer.name] = payer

    for payer_name, group in groups.items():
        payer = group["payer"]
        if payer.abrechnungsart in PAYING_BILLING_TYPES:
            group["members"].append(payer)

    for member in members.values():
        if member.abrechnungsart == BILLING_TYPE_COVERED and member.beitragszahler in groups:
            groups[member.beitragszahler]["members"].append(member)

    return groups


def make_preview_rows_for_payer(run, payer: MemberBillingInfo, members: list[MemberBillingInfo]) -> list[dict]:
    if payer.abrechnungsart == BILLING_TYPE_FREE:
        return []

    if not payer.customer:
        return [make_error_row(run, payer, _("Beitragszahler hat keinen Customer."))]

    rows = []
    periods_by_range = defaultdict(list)
    for period in run.periods:
        periods_by_range[(str(period.from_date), str(period.to_date or ""))].append(period)

    for period_key, periods in sorted(periods_by_range.items()):
        matched_members = set()
        plan_lines = []
        covered_count = 0
        total_qty = 0
        for period in periods:
            applicable_members = [member for member in members if flt(member.jahresbeitrag) == flt(period.apply_to_annual_fee)]
            if not applicable_members:
                continue

            matched_members.update(member.name for member in applicable_members)
            if not period.subscription_plan:
                rows.append(
                    make_error_row(
                        run,
                        payer,
                        _("Subscription Plan fehlt fuer Jahresbetrag {0}.").format(frappe.bold(period.annual_amount)),
                        total_qty=len(applicable_members),
                        covered_count=sum(1 for member in applicable_members if member.abrechnungsart == BILLING_TYPE_COVERED),
                        period_from=period.from_date,
                        period_to=period.to_date,
                    )
                )
                continue

            qty = len(applicable_members)
            total_qty += qty
            covered_count += sum(1 for member in applicable_members if member.abrechnungsart == BILLING_TYPE_COVERED)
            plan_lines.append({"plan": period.subscription_plan, "qty": qty})

        unmatched = [member for member in members if member.name not in matched_members]
        for member in unmatched:
            rows.append(
                make_error_row(
                    run,
                    payer,
                    _("Kein Subscription Plan fuer Jahresbeitrag {0} in Periode {1} gefunden.").format(
                        frappe.bold(member.jahresbeitrag), frappe.bold(period_key[0])
                    ),
                    period_from=period_key[0],
                    period_to=period_key[1] or None,
                )
            )

        if plan_lines:
            rows.append(
                make_preview_row_for_period(
                    run,
                    payer,
                    period_from=period_key[0],
                    period_to=period_key[1] or None,
                    plan_lines=plan_lines,
                    covered_count=covered_count,
                    total_qty=total_qty,
                )
            )

    return rows


def make_preview_row_for_period(
    run,
    payer: MemberBillingInfo,
    period_from,
    period_to,
    plan_lines: list[dict],
    covered_count: int,
    total_qty: int,
) -> dict:
    existing_managed = find_existing_subscription(payer, period_from, period_to, plan_lines, managed=True)
    if existing_managed:
        action = ACTION_SKIP
        message = _("App-verwaltete Subscription existiert bereits.")
        planned_subscription = existing_managed
    else:
        existing_unmanaged = find_existing_subscription(payer, period_from, period_to, plan_lines, managed=False)
        if existing_unmanaged:
            action = ACTION_ERROR
            message = _("Nicht app-verwaltete Subscription existiert bereits und muss manuell geprueft werden.")
            planned_subscription = existing_unmanaged
        else:
            action = ACTION_CREATE
            message = ""
            planned_subscription = None

    return {
        "payer_mitglied": payer.name,
        "customer": payer.customer,
        "billing_type": payer.abrechnungsart,
        "covered_count": covered_count,
        "total_qty": total_qty,
        "period_from": period_from,
        "period_to": period_to,
        "subscription_plan": plan_lines[0]["plan"] if plan_lines else None,
        "plans_json": json.dumps(plan_lines, sort_keys=True),
        "planned_subscription": planned_subscription,
        "estimated_invoice_count": estimate_invoice_count(
            frappe._dict({"from_date": period_from, "to_date": period_to}), run.generate_invoice_at, run.number_of_days
        ),
        "action": action,
        "message": message,
    }


def make_error_row(run, payer: MemberBillingInfo, message: str, **overrides) -> dict:
    row = {
        "payer_mitglied": payer.name,
        "customer": payer.customer,
        "billing_type": payer.abrechnungsart,
        "covered_count": 0,
        "total_qty": 0,
        "period_from": None,
        "period_to": None,
        "subscription_plan": None,
        "planned_subscription": None,
        "estimated_invoice_count": 0,
        "action": ACTION_ERROR,
        "message": message,
    }
    row.update(overrides)
    return row


def find_existing_subscription(payer: MemberBillingInfo, period_from, period_to, plan_lines: list[dict], managed: bool) -> str | None:
    filters = {
        "party_type": "Customer",
        "party": payer.customer,
        "start_date": period_from,
        "status": ["!=", "Cancelled"],
    }
    if period_to:
        filters["end_date"] = period_to
    else:
        filters["end_date"] = ["is", "not set"]

    if managed and frappe.get_meta("Subscription", cached=False).has_field(SUBSCRIPTION_MANAGED_FIELDNAME):
        filters[SUBSCRIPTION_MANAGED_FIELDNAME] = 1

    candidates = frappe.db.get_list("Subscription", filters=filters, pluck="name")
    for subscription in candidates:
        doc = frappe.get_doc("Subscription", subscription)
        if managed and doc.get(SUBSCRIPTION_PAYER_FIELDNAME) != payer.name:
            continue
        if not managed:
            if not doc.get(SUBSCRIPTION_MANAGED_FIELDNAME):
                return doc.name
            continue
        if subscription_has_plan_lines(doc, plan_lines):
            return doc.name
    return None


def subscription_has_plan_lines(subscription, plan_lines: list[dict]) -> bool:
    expected = {line["plan"]: cint(line["qty"]) for line in plan_lines}
    actual = {row.plan: cint(row.qty) for row in subscription.get("plans") or []}
    return expected == actual


def estimate_invoice_count(period, generate_invoice_at: str, number_of_days: int = 0) -> int:
    if not period.from_date:
        return 0

    today = getdate(nowdate())
    current_start = getdate(period.from_date)
    end_date = getdate(period.to_date) if period.to_date else None
    count = 0
    safety = 0

    while safety < 300:
        safety += 1
        current_end = add_days(add_to_date(current_start, years=1), -1)
        if end_date and getdate(current_end) > end_date:
            current_end = end_date

        trigger = get_invoice_trigger_date(current_start, current_end, generate_invoice_at, number_of_days)
        if getdate(trigger) <= today:
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
        if row.action == ACTION_SKIP or row.created_subscription:
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
    subscription.set(SUBSCRIPTION_MANAGED_FIELDNAME, 1)
    subscription.set(SUBSCRIPTION_PAYER_FIELDNAME, row.payer_mitglied)
    subscription.set(SUBSCRIPTION_GENERATION_RUN_FIELDNAME, run.name)
    subscription.set(
        SUBSCRIPTION_SYNC_STATE_FIELDNAME,
        json.dumps(
            {
                "payer_mitglied": row.payer_mitglied,
                "period_from": str(row.period_from),
                "period_to": str(row.period_to or ""),
                "plans": load_plan_lines(row),
            },
            sort_keys=True,
        ),
    )
    for plan_line in load_plan_lines(row):
        subscription.append("plans", plan_line)
    subscription.insert()
    return subscription


def load_plan_lines(row) -> list[dict]:
    if not row.plans_json:
        return [{"plan": row.subscription_plan, "qty": row.total_qty}]
    return json.loads(row.plans_json)
