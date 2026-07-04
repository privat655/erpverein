import json

import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.custom_fields import sync_custom_fields
from verein_erp.services.subscription_generation_service import (
    ACTION_CREATE,
    ACTION_ERROR,
    build_subscription_preview,
    estimate_invoice_count,
    suggest_subscription_plans,
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

    def test_preview_reports_missing_customer_as_error(self):
        plan_250 = make_subscription_plan(250)
        plan_350 = make_subscription_plan(350)
        payer = make_mitglied(abrechnungsart="Rechnung", jahresbeitrag=350)
        run = make_run(payer.name, plan_250.name, plan_350.name)

        result = build_subscription_preview(run.name)
        run.reload()

        self.assertEqual(result["errors"], 1)
        self.assertEqual(run.preview_rows[0].action, ACTION_ERROR)

    def test_lastschrift_without_sepa_mandat_still_previews_subscription(self):
        plan_250 = make_subscription_plan(250)
        plan_350 = make_subscription_plan(350)
        customer = make_customer("Subscription Direct Debit")
        payer = make_mitglied(customer=customer.name, abrechnungsart="Rechnung", jahresbeitrag=350)
        frappe.db.set_value("Mitglied", payer.name, "abrechnungsart", "Lastschrift")
        run = make_run(payer.name, plan_250.name, plan_350.name)

        result = build_subscription_preview(run.name)
        run.reload()

        self.assertEqual(result["errors"], 0)
        self.assertEqual(len(run.preview_rows), 2)

    def test_missing_plan_for_reduced_fee_is_preview_error(self):
        plan_250 = make_subscription_plan(250)
        plan_350 = make_subscription_plan(350)
        customer = make_customer("Subscription Reduced")
        payer = make_mitglied(customer=customer.name, abrechnungsart="Rechnung", jahresbeitrag=175)
        run = make_run(payer.name, plan_250.name, plan_350.name)

        result = build_subscription_preview(run.name)
        run.reload()

        self.assertGreater(result["errors"], 0)
        self.assertTrue(any(row.action == ACTION_ERROR for row in run.preview_rows))

    def test_preview_combines_multiple_plans_in_one_subscription_period(self):
        plan_250 = make_subscription_plan(250)
        plan_350 = make_subscription_plan(350)
        plan_175 = make_subscription_plan(175)
        customer = make_customer("Subscription Mixed")
        payer = make_mitglied(customer=customer.name, abrechnungsart="Rechnung", jahresbeitrag=350)
        make_mitglied(abrechnungsart="Beitrag wird uebernommen", beitragszahler=payer.name, jahresbeitrag=175)
        run = make_run(payer.name, plan_250.name, plan_350.name)
        run.append(
            "periods",
            {
                "from_date": "2023-01-01",
                "subscription_plan": plan_175.name,
                "annual_amount": 175,
                "apply_to_annual_fee": 175,
            },
        )
        run.save(ignore_permissions=True)

        result = build_subscription_preview(run.name)
        run.reload()
        current_row = next(row for row in run.preview_rows if str(row.period_from) == "2023-01-01")
        plan_lines = json.loads(current_row.plans_json)

        self.assertEqual(result["errors"], 0)
        self.assertEqual(current_row.total_qty, 2)
        self.assertEqual({line["plan"]: line["qty"] for line in plan_lines}, {plan_350.name: 1, plan_175.name: 1})

    def test_estimate_invoice_count_does_not_include_2027_before_2027(self):
        period = frappe._dict({"from_date": "2023-01-01", "to_date": None})

        self.assertEqual(estimate_invoice_count(period, "Beginning of the current subscription period"), 4)


def make_run(mitglied: str, plan_250: str, plan_350: str):
    run = frappe.get_doc(
        {
            "doctype": "Mitglied Subscription Lauf",
            "scope": "Einzelnes Mitglied",
            "mitglied": mitglied,
            "company": get_company(),
            "cost_center": get_cost_center(),
            "generate_new_invoices_past_due_date": 1,
            "generate_invoice_at": "Beginning of the current subscription period",
            "periods": [
                {
                    "from_date": "2016-01-01",
                    "to_date": "2022-12-31",
                    "subscription_plan": plan_250,
                    "annual_amount": 250,
                    "apply_to_annual_fee": 350,
                },
                {
                    "from_date": "2023-01-01",
                    "subscription_plan": plan_350,
                    "annual_amount": 350,
                    "apply_to_annual_fee": 350,
                },
            ],
        }
    )
    return run.insert(ignore_permissions=True)


def make_customer(label: str):
    customer = frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": f"{label} {frappe.generate_hash(length=8)}",
            "customer_type": "Individual",
        }
    )
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
        "vorname": "Test",
        "nachname": f"Mitglied {frappe.generate_hash(length=6)}",
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
        {
            "doctype": "Item",
            "item_code": item_code,
            "item_name": item_code,
            "item_group": item_group,
            "stock_uom": stock_uom,
            "is_stock_item": 0,
        }
    ).insert(ignore_permissions=True)


def get_company() -> str:
    return frappe.defaults.get_user_default("Company") or frappe.db.get_value("Company", {}, "name")


def get_cost_center() -> str:
    company = get_company()
    filters = {"is_group": 0}
    if company and frappe.get_meta("Cost Center").has_field("company"):
        filters["company"] = company
    return frappe.db.get_value("Cost Center", filters, "name")
