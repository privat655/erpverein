import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from erpverein.services.bank_statement_parser import CATEGORY_DIRECT_DEBIT_CREDIT


# Every rule that decides whether a bank credit settles a SEPA collection lives in this module, without Frappe.
# A real bank fetch may need other categories, patterns or tolerances; change them here (spec 15.2 and 15.3).
SETTLEMENT_CATEGORIES = frozenset({CATEGORY_DIRECT_DEBIT_CREDIT})
# HVB batch credits name the number of collected direct debits, for example "POSTEN 12".
POSITION_COUNT_PATTERN = re.compile(r"\bPOSTEN\s*:?\s*(\d{1,5})\b")
# Run name, PmtInfId and MsgId begin with SEPA-LAUF-YYYY-NNNNN; a bank may drop or replace the separators.
RUN_NAME_PATTERN = re.compile(r"SEPA[^A-Z0-9]*LAUF[^A-Z0-9]*(\d{4})[^A-Z0-9]*(\d{5})")
# Free references (MsgId, bank order reference) count only from this length on; short ones match by chance.
MIN_REFERENCE_LENGTH = 8
CENT = Decimal("0.01")

ACTION_SETTLE = "settle"
ACTION_REVIEW = "review"
ACTION_SKIP = "skip"


@dataclass(frozen=True)
class Credit:
    name: str
    booking_date: date
    amount: Decimal
    category: str = ""
    # Purpose, references and booking text: everything the bank may use to name a run or a count.
    texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Run:
    name: str
    collection_date: date
    total: Decimal
    position_count: int
    tolerance_days: int
    # MsgId and bank order reference; the run name is checked anyway.
    references: tuple[str, ...] = ()


@dataclass(frozen=True)
class Position:
    name: str
    run: str
    amount: Decimal
    collection_date: date


@dataclass
class Decision:
    credit: str
    action: str
    run: str | None = None
    position: str | None = None
    reason: str | None = None
    # A run that a credit names but does not match goes to manual review as well.
    review_run: str | None = None


@dataclass
class CreditPlan:
    credit: Credit
    relevant: bool
    named: set[str] = field(default_factory=set)
    count: int | None = None
    window: list[Run] = field(default_factory=list)
    candidates: list[Run] = field(default_factory=list)
    exact: list[Run] = field(default_factory=list)


def to_amount(value: object) -> Decimal:
    try:
        return Decimal(str(value or 0)).quantize(CENT)
    except InvalidOperation:
        return Decimal("0.00")


def format_amount(value: object) -> str:
    text = f"{to_amount(value):,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{text} EUR"


def normalize_reference(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def run_names_in(texts) -> set[str]:
    return {
        f"SEPA-LAUF-{year}-{number}"
        for text in texts
        for year, number in RUN_NAME_PATTERN.findall(str(text or "").upper())
    }


def position_count_in(texts) -> int | None:
    counts = {int(value) for text in texts for value in POSITION_COUNT_PATTERN.findall(str(text or "").upper())}
    return counts.pop() if len(counts) == 1 else None


def in_settlement_window(booking_date: date, collection_date: date, tolerance_days: int) -> bool:
    return collection_date <= booking_date <= collection_date + timedelta(days=max(0, int(tolerance_days or 0)))


def names_run(credit: Credit, run: Run) -> bool:
    text = normalize_reference(" ".join(credit.texts))
    references = (normalize_reference(value) for value in (run.name, *run.references))
    return any(len(reference) >= MIN_REFERENCE_LENGTH and reference in text for reference in references)


def is_settlement_credit(credit: Credit) -> bool:
    return credit.amount > 0 and (credit.category in SETTLEMENT_CATEGORIES or bool(run_names_in(credit.texts)))


def plan_credit(credit: Credit, runs: list[Run]) -> CreditPlan:
    if not is_settlement_credit(credit):
        return CreditPlan(credit, relevant=False)
    named = run_names_in(credit.texts)
    count = position_count_in(credit.texts)
    window = [run for run in runs if in_settlement_window(credit.booking_date, run.collection_date, run.tolerance_days)]
    if named:
        candidates = [run for run in window if run.name in named]
    else:
        candidates = [run for run in window if names_run(credit, run)] or window
    if count is not None:
        candidates = [run for run in candidates if run.position_count == count]
    exact = [run for run in candidates if run.total == credit.amount]
    return CreditPlan(credit, True, named, count, window, candidates, exact)


def match_run_credits(credits: list[Credit], runs: list[Run]) -> list[Decision]:
    """Assign batch credits to runs. A pair settles only if credit and run match each other and nothing else."""
    plans = [plan_credit(credit, runs) for credit in credits]
    claims = Counter(plan.exact[0].name for plan in plans if len(plan.exact) == 1)
    settled = {name for name, count in claims.items() if count == 1}
    return [decide(plan, claims, settled) for plan in plans]


def decide(plan: CreditPlan, claims: Counter, settled: set[str]) -> Decision:
    credit = plan.credit
    if not plan.relevant:
        return Decision(credit.name, ACTION_SKIP)
    if len(plan.exact) == 1:
        run = plan.exact[0]
        if claims[run.name] == 1:
            return Decision(credit.name, ACTION_SETTLE, run=run.name)
        return review(
            credit,
            f"Mehrere Bankumsaetze passen zum Einzugslauf {run.name}. ERPverein ordnet keinen davon zu.",
            run.name,
        )
    if plan.exact:
        names = ", ".join(run.name for run in plan.exact)
        return review(credit, f"Mehrere Einzugslaeufe passen: {names}. ERPverein ordnet keinen zu.")
    if len(plan.candidates) == 1:
        run = plan.candidates[0]
        return review(credit, amount_mismatch_reason(credit, run), None if run.name in settled else run.name)
    if plan.candidates:
        names = ", ".join(run.name for run in plan.candidates)
        return review(
            credit, f"Kein Einzugslauf hat den Betrag {format_amount(credit.amount)}. Moegliche Laeufe: {names}."
        )
    if plan.named:
        names = ", ".join(sorted(plan.named))
        return review(
            credit,
            f"Der Bankumsatz nennt {names}. Kein offener Einzugslauf mit diesem Namen passt "
            "(Status, Einzugsdatum oder Anzahl der Positionen).",
        )
    if plan.window and plan.count is not None:
        return review(credit, f"POSTEN {plan.count} passt zu keinem Einzugslauf im Zeitfenster.")
    return review(credit, "Kein exportierter Einzugslauf passt zum Buchungsdatum dieser Lastschrift-Gutschrift.")


def review(credit: Credit, reason: str, run: str | None = None) -> Decision:
    return Decision(credit.name, ACTION_REVIEW, reason=reason, review_run=run)


def amount_mismatch_reason(credit: Credit, run: Run) -> str:
    return (
        f"Der Betrag {format_amount(credit.amount)} weicht von der Summe {format_amount(run.total)} des Einzugslaufs "
        f"{run.name} ab (Differenz {format_amount(credit.amount - run.total)}). ERPverein bucht keine Zahlung."
    )


def match_position_credit(credit: Credit, position: Position) -> Decision:
    """Single booking (BtchBookg = false): the EndToEndId names the position; amount and date must fit."""
    if credit.booking_date < position.collection_date:
        return Decision(
            credit.name,
            ACTION_REVIEW,
            position=position.name,
            reason=f"Das Buchungsdatum liegt vor dem Einzugsdatum der Position {position.name}.",
        )
    if credit.amount != position.amount:
        return Decision(
            credit.name,
            ACTION_REVIEW,
            position=position.name,
            reason=(
                f"Der Betrag {format_amount(credit.amount)} weicht vom Betrag {format_amount(position.amount)} "
                f"der Position {position.name} ab. ERPverein bucht keine Zahlung."
            ),
        )
    return Decision(credit.name, ACTION_SETTLE, run=position.run, position=position.name)
