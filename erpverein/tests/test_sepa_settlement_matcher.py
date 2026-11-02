from datetime import date
from decimal import Decimal

from frappe.tests import UnitTestCase

from erpverein.services.bank_statement_parser import CATEGORY_DIRECT_DEBIT_CREDIT, CATEGORY_INCOMING_TRANSFER
from erpverein.services.sepa_settlement_matcher import (
    ACTION_REVIEW,
    ACTION_SETTLE,
    ACTION_SKIP,
    Credit,
    Position,
    Run,
    format_amount,
    in_settlement_window,
    match_position_credit,
    match_run_credits,
    position_count_in,
    run_names_in,
)


FEBRUARY = date(2027, 2, 1)


def run(name: str, total: str, count: int, collection_date: date = FEBRUARY, references=()) -> Run:
    return Run(name, collection_date, Decimal(total), count, 5, tuple(references))


def credit(
    name: str, amount: str, *texts: str, booking_date: date = FEBRUARY, category=CATEGORY_DIRECT_DEBIT_CREDIT
) -> Credit:
    return Credit(name, booking_date, Decimal(amount), category, texts)


def decisions(credits: list[Credit], runs: list[Run]) -> dict:
    return {decision.credit: decision for decision in match_run_credits(credits, runs)}


class TestSEPASettlementMatcher(UnitTestCase):
    def test_position_count_and_run_names_are_read_from_bank_texts(self):
        self.assertEqual(position_count_in(["SEPA-BASISLASTSCHRIFT E.V. POSTEN 12 DATEI X123"]), 12)
        self.assertEqual(position_count_in(["Lastschrift posten: 3"]), 3)
        self.assertIsNone(position_count_in(["POSTEN 2", "POSTEN 3"]))
        self.assertIsNone(position_count_in(["SEPA Basislastschrift"]))
        self.assertEqual(run_names_in(["Einreichung SEPA-LAUF-2027-00012"]), {"SEPA-LAUF-2027-00012"})
        self.assertEqual(run_names_in(["SEPALAUF202700012270129070000"]), {"SEPA-LAUF-2027-00012"})
        self.assertEqual(run_names_in(["sepa lauf 2027 00012"]), {"SEPA-LAUF-2027-00012"})
        self.assertEqual(run_names_in(["SEPA-LAUF-2027-0"]), set())

    def test_settlement_window_covers_collection_date_and_tolerance(self):
        self.assertTrue(in_settlement_window(FEBRUARY, FEBRUARY, 5))
        self.assertTrue(in_settlement_window(date(2027, 2, 6), FEBRUARY, 5))
        self.assertFalse(in_settlement_window(date(2027, 2, 7), FEBRUARY, 5))
        self.assertFalse(in_settlement_window(date(2027, 1, 31), FEBRUARY, 5))

    def test_batch_credit_settles_the_only_run_with_equal_total(self):
        runs = [run("SEPA-LAUF-2027-00001", "700.00", 2), run("SEPA-LAUF-2027-00002", "700.00", 2, date(2027, 3, 1))]

        result = decisions(
            [credit("BT-1", "700.00", "SEPA Basislastschrift POSTEN 2", booking_date=date(2027, 2, 2))], runs
        )

        self.assertEqual((result["BT-1"].action, result["BT-1"].run), (ACTION_SETTLE, "SEPA-LAUF-2027-00001"))

    def test_amount_difference_sends_credit_and_run_to_review(self):
        result = decisions([credit("BT-1", "695.00", "POSTEN 2")], [run("SEPA-LAUF-2027-00001", "700.00", 2)])

        self.assertEqual(result["BT-1"].action, ACTION_REVIEW)
        self.assertEqual(result["BT-1"].review_run, "SEPA-LAUF-2027-00001")
        self.assertIn("695,00 EUR", result["BT-1"].reason)
        self.assertIn("700,00 EUR", result["BT-1"].reason)
        self.assertIn("-5,00 EUR", result["BT-1"].reason)

    def test_position_count_and_run_name_narrow_the_candidates(self):
        twelve = run("SEPA-LAUF-2027-00012", "1200.00", 12)
        eleven = run("SEPA-LAUF-2027-00011", "1200.00", 11)
        twin = run("SEPA-LAUF-2027-00013", "1200.00", 12)
        late = run("SEPA-LAUF-2027-00014", "1200.00", 12, date(2027, 4, 1))

        by_count = decisions([credit("BT-1", "1200.00", "POSTEN 12")], [twelve, eleven])
        ambiguous = decisions([credit("BT-1", "1200.00", "POSTEN 12")], [twelve, twin])
        by_name = decisions([credit("BT-1", "1200.00", "POSTEN 12 SEPA-LAUF-2027-00013")], [twelve, twin])
        foreign_name = decisions([credit("BT-1", "1200.00", "SEPA-LAUF-2027-00014")], [twelve, late])

        self.assertEqual((by_count["BT-1"].action, by_count["BT-1"].run), (ACTION_SETTLE, twelve.name))
        self.assertEqual(ambiguous["BT-1"].action, ACTION_REVIEW)
        self.assertIn("Mehrere Einzugslaeufe", ambiguous["BT-1"].reason)
        self.assertIsNone(ambiguous["BT-1"].review_run)
        self.assertEqual((by_name["BT-1"].action, by_name["BT-1"].run), (ACTION_SETTLE, twin.name))
        self.assertEqual(foreign_name["BT-1"].action, ACTION_REVIEW)
        self.assertIn("SEPA-LAUF-2027-00014", foreign_name["BT-1"].reason)

    def test_bank_order_reference_names_the_run(self):
        first = run("SEPA-LAUF-2027-00001", "350.00", 1)
        second = run("SEPA-LAUF-2027-00002", "350.00", 1, references=("PG-2027-0042",))

        result = decisions([credit("BT-1", "350.00", "Auftrag PG2027 0042")], [first, second])

        self.assertEqual((result["BT-1"].action, result["BT-1"].run), (ACTION_SETTLE, second.name))

    def test_two_credits_for_one_run_assign_neither(self):
        result = decisions(
            [credit("BT-1", "700.00", "POSTEN 2"), credit("BT-2", "700.00", "POSTEN 2", booking_date=date(2027, 2, 3))],
            [run("SEPA-LAUF-2027-00001", "700.00", 2)],
        )

        self.assertEqual({decision.action for decision in result.values()}, {ACTION_REVIEW})
        self.assertEqual({decision.review_run for decision in result.values()}, {"SEPA-LAUF-2027-00001"})
        self.assertIn("Mehrere Bankumsaetze", result["BT-1"].reason)

    def test_mismatch_does_not_flag_a_run_that_another_credit_settles(self):
        result = decisions(
            [credit("BT-1", "700.00", "POSTEN 2"), credit("BT-2", "690.00", "POSTEN 2")],
            [run("SEPA-LAUF-2027-00001", "700.00", 2)],
        )

        self.assertEqual(result["BT-1"].action, ACTION_SETTLE)
        self.assertEqual(result["BT-2"].action, ACTION_REVIEW)
        self.assertIsNone(result["BT-2"].review_run)

    def test_credit_without_settlement_signal_is_left_open(self):
        runs = [run("SEPA-LAUF-2027-00001", "350.00", 1)]

        transfer = decisions([credit("BT-1", "350.00", "Beitrag 2027", category=CATEGORY_INCOMING_TRANSFER)], runs)
        named = decisions([credit("BT-2", "350.00", "SEPA-LAUF-2027-00001", category=CATEGORY_INCOMING_TRANSFER)], runs)

        self.assertEqual(transfer["BT-1"].action, ACTION_SKIP)
        self.assertEqual((named["BT-2"].action, named["BT-2"].run), (ACTION_SETTLE, "SEPA-LAUF-2027-00001"))

    def test_settlement_credit_without_a_fitting_run_gets_a_reason(self):
        runs = [run("SEPA-LAUF-2027-00001", "700.00", 2), run("SEPA-LAUF-2027-00002", "900.00", 3)]

        nothing = decisions([credit("BT-1", "700.00", booking_date=date(2027, 5, 3))], runs)
        count = decisions([credit("BT-1", "700.00", "POSTEN 4")], runs)
        amount = decisions([credit("BT-1", "800.00")], runs)

        self.assertIn("Kein exportierter Einzugslauf", nothing["BT-1"].reason)
        self.assertIn("POSTEN 4", count["BT-1"].reason)
        self.assertIn("Kein Einzugslauf hat den Betrag 800,00 EUR", amount["BT-1"].reason)
        self.assertTrue(all(result["BT-1"].action == ACTION_REVIEW for result in (nothing, count, amount)))

    def test_single_booking_credit_needs_equal_amount_and_no_earlier_date(self):
        position = Position("SEPA-POS-2027-0000001", "SEPA-LAUF-2027-00001", Decimal("350.00"), FEBRUARY)

        paid = match_position_credit(credit("BT-1", "350.00", booking_date=date(2027, 2, 9)), position)
        wrong_amount = match_position_credit(credit("BT-2", "349.00"), position)
        too_early = match_position_credit(credit("BT-3", "350.00", booking_date=date(2027, 1, 29)), position)

        self.assertEqual((paid.action, paid.position, paid.run), (ACTION_SETTLE, position.name, position.run))
        self.assertEqual((wrong_amount.action, wrong_amount.position), (ACTION_REVIEW, position.name))
        self.assertIn("349,00 EUR", wrong_amount.reason)
        self.assertEqual(too_early.action, ACTION_REVIEW)

    def test_amounts_are_formatted_for_reasons(self):
        self.assertEqual(format_amount("1234.5"), "1.234,50 EUR")
        self.assertEqual(format_amount(Decimal("-5")), "-5,00 EUR")
