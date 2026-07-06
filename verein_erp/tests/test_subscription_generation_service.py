import json
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.custom_fields import sync_custom_fields
from verein_erp.api.subscription_generation import create_subscriptions
from verein_erp.services.subscription_generation_service import (
    ACTION_CONFLICT,
    ACTION_CREATE,
    ACTION_ERROR,
    RUN_STATUS_RUNNING,
    build_subscription_preview,
    estimate_invoice_count,
    get_erpnext_generate_invoice_at,
    suggest_subscription_plans,
    create_subscription_for_preview_row,
)


class TestSubscriptionGenerationService(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()

    def test_plan_suggestion_uses_existing_yearly_fixed_rate_plans(self):
        plan = make_subscription_plan(275)

        suggestions = suggest_subscription_plans()

        self.assertEqual(suggestions[275], plan.name)

    def test_preview_groups_covered_members_into_payer_quantity(self):
        plan_250 = make_subscription_plan(250)
        plan_350 = make_subscription_plan(350)
        customer = make_customer("Subscription Payer")
        payer = make_mitglied(customer=customer.name, abrechnungsart="Rechnung", jahresbeitrag=350)
        make_mitglied(abrechnungsart="Beitrag wird uebernommen", beitragszahler=payer.name, jahresbeitrag=350)
        make_mitglied(abrechnungsart="Beitrag wird uebernommen", beitragszahler=payer.name, jahresbeitrag=350)
        run = make_run(payer.name, plan_250.name, plan_350.name)

        result = build_subscription_preview(run.name)
        run.reload()

        self.assertEqual(result["errors"], 0)
        self.assertEqual(len(run.preview_rows), 2)
        self.assertTrue(all(row.action == ACTION_CREATE for row in run.preview_rows))
        self.assertTrue(all(row.total_qty == 3 for row in run.preview_rows))
        self.assertEqual(sum(row.estimated_invoice_count for row in run.preview_rows), 11)

    def test_current_period_matches_reduced_plan_by_plan_amount(self):
        plan_250 = make_subscription_plan(250)
        plan_350 = make_subscription_plan(350)
        plan_175 = make_subscription_plan(175)
        customer = make_customer("Subscription Mixed")
        payer = make_mitglied(customer=customer.name, abrechnungsart="Rechnung", jahresbeitrag=350)
        make_mitglied(abrechnungsart="Beitrag wird uebernommen", beitragszahler=payer.name, jahresbeitrag=175)
        run = make_run(payer.name, plan_250.name, plan_350.name)
        run.append("periods", {"from_date": "2023-01-01", "subscription_plan": plan_175.name})
        run.save(ignore_permissions=True)

        result = build_subscription_preview(run.name)
        run.reload()
        current_row = next(row for row in run.preview_rows if str(row.period_from) == "2023-01-01")
        plan_lines = json.loads(current_row.plans_json)

        self.assertEqual(result["errors"], 0)
        self.assertEqual({line["plan"]: line["qty"] for line in plan_lines}, {plan_350.name: 1, plan_175.name: 1})

    def test_missing_plan_for_reduced_fee_is_preview_error(self):
        plan_250 = make_subscription_plan(250)
        plan_350 = make_subscription_plan(350)
        customer = make_customer("Subscription Reduced")
        payer = make_mitglied(customer=customer.name, abrechnungsart="Rechnung", jahresbeitrag=175)
        run = make_run(payer.name, plan_250.name, plan_350.name)

        result = build_subscription_preview(run.name)

        self.assertGreater(result["errors"], 0)

    def test_existing_subscription_is_conflict(self):
        plan_250 = make_subscription_plan(250)
        plan_350 = make_subscription_plan(350)
        customer = make_customer("Subscription Conflict")
        payer = make_mitglied(customer=customer.name, abrechnungsart="Rechnung", jahresbeitrag=350)
        make_subscription(customer.name, plan_350.name, "2023-01-01")
        run = make_run(payer.name, plan_250.name, plan_350.name)

        build_subscription_preview(run.name)
        run.reload()

        self.assertTrue(any(row.action == ACTION_CONFLICT for row in run.preview_rows))

    def test_lastschrift_without_sepa_mandat_still_previews_subscription(self):
        plan_250 = make_subscription_plan(250)
        plan_350 = make_subscription_plan(350)
        customer = make_customer("Subscription Direct Debit")
        payer = make_mitglied(customer=customer.name, abrechnungsart="Rechnung", jahresbeitrag=350)
        frappe.db.set_value("Mitglied", payer.name, "abrechnungsart", "Lastschrift")
        run = make_run(payer.name, plan_250.name, plan_350.name)

        result = build_subscription_preview(run.name)

        self.assertEqual(result["errors"], 0)

    def test_estimate_invoice_count_does_not_include_2027_before_2027(self):
        period = frappe._dict({"from_date": "2023-01-01", "to_date": None})

        self.assertEqual(estimate_invoice_count(period, "Periodenbeginn"), 4)

    def test_german_invoice_timing_maps_to_erpnext_subscription_value(self):
        self.assertEqual(get_erpnext_generate_invoice_at("Periodenbeginn"), "Beginning of the current subscription period")

    def test_legacy_invoice_timing_still_estimates_current_period(self):
        period = frappe._dict({"from_date": "2023-01-01", "to_date": None})

        self.assertEqual(estimate_invoice_count(period, "Beginning of the current subscription period"), 4)

    def test_create_subscriptions_enqueues_long_background_job(self):
        plan_250 = make_subscription_plan(250)
        plan_350 = make_subscription_plan(350)
        customer = make_customer("Subscription Queue")
        payer = make_mitglied(customer=customer.name, abrechnungsart="Rechnung", jahresbeitrag=350)
        run = make_run(payer.name, plan_250.name, plan_350.name)
        build_subscription_preview(run.name)

        with patch("frappe.enqueue", return_value=SimpleNamespace(id="job-1")) as enqueue:
            result = create_subscriptions(run.name)

        run.reload()
        self.assertEqual(result, {"run": run.name, "job_id": "job-1"})
        self.assertEqual(run.status, RUN_STATUS_RUNNING)
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.kwargs["queue"], "long")
        self.assertEqual(enqueue.call_args.kwargs["run_name"], run.name)
        self.assertEqual(
            enqueue.call_args.args[0],
            "verein_erp.services.subscription_generation_service.create_subscriptions_from_preview",
        )

    def test_subscription_creation_respects_submit_invoice_setting(self):
        plan_250 = make_subscription_plan(250)
        plan_350 = make_subscription_plan(350)
        customer = make_customer("Subscription Draft Invoice")
        payer = make_mitglied(customer=customer.name, abrechnungsart="Rechnung", jahresbeitrag=350)
        run = make_run(payer.name, plan_250.name, plan_350.name)
        run.submit_invoice = 0
        run.save(ignore_permissions=True)
        build_subscription_preview(run.name)
        run.reload()
        row = next(row for row in run.preview_rows if str(row.period_from) == "2023-01-01")

        with patch("frappe.model.document.Document.insert", return_value=None):
            subscription = create_subscription_for_preview_row(run, row)

        self.assertEqual(subscription.submit_invoice, 0)


def make_run(mitglied: str, plan_250: str, plan_350: str):
    return frappe.get_doc(
        {
            "doctype": "Beitragsabrechnung",
            "scope": "Einzelnes Mitglied",
            "mitglied": mitglied,
            "company": get_company(),
            "cost_center": get_cost_center(),
            "generate_new_invoices_past_due_date": 1,
            "generate_invoice_at": "Periodenbeginn",
            "periods": [
                {"from_date": "2016-01-01", "to_date": "2022-12-31", "subscription_plan": plan_250},
                {"from_date": "2023-01-01", "subscription_plan": plan_350},
            ],
        }
    ).insert(ignore_permissions=True)


def make_subscription(customer: str, plan: str, start_date: str):
    return frappe.get_doc(
        {
            "doctype": "Subscription",
            "party_type": "Customer",
            "party": customer,
            "company": get_company(),
            "cost_center": get_cost_center(),
            "start_date": start_date,
            "generate_invoice_at": "Beginning of the current subscription period",
            "generate_new_invoices_past_due_date": 0,
            "submit_invoice": 0,
            "plans": [{"plan": plan, "qty": 1}],
        }
    ).insert(ignore_permissions=True)


def make_customer(label: str):
    customer = frappe.get_doc({"doctype": "Customer", "customer_name": f"{label} {frappe.generate_hash(length=8)}", "customer_type": "Individual"})
    customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
    if customer_group and customer.meta.has_field("customer_group"):
        customer.customer_group = customer_group
    territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
    if territory and customer.meta.has_field("territory"):
        customer.territory = territory
    return customer.insert(ignore_permissions=True)


def make_mitglied(**overrides):
    data = {
        "doctype": "Mitglied",
        "vorname": "Sample",
        "nachname": f"Member {frappe.generate_hash(length=6)}",
        "eintrittsdatum": "2026-01-01",
        "abrechnungsart": "Rechnung",
        "jahresbeitrag": 350,
    }
    data.update(overrides)
    return frappe.get_doc(data).insert(ignore_permissions=True)


def make_subscription_plan(amount: int):
    item = make_item()
    plan_name = f"Test Plan {amount} {frappe.generate_hash(length=8)}"
    return frappe.get_doc(
        {
            "doctype": "Subscription Plan",
            "plan_name": plan_name,
            "currency": "EUR",
            "item": item.name,
            "price_determination": "Fixed Rate",
            "cost": amount,
            "billing_interval": "Year",
            "billing_interval_count": 1,
        }
    ).insert(ignore_permissions=True)


def make_item():
    item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
    stock_uom = frappe.db.get_value("UOM", {}, "name") or "Nos"
    item_code = f"TEST-SUB-{frappe.generate_hash(length=10)}"
    return frappe.get_doc(
        {"doctype": "Item", "item_code": item_code, "item_name": item_code, "item_group": item_group, "stock_uom": stock_uom, "is_stock_item": 0}
    ).insert(ignore_permissions=True)


def get_company() -> str:
    return frappe.defaults.get_user_default("Company") or frappe.db.get_value("Company", {}, "name")


def get_cost_center() -> str:
    company = get_company()
    filters = {"is_group": 0}
    if company and frappe.get_meta("Cost Center").has_field("company"):
        filters["company"] = company
    return frappe.db.get_value("Cost Center", filters, "name")
