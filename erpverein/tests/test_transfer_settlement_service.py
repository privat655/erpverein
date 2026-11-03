import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint, flt, formatdate, getdate

from erpverein.custom_fields import sync_custom_fields
from erpverein.erpverein.report.erpverein_arbeitsvorrat.erpverein_arbeitsvorrat import execute as work_queue
from erpverein.services import transfer_settlement_service
from erpverein.services.bank_import_service import IMPORT_DOCTYPE
from erpverein.services.sepa_collection_service import POSITION_DOCTYPE
from erpverein.services.sepa_settlement_service import (
    mark_position_done,
    reconcile_bank_account,
    validate_payment_entry_provenance,
)
from erpverein.services.transfer_settlement_service import (
    confirm_transfer_proposal,
    get_transfer_proposal,
    reject_transfer_proposal,
    transfer_key,
)
from erpverein.setup_data import sync_setup_data
from erpverein.tests.collection_fixtures import (
    NEXT_YEAR,
    TEST_COMPANY,
    ensure_export_profile,
    make_company_bank_account,
)
from erpverein.tests.test_bank_import_service import ALIAS, V3_SCHEMA, send, token, v3_file
from erpverein.tests.test_customer_sync_service import make_erpverein_manager_user
from erpverein.tests.test_payment_schedule_service import (
    make_eur_customer,
    make_managed_subscription,
    make_plan,
    make_price_list,
    make_user_with_roles,
)
from erpverein.tests.test_sepa_collection_service import (
    MARCH_CUTOFF,
    generate,
    make_member_invoice,
    make_member_with_mandate,
)
from erpverein.tests.test_sepa_mandat_doctype import (
    make_bank,
    make_bank_account,
    make_german_iban,
    make_mieter,
    make_mitglied,
)
from erpverein.tests.test_sepa_settlement_service import (
    BATCH_PURPOSE,
    credit_row,
    exported_run,
    import_credits,
    reconcile,
    run_positions,
    transactions_for,
)


BOOKING_DATE = f"{NEXT_YEAR}-02-03"
PAYMENT_FIELDS = [
    "name",
    "party",
    "paid_amount",
    "posting_date",
    "mode_of_payment",
    "reference_no",
    "clearance_date",
    "erpverein_bank_transaction",
    "erpverein_transfer_key",
]


class TestTransferSettlementService(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()
        sync_setup_data()
        make_price_list()
        ensure_export_profile()
        cls.bank_account = make_company_bank_account()
        frappe.db.set_value("Bank Account", cls.bank_account, "erpverein_bank_import_alias", ALIAS)

    def test_invoice_number_pays_and_reconciles_the_invoice(self):
        # T13: a self-payer names the invoice number; ERPverein books, reconciles and books nothing twice.
        invoice = make_self_payer_invoice("Transfer Number")
        bank_import = import_credits(transfer_row(350, f"Beitrag {invoice.name}"))

        reconcile(bank_import)
        reconcile(bank_import)

        transaction = transactions_for(bank_import)[0]
        payments = payments_for(transaction.name)
        self.assertEqual(len(payments), 1)
        payment = payments[0]
        self.assertEqual(
            (payment.party, flt(payment.paid_amount), str(payment.posting_date), payment.mode_of_payment),
            (invoice.customer, 350, BOOKING_DATE, "Überweisung"),
        )
        self.assertEqual(payment.erpverein_transfer_key, transfer_key(transaction.name, invoice.customer))
        self.assertTrue(payment.clearance_date)
        self.assertEqual(references(payment.name), [(invoice.name, invoice.payment_schedule[0].payment_term, 350)])
        self.assertEqual((transaction.status, transaction.erpverein_match_status), ("Reconciled", "Abgeglichen"))
        self.assertIn(invoice.name, transaction.erpverein_match_note)
        self.assertIn("Rechnungsnummer", transaction.erpverein_match_note)
        invoice.reload()
        self.assertEqual((invoice.status, flt(invoice.outstanding_amount)), ("Paid", 0))
        self.assertEqual(
            (flt(invoice.payment_schedule[0].paid_amount), flt(invoice.payment_schedule[0].outstanding)), (350, 0)
        )
        self.assertEqual(frappe.db.get_value(IMPORT_DOCTYPE, bank_import, "reconciliation_status"), "Abgeschlossen")
        stray = frappe.new_doc("Payment Entry")
        stray.erpverein_transfer_key = payment.erpverein_transfer_key
        with self.assertRaises(frappe.ValidationError):
            validate_payment_entry_provenance(stray)

    def test_name_and_amount_only_create_a_proposal(self):
        # T14: name and amount alone never book; the treasurer confirms or rejects the proposal.
        invoice = make_self_payer_invoice("Transfer Proposal")
        other = make_self_payer_invoice("Transfer Rejected")
        bank_import = import_credits(
            transfer_row(350, "Mitgliedsbeitrag", name=invoice.customer_name),
            transfer_row(350, "Mitgliedsbeitrag", name=other.customer_name),
        )

        reconcile(bank_import)

        proposal, rejected = transactions_for(bank_import)
        self.assertEqual(payments_for(proposal.name), [])
        self.assertEqual((proposal.status, proposal.erpverein_match_status), ("Unreconciled", "Vorschlag"))
        for part in (invoice.customer, invoice.name, "350,00 EUR", "Name der Gegenseite", "Name und Betrag allein"):
            self.assertIn(part, proposal.erpverein_match_note)

        shown = get_transfer_proposal(proposal.name)
        self.assertTrue(shown["bookable"])
        self.assertEqual([(row["invoice"], row["amount"]) for row in shown["allocations"]], [(invoice.name, 350)])
        with self.assertRaises(frappe.ValidationError):
            confirm_transfer_proposal(proposal.name, "veraltet")
        result = confirm_transfer_proposal(proposal.name, shown["proposal_hash"])
        reject_transfer_proposal(rejected.name, "Zahlung gehoert zu einem Spendenaufruf")
        reconcile(bank_import)

        proposal, rejected = transactions_for(bank_import)
        self.assertEqual(result["match_status"], "Abgeglichen")
        self.assertEqual([payment.name for payment in payments_for(proposal.name)], result["payments"])
        self.assertEqual((proposal.status, proposal.erpverein_match_status), ("Reconciled", "Abgeglichen"))
        self.assertIn("Vorschlag bestaetigt", proposal.erpverein_match_note)
        self.assertEqual((rejected.erpverein_match_status, payments_for(rejected.name)), ("Manuelle Pruefung", []))
        self.assertIn("Spendenaufruf", rejected.erpverein_match_note)
        with self.assertRaises(frappe.ValidationError):
            get_transfer_proposal(rejected.name)

    def test_partial_payment_pays_the_transfer_row_and_never_part_of_a_direct_debit_row(self):
        # Mandate 300 for an invoice of 350: one direct debit row and a transfer row with the rest (T4).
        invoice = make_member_invoice(make_member_with_mandate("Transfer Rest", dates=((3, 31, 300),)))
        transfer_term = next(
            row.payment_term for row in invoice.payment_schedule if row.mode_of_payment == "Überweisung"
        )
        self_payer = make_self_payer_invoice("Transfer Partial")
        bank_import = import_credits(
            transfer_row(50, f"Rest {invoice.name}"),
            transfer_row(100, f"Rate {self_payer.name}"),
        )

        reconcile(bank_import)
        later = import_credits(transfer_row(100, f"Beitrag {invoice.name}", booking_date=f"{NEXT_YEAR}-02-04"))
        reconcile(later)

        rest, partial = transactions_for(bank_import)
        debit_row = transactions_for(later)[0]
        self.assertEqual(references(payments_for(rest.name)[0].name), [(invoice.name, transfer_term, 50)])
        self.assertEqual(references(payments_for(partial.name)[0].name), [(self_payer.name, "ERPverein Rate 01", 100)])
        self.assertEqual((debit_row.erpverein_match_status, payments_for(debit_row.name)), ("Manuelle Pruefung", []))
        self.assertIn("Lastschriftzeile nur teilweise", debit_row.erpverein_match_note)
        invoice.reload()
        self_payer.reload()
        self.assertEqual(
            {row.mode_of_payment: flt(row.outstanding) for row in invoice.payment_schedule},
            {"SEPA-Lastschrift": 300, "Überweisung": 0},
        )
        self.assertEqual((self_payer.status, flt(self_payer.outstanding_amount)), ("Partly Paid", 250))

    def test_several_invoices_of_one_customer_get_one_payment(self):
        customer = make_eur_customer("Transfer Several")
        membership = make_self_payer_invoice("Transfer Several", customer=customer)
        rent = make_tenant_invoice(customer, 500)
        bank_import = import_credits(transfer_row(850, f"{membership.name} und {rent.name}"))

        reconcile(bank_import)

        transaction = transactions_for(bank_import)[0]
        payments = payments_for(transaction.name)
        self.assertEqual(len(payments), 1)
        self.assertEqual(
            sorted(references(payments[0].name)),
            sorted([(membership.name, "ERPverein Rate 01", 350), (rent.name, "ERPverein Rate 01", 500)]),
        )
        self.assertEqual((transaction.status, transaction.erpverein_match_status), ("Reconciled", "Abgeglichen"))

    def test_third_person_pays_and_the_invoice_customer_stays_the_party(self):
        invoice = make_self_payer_invoice("Transfer Child")
        parent = make_eur_customer("Transfer Parent")
        iban = make_german_iban()
        make_bank_account(parent, make_bank(), iban, managed=0)
        bank_import = import_credits(transfer_row(350, f"Fuer {invoice.name}", iban=iban, name="Eltern Zahler"))

        reconcile(bank_import)

        transaction = transactions_for(bank_import)[0]
        payment = payments_for(transaction.name)[0]
        self.assertEqual(payment.party, invoice.customer)
        self.assertEqual(transaction.erpverein_match_status, "Abgeglichen")
        self.assertIn("Partei der Zahlung bleibt der Kunde der Rechnung", transaction.erpverein_match_note)

    def test_shared_customer_keeps_membership_fee_and_rent_apart(self):
        customer = make_eur_customer("Transfer Shared")
        membership = make_self_payer_invoice("Transfer Shared", customer=customer)
        rent = make_tenant_invoice(customer, 350)
        iban = make_german_iban()
        make_bank_account(customer, make_bank(), iban, managed=0)
        bank_import = import_credits(
            transfer_row(350, "Danke", iban=iban),
            transfer_row(350, f"Miete {rent.name}", iban=iban),
        )

        reconcile(bank_import)

        ambiguous, named = transactions_for(bank_import)
        self.assertEqual((ambiguous.erpverein_match_status, payments_for(ambiguous.name)), ("Manuelle Pruefung", []))
        self.assertIn("Mitgliedsbeitrag und Miete", ambiguous.erpverein_match_note)
        self.assertEqual(references(payments_for(named.name)[0].name), [(rent.name, "ERPverein Rate 01", 350)])
        membership.reload()
        self.assertEqual(flt(membership.outstanding_amount), 350)

    def test_invoice_with_an_active_collection_position_is_not_booked(self):
        invoice = make_member_invoice(make_member_with_mandate("Transfer Position", dates=((3, 31, 350),)))
        generate(MARCH_CUTOFF, invoice.name)
        position = frappe.get_all(POSITION_DOCTYPE, filters={"sales_invoice": invoice.name}, pluck="name")[0]
        bank_import = import_credits(transfer_row(350, f"Beitrag {invoice.name}"))

        reconcile(bank_import)

        transaction = transactions_for(bank_import)[0]
        self.assertEqual(
            (transaction.erpverein_match_status, payments_for(transaction.name)), ("Manuelle Pruefung", [])
        )
        self.assertIn(position, transaction.erpverein_match_note)
        self.assertIn("doppelter Einzug", transaction.erpverein_match_note)

    def test_overpayment_books_the_invoice_and_leaves_the_rest_open(self):
        invoice = make_self_payer_invoice("Transfer Overpaid")
        bank_import = import_credits(transfer_row(400, f"Beitrag {invoice.name} und Spende"))

        reconcile(bank_import)
        reconcile(bank_import)

        transaction = transactions_for(bank_import)[0]
        payments = payments_for(transaction.name)
        self.assertEqual([flt(payment.paid_amount) for payment in payments], [350])
        self.assertEqual(
            (transaction.status, flt(transaction.unallocated_amount), transaction.erpverein_match_status),
            ("Unreconciled", 50, "Manuelle Pruefung"),
        )
        self.assertIn("Ueberzahlung: 50,00 EUR", transaction.erpverein_match_note)
        invoice.reload()
        self.assertEqual(invoice.status, "Paid")

    def test_historic_invoice_needs_the_invoice_number(self):
        customer = make_eur_customer("Transfer Historic")
        old = make_self_payer_invoice("Transfer Historic", customer=customer, start_date=f"{NEXT_YEAR - 1}-01-01")
        iban = make_german_iban()
        make_bank_account(customer, make_bank(), iban, managed=0)
        by_iban = import_credits(transfer_row(350, "Beitrag", iban=iban))

        reconcile(by_iban)

        suggestion = transactions_for(by_iban)[0]
        self.assertEqual((suggestion.erpverein_match_status, payments_for(suggestion.name)), ("Vorschlag", []))
        self.assertIn("vor dem Go-live-Stichtag", suggestion.erpverein_match_note)

        by_number = import_credits(
            transfer_row(350, f"Altschuld {old.name}", iban=iban, booking_date=f"{NEXT_YEAR}-02-05")
        )
        reconcile(by_number)

        self.assertEqual(transactions_for(by_number)[0].erpverein_match_status, "Abgeglichen")
        self.assertEqual(references(payments_for(transactions_for(by_number)[0].name)[0].name)[0][0], old.name)

    def test_restart_and_cancellation_never_create_a_second_payment(self):
        invoice = make_self_payer_invoice("Transfer Restart")
        bank_import = import_credits(transfer_row(350, f"Beitrag {invoice.name}"))
        build = transfer_settlement_service.build_transfer_payment
        calls = []

        def fail_once(*args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                frappe.throw("Testfehler Buchungsperiode")
            return build(*args, **kwargs)

        with patch.object(transfer_settlement_service, "build_transfer_payment", side_effect=fail_once):
            reconcile(bank_import)
            failed = transactions_for(bank_import)[0]
            self.assertEqual((failed.erpverein_match_status, payments_for(failed.name)), ("Offen", []))
            self.assertIn("Testfehler", failed.erpverein_match_note)
            self.assertIn(", 1 Fehler", frappe.db.get_value(IMPORT_DOCTYPE, bank_import, "reconciliation_message"))
            reconcile(bank_import)
            reconcile(bank_import)

        transaction = transactions_for(bank_import)[0]
        key = transfer_key(transaction.name, invoice.customer)
        self.assertEqual(transaction.erpverein_match_status, "Abgeglichen")
        self.assertEqual(frappe.db.count("Payment Entry", {"erpverein_transfer_key": key}), 1)

        frappe.get_doc("Payment Entry", payments_for(transaction.name)[0].name).cancel()
        reconcile(bank_import)

        transaction = transactions_for(bank_import)[0]
        self.assertEqual(
            (transaction.status, transaction.erpverein_match_status), ("Unreconciled", "Manuelle Pruefung")
        )
        self.assertIn("storniert", transaction.erpverein_match_note)
        self.assertEqual(frappe.db.count("Payment Entry", {"erpverein_transfer_key": key}), 1)
        self.assertEqual(payments_for(transaction.name), [])

    def test_work_queue_shows_every_open_case_with_reason_link_and_masked_iban(self):
        iban = make_german_iban()
        invoice = make_self_payer_invoice("Queue Proposal")
        bank_import = import_credits(transfer_row(350, "Beitrag", name=invoice.customer_name, iban=iban))
        with patch("frappe.enqueue"):
            broken = send(
                v3_file([transfer_row(10, "Gut"), {**transfer_row(20, "Kaputt"), "valueAsString": "zwanzig"}]),
                schema=V3_SCHEMA,
            )["bank_import"]
        conflict = make_member_invoice(make_member_with_mandate("Queue Conflict", dates=((3, 31, 400),)))
        unnotified = make_member_invoice(make_member_with_mandate("Queue Notice"), notified=False)
        reconcile(bank_import)
        proposal = transactions_for(bank_import)[0]

        columns, rows, message = work_queue({"company": TEST_COMPANY, "days_ahead": 500})
        cases = {(row["reference_doctype"], row["reference_name"]): row for row in rows}

        self.assertIn("reason", {column["fieldname"] for column in columns})
        self.assertIn("offene Faelle", message)
        bank_case = cases[("Bank Transaction", proposal.name)]
        self.assertEqual((bank_case["area"], bank_case["status"]), ("Bankumsatz", "Vorschlag"))
        self.assertIn(invoice.name, bank_case["reason"])
        self.assertIn("bestaetigen", bank_case["next_action"])
        self.assertEqual(bank_case["party"], f"{invoice.customer_name} **** {iban[-4:]}")
        self.assertEqual(cases[(IMPORT_DOCTYPE, broken)]["status"], "Teilfehler")
        self.assertIn("Mandatsplans", cases[("Sales Invoice", conflict.name)]["reason"])
        self.assertEqual(cases[("Sales Invoice", conflict.name)]["area"], "Zahlungsplan")
        notice = next(
            row for row in rows if row["area"] == "Vorabankündigung" and row["reference_name"] == unnotified.name
        )
        self.assertIn("Vorabankuendigung fehlt", notice["reason"])
        self.assertNotIn(iban, json.dumps(rows, default=str))
        only_imports = work_queue({"company": TEST_COMPANY, "area": "Bankimport"})[1]
        self.assertEqual({row["area"] for row in only_imports}, {"Bankimport"})

    def test_reviewed_position_marked_done_settles_the_run(self):
        invoice = make_member_invoice(make_member_with_mandate("Queue Done", dates=((9, 30, 350),)))
        run = exported_run(f"{NEXT_YEAR}-09-30", invoice.name)
        bank_import = import_credits(credit_row(350, run.collection_date, f"{BATCH_PURPOSE} POSTEN 1"))
        reconcile(bank_import)
        position = run_positions(run.name)[0]
        frappe.get_doc("Payment Entry", position.payment_entry).cancel()
        transaction = transactions_for(bank_import)[0]

        queue = {row["reference_name"]: row for row in work_queue({"company": TEST_COMPANY})[1]}
        self.assertEqual(queue[position.name]["area"], "Einzugsposition")
        self.assertEqual(queue[run.name]["area"], "Einzugslauf")
        self.assertEqual(queue[transaction.name]["status"], "Manuelle Pruefung")

        manual = book_manual_payment(invoice.name, transaction.name, run.collection_date)
        result = mark_position_done(position.name, "Zahlung im Banking-Bereich neu gebucht")
        reconcile(bank_import)

        run.reload()
        position = run_positions(run.name)[0]
        transaction = transactions_for(bank_import)[0]
        self.assertEqual((result["status"], result["run_status"]), ("Erledigt", "Abgestimmt"))
        self.assertEqual((position.status, run.status), ("Erledigt", "Abgestimmt"))
        resolution = frappe.db.get_value(
            POSITION_DOCTYPE, position.name, ["resolution_note", "processing_error"], as_dict=True
        )
        self.assertIn("Zahlung im Banking-Bereich neu gebucht", resolution.resolution_note)
        self.assertIn("Pruefergebnis war", resolution.resolution_note)
        self.assertEqual((resolution.processing_error or None, cint(run.error_count)), (None, 0))
        self.assertEqual((transaction.status, transaction.erpverein_match_status), ("Reconciled", "Abgeglichen"))
        self.assertEqual(frappe.db.count("Payment Entry", {"reference_no": manual, "docstatus": 1}), 1)
        queue = {row["reference_name"] for row in work_queue({"company": TEST_COMPANY})[1]}
        self.assertFalse({position.name, run.name, transaction.name} & queue)
        with self.assertRaises(frappe.ValidationError):
            mark_position_done(position.name, "noch einmal")

    def test_user_without_accounts_role_cannot_book_confirm_or_read_the_work_queue(self):
        invoice = make_self_payer_invoice("Transfer Rights")
        bank_import = import_credits(transfer_row(350, "Beitrag", name=invoice.customer_name))
        reconcile(bank_import)
        proposal = transactions_for(bank_import)[0]
        user = make_user_with_roles("System Manager", "Sales User", "Sales Master Manager")
        accounts_only = make_user_with_roles("Accounts User")
        treasurer = make_erpverein_manager_user()
        try:
            frappe.set_user(user.name)
            for call in (
                lambda: reconcile_bank_account(self.bank_account),
                lambda: get_transfer_proposal(proposal.name),
                lambda: confirm_transfer_proposal(proposal.name, "x"),
                lambda: reject_transfer_proposal(proposal.name, "x"),
                lambda: work_queue({"company": TEST_COMPANY}),
            ):
                with self.assertRaises(frappe.PermissionError):
                    call()
            frappe.set_user(accounts_only.name)
            message = work_queue({"company": TEST_COMPANY})[2]
            self.assertIn("Ohne Leserecht ausgeblendet", message)
            frappe.set_user(treasurer.name)
            shown = get_transfer_proposal(proposal.name)
            confirm_transfer_proposal(proposal.name, shown["proposal_hash"])
        finally:
            frappe.set_user("Administrator")

        payments = payments_for(proposal.name)
        self.assertEqual(len(payments), 1)
        self.assertEqual(frappe.db.get_value("Payment Entry", payments[0].name, "owner"), treasurer.name)


def make_self_payer_invoice(label: str, customer=None, amount: int = 350, start_date: str | None = None):
    customer = customer or make_eur_customer(label)
    mitglied = make_mitglied(
        customer=customer.name, vorname=label.split()[0], nachname=f"{label.split()[-1]}{token()}", jahresbeitrag=amount
    )
    subscription = make_managed_subscription(
        customer.name,
        "Mitglied",
        mitglied.name,
        "Beitragszahler",
        make_plan(amount, "Year").name,
        start_date or f"{NEXT_YEAR}-01-01",
        "Mitgliedsbeitrag",
    )
    invoice = subscription.create_invoice()
    invoice.submit()
    invoice.reload()
    return invoice


def make_tenant_invoice(customer, amount: int):
    mieter = make_mieter(customer=customer.name, nachname=f"Miete{token()}")
    subscription = make_managed_subscription(
        customer.name, "Mieter", mieter.name, "Mieter", make_plan(amount, "Month").name, f"{NEXT_YEAR}-03-01", "Miete"
    )
    invoice = subscription.create_invoice()
    invoice.submit()
    invoice.reload()
    return invoice


def transfer_row(
    amount: float, purpose: str, *, name: str = "Testzahler", iban: str | None = None, booking_date=None
) -> dict:
    day = formatdate(getdate(booking_date or BOOKING_DATE), "dd.MM.yyyy")
    return {
        "dateAsString": day,
        "valutaDateAsString": day,
        "valueAsString": f"{amount:.2f}",
        "purposeInOneLine": f"{purpose} {token()}",
        "transactionText": "Gutschrift",
        "remoteName": name,
        "remoteIban": iban or make_german_iban(),
    }


def payments_for(transaction_name: str) -> list:
    return frappe.get_all(
        "Payment Entry",
        filters={"erpverein_bank_transaction": transaction_name, "docstatus": 1},
        fields=PAYMENT_FIELDS,
        order_by="name asc",
    )


def references(payment_name: str) -> list[tuple]:
    return [
        (row.reference_name, row.payment_term, flt(row.allocated_amount))
        for row in frappe.get_all(
            "Payment Entry Reference",
            filters={"parent": payment_name},
            fields=["reference_name", "payment_term", "allocated_amount"],
            order_by="idx asc",
        )
    ]


def book_manual_payment(invoice: str, transaction: str, booking_date) -> str:
    """What the treasurer does in the banking area: a payment of their own, matched with the bank transaction."""
    from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import reconcile_vouchers
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    bank_account = frappe.db.get_value("Bank Transaction", transaction, "bank_account")
    payment = get_payment_entry(
        "Sales Invoice",
        invoice,
        bank_account=frappe.db.get_value("Bank Account", bank_account, "account"),
        reference_date=booking_date,
    )
    reference = f"MANUELL-{token()}"
    payment.update(
        {
            "posting_date": booking_date,
            "reference_no": reference,
            "reference_date": booking_date,
            "bank_account": bank_account,
        }
    )
    payment.insert()
    payment.submit()
    reconcile_vouchers(
        transaction,
        json.dumps(
            [{"payment_doctype": "Payment Entry", "payment_name": payment.name, "amount": flt(payment.paid_amount)}]
        ),
    )
    return reference
