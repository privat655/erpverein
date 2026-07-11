import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpverein.api.rental_subscription_generation import create_subscriptions
from erpverein.custom_fields import sync_custom_fields
from erpverein.services.billing_common import RUN_STATUS_QUEUED
from erpverein.services.billing_job_service import deterministic_job_ids
from erpverein.services.rental_subscription_generation_service import (
    ACTION_CONFLICT,
    ACTION_CREATE,
    ACTION_ERROR,
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

    def test_lastschrift_without_active_mandate_is_preview_error(self):
        plan = make_subscription_plan(602)
        customer = make_customer("Rental Direct Debit")
        mieter = make_mieter(customer=customer.name, mietbeginn="2026-01-01")
        frappe.db.set_value("Mieter", mieter.name, "abrechnungsart", "Lastschrift")
        run = make_run(mieter.name, plan.name, "2026-01-01", "2026-12-31")

        result = build_rental_subscription_preview(run.name)
        run.reload()

        self.assertEqual(result["errors"], 1)
        self.assertEqual(run.preview_rows[0].action, ACTION_ERROR)

    def test_managed_membership_subscription_may_overlap_rental(self):
        plan = make_subscription_plan(605)
        customer = make_customer("Rental Opposite Kind")
        mieter = make_mieter(customer=customer.name, mietbeginn="2026-01-01")
        existing = make_subscription(customer.name, plan.name, "2026-01-01", "2026-12-31")
        frappe.db.set_value(
            "Subscription",
            existing.name,
            {"erpverein_managed": 1, "erpverein_billing_kind": "Mitgliedsbeitrag"},
        )
        run = make_run(mieter.name, plan.name, "2026-01-01", "2026-12-31")

        build_rental_subscription_preview(run.name)
        run.reload()

        self.assertEqual(run.preview_rows[0].action, ACTION_CREATE)

    def test_monthly_estimate_counts_due_periods(self):
        period = frappe._dict({"from_date": "2026-01-01", "to_date": "2026-03-31"})

        with patch("erpverein.services.rental_subscription_generation_service.nowdate", return_value="2026-07-11"):
            self.assertEqual(estimate_invoice_count(period, "Periodenbeginn"), 3)

    def test_overlapping_rental_periods_are_rejected(self):
        plan = make_subscription_plan(690)
        customer = make_customer("Rental Period Overlap")
        mieter = make_mieter(customer=customer.name, mietbeginn="2026-01-01")
        run = make_run(mieter.name, plan.name, "2026-01-01", "2026-12-31")
        run.append("periods", {"from_date": "2026-06-01", "to_date": None, "subscription_plan": plan.name})

        with self.assertRaises(frappe.ValidationError):
            run.save(ignore_permissions=True)

    def test_non_monthly_rental_plan_is_rejected(self):
        annual_plan = make_subscription_plan(695, interval="Year")
        customer = make_customer("Rental Wrong Interval")
        mieter = make_mieter(customer=customer.name, mietbeginn="2026-01-01")

        with self.assertRaises(frappe.ValidationError):
            make_run(mieter.name, annual_plan.name, "2026-01-01", "2026-12-31")

    def test_create_subscriptions_enqueues_deterministic_launcher(self):
        plan = make_subscription_plan(700)
        customer = make_customer("Rental Queue")
        mieter = make_mieter(customer=customer.name, mietbeginn="2026-01-01")
        run = make_run(mieter.name, plan.name, "2026-01-01", "2026-12-31")
        build_rental_subscription_preview(run.name)
        run.reload()

        launcher_id, worker_id = deterministic_job_ids("Mietabrechnung", run.name)
        with patch("frappe.enqueue", return_value=None) as enqueue:
            result = create_subscriptions(run.name, run.preview_hash, "create_subscriptions")

        run.reload()
        self.assertEqual(result, {"run": run.name, "job_id": launcher_id})
        self.assertEqual(run.status, RUN_STATUS_QUEUED)
        self.assertEqual(run.worker_job_id, worker_id)
        self.assertEqual(enqueue.call_args.kwargs["queue"], "default")
        self.assertEqual(enqueue.call_args.kwargs["run_name"], run.name)
        self.assertEqual(enqueue.call_args.kwargs["job_id"], launcher_id)
        self.assertEqual(
            enqueue.call_args.args[0],
            "erpverein.services.billing_job_service.launch_billing_worker",
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
            subscription, existing, _message = create_subscription_for_preview_row(run, run.preview_rows[0])

        self.assertIsNone(existing)
        self.assertEqual(subscription.submit_invoice, 0)
        self.assertEqual(subscription.erpverein_managed, 1)
        self.assertEqual(subscription.erpverein_billing_kind, "Miete")
        self.assertEqual(subscription.erpverein_sources[0].source_name, mieter.name)

    def test_finite_effective_period_must_cover_more_than_first_cycle(self):
        plan = make_subscription_plan(810)
        customer = make_customer("Rental Short")
        mieter = make_mieter(customer=customer.name, mietbeginn="2026-03-15", mietende="2026-03-31")
        run = make_run(mieter.name, plan.name, "2026-01-01", "2026-12-31")

        result = build_rental_subscription_preview(run.name)
        run.reload()

        self.assertEqual(result["errors"], 1)
        self.assertEqual(run.preview_rows[0].action, "Error")

    def test_same_generation_key_and_payload_is_idempotent(self):
        plan = make_subscription_plan(820)
        customer = make_customer("Rental Idempotent")
        mieter = make_mieter(customer=customer.name, mietbeginn="2099-01-01")
        run = make_run(mieter.name, plan.name, "2099-01-01", "2099-12-31")
        run.submit_invoice = 0
        run.generate_new_invoices_past_due_date = 0
        run.save(ignore_permissions=True)
        build_rental_subscription_preview(run.name)
        run.reload()

        created, existing, _message = create_subscription_for_preview_row(run, run.preview_rows[0])
        duplicate, winner, _message = create_subscription_for_preview_row(run, run.preview_rows[0])

        self.assertIsNotNone(created)
        self.assertIsNone(existing)
        self.assertIsNone(duplicate)
        self.assertEqual(winner, created.name)

        created.reload()
        created.save(ignore_permissions=True)
        created.reload()
        created.erpverein_generation_key = "forged"
        with self.assertRaises(frappe.ValidationError):
            created.save(ignore_permissions=True)

    def test_rental_change_after_preview_is_rejected_at_creation(self):
        plan = make_subscription_plan(825)
        customer = make_customer("Rental Stale Source")
        mieter = make_mieter(customer=customer.name, mietbeginn="2027-01-01")
        run = make_run(mieter.name, plan.name, "2027-01-01", "2027-12-31")
        build_rental_subscription_preview(run.name)
        run.reload()
        frappe.db.set_value("Mieter", mieter.name, "mietende", "2027-10-31")

        with self.assertRaisesRegex(Exception, "seit der Vorschau"):
            create_subscription_for_preview_row(run, run.preview_rows[0])

    def test_estimate_invoice_count_stops_after_one_when_catchup_is_disabled(self):
        period = frappe._dict({"from_date": "2026-01-01", "to_date": "2026-06-30"})
        with patch("erpverein.services.rental_subscription_generation_service.nowdate", return_value="2026-07-11"):
            self.assertEqual(estimate_invoice_count(period, "Periodenbeginn", 0, False), 1)


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


def make_subscription_plan(amount: int, interval: str = "Month"):
    item = make_item()
    plan_name = f"Test Rent Plan {amount} {frappe.generate_hash(length=8)}"
    return frappe.get_doc(
        {
            "doctype": "Subscription Plan",
            "plan_name": plan_name,
            "currency": get_company_currency(),
            "item": item.name,
            "price_determination": "Fixed Rate",
            "cost": amount,
            "billing_interval": interval,
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


def get_company_currency() -> str:
    return frappe.db.get_value("Company", get_company(), "default_currency")
