import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, flt, get_datetime, getdate, now_datetime

from erpverein.custom_fields import (
    SUBSCRIPTION_BILLING_KIND_FIELDNAME,
    SUBSCRIPTION_GENERATION_KEY_FIELDNAME,
    SUBSCRIPTION_MANAGED_FIELDNAME,
    SUBSCRIPTION_SOURCES_FIELDNAME,
    sync_custom_fields,
)
from erpverein.services import prenotification_service
from erpverein.services.payment_schedule_service import (
    ORIGIN_EDITED,
    ORIGIN_MANDATE,
    ORIGIN_STANDARD,
    PRENOTIFICATION_MANUAL,
    PRENOTIFICATION_NOT_REQUIRED,
    PRENOTIFICATION_PENDING,
    PRENOTIFICATION_RESEND,
    PRENOTIFICATION_SENT,
    apply_schedule_regeneration,
    get_schedule_editor,
    preview_schedule_regeneration,
    update_submitted_schedule,
)
from erpverein.services.prenotification_service import (
    PRINT_FORMAT_NAME,
    confirm_prenotification_manually,
    update_prenotification_status,
)
from erpverein.setup_data import (
    MODE_OF_PAYMENT_DIRECT_DEBIT,
    MODE_OF_PAYMENT_TRANSFER,
    PAYMENT_TERM_COUNT,
    payment_term_name,
    sync_setup_data,
)
from erpverein.tests.test_customer_sync_service import make_erpverein_manager_user
from erpverein.tests.test_sepa_mandat_doctype import make_mieter, make_mitglied, make_rent_sepa_mandat, make_sepa_mandat
from erpverein.tests.test_subscription_generation_service import make_item


TEST_COMPANY = "_Test Company 2"
TEST_PRICE_LIST = "ERPverein Test EUR"
HALF_YEARLY = ((3, 31, 175), (9, 30, 175))


class TestPaymentScheduleService(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()
        sync_setup_data()
        make_price_list()

    def test_setup_creates_payment_masters_once(self):
        sync_setup_data()

        self.assertTrue(frappe.db.exists("Mode of Payment", MODE_OF_PAYMENT_DIRECT_DEBIT))
        self.assertTrue(frappe.db.exists("Mode of Payment", MODE_OF_PAYMENT_TRANSFER))
        self.assertEqual(frappe.db.count("Payment Term", {"name": ["like", "ERPverein Rate %"]}), PAYMENT_TERM_COUNT)

    def test_direct_debit_member_invoice_follows_half_yearly_mandate(self):
        mitglied, mandate = make_direct_debit_member(HALF_YEARLY)

        invoice = make_member_invoice(mitglied)

        self.assertEqual(
            schedule_rows(invoice),
            [
                ("2027-03-31", 175.0, MODE_OF_PAYMENT_DIRECT_DEBIT, payment_term_name(1)),
                ("2027-09-30", 175.0, MODE_OF_PAYMENT_DIRECT_DEBIT, payment_term_name(2)),
            ],
        )
        self.assertEqual(invoice.erpverein_schedule_origin, ORIGIN_MANDATE)
        self.assertEqual(invoice.erpverein_sepa_mandate, mandate.name)
        self.assertEqual(invoice.erpverein_billing_kind, "Mitgliedsbeitrag")
        self.assertEqual((invoice.erpverein_payer_doctype, invoice.erpverein_payer), ("Mitglied", mitglied.name))
        self.assertEqual(invoice.erpverein_prenotification_status, PRENOTIFICATION_PENDING)
        self.assertEqual(str(invoice.due_date), "2027-09-30")
        self.assertFalse(invoice.payment_terms_template)
        self.assertTrue(all(row.erpverein_changed_at for row in invoice.payment_schedule))

    def test_saving_and_submitting_keep_generated_schedule(self):
        mitglied, _mandate = make_direct_debit_member(HALF_YEARLY)
        invoice = make_member_invoice(mitglied)
        before = schedule_rows(invoice)
        stamps = [str(row.erpverein_changed_at) for row in invoice.payment_schedule]

        invoice.save()
        invoice.submit()
        invoice.reload()

        self.assertEqual(schedule_rows(invoice), before)
        self.assertEqual([str(row.erpverein_changed_at) for row in invoice.payment_schedule], stamps)
        self.assertEqual(invoice.erpverein_prenotification_status, PRENOTIFICATION_PENDING)
        self.assertEqual([flt(row.outstanding) for row in invoice.payment_schedule], [175.0, 175.0])

    def test_invoice_payer_gets_standard_transfer_plan(self):
        mitglied = make_mitglied(customer=make_eur_customer("Schedule Transfer").name, jahresbeitrag=350)

        invoice = make_member_invoice(mitglied)

        self.assertEqual(len(invoice.payment_schedule), 1)
        self.assertEqual(invoice.payment_schedule[0].mode_of_payment, MODE_OF_PAYMENT_TRANSFER)
        self.assertEqual(invoice.payment_schedule[0].payment_term, payment_term_name(1))
        self.assertEqual(flt(invoice.payment_schedule[0].payment_amount), 350.0)
        self.assertEqual(invoice.erpverein_schedule_origin, ORIGIN_STANDARD)
        self.assertEqual(invoice.erpverein_prenotification_status, PRENOTIFICATION_NOT_REQUIRED)
        self.assertFalse(invoice.erpverein_sepa_mandate)

    def test_smaller_mandate_total_adds_transfer_rest_row(self):
        mitglied, _mandate = make_direct_debit_member(((1, 31, 300),))

        invoice = make_member_invoice(mitglied)

        self.assertEqual(
            [(row[0], row[1], row[2]) for row in schedule_rows(invoice)],
            [("2027-01-01", 50.0, MODE_OF_PAYMENT_TRANSFER), ("2027-01-31", 300.0, MODE_OF_PAYMENT_DIRECT_DEBIT)],
        )
        self.assertEqual(invoice.erpverein_schedule_origin, ORIGIN_MANDATE)

    def test_larger_mandate_total_falls_back_to_standard_plan(self):
        mitglied, _mandate = make_direct_debit_member(((1, 31, 400),))

        invoice = make_member_invoice(mitglied)

        self.assertEqual(invoice.erpverein_schedule_origin, ORIGIN_STANDARD)
        self.assertEqual([row.mode_of_payment for row in invoice.payment_schedule], [MODE_OF_PAYMENT_TRANSFER])
        self.assertIn("groesser", invoice.erpverein_schedule_note)
        self.assertEqual(invoice.erpverein_prenotification_status, PRENOTIFICATION_NOT_REQUIRED)

    def test_draft_edit_is_kept_on_submit(self):
        mitglied, _mandate = make_direct_debit_member(HALF_YEARLY)
        invoice = make_member_invoice(mitglied)

        invoice.payment_schedule[1].due_date = getdate("2027-10-15")
        invoice.save()
        invoice.submit()
        invoice.reload()

        self.assertEqual(invoice.erpverein_schedule_origin, ORIGIN_EDITED)
        self.assertEqual([row[0] for row in schedule_rows(invoice)], ["2027-03-31", "2027-10-15"])
        self.assertEqual(str(invoice.due_date), "2027-10-15")

    def test_editing_submitted_direct_debit_row_requires_new_prenotification(self):
        mitglied, _mandate = make_direct_debit_member(HALF_YEARLY)
        invoice = make_member_invoice(mitglied, submit=True)
        frappe.db.set_value("Sales Invoice", invoice.name, "erpverein_prenotification_status", PRENOTIFICATION_SENT)
        editor = get_schedule_editor(invoice.name)
        rows = editor_rows(editor)
        rows[1]["due_date"] = "2027-10-30"

        update_submitted_schedule(invoice.name, json.dumps(rows), editor["modified"])
        invoice.reload()

        self.assertEqual(
            schedule_rows(invoice),
            [
                ("2027-03-31", 175.0, MODE_OF_PAYMENT_DIRECT_DEBIT, payment_term_name(1)),
                ("2027-10-30", 175.0, MODE_OF_PAYMENT_DIRECT_DEBIT, payment_term_name(2)),
            ],
        )
        self.assertEqual(invoice.erpverein_schedule_origin, ORIGIN_EDITED)
        self.assertEqual(invoice.erpverein_prenotification_status, PRENOTIFICATION_RESEND)
        self.assertEqual(str(invoice.due_date), "2027-10-30")

    def test_submitted_schedule_must_match_invoice_total(self):
        mitglied, _mandate = make_direct_debit_member(HALF_YEARLY)
        invoice = make_member_invoice(mitglied, submit=True)
        editor = get_schedule_editor(invoice.name)
        rows = editor_rows(editor)
        rows[1]["payment_amount"] = 100

        with self.assertRaises(frappe.ValidationError):
            update_submitted_schedule(invoice.name, json.dumps(rows), editor["modified"])

    def test_paid_row_cannot_be_changed_or_removed(self):
        mitglied, _mandate = make_direct_debit_member(HALF_YEARLY)
        invoice = make_member_invoice(mitglied, submit=True)
        paid_row = invoice.payment_schedule[0]
        frappe.db.set_value("Payment Schedule", paid_row.name, {"paid_amount": 175, "outstanding": 0})
        editor = get_schedule_editor(invoice.name)
        self.assertTrue(editor["rows"][0]["locked"])

        changed = editor_rows(editor)
        changed[0]["due_date"] = "2027-04-15"
        with self.assertRaises(frappe.ValidationError):
            update_submitted_schedule(invoice.name, json.dumps(changed), editor["modified"])

        removed = editor_rows(editor)[1:]
        removed[0]["payment_amount"] = 350
        with self.assertRaises(frappe.ValidationError):
            update_submitted_schedule(invoice.name, json.dumps(removed), editor["modified"])

    def test_regeneration_restores_mandate_schedule(self):
        mitglied, _mandate = make_direct_debit_member(HALF_YEARLY)
        invoice = make_member_invoice(mitglied, submit=True)
        editor = get_schedule_editor(invoice.name)
        rows = editor_rows(editor)
        rows[0]["mode_of_payment"] = MODE_OF_PAYMENT_TRANSFER
        update_submitted_schedule(invoice.name, json.dumps(rows), editor["modified"])

        preview = preview_schedule_regeneration(invoice.name)
        apply_schedule_regeneration(invoice.name, preview["preview_hash"])
        invoice.reload()

        self.assertTrue(preview["allowed"])
        self.assertEqual(preview["origin"], ORIGIN_MANDATE)
        self.assertEqual(invoice.erpverein_schedule_origin, ORIGIN_MANDATE)
        self.assertEqual(
            [row[:3] for row in schedule_rows(invoice)],
            [("2027-03-31", 175.0, MODE_OF_PAYMENT_DIRECT_DEBIT), ("2027-09-30", 175.0, MODE_OF_PAYMENT_DIRECT_DEBIT)],
        )

    def test_unchanged_regeneration_keeps_sent_prenotification(self):
        mitglied, _mandate = make_direct_debit_member(HALF_YEARLY)
        invoice = make_member_invoice(mitglied, submit=True)
        stamps = [str(row.erpverein_changed_at) for row in invoice.payment_schedule]
        frappe.db.set_value("Sales Invoice", invoice.name, "erpverein_prenotification_status", PRENOTIFICATION_SENT)

        preview = preview_schedule_regeneration(invoice.name)
        apply_schedule_regeneration(invoice.name, preview["preview_hash"])
        invoice.reload()

        self.assertEqual(invoice.erpverein_prenotification_status, PRENOTIFICATION_SENT)
        self.assertEqual([str(row.erpverein_changed_at) for row in invoice.payment_schedule], stamps)

    def test_treasurer_without_administrator_can_edit_submitted_schedule(self):
        mitglied, _mandate = make_direct_debit_member(HALF_YEARLY)
        invoice = make_member_invoice(mitglied, submit=True)
        user = make_erpverein_manager_user()
        try:
            frappe.set_user(user.name)
            editor = get_schedule_editor(invoice.name)
            rows = editor_rows(editor)
            rows[1]["due_date"] = "2027-10-30"
            update_submitted_schedule(invoice.name, json.dumps(rows), editor["modified"])
        finally:
            frappe.set_user("Administrator")
        invoice.reload()

        self.assertEqual(invoice.erpverein_schedule_origin, ORIGIN_EDITED)
        self.assertEqual(str(invoice.due_date), "2027-10-30")

    def test_user_without_accounts_role_cannot_edit_submitted_schedule(self):
        mitglied, _mandate = make_direct_debit_member(HALF_YEARLY)
        invoice = make_member_invoice(mitglied, submit=True)
        editor = get_schedule_editor(invoice.name)
        user = make_user_with_roles("Sales User", "Sales Master Manager")
        try:
            frappe.set_user(user.name)
            with self.assertRaises(frappe.PermissionError):
                update_submitted_schedule(invoice.name, json.dumps(editor_rows(editor)), editor["modified"])
        finally:
            frappe.set_user("Administrator")

    def test_rent_invoice_uses_monthly_tenant_mandate(self):
        customer = make_eur_customer("Schedule Rent")
        mieter = make_mieter(customer=customer.name)
        make_rent_sepa_mandat(
            mieter,
            einzugsintervall="Monatlich",
            monatstag=3,
            regelmaessiger_einzugsbetrag=500,
            einzugstermine=[],
        ).insert(ignore_permissions=True)
        mieter.reload()
        mieter.abrechnungsart = "Lastschrift"
        mieter.save(ignore_permissions=True)
        subscription = make_managed_subscription(
            customer.name, "Mieter", mieter.name, "Mieter", make_plan(500, "Month").name, "2027-02-01", "Miete"
        )

        invoice = subscription.create_invoice()

        self.assertEqual(
            schedule_rows(invoice),
            [("2027-02-03", 500.0, MODE_OF_PAYMENT_DIRECT_DEBIT, payment_term_name(1))],
        )
        self.assertEqual(invoice.erpverein_billing_kind, "Miete")
        self.assertEqual((invoice.erpverein_payer_doctype, invoice.erpverein_payer), ("Mieter", mieter.name))

    def test_sent_invoice_email_marks_prenotification_as_sent(self):
        mitglied, _mandate = make_direct_debit_member(HALF_YEARLY)
        invoice = make_member_invoice(mitglied, submit=True)
        sent_at = add_to_date(now_datetime(), minutes=5)
        make_sent_invoice_email(invoice.name, PRINT_FORMAT_NAME, sent_at)

        status = update_prenotification_status(invoice.name)
        invoice.reload()

        self.assertEqual(status, PRENOTIFICATION_SENT)
        self.assertEqual(invoice.erpverein_prenotification_status, PRENOTIFICATION_SENT)
        self.assertEqual(get_datetime(invoice.erpverein_prenotification_sent_at), get_datetime(sent_at))

    def test_email_without_erpverein_print_format_is_no_prenotification(self):
        mitglied, _mandate = make_direct_debit_member(HALF_YEARLY)
        invoice = make_member_invoice(mitglied, submit=True)
        make_sent_invoice_email(invoice.name, "Standard", add_to_date(now_datetime(), minutes=5))

        self.assertEqual(update_prenotification_status(invoice.name), PRENOTIFICATION_PENDING)

    def test_prenotification_refresh_continues_after_failed_invoice(self):
        failing_member, _mandate = make_direct_debit_member(HALF_YEARLY)
        failing = make_member_invoice(failing_member, submit=True)
        sent_member, _mandate = make_direct_debit_member(HALF_YEARLY)
        sent = make_member_invoice(sent_member, submit=True)
        make_sent_invoice_email(sent.name, PRINT_FORMAT_NAME, add_to_date(now_datetime(), minutes=5))
        update_status = prenotification_service.update_prenotification_status

        def fail_for_one_invoice(invoice):
            if invoice == failing.name:
                raise frappe.ValidationError("synthetic failure")
            return update_status(invoice)

        with patch.object(prenotification_service, "update_prenotification_status", side_effect=fail_for_one_invoice):
            prenotification_service.refresh_prenotification_status()

        self.assertEqual(
            frappe.db.get_value("Sales Invoice", sent.name, "erpverein_prenotification_status"), PRENOTIFICATION_SENT
        )
        self.assertEqual(
            frappe.db.get_value("Sales Invoice", failing.name, "erpverein_prenotification_status"), PRENOTIFICATION_PENDING
        )

    def test_prenotification_can_be_confirmed_manually(self):
        mitglied, _mandate = make_direct_debit_member(HALF_YEARLY)
        invoice = make_member_invoice(mitglied, submit=True)

        confirm_prenotification_manually(invoice.name)
        invoice.reload()

        self.assertEqual(invoice.erpverein_prenotification_status, PRENOTIFICATION_MANUAL)
        self.assertTrue(invoice.erpverein_prenotification_sent_at)


def schedule_rows(invoice) -> list[tuple]:
    return [
        (str(row.due_date), flt(row.payment_amount), row.mode_of_payment, row.payment_term)
        for row in sorted(invoice.payment_schedule, key=lambda row: str(row.due_date))
    ]


def editor_rows(editor: dict) -> list[dict]:
    return [
        {
            "name": row["name"],
            "due_date": row["due_date"],
            "payment_amount": row["payment_amount"],
            "mode_of_payment": row["mode_of_payment"],
        }
        for row in editor["rows"]
    ]


def make_direct_debit_member(dates):
    customer = make_eur_customer("Schedule Direct Debit")
    mitglied = make_mitglied(customer=customer.name, jahresbeitrag=350)
    interval = {1: "Jaehrlich", 2: "Halbjaehrlich", 4: "Vierteljaehrlich"}[len(dates)]
    mandate = make_sepa_mandat(
        mitglied,
        einzugsintervall=interval,
        einzugstermine=[{"monat": month, "kalendertag": day, "einzugsbetrag": amount} for month, day, amount in dates],
    ).insert(ignore_permissions=True)
    mitglied.reload()
    mitglied.abrechnungsart = "Lastschrift"
    mitglied.save(ignore_permissions=True)
    return mitglied, mandate


def make_member_invoice(mitglied, *, submit: bool = False):
    subscription = make_managed_subscription(
        mitglied.customer, "Mitglied", mitglied.name, "Beitragszahler", make_plan(350, "Year").name, "2027-01-01", "Mitgliedsbeitrag"
    )
    invoice = subscription.create_invoice()
    if submit:
        invoice.submit()
    invoice.reload()
    return invoice


def make_managed_subscription(customer: str, source_doctype: str, source_name: str, role: str, plan: str, start_date: str, kind: str):
    subscription = frappe.get_doc(
        {
            "doctype": "Subscription",
            "party_type": "Customer",
            "party": customer,
            "company": TEST_COMPANY,
            "cost_center": frappe.db.get_value("Cost Center", {"company": TEST_COMPANY, "is_group": 0}, "name"),
            "start_date": start_date,
            "generate_invoice_at": "Beginning of the current subscription period",
            "generate_new_invoices_past_due_date": 0,
            "submit_invoice": 0,
            "days_until_due": 0,
            "plans": [{"plan": plan, "qty": 1}],
            SUBSCRIPTION_MANAGED_FIELDNAME: 1,
            SUBSCRIPTION_BILLING_KIND_FIELDNAME: kind,
            SUBSCRIPTION_GENERATION_KEY_FIELDNAME: frappe.generate_hash(length=20),
            SUBSCRIPTION_SOURCES_FIELDNAME: [{"source_doctype": source_doctype, "source_name": source_name, "source_role": role}],
        }
    )
    subscription.flags.erpverein_generation = True
    return subscription.insert(ignore_permissions=True)


def make_plan(amount: int, interval: str):
    return frappe.get_doc(
        {
            "doctype": "Subscription Plan",
            "plan_name": f"ERPverein Schedule {interval} {amount} {frappe.generate_hash(length=8)}",
            "currency": "EUR",
            "item": make_item().name,
            "price_determination": "Fixed Rate",
            "cost": amount,
            "billing_interval": interval,
            "billing_interval_count": 1,
        }
    ).insert(ignore_permissions=True)


def make_price_list() -> str:
    if not frappe.db.exists("Price List", TEST_PRICE_LIST):
        frappe.get_doc(
            {"doctype": "Price List", "price_list_name": TEST_PRICE_LIST, "currency": "EUR", "selling": 1, "enabled": 1}
        ).insert(ignore_permissions=True)
    return TEST_PRICE_LIST


def make_eur_customer(label: str):
    customer = frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": f"{label} {frappe.generate_hash(length=8)}",
            "customer_type": "Individual",
            "default_price_list": TEST_PRICE_LIST,
        }
    )
    customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
    if customer_group:
        customer.customer_group = customer_group
    territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
    if territory:
        customer.territory = territory
    return customer.insert(ignore_permissions=True)


def make_user_with_roles(*roles: str):
    user = frappe.get_doc(
        {
            "doctype": "User",
            "email": f"erpverein-schedule-{frappe.generate_hash(length=8)}@example.org",
            "first_name": "ERPverein",
            "last_name": "Schedule",
            "enabled": 1,
            "send_welcome_email": 0,
        }
    ).insert(ignore_permissions=True)
    user.add_roles(*roles)
    return user


def make_sent_invoice_email(invoice: str, print_format: str, sent_at) -> None:
    communication = frappe.get_doc(
        {
            "doctype": "Communication",
            "communication_medium": "Email",
            "sent_or_received": "Sent",
            "reference_doctype": "Sales Invoice",
            "reference_name": invoice,
            "subject": "Rechnung",
            "content": "Rechnung",
            "recipients": "member@example.com",
        }
    ).insert(ignore_permissions=True)
    queue = frappe.get_doc(
        {
            "doctype": "Email Queue",
            "status": "Sent",
            "communication": communication.name,
            "reference_doctype": "Sales Invoice",
            "reference_name": invoice,
            "sender": "verein@example.com",
            "message": "Rechnung",
            "attachments": json.dumps(
                [{"print_format_attachment": 1, "print_format": print_format, "doctype": "Sales Invoice", "name": invoice}]
            ),
        }
    )
    queue.name = frappe.generate_hash(length=12)
    queue.db_insert()
    frappe.db.set_value("Email Queue", queue.name, "modified", sent_at, update_modified=False)
