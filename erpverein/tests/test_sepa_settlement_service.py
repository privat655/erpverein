from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, flt, formatdate, getdate

from erpverein.custom_fields import sync_custom_fields
from erpverein.services import sepa_settlement_service
from erpverein.services.bank_import_service import IMPORT_DOCTYPE
from erpverein.services.sepa_collection_export_service import export_collection_run
from erpverein.services.sepa_collection_service import (
    POSITION_DOCTYPE,
    RUN_DOCTYPE,
    cancel_collection_run,
    check_collection_run,
)
from erpverein.services.sepa_settlement_service import (
    reconcile_bank_account,
    run_bank_reconciliation,
    start_import_reconciliation,
    validate_payment_entry_provenance,
)
from erpverein.setup_data import sync_setup_data
from erpverein.tests.collection_fixtures import NEXT_YEAR, ensure_export_profile, make_company_bank_account
from erpverein.tests.test_bank_import_service import ALIAS, V3_SCHEMA, send, token, v3_file
from erpverein.tests.test_customer_sync_service import make_erpverein_manager_user
from erpverein.tests.test_payment_schedule_service import make_eur_customer, make_price_list, make_user_with_roles
from erpverein.tests.test_sepa_collection_service import (
    generate,
    make_member_invoice,
    make_member_with_mandate,
    make_rent_invoice,
    make_tenant_with_mandate,
)
from erpverein.tests.test_sepa_mandat_doctype import make_german_iban


BATCH_PURPOSE = "SEPA-BASISLASTSCHRIFT E.V."
POSITION_FIELDS = [
    "name",
    "status",
    "sales_invoice",
    "payment_term",
    "amount",
    "billing_kind",
    "end_to_end_reference",
    "payment_entry",
    "settlement_bank_transaction",
    "processing_error",
]
PAYMENT_FIELDS = [
    "name",
    "owner",
    "party",
    "paid_amount",
    "posting_date",
    "reference_no",
    "mode_of_payment",
    "clearance_date",
    "erpverein_sepa_collection_item",
    "erpverein_bank_transaction",
]
TRANSACTION_FIELDS = [
    "name",
    "date",
    "status",
    "unallocated_amount",
    "erpverein_match_status",
    "erpverein_match_note",
]


class TestSEPASettlementService(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()
        sync_setup_data()
        make_price_list()
        ensure_export_profile()
        cls.bank_account = make_company_bank_account()
        frappe.db.set_value("Bank Account", cls.bank_account, "erpverein_bank_import_alias", ALIAS)

    def test_batch_credit_equal_to_run_total_pays_each_position(self):
        # T10: one payment per position, the bank credit is reconciled, the invoices are paid.
        first = make_member_invoice(make_member_with_mandate("Settle Equal A", dates=((2, 26, 350),)))
        second = make_member_invoice(make_member_with_mandate("Settle Equal B", dates=((2, 26, 350),)))
        run = exported_run(f"{NEXT_YEAR}-02-26", first.name, second.name)
        bank_import = import_credits(credit_row(700, run.collection_date, f"{BATCH_PURPOSE} POSTEN 2"))

        reconcile(bank_import)
        reconcile(bank_import)

        run.reload()
        positions = run_positions(run.name)
        payments = payments_for(positions)
        transaction = transactions_for(bank_import)[0]

        self.assertEqual((run.status, run.settlement_bank_transaction), ("Abgestimmt", transaction.name))
        self.assertTrue(run.settled_at)
        self.assertEqual({position.status for position in positions}, {"Bezahlt"})
        self.assertEqual(len(payments), 2)
        self.assertEqual({payment.erpverein_sepa_collection_item for payment in payments}, {p.name for p in positions})
        self.assertEqual({position.payment_entry for position in positions}, {payment.name for payment in payments})
        self.assertEqual((transaction.status, transaction.erpverein_match_status), ("Reconciled", "Abgeglichen"))
        self.assertEqual(flt(transaction.unallocated_amount), 0)
        self.assertIn(run.name, transaction.erpverein_match_note)
        for position in positions:
            payment = next(row for row in payments if row.erpverein_sepa_collection_item == position.name)
            references = frappe.get_all(
                "Payment Entry Reference",
                filters={"parent": payment.name},
                fields=["reference_name", "payment_term", "allocated_amount"],
            )
            self.assertEqual(
                (
                    payment.reference_no,
                    str(payment.posting_date),
                    payment.mode_of_payment,
                    payment.erpverein_bank_transaction,
                ),
                (position.end_to_end_reference, str(transaction.date), "SEPA-Lastschrift", transaction.name),
            )
            self.assertEqual(flt(payment.paid_amount), 350)
            self.assertTrue(payment.clearance_date)
            self.assertEqual(
                [(row.reference_name, row.payment_term, flt(row.allocated_amount)) for row in references],
                [(position.sales_invoice, position.payment_term, 350)],
            )
        for invoice in (first, second):
            invoice.reload()
            self.assertEqual((invoice.status, flt(invoice.outstanding_amount)), ("Paid", 0))
            self.assertEqual(
                (flt(invoice.payment_schedule[0].paid_amount), flt(invoice.payment_schedule[0].outstanding)), (350, 0)
            )
        bank_import_doc = frappe.get_doc(IMPORT_DOCTYPE, bank_import)
        self.assertEqual(
            (bank_import_doc.reconciliation_status, bank_import_doc.reconciled_count), ("Abgeschlossen", 1)
        )
        with self.assertRaises(frappe.ValidationError):
            cancel_collection_run(run.name, "Test", confirm_not_submitted=1)
        stray = frappe.new_doc("Payment Entry")
        stray.erpverein_sepa_collection_item = positions[0].name
        with self.assertRaises(frappe.ValidationError):
            validate_payment_entry_provenance(stray)

    def test_batch_credit_unequal_to_run_total_creates_no_payment(self):
        # T11: no payment, manual review with the reason; the exact credit can settle the run later.
        first = make_member_invoice(make_member_with_mandate("Settle Unequal A", dates=((4, 30, 350),)))
        second = make_member_invoice(make_member_with_mandate("Settle Unequal B", dates=((4, 30, 350),)))
        run = exported_run(f"{NEXT_YEAR}-04-30", first.name, second.name)
        short = import_credits(credit_row(690, run.collection_date, f"{BATCH_PURPOSE} POSTEN 2"))

        reconcile(short)

        run.reload()
        transaction = transactions_for(short)[0]
        self.assertEqual(payments_for(run_positions(run.name)), [])
        self.assertEqual((run.status, run.settlement_bank_transaction), ("Manuelle Pruefung", None))
        self.assertIn("690,00 EUR", run.settlement_note)
        self.assertIn("700,00 EUR", run.settlement_note)
        self.assertEqual({position.status for position in run_positions(run.name)}, {"Exportiert"})
        self.assertEqual(
            (transaction.status, transaction.erpverein_match_status), ("Unreconciled", "Manuelle Pruefung")
        )
        self.assertIn(run.name, transaction.erpverein_match_note)

        exact = import_credits(credit_row(700, add_days(run.collection_date, 1), f"{BATCH_PURPOSE} POSTEN 2"))
        reconcile(exact)

        run.reload()
        self.assertEqual((run.status, run.settlement_bank_transaction), ("Abgestimmt", transactions_for(exact)[0].name))
        self.assertEqual(transactions_for(short)[0].erpverein_match_status, "Manuelle Pruefung")
        self.assertEqual(len(payments_for(run_positions(run.name))), 2)

    def test_restart_creates_only_the_missing_payment(self):
        first = make_member_invoice(make_member_with_mandate("Settle Restart A", dates=((5, 31, 350),)))
        second = make_member_invoice(make_member_with_mandate("Settle Restart B", dates=((5, 31, 350),)))
        run = exported_run(f"{NEXT_YEAR}-05-31", first.name, second.name)
        bank_import = import_credits(credit_row(700, run.collection_date, f"{BATCH_PURPOSE} POSTEN 2"))
        build = sepa_settlement_service.build_payment_entry

        def fail_second_invoice(run_doc, position, transaction):
            if position.sales_invoice == second.name:
                frappe.throw("Testfehler Buchungsperiode")
            return build(run_doc, position, transaction)

        with patch.object(sepa_settlement_service, "build_payment_entry", side_effect=fail_second_invoice):
            reconcile(bank_import)

        run.reload()
        positions = {position.sales_invoice: position for position in run_positions(run.name)}
        transaction = transactions_for(bank_import)[0]
        self.assertEqual(run.status, "Manuelle Pruefung")
        self.assertEqual(
            (positions[first.name].status, positions[second.name].status), ("Bezahlt", "Manuelle Pruefung")
        )
        self.assertIn("Testfehler", positions[second.name].processing_error)
        self.assertEqual(
            (flt(transaction.unallocated_amount), transaction.erpverein_match_status), (350, "Manuelle Pruefung")
        )
        first_payment = positions[first.name].payment_entry

        reconcile(bank_import)
        reconcile(bank_import)

        run.reload()
        positions = {position.sales_invoice: position for position in run_positions(run.name)}
        transaction = transactions_for(bank_import)[0]
        self.assertEqual(run.status, "Abgestimmt")
        self.assertEqual(positions[first.name].payment_entry, first_payment)
        self.assertEqual(len(payments_for(list(positions.values()))), 2)
        self.assertEqual(
            frappe.db.count("Payment Entry", {"erpverein_sepa_collection_item": positions[first.name].name}), 1
        )
        self.assertEqual((transaction.status, transaction.erpverein_match_status), ("Reconciled", "Abgeglichen"))

    def test_single_booking_credits_pay_positions_by_end_to_end_id(self):
        first = make_member_invoice(make_member_with_mandate("Settle Single A", dates=((6, 30, 350),)))
        second = make_member_invoice(make_member_with_mandate("Settle Single B", dates=((6, 30, 350),)))
        run = exported_run(f"{NEXT_YEAR}-06-30", first.name, second.name, batch_booking=0)
        positions = run_positions(run.name)
        first_import = import_credits(single_credit_row(positions[0], run.collection_date))

        reconcile(first_import)

        run.reload()
        self.assertEqual(run.status, "Bankgutschrift erkannt")
        self.assertEqual([position.status for position in run_positions(run.name)], ["Bezahlt", "Exportiert"])

        second_import = import_credits(single_credit_row(positions[1], run.collection_date))
        reconcile(second_import)

        run.reload()
        positions = run_positions(run.name)
        transactions = transactions_for(first_import) + transactions_for(second_import)
        self.assertEqual((run.status, run.settlement_bank_transaction), ("Abgestimmt", None))
        self.assertEqual(
            [position.settlement_bank_transaction for position in positions], [row.name for row in transactions]
        )
        self.assertEqual(
            {(row.status, row.erpverein_match_status) for row in transactions}, {("Reconciled", "Abgeglichen")}
        )
        self.assertEqual(len(payments_for(positions)), 2)

    def test_member_and_tenant_of_one_customer_are_paid_separately(self):
        customer = make_eur_customer("Settle Shared")
        membership = make_member_invoice(
            make_member_with_mandate("Settle Shared", dates=((3, 1, 350),), customer=customer)
        )
        rent = make_rent_invoice(make_tenant_with_mandate(customer))
        run = exported_run(f"{NEXT_YEAR}-03-01", membership.name, rent.name)
        bank_import = import_credits(credit_row(850, run.collection_date, f"{BATCH_PURPOSE} POSTEN 2"))

        reconcile(bank_import)

        run.reload()
        positions = {position.billing_kind: position for position in run_positions(run.name)}
        payments = {
            payment.erpverein_sepa_collection_item: payment for payment in payments_for(list(positions.values()))
        }
        self.assertEqual((run.status, run.position_count), ("Abgestimmt", 2))
        for kind, invoice, amount in (("Mitgliedsbeitrag", membership, 350), ("Miete", rent, 500)):
            position = positions[kind]
            payment = payments[position.name]
            references = frappe.get_all(
                "Payment Entry Reference", filters={"parent": payment.name}, fields=["reference_name", "payment_term"]
            )
            invoice.reload()
            self.assertEqual((payment.party, flt(payment.paid_amount)), (customer.name, amount))
            self.assertEqual(
                [(row.reference_name, row.payment_term) for row in references], [(invoice.name, position.payment_term)]
            )
            self.assertEqual((position.sales_invoice, invoice.status), (invoice.name, "Paid"))

    def test_cancelled_payment_sends_the_position_to_review_and_is_not_recreated(self):
        invoice = make_member_invoice(make_member_with_mandate("Settle Cancel", dates=((9, 30, 350),)))
        run = exported_run(f"{NEXT_YEAR}-09-30", invoice.name)
        bank_import = import_credits(credit_row(350, run.collection_date, f"{BATCH_PURPOSE} POSTEN 1"))
        reconcile(bank_import)
        payment = run_positions(run.name)[0].payment_entry

        frappe.get_doc("Payment Entry", payment).cancel()
        reconcile(bank_import)

        run.reload()
        position = run_positions(run.name)[0]
        transaction = transactions_for(bank_import)[0]
        self.assertEqual((position.status, position.payment_entry), ("Manuelle Pruefung", payment))
        self.assertIn(payment, position.processing_error)
        self.assertEqual(run.status, "Manuelle Pruefung")
        self.assertEqual(
            (transaction.status, transaction.erpverein_match_status), ("Unreconciled", "Manuelle Pruefung")
        )
        self.assertEqual(frappe.db.count("Payment Entry", {"erpverein_sepa_collection_item": position.name}), 1)

    def test_import_queues_the_reconciliation_after_commit(self):
        content = v3_file(
            [
                credit_row(
                    12.34, f"{NEXT_YEAR}-02-03", "Spende", remoteIban=make_german_iban(), transactionText="Gutschrift"
                )
            ]
        )
        with patch("frappe.enqueue") as enqueue:
            result = send(content, schema=V3_SCHEMA)

        enqueue.assert_called_once()
        self.assertEqual(
            enqueue.call_args.args[0], "erpverein.services.sepa_settlement_service.run_bank_reconciliation"
        )
        self.assertEqual(
            {key: enqueue.call_args.kwargs[key] for key in ("queue", "job_id", "enqueue_after_commit", "deduplicate")},
            {
                "queue": "long",
                "job_id": f"erpverein-bank-reconcile-{result['bank_import'].lower()}",
                "enqueue_after_commit": True,
                "deduplicate": True,
            },
        )
        self.assertEqual(
            (enqueue.call_args.kwargs["bank_account"], enqueue.call_args.kwargs["bank_import"]),
            (self.bank_account, result["bank_import"]),
        )
        self.assertEqual(result["reconciliation"], "Eingereiht")
        self.assertEqual(
            frappe.db.get_value(IMPORT_DOCTYPE, result["bank_import"], "reconciliation_status"), "Eingereiht"
        )

    def test_api_key_without_treasurer_roles_reports_the_missing_rights(self):
        invoice = make_member_invoice(make_member_with_mandate("Settle API", dates=((8, 31, 350),)))
        run = exported_run(f"{NEXT_YEAR}-08-31", invoice.name)
        api_user = make_user_with_roles("Accounts User")
        treasurer = make_erpverein_manager_user()
        try:
            frappe.set_user(api_user.name)
            bank_import = import_credits(credit_row(350, run.collection_date, f"{BATCH_PURPOSE} POSTEN 1"))
            run_bank_reconciliation(self.bank_account, bank_import)
        finally:
            frappe.set_user("Administrator")

        failed = frappe.get_doc(IMPORT_DOCTYPE, bank_import)
        self.assertEqual(failed.reconciliation_status, "Fehler")
        self.assertIn(api_user.name, failed.reconciliation_message)
        self.assertEqual(payments_for(run_positions(run.name)), [])

        try:
            frappe.set_user(treasurer.name)
            run_bank_reconciliation(self.bank_account, bank_import)
        finally:
            frappe.set_user("Administrator")

        run.reload()
        payments = payments_for(run_positions(run.name))
        self.assertEqual(run.status, "Abgestimmt")
        self.assertEqual([payment.owner for payment in payments], [treasurer.name])
        self.assertEqual(frappe.db.get_value(IMPORT_DOCTYPE, bank_import, "reconciliation_status"), "Abgeschlossen")

    def test_user_without_accounts_role_cannot_reconcile(self):
        invoice = make_member_invoice(make_member_with_mandate("Settle Rights", dates=((7, 30, 350),)))
        run = exported_run(f"{NEXT_YEAR}-07-30", invoice.name)
        bank_import = import_credits(credit_row(350, run.collection_date, f"{BATCH_PURPOSE} POSTEN 1"))
        user = make_user_with_roles("System Manager", "Sales User", "Sales Master Manager")
        try:
            frappe.set_user(user.name)
            with self.assertRaises(frappe.PermissionError):
                start_import_reconciliation(bank_import)
            with self.assertRaises(frappe.PermissionError):
                reconcile_bank_account(self.bank_account)
        finally:
            frappe.set_user("Administrator")

        run.reload()
        self.assertEqual(run.status, "Exportiert")
        self.assertEqual(payments_for(run_positions(run.name)), [])
        self.assertEqual(transactions_for(bank_import)[0].erpverein_match_status, "Offen")


def exported_run(cutoff: str, *invoices: str, batch_booking=None):
    run_name = generate(cutoff, *invoices, batch_booking=batch_booking)["runs"][0]
    check_collection_run(run_name)
    export_collection_run(run_name)
    return frappe.get_doc(RUN_DOCTYPE, run_name)


def credit_row(amount: float, booking_date, purpose: str, **values) -> dict:
    day = formatdate(getdate(booking_date), "dd.MM.yyyy")
    return {
        "dateAsString": day,
        "valutaDateAsString": day,
        "valueAsString": f"{amount:.2f}",
        "purposeInOneLine": f"{purpose} {token()}",
        "transactionText": "SEPA Basislastschrift E.v.",
        **values,
    }


def single_credit_row(position, collection_date) -> dict:
    return credit_row(
        flt(position.amount),
        collection_date,
        "SEPA Lastschrift",
        endToEndReference=position.end_to_end_reference,
        remoteName="Testzahler",
        remoteIban=make_german_iban(),
    )


def import_credits(*rows: dict) -> str:
    with patch("frappe.enqueue"):
        return send(v3_file(list(rows)), schema=V3_SCHEMA)["bank_import"]


def reconcile(bank_import: str) -> None:
    run_bank_reconciliation(frappe.db.get_value(IMPORT_DOCTYPE, bank_import, "bank_account"), bank_import)


def run_positions(run_name: str) -> list:
    return frappe.get_all(
        POSITION_DOCTYPE, filters={"collection_run": run_name}, fields=POSITION_FIELDS, order_by="name asc"
    )


def payments_for(positions: list) -> list:
    if not positions:
        return []
    return frappe.get_all(
        "Payment Entry",
        filters={"erpverein_sepa_collection_item": ["in", [position.name for position in positions]], "docstatus": 1},
        fields=PAYMENT_FIELDS,
        order_by="name asc",
    )


def transactions_for(bank_import: str) -> list:
    return frappe.get_all(
        "Bank Transaction",
        filters={"erpverein_import_batch": bank_import},
        fields=TRANSACTION_FIELDS,
        order_by="erpverein_source_row_number asc",
    )
