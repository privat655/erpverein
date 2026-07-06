import json
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.api.rental_subscription_generation import create_subscriptions
from verein_erp.custom_fields import sync_custom_fields
from verein_erp.services.rental_subscription_generation_service import (
    ACTION_CONFLICT,
    ACTION_CREATE,
    RUN_STATUS_RUNNING,
    build_rental_subscription_preview,
    create_run_for_mieter,
    create_subscription_for_preview_row,
    estimate_invoice_count,
)


class TestRentalSubscriptionGenerationService(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()

    def test_run_for_mieter_prefills_mietbeginn_and_mietende(self):
        mieter = make_mieter(mietbeginn="2026-03-01", mietende="2026-08-31")

        run = create_run_for_mieter(mieter.name)

        self.assertEqual(run.scope, "Einzelner Mieter")
        self.assertEqual(run.mieter, mieter.name)
        self.assertEqual(str(run.periods[0].from_date), "2026-03-01")
        self.assertEqual(str(run.periods[0].to_date), "2026-08-31")

    def test_preview_uses_overlap_of_period_and_rental_dates(self):
        plan = make_subscription_plan(500)
        customer = make_customer("Rental Preview")
        mieter = make_mieter(customer=customer.name, mietbeginn="2026-03-01", mietende="2026-08-31")
        run = make_run(mieter.name, plan.name, "2026-01-01", "2026-12-31")

        result = build_rental_subscription_preview(run.name)
        run.reload()
        row = run.preview_rows[0]
        plan_lines = json.loads(row.plans_json)

        self.assertEqual(result["errors"], 0)
        self.assertEqual(row.action, ACTION_CREATE)
        self.assertEqual(str(row.period_from), "2026-03-01")
        self.assertEqual(str(row.period_to), "2026-08-31")
        self.assertEqual(plan_lines, [{"plan": plan.name, "qty": 1}])

    def test_existing_subscription_is_conflict(self):
        plan = make_subscription_plan(600)
        customer = make_customer("Rental Conflict")
        mieter = make_mieter(customer=customer.name, mietbeginn="2026-01-01")
        make_subscription(customer.name, plan.name, "2026-01-01", None)
        run = make_run(mieter.name, plan.name, "2026-01-01", None)

        build_rental_subscription_preview(run.name)
        run.reload()

        self.assertEqual(run.preview_rows[0].action, ACTION_CONFLICT)

    def test_monthly_estimate_counts_due_periods(self):
        period = frappe._dict({"from_date": "2026-01-01", "to_date": "2026-03-31"})

        self.assertEqual(estimate_invoice_count(period, "Periodenbeginn"), 3)

    def test_create_subscriptions_enqueues_long_background_job(self):
        plan = make_subscription_plan(700)
        customer = make_customer("Rental Queue")
        mieter = make_mieter(customer=customer.name, mietbeginn="2026-01-01")
        run = make_run(mieter.name, plan.name, "2026-01-01", "2026-12-31")
        build_rental_subscription_preview(run.name)

        with patch("frappe.enqueue", return_value=SimpleNamespace(id="job-1")) as enqueue:
            result = create_subscriptions(run.name)

        run.reload()
        self.assertEqual(result, {"run": run.name, "job_id": "job-1"})
        self.assertEqual(run.status, RUN_STATUS_RUNNING)
        self.assertEqual(enqueue.call_args.kwargs["queue"], "long")
        self.assertEqual(enqueue.call_args.kwargs["run_name"], run.name)
        self.assertEqual(
            enqueue.call_args.args[0],
            "verein_erp.services.rental_subscription_generation_service.create_subscriptions_from_preview",
        )

    def test_subscription_creation_respects_submit_invoice_setting(self):
        plan = make_subscription_plan(800)
        customer = make_customer("Rental Draft Invoice")
        mieter = make_mieter(customer=customer.name, mietbeginn="2026-01-01")
        run = make_run(mieter.name, plan.name, "2026-01-01", "2026-12-31")
        run.submit_invoice = 0
        run.save(ignore_permissions=True)
        build_rental_subscription_preview(run.name)
        run.reload()

        with patch("frappe.model.document.Document.insert", return_value=None):
            subscription = create_subscription_for_preview_row(run, run.preview_rows[0])

        self.assertEqual(subscription.submit_invoice, 0)


def make_run(mieter: str, plan: str, from_date: str, to_date: str | None):
    return frappe.get_doc(
        {
            "doctype": "Mietabrechnung",
            "scope": "Einzelner Mieter",
            "mieter": mieter,
            "company": get_company(),
            "cost_center": get_cost_center(),
            "generate_new_invoices_past_due_date": 1,
            "generate_invoice_at": "Periodenbeginn",
            "periods": [{"from_date": from_date, "to_date": to_date, "subscription_plan": plan}],
        }
    ).insert(ignore_permissions=True)


def make_subscription(customer: str, plan: str, start_date: str, end_date: str | None):
    data = {
        "doctype": "Subscription",
        "party_type": "Customer",
        "party": customer,
        "company": get_company(),
        "cost_center": get_cost_center(),
        "start_date": start_date,
        "end_date": end_date,
        "generate_invoice_at": "Beginning of the current subscription period",
        "generate_new_invoices_past_due_date": 0,
        "submit_invoice": 0,
        "plans": [{"plan": plan, "qty": 1}],
    }
    return frappe.get_doc(data).insert(ignore_permissions=True)


def make_customer(label: str):
    customer = frappe.get_doc({"doctype": "Customer", "customer_name": f"{label} {frappe.generate_hash(length=8)}", "customer_type": "Individual"})
    customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
    if customer_group and customer.meta.has_field("customer_group"):
        customer.customer_group = customer_group
    territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
    if territory and customer.meta.has_field("territory"):
        customer.territory = territory
    return customer.insert(ignore_permissions=True)


def make_mieter(**overrides):
    data = {
        "doctype": "Mieter",
        "vorname": "Sample",
        "nachname": f"Tenant {frappe.generate_hash(length=6)}",
        "mietbeginn": "2026-01-01",
        "abrechnungsart": "Rechnung",
    }
    data.update(overrides)
    return frappe.get_doc(data).insert(ignore_permissions=True)


def make_subscription_plan(amount: int):
    item = make_item()
    plan_name = f"Test Rent Plan {amount} {frappe.generate_hash(length=8)}"
    return frappe.get_doc(
        {
            "doctype": "Subscription Plan",
            "plan_name": plan_name,
            "currency": "EUR",
            "item": item.name,
            "price_determination": "Fixed Rate",
            "cost": amount,
            "billing_interval": "Month",
            "billing_interval_count": 1,
        }
    ).insert(ignore_permissions=True)


def make_item():
    item_group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
    stock_uom = frappe.db.get_value("UOM", {}, "name") or "Nos"
    item_code = f"TEST-RENT-{frappe.generate_hash(length=10)}"
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
