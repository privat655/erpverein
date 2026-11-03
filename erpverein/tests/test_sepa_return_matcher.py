from datetime import date
from decimal import Decimal

from frappe.tests import UnitTestCase

from erpverein.services.bank_statement_parser import (
    CATEGORY_DIRECT_DEBIT_REJECT,
    CATEGORY_DIRECT_DEBIT_RETURN,
    CATEGORY_OUTGOING_DIRECT_DEBIT,
)
from erpverein.services.sepa_return_matcher import (
    ACTION_BOOK,
    ACTION_REVIEW,
    ACTION_SKIP,
    ACTION_WAIT,
    SIGNAL_AMOUNT,
    SIGNAL_END_TO_END,
    SIGNAL_IBAN,
    SIGNAL_MANDATE,
    SIGNAL_PROXIMITY,
    STATE_DONE,
    STATE_PAID,
    STATE_RETURNED,
    STATE_REVIEW,
    STATE_UNPAID,
    Debit,
    ReturnPosition,
    confirm_return,
    decide_return,
    fee_amounts_in,
    refund_period_over,
    return_reason_in,
)


FEBRUARY = date(2027, 2, 1)
RETURN_TEXT = "SEPA Lastschrift-Rueckrechn"
HVB_PURPOSE = "Sonstige Gründe Eigengebühren - 3,00 Fremdgebühren - 7,67Rechnung ACC-SINV-2027-00012"
IBAN_HASH = "a" * 64
OTHER_HASH = "b" * 64


def position(
    name: str = "SEPA-POS-2027-0000001",
    amount: str = "350.00",
    state: str = STATE_PAID,
    collection_date: date = FEBRUARY,
    mandate: str = "MR-0001",
    iban_hash: str = IBAN_HASH,
    return_transaction: str = "",
) -> ReturnPosition:
    return ReturnPosition(name, state, Decimal(amount), collection_date, name, mandate, iban_hash, return_transaction)


def debit(
    amount: str,
    *texts: str,
    references=(),
    mandate: str = "",
    iban_hash: str = "",
    booking_date: date = date(2027, 2, 3),
    category: str = CATEGORY_DIRECT_DEBIT_RETURN,
    name: str = "BT-1",
) -> Debit:
    return Debit(name, booking_date, Decimal(amount), category, tuple(references), mandate, iban_hash, texts)


class TestSEPAReturnMatcher(UnitTestCase):
    def test_fees_and_reasons_are_read_from_bank_texts(self):
        self.assertEqual(fee_amounts_in([HVB_PURPOSE]), [Decimal("3.00"), Decimal("7.67")])
        self.assertEqual(
            fee_amounts_in(["Entgelt: 4,50 EUR Fremdentgelt EUR 1.000,00"]), [Decimal("4.50"), Decimal("1000.00")]
        )
        self.assertEqual(fee_amounts_in(["Rueckgabegebuehr 3.00"]), [Decimal("3.00")])
        self.assertEqual(fee_amounts_in(["Miete 2027-03 Rechnung 350,00", "SEPA Reject Deckung fehlt"]), [])
        self.assertEqual(return_reason_in([HVB_PURPOSE]), ("MS03", "Sonstige Gruende"))
        self.assertEqual(return_reason_in(["KONTO AUFGELÖST Eigengebühren - 3,00"]), ("AC04", "Konto aufgeloest"))
        self.assertEqual(return_reason_in(["SEPA Reject", "Deckung fehlt Miete"]), ("AM04", "Deckung fehlt"))
        self.assertEqual(return_reason_in(["RUECKGABE MD06 Widerspruch"]), ("MD06", "Widerspruch des Zahlers"))
        self.assertEqual(return_reason_in(["Rechnung ACC-SINV-2027-00012"]), ("", ""))

    def test_return_with_fee_books_principal_and_fee(self):
        # T15 and T28: the End-to-End reference names the position; the fee is the rest and matches the text.
        result = decide_return(
            debit("360.67", HVB_PURPOSE, RETURN_TEXT, references=("SEPA-POS-2027-0000001",)), [position()]
        )

        self.assertEqual(result.action, ACTION_BOOK)
        self.assertEqual(
            (result.position, result.principal, result.fee),
            ("SEPA-POS-2027-0000001", Decimal("350.00"), Decimal("10.67")),
        )
        self.assertEqual(result.signals, (SIGNAL_END_TO_END,))
        self.assertEqual((result.reason_code, result.reason_text), ("MS03", "Sonstige Gruende"))
        self.assertIn("Gebuehr 10,67 EUR", result.note)

    def test_return_without_fee_and_reject_after_the_credit_book_the_principal_only(self):
        for category, texts in (
            (CATEGORY_DIRECT_DEBIT_RETURN, ("Sonstige Gruende Rechnung ACC-SINV-2027-00012", RETURN_TEXT)),
            (CATEGORY_DIRECT_DEBIT_REJECT, ("Deckung fehlt Miete 2027-02", "SEPA Reject")),
        ):
            with self.subTest(category=category):
                result = decide_return(
                    debit("350.00", *texts, references=("SEPA-POS-2027-0000001",), category=category), [position()]
                )
                self.assertEqual(
                    (result.action, result.principal, result.fee), (ACTION_BOOK, Decimal("350.00"), Decimal("0.00"))
                )

    def test_amount_below_the_principal_and_contradicting_fee_text_go_to_review(self):
        smaller = decide_return(debit("300.00", RETURN_TEXT, references=("SEPA-POS-2027-0000001",)), [position()])
        contradiction = decide_return(debit("365.00", HVB_PURPOSE, references=("SEPA-POS-2027-0000001",)), [position()])
        no_fee_text = decide_return(debit("1350.00", RETURN_TEXT, references=("SEPA-POS-2027-0000001",)), [position()])

        self.assertEqual(smaller.action, ACTION_REVIEW)
        self.assertIn("kleiner als der Betrag 350,00 EUR", smaller.note)
        self.assertEqual(contradiction.action, ACTION_REVIEW)
        self.assertIn("laut Text 10,67 EUR", contradiction.note)
        self.assertIn("15,00 EUR", contradiction.note)
        # The user decided: no upper limit for the fee when the End-to-End reference names the position.
        self.assertEqual((no_fee_text.action, no_fee_text.fee), (ACTION_BOOK, Decimal("1000.00")))

    def test_end_to_end_reference_counts_in_every_category_and_before_the_collection_date_never(self):
        outgoing = decide_return(
            debit(
                "350.00", "Lastschrift", references=("sepa-pos-2027-0000001",), category=CATEGORY_OUTGOING_DIRECT_DEBIT
            ),
            [position()],
        )
        early = decide_return(
            debit("350.00", RETURN_TEXT, references=("SEPA-POS-2027-0000001",), booking_date=date(2027, 1, 29)),
            [position()],
        )
        foreign = decide_return(
            debit("350.00", "Lastschrift", mandate="MR-0001", category=CATEGORY_OUTGOING_DIRECT_DEBIT), [position()]
        )

        self.assertEqual(outgoing.action, ACTION_BOOK)
        self.assertEqual(early.action, ACTION_REVIEW)
        self.assertIn("vor dem Einzugsdatum", early.note)
        # A foreign direct debit with a mandate reference is no return of ERPverein.
        self.assertEqual(foreign.action, ACTION_SKIP)

    def test_position_state_decides_between_booking_waiting_and_review(self):
        cases = (
            (STATE_UNPAID, "", ACTION_WAIT),
            (STATE_REVIEW, "", ACTION_WAIT),
            (STATE_DONE, "", ACTION_REVIEW),
            (STATE_RETURNED, "BT-0", ACTION_REVIEW),
            (STATE_RETURNED, "BT-1", ACTION_BOOK),
        )
        for state, earlier, action in cases:
            with self.subTest(state=state, earlier=earlier):
                result = decide_return(
                    debit("350.00", RETURN_TEXT, references=("SEPA-POS-2027-0000001",)),
                    [position(state=state, return_transaction=earlier)],
                )
                self.assertEqual(result.action, action)
                self.assertEqual(result.position, "SEPA-POS-2027-0000001")

    def test_mandate_or_iban_with_the_exact_amount_book_without_end_to_end_reference(self):
        by_mandate = decide_return(debit("360.67", HVB_PURPOSE, mandate="mr-0001", iban_hash=IBAN_HASH), [position()])
        by_iban = decide_return(debit("350.00", RETURN_TEXT, iban_hash=IBAN_HASH), [position()])
        other_iban = decide_return(debit("350.00", RETURN_TEXT, mandate="MR-0001", iban_hash=OTHER_HASH), [position()])
        untexted_fee = decide_return(debit("355.00", RETURN_TEXT, mandate="MR-0001"), [position()])

        self.assertEqual((by_mandate.action, by_mandate.fee), (ACTION_BOOK, Decimal("10.67")))
        self.assertEqual(by_mandate.signals, (SIGNAL_MANDATE, SIGNAL_IBAN, SIGNAL_AMOUNT))
        self.assertEqual((by_iban.action, by_iban.signals), (ACTION_BOOK, (SIGNAL_IBAN, SIGNAL_AMOUNT)))
        self.assertEqual(other_iban.action, ACTION_REVIEW)
        self.assertEqual(untexted_fee.action, ACTION_REVIEW)
        self.assertIn("muss der Betrag aufgehen", untexted_fee.note)

    def test_closest_collection_decides_between_equal_amounts_only_within_the_proximity(self):
        january = position("SEPA-POS-2027-0000001", "500.00", collection_date=date(2027, 1, 4))
        february = position("SEPA-POS-2027-0000002", "500.00", collection_date=FEBRUARY)
        march = position("SEPA-POS-2027-0000003", "500.00", collection_date=date(2027, 3, 1))

        near = decide_return(debit("500.00", RETURN_TEXT, mandate="MR-0001"), [january, february, march])
        late = decide_return(
            debit("500.00", RETURN_TEXT, mandate="MR-0001", booking_date=date(2027, 2, 20)), [january, february]
        )
        twins = decide_return(
            debit("500.00", RETURN_TEXT, iban_hash=IBAN_HASH),
            [february, position("SEPA-POS-2027-0000009", "500.00", mandate="MR-0002")],
        )

        self.assertEqual((near.action, near.position), (ACTION_BOOK, february.name))
        self.assertEqual(near.signals[-1], SIGNAL_PROXIMITY)
        self.assertEqual(late.action, ACTION_REVIEW)
        self.assertIn("Mehrere Einzugspositionen passen", late.note)
        # Two mandates on one shared account: the IBAN cannot tell them apart.
        self.assertEqual(twins.action, ACTION_REVIEW)

    def test_returned_position_counts_for_ambiguity_and_no_reference_means_review(self):
        returned = position("SEPA-POS-2027-0000002", "500.00", state=STATE_RETURNED, return_transaction="BT-0")
        older = position("SEPA-POS-2027-0000001", "500.00", collection_date=date(2027, 1, 4))

        second = decide_return(debit("500.00", RETURN_TEXT, mandate="MR-0001"), [older, returned])
        blind = decide_return(debit("350.00", RETURN_TEXT), [position()])
        unknown = decide_return(debit("350.00", RETURN_TEXT, mandate="MR-9999"), [position()])
        old = decide_return(
            debit("350.00", RETURN_TEXT, mandate="MR-0001", booking_date=date(2028, 4, 1)), [position()]
        )

        self.assertEqual((second.action, second.position), (ACTION_REVIEW, returned.name))
        self.assertIn("schon zurueckbelastet", second.note)
        self.assertEqual(blind.action, ACTION_REVIEW)
        self.assertEqual(unknown.action, ACTION_REVIEW)
        self.assertIn("Keine Einzugsposition passt", unknown.note)
        self.assertEqual(old.action, ACTION_REVIEW)

    def test_confirmed_assignment_keeps_the_amount_rules_and_overrides_only_the_fee_text(self):
        contradiction = debit("365.00", "Sonstige Gruende Entgelt 3,00 Fremdentgelt 7,67")

        confirmed = confirm_return(contradiction, position(), "kassenwart@example.org")
        smaller = confirm_return(debit("300.00", RETURN_TEXT), position(), "kassenwart@example.org")
        unpaid = confirm_return(debit("350.00", RETURN_TEXT), position(state=STATE_UNPAID), "kassenwart@example.org")
        done = confirm_return(debit("350.00", RETURN_TEXT), position(state=STATE_DONE), "kassenwart@example.org")

        self.assertEqual((confirmed.action, confirmed.fee), (ACTION_BOOK, Decimal("15.00")))
        self.assertIn("bestaetigt von kassenwart@example.org", confirmed.note)
        self.assertIn("Laut Text 10,67 EUR", confirmed.note)
        for result in (smaller, unpaid, done):
            self.assertEqual(result.action, ACTION_REVIEW)
        self.assertIn("noch nicht gebucht", unpaid.note)

    def test_refund_period_closes_a_run_after_eight_weeks(self):
        self.assertFalse(refund_period_over(FEBRUARY, date(2027, 3, 28)))
        self.assertTrue(refund_period_over(FEBRUARY, date(2027, 3, 29)))
