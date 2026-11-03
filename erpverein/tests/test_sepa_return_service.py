from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, flt, formatdate, getdate, now_datetime, nowdate

from erpverein.custom_fields import sync_custom_fields
from erpverein.erpverein.report.erpverein_arbeitsvorrat.erpverein_arbeitsvorrat import execute as work_queue
from erpverein.services import sepa_return_service
from erpverein.services.bank_import_service import IMPORT_DOCTYPE
from erpverein.services.sepa_collection_service import POSITION_DOCTYPE, build_collection_preview
from erpverein.services.sepa_return_service import (
    close_settled_runs,
    confirm_return_assignment,
    decide_returned_position,
    validate_journal_entry_provenance,
)
from erpverein.services.sepa_settlement_service import (
    mark_position_done,
    reconcile_bank_account,
    validate_payment_entry_provenance,
)
from erpverein.setup_data import sync_setup_data
from erpverein.tests.collection_fixtures import (
    NEXT_YEAR,
    TEST_COMPANY,
    ensure_export_profile,
    make_company_bank_account,
    make_fee_account,
)
from erpverein.tests.test_bank_import_service import ALIAS, token
from erpverein.tests.test_customer_sync_service import make_erpverein_manager_user
from erpverein.tests.test_payment_schedule_service import make_eur_customer, make_price_list, make_user_with_roles
from erpverein.tests.test_sepa_collection_service import (
    HALF_YEARLY,
    make_member_invoice,
    make_member_with_mandate,
    make_rent_invoice,
    make_tenant_with_mandate,
    rows_for,
)
from erpverein.tests.test_sepa_mandat_doctype import make_german_iban
from erpverein.tests.test_sepa_settlement_service import (
    BATCH_PURPOSE,
    credit_row,
    exported_run,
    import_credits,
    reconcile,
    transactions_for,
)
from erpverein.tests.test_transfer_settlement_service import transfer_row


RETURN_TEXT = "SEPA Lastschrift-Rueckrechn"
REJECT_TEXT = "SEPA Reject"
POSITION_FIELDS = [
    "name",
    "status",
    "billing_kind",
    "sales_invoice",
    "customer",
    "payment_term",
    "amount",
    "collection_date",
    "sepa_mandate",
    "end_to_end_reference",
    "mandate_reference_snapshot",
    "payment_entry",
    "active_key",
    "processing_error",
    "return_bank_transaction",
    "return_journal_entry",
    "return_principal_amount",
    "return_fee_amount",
    "return_reason_code",
    "return_reason_text",
    "return_decision",
    "return_decision_note",
]
ENTRY_FIELDS = [
    "name",
    "docstatus",
    "voucher_type",
    "posting_date",
    "cheque_no",
    "clearance_date",
    "erpverein_sepa_collection_item",
    "erpverein_return_bank_transaction",
    "erpverein_return_principal_amount",
    "erpverein_return_fee_amount",
    "erpverein_return_reason_code",
]
TRANSACTION_FIELDS = [
    "name",
    "date",
    "status",
    "unallocated_amount",
    "erpverein_bank_category",
    "erpverein_match_status",
    "erpverein_match_note",
]


class TestSEPAReturnService(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()
        sync_setup_data()
        make_price_list()
        cls.fee_account = make_fee_account()
        ensure_export_profile(default_fee_account=cls.fee_account)
        cls.bank_account = make_company_bank_account()
        cls.bank_gl_account = frappe.db.get_value("Bank Account", cls.bank_account, "account")
        frappe.db.set_value("Bank Account", cls.bank_account, "erpverein_bank_import_alias", ALIAS)

    def test_return_with_fee_opens_the_invoice_and_keeps_the_payment(self):
        # T15: journal entry, invoice open again, original payment stays, fee on the fee account.
        run, (invoice,) = settled_run("Return Fee", 4, 15)
        position = run_positions(run.name)[0]
        bank_import = import_credits(return_row(position, 362.50, "Sonstige Gruende"))

        reconcile(bank_import)
        reconcile(bank_import)

        transaction = debits_for(bank_import)[0]
        position = run_positions(run.name)[0]
        entries = entries_for(position.name)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(
            (entry.docstatus, entry.voucher_type, str(entry.posting_date), entry.cheque_no),
            (1, "Bank Entry", str(transaction.date), position.end_to_end_reference),
        )
        self.assertEqual(
            (
                entry.erpverein_return_bank_transaction,
                flt(entry.erpverein_return_principal_amount),
                flt(entry.erpverein_return_fee_amount),
                entry.erpverein_return_reason_code,
            ),
            (transaction.name, 350, 12.5, "MS03"),
        )
        self.assertTrue(entry.clearance_date)
        self.assertEqual(
            ledger(entry.name),
            {
                (invoice.debit_to, invoice.customer, invoice.name, 350.0, 0.0),
                (self.fee_account, None, None, 12.5, 0.0),
                (self.bank_gl_account, None, None, 0.0, 362.5),
            },
        )
        self.assertEqual((transaction.status, transaction.erpverein_match_status), ("Reconciled", "Abgeglichen"))
        self.assertEqual(transaction.erpverein_bank_category, "Ruecklastschrift")
        self.assertIn(position.name, transaction.erpverein_match_note)
        self.assertEqual(
            (position.status, position.return_bank_transaction, position.return_journal_entry),
            ("Rueckbelastet", transaction.name, entry.name),
        )
        self.assertEqual(
            (flt(position.return_principal_amount), flt(position.return_fee_amount), position.return_reason_text),
            (350, 12.5, "Sonstige Gruende"),
        )
        # The row stays locked until the treasurer decides.
        self.assertTrue(position.active_key)
        invoice.reload()
        self.assertEqual(flt(invoice.outstanding_amount), 350)
        self.assertEqual(
            (flt(invoice.payment_schedule[0].paid_amount), flt(invoice.payment_schedule[0].outstanding)), (0, 350)
        )
        self.assertEqual(
            frappe.db.get_value(
                "Payment Entry", position.payment_entry, ["docstatus", "erpverein_sepa_collection_item"]
            ),
            (1, position.name),
        )
        run.reload()
        self.assertEqual((run.status, run.position_count, flt(run.total_amount)), ("Teilweise zurueckbelastet", 1, 350))
        self.assertIn(
            "Ruecklastschriften gebucht", frappe.db.get_value(IMPORT_DOCTYPE, bank_import, "reconciliation_message")
        )
        # The original payment stays (E8): cancelling it is refused while the return is booked.
        frappe.db.savepoint("erpverein_return_payment_cancel")
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc("Payment Entry", position.payment_entry).cancel()
        frappe.db.rollback(save_point="erpverein_return_payment_cancel")
        stray = frappe.new_doc("Journal Entry")
        stray.erpverein_sepa_collection_item = position.name
        with self.assertRaises(frappe.ValidationError):
            validate_journal_entry_provenance(stray)
        # Frappe copies read-only fields into an amendment; ERPverein clears them instead of refusing it.
        for doctype, name, validate, fieldname in (
            ("Journal Entry", entry.name, validate_journal_entry_provenance, "erpverein_return_bank_transaction"),
            ("Payment Entry", position.payment_entry, validate_payment_entry_provenance, "erpverein_bank_transaction"),
        ):
            amendment = frappe.copy_doc(frappe.get_doc(doctype, name), ignore_no_copy=True)
            amendment.amended_from = name
            self.assertTrue(amendment.get(fieldname))
            validate(amendment)
            self.assertEqual((amendment.erpverein_sepa_collection_item, amendment.get(fieldname)), (None, None))

    def test_return_with_fees_named_in_the_text(self):
        # T28: principal to the position, both fees from the text on the fee account.
        run, (invoice,) = settled_run("Return Text", 4, 30)
        position = run_positions(run.name)[0]

        reconcile(
            import_credits(return_row(position, 360.67, "Konto aufgeloest Entgelt 3,00 Fremdentgelt 7,67 Beitrag"))
        )

        position = run_positions(run.name)[0]
        entry = entries_for(position.name)[0]
        self.assertEqual(
            ledger(entry.name),
            {
                (invoice.debit_to, invoice.customer, invoice.name, 350.0, 0.0),
                (self.fee_account, None, None, 10.67, 0.0),
                (self.bank_gl_account, None, None, 0.0, 360.67),
            },
        )
        self.assertEqual(
            (position.status, flt(position.return_fee_amount), position.return_reason_code),
            ("Rueckbelastet", 10.67, "AC04"),
        )
        self.assertEqual(flt(frappe.db.get_value("Sales Invoice", invoice.name, "outstanding_amount")), 350)

    def test_return_without_fee_books_principal_only(self):
        run, (invoice,) = settled_run("Return Plain", 5, 14)
        position = run_positions(run.name)[0]

        reconcile(import_credits(return_row(position, 350, "Sonstige Gruende")))

        position = run_positions(run.name)[0]
        entry = entries_for(position.name)[0]
        self.assertEqual(
            ledger(entry.name),
            {
                (invoice.debit_to, invoice.customer, invoice.name, 350.0, 0.0),
                (self.bank_gl_account, None, None, 0.0, 350.0),
            },
        )
        self.assertEqual((position.status, flt(position.return_fee_amount)), ("Rueckbelastet", 0))

    def test_reject_on_the_day_of_the_batch_credit_is_booked_like_a_return(self):
        invoices = [
            make_member_invoice(make_member_with_mandate(f"Reject {index}", dates=((5, 31, 350),))) for index in (1, 2)
        ]
        run = exported_run(f"{NEXT_YEAR}-05-31", *(invoice.name for invoice in invoices))
        first, second = run_positions(run.name)
        bank_import = import_credits(
            credit_row(700, run.collection_date, f"{BATCH_PURPOSE} POSTEN 2"),
            return_row(first, 350, "Deckung fehlt Miete", text=REJECT_TEXT, booking_date=run.collection_date),
        )

        reconcile(bank_import)

        run.reload()
        first, second = run_positions(run.name)
        reject = debits_for(bank_import)[0]
        self.assertEqual(reject.erpverein_bank_category, "Lastschrift-Rueckgabe (Reject)")
        self.assertEqual((reject.status, reject.erpverein_match_status), ("Reconciled", "Abgeglichen"))
        self.assertEqual(
            (first.status, first.return_reason_code, flt(first.return_fee_amount)), ("Rueckbelastet", "AM04", 0)
        )
        self.assertEqual(second.status, "Bezahlt")
        self.assertEqual(run.status, "Teilweise zurueckbelastet")
        outstanding = {invoice.name: flt(invoice.outstanding_amount) for invoice in reload_all(invoices)}
        self.assertEqual(outstanding, {first.sales_invoice: 350, second.sales_invoice: 0})

    def test_ambiguous_return_without_end_to_end_reference_goes_to_review(self):
        first = make_member_with_mandate("Return Twin A", dates=((6, 15, 350),))
        second = make_member_with_mandate("Return Twin B", dates=((6, 15, 350),))
        iban = frappe.db.get_value("SEPA Mandat", mandate_of(first), "iban")
        # A couple with one account: the IBAN cannot tell both collections apart.
        frappe.db.set_value("SEPA Mandat", mandate_of(second), "iban", iban)
        run, invoices = settle(
            exported_run(f"{NEXT_YEAR}-06-15", *(make_member_invoice(m).name for m in (first, second)))
        )
        position = run_positions(run.name)[0]
        bank_import = import_credits(
            return_row(position, 350, "Sonstige Gruende", end_to_end=False, mandate=False, iban=iban)
        )

        reconcile(bank_import)
        reconcile(bank_import)

        transaction = debits_for(bank_import)[0]
        self.assertEqual(
            (transaction.status, transaction.erpverein_match_status), ("Unreconciled", "Manuelle Pruefung")
        )
        self.assertIn("Mehrere Einzugspositionen passen", transaction.erpverein_match_note)
        for row in run_positions(run.name):
            self.assertIn(row.name, transaction.erpverein_match_note)
            self.assertEqual((row.status, entries_for(row.name)), ("Bezahlt", []))

    def test_smaller_debit_and_contradicting_fee_text_go_to_review(self):
        run, invoices = settled_run("Return Check", 6, 30, count=2)
        first, second = run_positions(run.name)
        bank_import = import_credits(
            return_row(first, 300, "Sonstige Gruende"),
            return_row(second, 365, "Sonstige Gruende Entgelt 3,00 Fremdentgelt 7,67"),
        )

        reconcile(bank_import)
        reconcile(bank_import)

        smaller, contradiction = debits_for(bank_import)
        self.assertEqual(smaller.erpverein_match_status, "Manuelle Pruefung")
        self.assertIn("kleiner als der Betrag 350,00 EUR", smaller.erpverein_match_note)
        self.assertEqual(contradiction.erpverein_match_status, "Manuelle Pruefung")
        self.assertIn("Widerspruch bei der Gebuehr", contradiction.erpverein_match_note)
        for row in run_positions(run.name):
            self.assertEqual((row.status, entries_for(row.name)), ("Bezahlt", []))
        self.assertEqual({flt(invoice.outstanding_amount) for invoice in reload_all(invoices)}, {0})

        # The treasurer assigns the contradicting return: ERPverein books it, the debit is the fee base.
        with self.assertRaises(frappe.ValidationError):
            confirm_return_assignment(smaller.name, first.name)
        result = confirm_return_assignment(contradiction.name, second.name, "Gebuehr mit der Bank geklaert")

        second_row = next(row for row in run_positions(run.name) if row.name == second.name)
        transaction = next(row for row in debits_for(bank_import) if row.name == contradiction.name)
        self.assertEqual((second_row.status, flt(second_row.return_fee_amount)), ("Rueckbelastet", 15))
        self.assertEqual(
            (result["journal_entry"], transaction.erpverein_match_status),
            (second_row.return_journal_entry, "Abgeglichen"),
        )
        self.assertIn("bestaetigt von Administrator", transaction.erpverein_match_note)
        self.assertEqual(next(row for row in run_positions(run.name) if row.name == first.name).status, "Bezahlt")

    def test_restart_books_one_journal_entry_after_a_failed_job(self):
        run, (invoice,) = settled_run("Return Restart", 7, 15)
        position = run_positions(run.name)[0]
        bank_import = import_credits(return_row(position, 352.50, "Sonstige Gruende"))
        build = sepa_return_service.build_return_entry
        calls = []

        def fail_once(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                frappe.throw("Testfehler Buchungsperiode")
            return build(*args, **kwargs)

        with patch.object(sepa_return_service, "build_return_entry", side_effect=fail_once):
            reconcile(bank_import)
            failed = debits_for(bank_import)[0]
            self.assertEqual((failed.erpverein_match_status, entries_for(position.name)), ("Offen", []))
            self.assertIn("Testfehler", failed.erpverein_match_note)
            self.assertEqual(run_positions(run.name)[0].status, "Bezahlt")
            self.assertEqual(flt(frappe.db.get_value("Sales Invoice", invoice.name, "outstanding_amount")), 0)
            reconcile(bank_import)
            reconcile(bank_import)

        transaction = debits_for(bank_import)[0]
        self.assertEqual(transaction.erpverein_match_status, "Abgeglichen")
        self.assertEqual(frappe.db.count("Journal Entry", {"erpverein_sepa_collection_item": position.name}), 1)
        self.assertEqual(run_positions(run.name)[0].status, "Rueckbelastet")
        invoice.reload()
        self.assertEqual((flt(invoice.outstanding_amount), flt(invoice.payment_schedule[0].outstanding)), (350, 350))

    def test_cancelled_journal_entry_sends_position_and_debit_to_review(self):
        run, (invoice,) = settled_run("Return Cancel", 7, 31)
        position = run_positions(run.name)[0]
        bank_import = import_credits(return_row(position, 355, "Sonstige Gruende"))
        reconcile(bank_import)
        entry = run_positions(run.name)[0].return_journal_entry

        frappe.get_doc("Journal Entry", entry).cancel()
        reconcile(bank_import)

        run.reload()
        position = run_positions(run.name)[0]
        transaction = debits_for(bank_import)[0]
        self.assertEqual((position.status, position.return_journal_entry), ("Manuelle Pruefung", entry))
        self.assertIn(entry, position.processing_error)
        self.assertEqual(
            (transaction.status, transaction.erpverein_match_status), ("Unreconciled", "Manuelle Pruefung")
        )
        self.assertIn("storniert", transaction.erpverein_match_note)
        self.assertEqual(run.status, "Manuelle Pruefung")
        self.assertEqual(frappe.db.count("Journal Entry", {"erpverein_sepa_collection_item": position.name}), 1)
        invoice.reload()
        self.assertEqual((flt(invoice.outstanding_amount), flt(invoice.payment_schedule[0].paid_amount)), (0, 350))

        mark_position_done(position.name, "Ruecklastschrift im Banking-Bereich von Hand gebucht")
        run.reload()
        self.assertEqual(run.status, "Abgestimmt")
        # Cancel and amend stays possible; the amendment is the treasurer's own journal entry.
        amendment = frappe.copy_doc(frappe.get_doc("Journal Entry", entry), ignore_no_copy=True)
        amendment.update({"amended_from": entry, "docstatus": 0})
        amendment.insert()
        self.assertEqual(
            (
                amendment.docstatus,
                amendment.erpverein_sepa_collection_item,
                amendment.erpverein_return_bank_transaction,
            ),
            (0, None, None),
        )

    def test_missing_fee_account_is_retried_after_the_fix(self):
        run, (invoice,) = settled_run("Return Setup", 2, 15)
        position = run_positions(run.name)[0]
        bank_import = import_credits(return_row(position, 359.50, "Sonstige Gruende"))
        try:
            ensure_export_profile(default_fee_account=None)
            reconcile(bank_import)
            waiting = debits_for(bank_import)[0]
            self.assertEqual((waiting.erpverein_match_status, entries_for(position.name)), ("Offen", []))
            self.assertIn("Gebuehrenkonto", waiting.erpverein_match_note)
        finally:
            ensure_export_profile(default_fee_account=self.fee_account)

        reconcile(bank_import)

        self.assertEqual(debits_for(bank_import)[0].erpverein_match_status, "Abgeglichen")
        self.assertEqual(flt(entries_for(position.name)[0].erpverein_return_fee_amount), 9.5)

    def test_transfer_decision_works_after_the_mandate_was_revoked(self):
        mitglied = make_member_with_mandate("Return Revoked", dates=HALF_YEARLY)
        invoice = make_member_invoice(mitglied)
        run, _invoices = settle(exported_run(f"{NEXT_YEAR}-03-31", invoice.name))
        position = run_positions(run.name)[0]
        reconcile(import_credits(return_row(position, 175, "Konto aufgeloest")))
        # Account closed: the treasurer revokes the mandate before deciding.
        frappe.db.set_value("SEPA Mandat", mandate_of(mitglied), "status", "Widerrufen")

        decide_returned_position(position.name, "Ueberweisung", note="Konto aufgeloest")

        invoice.reload()
        modes = {str(row.due_date)[5:]: row.mode_of_payment for row in invoice.payment_schedule}
        self.assertEqual(modes, {"03-31": "Überweisung", "09-30": "SEPA-Lastschrift"})
        self.assertEqual(invoice.erpverein_sepa_mandate, mandate_of(mitglied))
        self.assertEqual(run_positions(run.name)[0].return_decision, "Ueberweisung")

    def test_shared_customer_only_the_rent_comes_back(self):
        customer = make_eur_customer("Return Shared")
        membership = make_member_invoice(
            make_member_with_mandate("Return Shared", dates=((3, 1, 350),), customer=customer)
        )
        rent = make_rent_invoice(make_tenant_with_mandate(customer))
        run, _invoices = settle(exported_run(f"{NEXT_YEAR}-03-01", membership.name, rent.name))
        positions = {row.billing_kind: row for row in run_positions(run.name)}

        reconcile(import_credits(return_row(positions["Miete"], 508.50, "Sonstige Gruende")))

        positions = {row.billing_kind: row for row in run_positions(run.name)}
        entry = entries_for(positions["Miete"].name)[0]
        self.assertEqual(
            ledger(entry.name),
            {
                (rent.debit_to, customer.name, rent.name, 500.0, 0.0),
                (self.fee_account, None, None, 8.5, 0.0),
                (self.bank_gl_account, None, None, 0.0, 508.5),
            },
        )
        self.assertEqual(
            (positions["Miete"].status, positions["Mitgliedsbeitrag"].status), ("Rueckbelastet", "Bezahlt")
        )
        self.assertEqual(entries_for(positions["Mitgliedsbeitrag"].name), [])
        membership.reload()
        rent.reload()
        self.assertEqual((membership.status, flt(membership.outstanding_amount)), ("Paid", 0))
        self.assertEqual(flt(rent.outstanding_amount), 500)

    def test_decision_to_collect_again_needs_a_new_due_date_and_pre_notification(self):
        run, (invoice,) = settled_run("Return Again", 8, 16)
        position = run_positions(run.name)[0]
        reconcile(import_credits(return_row(position, 350, "Deckung fehlt")))
        due_date = f"{NEXT_YEAR}-11-16"

        # Without a decision the row stays in the returned position: no automatic new collection.
        locked = rows_for(build_collection_preview(TEST_COMPANY, due_date), invoice.name)
        self.assertEqual([(row["status"], row["position"]) for row in locked], [("Vorhanden", position.name)])
        with self.assertRaises(frappe.ValidationError):
            decide_returned_position(position.name, "Erneut einziehen")
        with self.assertRaises(frappe.ValidationError):
            decide_returned_position(position.name, "Erneut einziehen", due_date=add_days(nowdate(), 3))

        result = decide_returned_position(
            position.name, "Erneut einziehen", due_date=due_date, note="Zahler bittet um neuen Einzug"
        )

        self.assertEqual(result["prenotification_status"], "Erneut senden")
        position = run_positions(run.name)[0]
        self.assertEqual((position.return_decision, position.active_key), ("Erneut einziehen", None))
        self.assertIn(formatdate(due_date, "dd.MM.yyyy"), position.return_decision_note)
        invoice.reload()
        row = invoice.payment_schedule[0]
        self.assertEqual(
            (str(row.due_date), row.mode_of_payment, flt(row.outstanding)), (due_date, "SEPA-Lastschrift", 350)
        )
        run.reload()
        self.assertEqual(run.status, "Teilweise zurueckbelastet")
        waiting = rows_for(build_collection_preview(TEST_COMPANY, due_date), invoice.name)
        self.assertEqual([row["status"] for row in waiting], ["Konflikt"])
        self.assertIn("Vorabankuendigung", waiting[0]["reason"])

        # The treasurer sends the invoice again; it is the new pre-notification.
        frappe.db.set_value(
            "Sales Invoice",
            invoice.name,
            {
                "erpverein_prenotification_status": "Versendet",
                "erpverein_prenotification_sent_at": add_to_date(now_datetime(), seconds=2),
            },
            update_modified=False,
        )
        new_run = exported_run(due_date, invoice.name)
        new_position = run_positions(new_run.name)[0]
        self.assertNotEqual(new_position.end_to_end_reference, position.end_to_end_reference)
        self.assertEqual((flt(new_position.amount), str(new_position.collection_date) >= due_date), (350, True))
        with self.assertRaises(frappe.ValidationError):
            decide_returned_position(position.name, "Ueberweisung")

    def test_decision_for_a_transfer_books_the_waiting_transfer(self):
        run, (invoice,) = settled_run("Return Transfer", 8, 31)
        position = run_positions(run.name)[0]
        reconcile(import_credits(return_row(position, 350, "Sonstige Gruende")))
        transfer_import = import_credits(
            transfer_row(350, f"Beitrag {invoice.name}", booking_date=add_days(run.collection_date, 6))
        )

        reconcile(transfer_import)

        transfer = transactions_for(transfer_import)[0]
        self.assertEqual(transfer.erpverein_match_status, "Offen")
        self.assertIn(position.name, transfer.erpverein_match_note)

        decide_returned_position(position.name, "Ueberweisung", note="Zahler ueberweist selbst")

        position = run_positions(run.name)[0]
        self.assertEqual((position.return_decision, position.active_key), ("Ueberweisung", None))
        invoice.reload()
        self.assertEqual(invoice.payment_schedule[0].mode_of_payment, "Überweisung")
        self.assertEqual(invoice.erpverein_prenotification_status, "Nicht erforderlich")
        # After the decision the return is final: the journal entry cannot be cancelled any more.
        frappe.db.savepoint("erpverein_return_entry_cancel")
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc("Journal Entry", position.return_journal_entry).cancel()
        frappe.db.rollback(save_point="erpverein_return_entry_cancel")

        reconcile(transfer_import)

        transfer = transactions_for(transfer_import)[0]
        self.assertEqual(transfer.erpverein_match_status, "Abgeglichen")
        invoice.reload()
        self.assertEqual((invoice.status, flt(invoice.outstanding_amount)), ("Paid", 0))
        payment = frappe.get_all(
            "Payment Entry",
            filters={"erpverein_bank_transaction": transfer.name, "docstatus": 1},
            fields=["name", "mode_of_payment"],
        )
        self.assertEqual([row.mode_of_payment for row in payment], ["Überweisung"])

    def test_work_queue_and_rights_for_returns(self):
        run, (invoice,) = settled_run("Return Queue", 9, 15)
        position = run_positions(run.name)[0]
        reconcile(import_credits(return_row(position, 350, "Sonstige Gruende")))
        unknown_import = import_credits(
            return_row(position, 99, "Sonstige Gruende", end_to_end=False, mandate=False, iban=make_german_iban())
        )
        reconcile(unknown_import)
        unknown = debits_for(unknown_import)[0]
        position = run_positions(run.name)[0]

        cases = queue_cases()

        returned = cases[(POSITION_DOCTYPE, position.name)]
        self.assertEqual((returned["area"], returned["status"]), ("Einzugsposition", "Rueckbelastet"))
        self.assertIn(position.return_journal_entry, returned["reason"])
        self.assertIn(position.return_bank_transaction, returned["reason"])
        self.assertIn("Sonstige Gruende", returned["reason"])
        self.assertIn("Erneut einziehen", returned["next_action"])
        review = cases[("Bank Transaction", unknown.name)]
        self.assertEqual((review["area"], review["status"]), ("Bankumsatz", "Manuelle Pruefung"))
        self.assertIn("Keine Einzugsposition passt", review["reason"])
        self.assertIn("Gegenbuchung", review["next_action"])

        no_accounts = make_user_with_roles("System Manager", "Sales User", "Sales Master Manager")
        accounts_only = make_user_with_roles("Accounts User")
        for user in (no_accounts, accounts_only):
            try:
                frappe.set_user(user.name)
                with self.assertRaises(frappe.PermissionError):
                    decide_returned_position(position.name, "Ueberweisung")
                with self.assertRaises(frappe.PermissionError):
                    reconcile_bank_account(self.bank_account)
            finally:
                frappe.set_user("Administrator")
        try:
            frappe.set_user(no_accounts.name)
            with self.assertRaises(frappe.PermissionError):
                work_queue({"company": TEST_COMPANY})
            frappe.set_user(accounts_only.name)
            _columns, rows, message = work_queue({"company": TEST_COMPANY, "area": "Einzugsposition"})
            self.assertEqual(rows, [])
            self.assertIn("Einzugsposition", message)
        finally:
            frappe.set_user("Administrator")
        self.assertEqual(run_positions(run.name)[0].return_decision, None)

        treasurer = make_erpverein_manager_user()
        try:
            frappe.set_user(treasurer.name)
            decide_returned_position(position.name, "Ueberweisung")
        finally:
            frappe.set_user("Administrator")
        self.assertNotIn((POSITION_DOCTYPE, position.name), queue_cases())

    def test_run_is_closed_eight_weeks_after_the_collection_date(self):
        returned_run, _invoices = settled_run("Return Close A", 10, 15)
        plain_run, _invoices = settled_run("Return Close B", 10, 29)
        position = run_positions(returned_run.name)[0]
        reconcile(import_credits(return_row(position, 350, "Sonstige Gruende")))
        later = add_days(plain_run.collection_date, 60)

        close_settled_runs(today=later)

        returned_run.reload()
        plain_run.reload()
        self.assertEqual((plain_run.status, bool(plain_run.closed_at)), ("Abgeschlossen", True))
        self.assertEqual((returned_run.status, returned_run.closed_at), ("Teilweise zurueckbelastet", None))

        decide_returned_position(position.name, "Ueberweisung")
        close_settled_runs(today=later)

        returned_run.reload()
        self.assertEqual((returned_run.status, bool(returned_run.closed_at)), ("Abgeschlossen", True))
        self.assertEqual(returned_run.position_count, 1)


def settled_run(label: str, month: int, day: int, count: int = 1, customer=None) -> tuple:
    invoices = [
        make_member_invoice(make_member_with_mandate(f"{label} {index}", dates=((month, day, 350),), customer=customer))
        for index in range(1, count + 1)
    ]
    return settle(exported_run(f"{NEXT_YEAR}-{month:02d}-{day:02d}", *(invoice.name for invoice in invoices)))


def settle(run) -> tuple:
    positions = run_positions(run.name)
    total = sum(flt(position.amount) for position in positions)
    reconcile(import_credits(credit_row(total, run.collection_date, f"{BATCH_PURPOSE} POSTEN {len(positions)}")))
    run.reload()
    invoices = [frappe.get_doc("Sales Invoice", position.sales_invoice) for position in positions]
    return run, invoices


def return_row(
    position,
    amount: float,
    purpose: str,
    *,
    text: str = RETURN_TEXT,
    end_to_end: bool = True,
    mandate: bool = True,
    iban: str | None = None,
    booking_date=None,
) -> dict:
    day = formatdate(getdate(booking_date or add_days(position.collection_date, 2)), "dd.MM.yyyy")
    return {
        "dateAsString": day,
        "valutaDateAsString": day,
        "valueAsString": f"-{amount:.2f}",
        "purposeInOneLine": f"{purpose} {token()}",
        "transactionText": text,
        "endToEndReference": position.end_to_end_reference if end_to_end else "",
        "mandateId": position.mandate_reference_snapshot if mandate else "",
        "remoteName": "Testzahler",
        "remoteIban": iban or frappe.db.get_value("SEPA Mandat", position.sepa_mandate, "iban"),
    }


def mandate_of(mitglied) -> str:
    return frappe.db.get_value("SEPA Mandat", {"bezugs_doctype": "Mitglied", "bezugs_name": mitglied.name}, "name")


def run_positions(run_name: str) -> list:
    return frappe.get_all(
        POSITION_DOCTYPE, filters={"collection_run": run_name}, fields=POSITION_FIELDS, order_by="name asc"
    )


def entries_for(position_name: str) -> list:
    return frappe.get_all(
        "Journal Entry",
        filters={"erpverein_sepa_collection_item": position_name, "docstatus": 1},
        fields=ENTRY_FIELDS,
        order_by="name asc",
    )


def ledger(entry_name: str) -> set:
    return {
        (row.account, row.party or None, row.against_voucher or None, flt(row.debit), flt(row.credit))
        for row in frappe.get_all(
            "GL Entry",
            filters={"voucher_type": "Journal Entry", "voucher_no": entry_name, "is_cancelled": 0},
            fields=["account", "party", "against_voucher", "debit", "credit"],
        )
    }


def debits_for(bank_import: str) -> list:
    return frappe.get_all(
        "Bank Transaction",
        filters={"erpverein_import_batch": bank_import, "withdrawal": [">", 0]},
        fields=TRANSACTION_FIELDS,
        order_by="erpverein_source_row_number asc",
    )


def reload_all(invoices: list) -> list:
    for invoice in invoices:
        invoice.reload()
    return invoices


def queue_cases() -> dict:
    _columns, rows, _message = work_queue({"company": TEST_COMPANY, "days_ahead": 0})
    return {(row["reference_doctype"], row["reference_name"]): row for row in rows}
