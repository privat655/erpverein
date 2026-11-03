from datetime import date
from decimal import Decimal

from frappe.tests import UnitTestCase

from erpverein.services.bank_statement_parser import CATEGORY_DIRECT_DEBIT_CREDIT, CATEGORY_INCOMING_TRANSFER
from erpverein.services.transfer_settlement_matcher import (
    ACTION_BOOK,
    ACTION_OPEN,
    ACTION_REVIEW,
    ACTION_SKIP,
    ACTION_SUGGEST,
    NO_SIGNAL_REASON,
    Invoice,
    Payer,
    ScheduleRow,
    TransferCredit,
    TransferFacts,
    allocate_invoice,
    decide_transfer,
    describe_decision,
    invoice_numbers_in,
    names_match,
    payer_ids_in,
    proposal_hash,
)


GO_LIVE = date(2027, 1, 1)
BOOKING_DATE = date(2027, 2, 3)
RATE_1 = "ERPverein Rate 01"
RATE_2 = "ERPverein Rate 02"


def credit(amount: str, *texts: str, party_name="", category=CATEGORY_INCOMING_TRANSFER, rule="") -> TransferCredit:
    return TransferCredit("BT-1", BOOKING_DATE, Decimal(amount), category, texts, party_name, rule)


def row(term: str, due_date: date, outstanding: str, direct_debit=False, idx=1) -> ScheduleRow:
    return ScheduleRow(term, due_date, Decimal(outstanding), direct_debit, idx)


def invoice(
    name,
    customer,
    outstanding,
    *,
    rows=None,
    kind="Mitgliedsbeitrag",
    posting=date(2027, 1, 2),
    blocking=(),
    closed="",
    created=None,
) -> Invoice:
    rows = rows if rows is not None else (row(RATE_1, posting, outstanding),)
    return Invoice(
        name, customer, Decimal(outstanding), posting, posting, kind, closed, tuple(rows), tuple(blocking), created
    )


def facts(*invoices, payers=None, iban=(), names=None) -> TransferFacts:
    by_customer = {}
    for item in invoices:
        if item.is_open:
            by_customer.setdefault(item.customer, []).append(item)
    return TransferFacts(
        go_live_date=GO_LIVE,
        invoices={item.name: item for item in invoices},
        payers=payers or {},
        iban_customers=tuple(iban),
        customer_invoices={customer: tuple(items) for customer, items in by_customer.items()},
        customer_names=names or {},
    )


def allocations(decision) -> list[tuple]:
    return [(row.customer, row.invoice, row.payment_term, str(row.amount)) for row in decision.allocations]


class TestTransferSettlementMatcher(UnitTestCase):
    def test_invoice_numbers_and_ids_are_read_from_bank_texts(self):
        self.assertEqual(invoice_numbers_in(["Beitrag ACC-SINV-2027-00012 Max"]), ["ACC-SINV-2027-00012"])
        self.assertEqual(
            invoice_numbers_in(["acc sinv 2027 00012", "ACCSINV202700013", "SINV/2027/00014"]),
            ["ACC-SINV-2027-00012", "ACC-SINV-2027-00013", "ACC-SINV-2027-00014"],
        )
        # A wrapped or longer number is no invoice number: it may name another invoice.
        self.assertEqual(invoice_numbers_in(["ACC-SINV-2027-0001 2", "ACC-SINV-2027-000123", "XSINV-2027-00015"]), [])
        self.assertEqual(
            payer_ids_in(["Beitrag MIT-2027-00001", "Miete mie 2026 00002"]), ["MIE-2026-00002", "MIT-2027-00001"]
        )
        self.assertEqual(payer_ids_in(["Mitglied 2027 00001", "Miete 2027-00002", "LIMIT2027-00003", "mit 2027"]), [])

    def test_names_match_without_order_titles_or_initials(self):
        self.assertTrue(names_match("MUELLER, MAX", "Max Müller"))
        self.assertTrue(names_match("Herr Dr. Max M. Mueller", "Max Müller"))
        self.assertTrue(names_match("José Pérez", "JOSE PEREZ"))
        self.assertFalse(names_match("Max und Erika Mueller", "Max Müller"))
        self.assertFalse(names_match("", "Max Müller"))

    def test_one_open_invoice_number_books_full_partial_and_overpaid_amounts(self):
        # T13 and spec 16.6/16.7: the invoice number is a safe signal; the rest of an overpayment stays open.
        data = facts(invoice("ACC-SINV-2027-00012", "Kunde A", "350.00"))

        full = decide_transfer(credit("350.00", "Beitrag ACC-SINV-2027-00012"), data)
        partial = decide_transfer(credit("100.00", "ACC-SINV-2027-00012"), data)
        overpaid = decide_transfer(credit("400.00", "ACC-SINV-2027-00012"), data)

        self.assertEqual(
            (full.action, allocations(full), full.rest),
            (ACTION_BOOK, [("Kunde A", "ACC-SINV-2027-00012", RATE_1, "350.00")], Decimal("0.00")),
        )
        self.assertEqual(
            (partial.action, allocations(partial)),
            (ACTION_BOOK, [("Kunde A", "ACC-SINV-2027-00012", RATE_1, "100.00")]),
        )
        self.assertEqual(
            (overpaid.action, overpaid.total, overpaid.rest), (ACTION_BOOK, Decimal("350.00"), Decimal("50.00"))
        )
        self.assertIn("Ueberzahlung: 50,00 EUR bleibt am Bankumsatz offen", describe_decision(overpaid))

    def test_partial_payment_pays_transfer_rows_first_and_never_part_of_a_direct_debit_row(self):
        rows = (
            row(RATE_1, date(2027, 1, 31), "300.00", direct_debit=True, idx=1),
            row(RATE_2, date(2027, 2, 15), "50.00", idx=2),
        )
        data = facts(invoice("ACC-SINV-2027-00020", "Kunde A", "350.00", rows=rows))
        transfers = facts(
            invoice(
                "ACC-SINV-2027-00021",
                "Kunde B",
                "200.00",
                rows=(row(RATE_2, date(2027, 2, 15), "100.00", idx=2), row(RATE_1, date(2027, 1, 15), "100.00", idx=1)),
            )
        )

        rest_row = decide_transfer(credit("50.00", "ACC-SINV-2027-00020"), data)
        into_debit = decide_transfer(credit("100.00", "ACC-SINV-2027-00020"), data)
        full = decide_transfer(credit("350.00", "ACC-SINV-2027-00020"), data)
        oldest_first = decide_transfer(credit("150.00", "ACC-SINV-2027-00021"), transfers)

        self.assertEqual(allocations(rest_row), [("Kunde A", "ACC-SINV-2027-00020", RATE_2, "50.00")])
        self.assertEqual(rest_row.action, ACTION_BOOK)
        self.assertEqual(into_debit.action, ACTION_REVIEW)
        self.assertIn("Lastschriftzeile nur teilweise", into_debit.reason)
        self.assertEqual(
            [(term, amount) for _customer, _invoice, term, amount in allocations(full)],
            [(RATE_2, "50.00"), (RATE_1, "300.00")],
        )
        self.assertEqual(
            [(term, amount) for _customer, _invoice, term, amount in allocations(oldest_first)],
            [(RATE_1, "100.00"), (RATE_2, "50.00")],
        )

    def test_invoice_without_payment_terms_gets_one_reference(self):
        plain = invoice(
            "ACC-SINV-2026-00099",
            "Kunde A",
            "80.00",
            posting=date(2026, 5, 1),
            rows=(row("", date(2026, 5, 1), "80.00"),),
        )

        result, partial = allocate_invoice(plain, Decimal("50.00"))

        self.assertEqual(
            [(row.invoice, row.payment_term, str(row.amount)) for row in result], [(plain.name, "", "50.00")]
        )
        self.assertFalse(partial)

    def test_invoice_number_wins_over_iban_and_a_third_person_keeps_the_invoice_customer(self):
        child = invoice("ACC-SINV-2027-00030", "Kunde Kind", "175.00")
        parent = invoice("ACC-SINV-2027-00031", "Kunde Eltern", "175.00")

        decision = decide_transfer(
            credit("175.00", "Beitrag ACC-SINV-2027-00030"), facts(child, parent, iban=["Kunde Eltern"])
        )

        self.assertEqual((decision.action, decision.customers), (ACTION_BOOK, ["Kunde Kind"]))
        self.assertIn("Partei der Zahlung bleibt der Kunde der Rechnung", describe_decision(decision))

    def test_several_invoice_numbers(self):
        fee = invoice("ACC-SINV-2027-00040", "Kunde A", "175.00", posting=date(2027, 1, 2))
        rent = invoice("ACC-SINV-2027-00041", "Kunde A", "500.00", kind="Miete", posting=date(2027, 1, 5))
        other = invoice("ACC-SINV-2027-00042", "Kunde B", "175.00")
        data = facts(fee, rent, other)

        same_customer = decide_transfer(credit("675.00", "ACC-SINV-2027-00040 ACC-SINV-2027-00041"), data)
        short = decide_transfer(credit("300.00", "ACC-SINV-2027-00040 ACC-SINV-2027-00041"), data)
        family = decide_transfer(credit("350.00", "ACC-SINV-2027-00040, ACC-SINV-2027-00042"), data)
        family_short = decide_transfer(credit("300.00", "ACC-SINV-2027-00040, ACC-SINV-2027-00042"), data)

        self.assertEqual(same_customer.action, ACTION_BOOK)
        self.assertEqual(
            allocations(same_customer),
            [("Kunde A", fee.name, RATE_1, "175.00"), ("Kunde A", rent.name, RATE_1, "500.00")],
        )
        self.assertEqual(
            (short.action, allocations(short)),
            (ACTION_SUGGEST, [("Kunde A", fee.name, RATE_1, "175.00"), ("Kunde A", rent.name, RATE_1, "125.00")]),
        )
        self.assertEqual((family.action, family.customers), (ACTION_SUGGEST, ["Kunde A", "Kunde B"]))
        self.assertIn("Je Kunde entsteht eine Zahlung", describe_decision(family))
        self.assertEqual(family_short.action, ACTION_REVIEW)

    def test_named_invoice_that_is_not_open_needs_review(self):
        paid = invoice("ACC-SINV-2027-00050", "Kunde A", "0.00", closed="bezahlt")
        other = invoice("ACC-SINV-2027-00051", "Kunde A", "175.00")

        decision = decide_transfer(
            credit("175.00", "ACC-SINV-2027-00050 ACC-SINV-2027-09999"), facts(paid, other, iban=["Kunde A"])
        )

        self.assertEqual((decision.action, decision.allocations), (ACTION_REVIEW, []))
        self.assertIn("ACC-SINV-2027-00050: bezahlt.", describe_decision(decision))
        self.assertIn("ACC-SINV-2027-09999: existiert nicht.", describe_decision(decision))

    def test_payer_id_needs_exactly_one_invoice_with_the_amount(self):
        current = invoice("ACC-SINV-2027-00060", "Kunde A", "175.00")
        old = invoice("ACC-SINV-2026-00007", "Kunde A", "150.00", posting=date(2026, 1, 2))
        payer = Payer("MIT-2026-00001", "Kunde A", (current, old))
        data = facts(current, old, payers={"MIT-2026-00001": payer})

        booked = decide_transfer(credit("175.00", "Beitrag MIT-2026-00001"), data)
        historic = decide_transfer(credit("150.00", "MIT 2026 00001"), data)
        both = decide_transfer(credit("325.00", "MIT-2026-00001"), data)
        none = decide_transfer(credit("99.00", "MIT-2026-00001"), data)
        partial = decide_transfer(
            credit("100.00", "MIT-2026-00001"),
            facts(current, payers={"MIT-2026-00001": Payer("MIT-2026-00001", "Kunde A", (current,))}),
        )
        twins = decide_transfer(
            credit("175.00", "MIT-2026-00001"),
            facts(
                payers={
                    "MIT-2026-00001": Payer(
                        "MIT-2026-00001", "Kunde A", (current, invoice("ACC-SINV-2027-00061", "Kunde A", "175.00"))
                    )
                }
            ),
        )

        self.assertEqual((booked.action, booked.invoices), (ACTION_BOOK, [current.name]))
        self.assertEqual((historic.action, historic.invoices), (ACTION_SUGGEST, [old.name]))
        self.assertIn("vor dem Go-live-Stichtag", describe_decision(historic))
        self.assertEqual((both.action, both.invoices), (ACTION_SUGGEST, [old.name, current.name]))
        self.assertEqual(none.action, ACTION_OPEN)
        self.assertEqual(
            (partial.action, allocations(partial)), (ACTION_SUGGEST, [("Kunde A", current.name, RATE_1, "100.00")])
        )
        self.assertEqual(twins.action, ACTION_REVIEW)

    def test_unknown_or_several_ids(self):
        bill = invoice("ACC-SINV-2027-00062", "Kunde A", "175.00")
        payers = {
            "MIT-2027-00001": Payer("MIT-2027-00001", "Kunde A", (bill,)),
            "MIT-2027-00002": Payer("MIT-2027-00002", "Kunde B", ()),
        }

        # An unknown ID is ignored; the IBAN still decides.
        unknown = decide_transfer(credit("175.00", "Spende mit 2027 12345"), facts(bill, iban=["Kunde A"]))
        several = decide_transfer(credit("175.00", "MIT-2027-00001 MIT-2027-00002"), facts(bill, payers=payers))

        self.assertEqual((unknown.action, unknown.invoices), (ACTION_BOOK, [bill.name]))
        self.assertEqual(several.action, ACTION_REVIEW)

    def test_iban_counts_membership_fee_and_rent_of_a_shared_customer_separately(self):
        fee = invoice("ACC-SINV-2027-00070", "Kunde Gemeinsam", "175.00")
        rent = invoice("ACC-SINV-2027-00071", "Kunde Gemeinsam", "175.00", kind="Miete")
        big_rent = invoice("ACC-SINV-2027-00072", "Kunde Gemeinsam", "500.00", kind="Miete")

        ambiguous = decide_transfer(credit("175.00", "Danke"), facts(fee, rent, iban=["Kunde Gemeinsam"]))
        clear = decide_transfer(credit("175.00", "Danke"), facts(fee, big_rent, iban=["Kunde Gemeinsam"]))
        named = decide_transfer(credit("175.00", "ACC-SINV-2027-00071"), facts(fee, rent, iban=["Kunde Gemeinsam"]))
        not_mixed = decide_transfer(credit("675.00", "Danke"), facts(fee, big_rent, iban=["Kunde Gemeinsam"]))

        self.assertEqual(ambiguous.action, ACTION_REVIEW)
        self.assertIn("Mitgliedsbeitrag und Miete", ambiguous.reason)
        self.assertEqual((clear.action, clear.invoices), (ACTION_BOOK, [fee.name]))
        self.assertEqual((named.action, named.invoices), (ACTION_BOOK, [rent.name]))
        self.assertEqual(not_mixed.action, ACTION_OPEN)

    def test_name_and_amount_alone_only_suggest(self):
        # T14: name and amount never book; the suggestion names customer, invoice, amount, signals and conflicts.
        bill = invoice("ACC-SINV-2027-00080", "Kunde Max", "175.00")
        data = facts(bill, names={"Kunde Max": "Max Müller"})

        decision = decide_transfer(credit("175.00", "Beitrag 2027", party_name="MUELLER, MAX"), data)
        other_amount = decide_transfer(credit("170.00", "Beitrag 2027", party_name="MUELLER, MAX"), data)

        text = describe_decision(decision)
        self.assertEqual(
            (decision.action, allocations(decision)), (ACTION_SUGGEST, [("Kunde Max", bill.name, RATE_1, "175.00")])
        )
        for part in (
            "Vorschlag",
            "Kunde Max",
            bill.name,
            "175,00 EUR",
            "Name der Gegenseite",
            "Name und Betrag allein",
        ):
            self.assertIn(part, text)
        self.assertEqual(other_amount.action, ACTION_OPEN)

    def test_active_collection_position_blocks_every_automatic_booking(self):
        bill = invoice("ACC-SINV-2027-00090", "Kunde A", "175.00", blocking=("SEPA-POS-2027-0000001",))
        data = facts(bill, names={"Kunde A": "Max Mueller"})

        named = decide_transfer(credit("175.00", "ACC-SINV-2027-00090"), data)
        by_name = decide_transfer(credit("175.00", "Beitrag", party_name="Max Mueller"), data)

        for decision in (named, by_name):
            self.assertEqual(decision.action, ACTION_REVIEW)
            self.assertIn("SEPA-POS-2027-0000001", decision.reason)
            self.assertIn("doppelter Einzug", decision.reason)

    def test_matched_rule_turns_a_booking_into_a_suggestion(self):
        data = facts(invoice("ACC-SINV-2027-00012", "Kunde A", "175.00"))

        decision = decide_transfer(credit("175.00", "ACC-SINV-2027-00012", rule="Spenden"), data)

        self.assertEqual(decision.action, ACTION_SUGGEST)
        self.assertIn("Spenden", describe_decision(decision))

    def test_direct_debit_credits_and_credits_without_signal(self):
        data = facts(invoice("ACC-SINV-2027-00012", "Kunde A", "175.00"))

        batch = decide_transfer(credit("175.00", "ACC-SINV-2027-00012", category=CATEGORY_DIRECT_DEBIT_CREDIT), data)
        run = decide_transfer(credit("175.00", "SEPA-LAUF-2027-00001 ACC-SINV-2027-00012"), data)
        nothing = decide_transfer(credit("12.34", "Spende"), data)

        self.assertEqual((batch.action, run.action), (ACTION_SKIP, ACTION_SKIP))
        self.assertEqual((nothing.action, nothing.reason), (ACTION_OPEN, NO_SIGNAL_REASON))

    def test_proposal_hash_follows_the_allocations(self):
        data = facts(invoice("ACC-SINV-2027-00080", "Kunde Max", "175.00"), names={"Kunde Max": "Max Mueller"})

        first = decide_transfer(credit("175.00", "x", party_name="Max Mueller"), data)
        again = decide_transfer(credit("175.00", "y", party_name="Max Mueller"), data)
        changed = decide_transfer(
            credit("175.00", "x", party_name="Max Mueller"),
            facts(invoice("ACC-SINV-2027-00081", "Kunde Max", "175.00"), names={"Kunde Max": "Max Mueller"}),
        )

        self.assertEqual(proposal_hash(first), proposal_hash(again))
        self.assertNotEqual(proposal_hash(first), proposal_hash(changed))

    def test_rows_without_open_amount_never_give_an_empty_booking(self):
        odd = invoice("ACC-SINV-2027-00095", "Kunde A", "175.00", rows=(row(RATE_1, date(2027, 1, 2), "0.00"),))

        decision = decide_transfer(credit("175.00", "ACC-SINV-2027-00095"), facts(odd))

        self.assertEqual((decision.action, decision.allocations), (ACTION_REVIEW, []))
        self.assertIn("keinen offenen Betrag", decision.reason)

    def test_cancelled_invoice_number_follows_its_amendment(self):
        # Frappe names the amendment of a cancelled invoice "<name>-1"; the payer may write either number.
        amended = invoice("ACC-SINV-2027-00012-1", "Kunde A", "175.00")
        data = facts(amended)
        data.invoices = {"ACC-SINV-2027-00012": amended}

        old_number = decide_transfer(credit("175.00", "Beitrag ACC-SINV-2027-00012"), data)
        both = decide_transfer(credit("175.00", "ACC-SINV-2027-00012 bzw. ACC-SINV-2027-00012-1"), data)

        self.assertEqual(invoice_numbers_in(["ACC-SINV-2027-00012-1"]), ["ACC-SINV-2027-00012"])
        for decision in (old_number, both):
            self.assertEqual(
                (decision.action, decision.invoices, decision.total), (ACTION_BOOK, [amended.name], Decimal("175.00"))
            )
        self.assertIn("ersetzt durch ACC-SINV-2027-00012-1", describe_decision(old_number))

    def test_credit_older_than_the_invoice_needs_the_invoice_number(self):
        # Money from before the invoice is a prepayment, a second payment or a gift: ID and IBAN only suggest.
        later = invoice("ACC-SINV-2027-00096", "Kunde A", "175.00", created=date(2027, 3, 1))
        payers = {"MIT-2027-00009": Payer("MIT-2027-00009", "Kunde A", (later,))}

        by_iban = decide_transfer(credit("175.00", "Danke"), facts(later, iban=["Kunde A"]))
        by_id = decide_transfer(credit("175.00", "MIT-2027-00009"), facts(later, payers=payers))
        by_number = decide_transfer(credit("175.00", "ACC-SINV-2027-00096"), facts(later))
        in_time = decide_transfer(
            credit("175.00", "Danke"),
            facts(invoice("ACC-SINV-2027-00097", "Kunde A", "175.00", created=BOOKING_DATE), iban=["Kunde A"]),
        )

        for decision in (by_iban, by_id):
            self.assertEqual(decision.action, ACTION_SUGGEST)
            self.assertIn("aelter als die Rechnung ACC-SINV-2027-00096", describe_decision(decision))
        self.assertEqual((by_number.action, in_time.action), (ACTION_BOOK, ACTION_BOOK))
