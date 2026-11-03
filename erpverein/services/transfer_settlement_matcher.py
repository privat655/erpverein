import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from erpverein.services.bank_statement_parser import CATEGORY_DIRECT_DEBIT_CREDIT, CATEGORY_MANUAL_REVIEW, UMLAUTS
from erpverein.services.sepa_settlement_matcher import format_amount, run_names_in, to_amount


# Every rule that decides whether an incoming transfer pays invoices lives in this module, without Frappe (spec 16).
# Payers write references in their own way; change the patterns and word lists here (spec 36.8).
# ERPNext names Sales Invoices ACC-SINV-YYYY-NNNNN; the invoice asks for this number as purpose of the transfer.
# Only the full number counts: a bank that wraps the purpose may split the digits, and a shortened number may
# name another invoice.
INVOICE_NUMBER_PATTERN = re.compile(
    r"(?<![A-Z0-9])(?:ACC[^A-Z0-9]{0,3})?SINV[^A-Z0-9]{0,3}(\d{4})[^A-Z0-9]{0,3}(\d{5})(?!\d)"
)
# Mitglied and Mieter are named MIT-YYYY-NNNNN and MIE-YYYY-NNNNN; the name is the ID (mitglied_id, mieter_id).
PAYER_ID_PATTERN = re.compile(r"(?<![A-Z0-9])(MIT|MIE)[^A-Z0-9]{0,3}(\d{4})[^A-Z0-9]{0,3}(\d{5})(?!\d)")
# Doctype and billing kind per ID prefix: a shared Customer keeps membership fees and rent apart.
PAYER_ID_KINDS = {"MIT": ("Mitglied", "Mitgliedsbeitrag"), "MIE": ("Mieter", "Miete")}
# Credits of the direct debit settlement (Phase 4) and returns are never transfers of a self-payer.
EXCLUDED_CATEGORIES = frozenset({CATEGORY_DIRECT_DEBIT_CREDIT, CATEGORY_MANUAL_REVIEW})
# Titles that banks and payers add to a name; single letters (initials) do not count either.
NAME_NOISE_WORDS = frozenset({"HERR", "HERRN", "FRAU", "DR", "PROF", "DIPL", "ING"})
ZERO = Decimal("0.00")

ACTION_BOOK = "book"
ACTION_SUGGEST = "suggest"
ACTION_REVIEW = "review"
ACTION_OPEN = "open"
ACTION_SKIP = "skip"

SIGNAL_INVOICE_NUMBER = "Rechnungsnummer im Verwendungszweck"
SIGNAL_PAYER_ID = "Mitglieds- oder Mieter-ID im Verwendungszweck"
SIGNAL_IBAN = "bekannte IBAN der Gegenseite"
SIGNAL_NAME = "Name der Gegenseite"
SIGNAL_AMOUNT = "Betrag gleich offenem Betrag einer Rechnung"
SIGNAL_SUM = "Betrag gleich Summe offener Rechnungen"
NO_SIGNAL_REASON = (
    "Kein Signal: keine Rechnungsnummer, keine Mitglieds- oder Mieter-ID, unbekannte IBAN, kein passender Name."
)
RECHECK_HINT = "ERPverein prueft beim naechsten Abgleich erneut."


@dataclass(frozen=True)
class TransferCredit:
    name: str
    booking_date: date
    amount: Decimal
    category: str = ""
    # Purpose, references and booking text: everything a payer may use to name an invoice or an ID.
    texts: tuple[str, ...] = ()
    party_name: str = ""
    # Bank Transaction Rule that ERPNext already matched (matched_transaction_rule).
    rule: str = ""


@dataclass(frozen=True)
class ScheduleRow:
    # Empty for a row without payment term (invoice not planned by ERPverein).
    payment_term: str
    due_date: date
    # Open amount of the row; payments without payment term already reduce the oldest rows (spec 31.5).
    outstanding: Decimal
    direct_debit: bool = False
    idx: int = 0


@dataclass(frozen=True)
class Invoice:
    name: str
    customer: str
    outstanding: Decimal
    posting_date: date
    due_date: date | None = None
    billing_kind: str = ""
    # Why the invoice cannot take a payment (for example "bezahlt"); empty for an open invoice.
    closed_reason: str = ""
    rows: tuple[ScheduleRow, ...] = ()
    # Collection positions that are neither paid nor done: a transfer could lead to a second collection.
    blocking_positions: tuple[str, ...] = ()
    # Day the invoice was created; money that came before cannot answer it.
    created_on: date | None = None

    @property
    def is_open(self) -> bool:
        return not self.closed_reason and self.outstanding > 0


@dataclass(frozen=True)
class Payer:
    reference: str
    customer: str = ""
    # Open invoices of the payer's Customer with the billing kind of the ID.
    invoices: tuple[Invoice, ...] = ()


@dataclass
class TransferFacts:
    go_live_date: date | None = None
    # Invoices and IDs named in the texts; None when the name does not exist.
    invoices: dict[str, Invoice | None] = field(default_factory=dict)
    payers: dict[str, Payer | None] = field(default_factory=dict)
    # Customers whose Bank Account has the IBAN of the counterparty.
    iban_customers: tuple[str, ...] = ()
    # Open invoices and names of the Customers that IBAN or name may point to.
    customer_invoices: dict[str, tuple[Invoice, ...]] = field(default_factory=dict)
    customer_names: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Allocation:
    customer: str
    invoice: str
    payment_term: str
    amount: Decimal


@dataclass
class TransferDecision:
    credit: str
    action: str
    allocations: list[Allocation] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    # Reasons why ERPverein does not book a suggestion on its own.
    conflicts: list[str] = field(default_factory=list)
    # Information that does not stop a booking, for example a third person as payer.
    hints: list[str] = field(default_factory=list)
    reason: str = ""
    # Amount above the allocations; it stays open on the bank transaction (overpayment, spec 16.7).
    rest: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return sum((allocation.amount for allocation in self.allocations), ZERO)

    @property
    def customers(self) -> list[str]:
        return list(dict.fromkeys(allocation.customer for allocation in self.allocations))

    @property
    def invoices(self) -> list[str]:
        return list(dict.fromkeys(allocation.invoice for allocation in self.allocations))


def fold_text(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").upper().translate(UMLAUTS))
    return "".join(char for char in text if not unicodedata.combining(char))


def invoice_numbers_in(texts) -> list[str]:
    return sorted(
        {
            f"ACC-SINV-{year}-{number}"
            for text in texts
            for year, number in INVOICE_NUMBER_PATTERN.findall(fold_text(text))
        }
    )


def payer_ids_in(texts) -> list[str]:
    return sorted(
        {
            f"{prefix}-{year}-{number}"
            for text in texts
            for prefix, year, number in PAYER_ID_PATTERN.findall(fold_text(text))
        }
    )


def payer_kind(reference: str) -> tuple[str, str]:
    """Doctype and billing kind of a Mitglied or Mieter ID."""
    return PAYER_ID_KINDS[reference[:3]]


def name_tokens(value: object) -> frozenset[str]:
    words = re.sub(r"[^A-Z0-9]+", " ", fold_text(value)).split()
    return frozenset(word for word in words if len(word) > 1 and word not in NAME_NOISE_WORDS)


def names_match(bank_name: object, customer_name: object) -> bool:
    tokens = name_tokens(bank_name)
    return bool(tokens) and tokens == name_tokens(customer_name)


def is_transfer_credit(credit: TransferCredit) -> bool:
    return credit.amount > 0 and credit.category not in EXCLUDED_CATEGORIES and not run_names_in(credit.texts)


def is_historic(invoice: Invoice, go_live_date: date | None) -> bool:
    return bool(go_live_date) and invoice.posting_date < go_live_date


def total_outstanding(invoices) -> Decimal:
    return sum((invoice.outstanding for invoice in invoices), ZERO)


def invoice_names(invoices) -> str:
    return ", ".join(invoice.name for invoice in invoices)


def allocation_order(row: ScheduleRow) -> tuple:
    # Transfer rows first: the invoice asks for them by transfer; direct debit rows wait for the announced collection.
    return (row.direct_debit, row.due_date, row.idx)


def invoice_order(invoice: Invoice) -> tuple:
    return (invoice.due_date or invoice.posting_date, invoice.posting_date, invoice.name)


def allocate_invoice(invoice: Invoice, amount: object) -> tuple[list[Allocation], bool]:
    """Spread a payment over the open rows of one invoice (spec 16.6).

    Returns the allocations and whether a direct debit row would stay partly paid."""
    amount = min(to_amount(amount), invoice.outstanding)
    if not invoice.rows or not all(row.payment_term for row in invoice.rows):
        # Without a payment term per row ERPNext keeps one reference per invoice; ERPverein does the same.
        return [Allocation(invoice.customer, invoice.name, "", amount)], False
    rows = [row for row in invoice.rows if row.outstanding > 0]
    allocations = []
    partial_direct_debit = False
    remaining = amount
    for row in sorted(rows, key=allocation_order):
        if remaining <= 0:
            break
        share = min(remaining, row.outstanding)
        allocations.append(Allocation(invoice.customer, invoice.name, row.payment_term, share))
        partial_direct_debit = partial_direct_debit or (row.direct_debit and share < row.outstanding)
        remaining -= share
    return allocations, partial_direct_debit


def allocate_invoices(invoices, amount: object) -> tuple[list[Allocation], bool]:
    """Several invoices: the oldest due date first; only the last one may stay partly open."""
    allocations = []
    partial_direct_debit = False
    remaining = to_amount(amount)
    for invoice in sorted(invoices, key=invoice_order):
        if remaining <= 0:
            break
        rows, partial = allocate_invoice(invoice, remaining)
        allocations += rows
        partial_direct_debit = partial_direct_debit or partial
        remaining -= sum((row.amount for row in rows), ZERO)
    return allocations, partial_direct_debit


def decide_transfer(credit: TransferCredit, facts: TransferFacts) -> TransferDecision:
    """Decide what ERPverein does with one incoming transfer: book, suggest, review or leave it open (spec 16.2)."""
    if not is_transfer_credit(credit):
        return TransferDecision(credit.name, ACTION_SKIP)
    numbers = invoice_numbers_in(credit.texts)
    # An ID counts only when it names a Mitglied or Mieter with Customer; "mit 2027 12345" in a text is no ID.
    payer_ids = [
        reference for reference in payer_ids_in(credit.texts) if (facts.payers.get(reference) or Payer("")).customer
    ]
    if numbers:
        # An invoice number wins over ID and IBAN (spec 16.3).
        decision = decide_by_invoice_numbers(credit, numbers, facts)
    elif payer_ids:
        decision = decide_by_payer_id(credit, payer_ids, facts)
    else:
        decision = decide_by_bank_party(credit, facts)
    if credit.rule and decision.action == ACTION_BOOK:
        decision.action = ACTION_SUGGEST
        decision.conflicts.append(f"Die Bank Transaction Rule {credit.rule} passt ebenfalls.")
    return decision


def decide_by_invoice_numbers(credit: TransferCredit, numbers: list[str], facts: TransferFacts) -> TransferDecision:
    signals = [SIGNAL_INVOICE_NUMBER]
    found = {}
    hints = []
    for number in numbers:
        invoice = facts.invoices.get(number)
        if invoice and invoice.is_open:
            found[invoice.name] = invoice
            if invoice.name != number:
                # A cancelled invoice lives on as its amendment ("-1"); the payer may still write the old number.
                hints.append(f"{number} ist storniert und ersetzt durch {invoice.name}.")
        elif invoice and invoice.name != number:
            hints.append(f"{number} (ersetzt durch {invoice.name}): {invoice.closed_reason}.")
        else:
            hints.append(f"{number}: {invoice.closed_reason if invoice else 'existiert nicht'}.")
    open_invoices = list(found.values())
    if not open_invoices:
        return review(credit, "Der Verwendungszweck nennt keine offene Rechnung.", signals, hints)
    customers = sorted({invoice.customer for invoice in open_invoices})
    open_total = total_outstanding(open_invoices)
    hints += third_party_hints(customers, facts)
    allocations, partial = allocate_invoices(open_invoices, credit.amount)
    if len(customers) > 1:
        if credit.amount != open_total:
            return review(
                credit,
                f"Die Rechnungen {invoice_names(open_invoices)} gehoeren zu verschiedenen Kunden. Der Betrag ist nicht "
                f"ihre Summe ({format_amount(open_total)}).",
                signals,
                hints,
            )
        return finish(
            credit,
            open_invoices,
            allocations,
            partial,
            signals + [SIGNAL_SUM],
            hints,
            conflict="Die Rechnungen gehoeren zu verschiedenen Kunden. Je Kunde entsteht eine Zahlung.",
        )
    if len(open_invoices) > 1 and credit.amount < open_total:
        return finish(
            credit,
            open_invoices,
            allocations,
            partial,
            signals,
            hints,
            conflict=(
                "Der Betrag reicht nicht fuer alle genannten Rechnungen. Der Vorschlag bezahlt die aelteste zuerst."
            ),
        )
    return finish(credit, open_invoices, allocations, partial, signals, hints, safe=True)


def decide_by_payer_id(credit: TransferCredit, payer_ids: list[str], facts: TransferFacts) -> TransferDecision:
    signals = [SIGNAL_PAYER_ID]
    if len(payer_ids) > 1:
        return review(
            credit,
            f"Der Verwendungszweck nennt mehrere IDs ({', '.join(payer_ids)}). ERPverein ordnet nicht automatisch zu.",
            signals,
        )
    reference = payer_ids[0]
    payer = facts.payers[reference]
    return decide_by_amount(
        credit, payer.invoices, signals, third_party_hints([payer.customer], facts), facts, owner=reference
    )


def decide_by_bank_party(credit: TransferCredit, facts: TransferFacts) -> TransferDecision:
    customers = list(facts.iban_customers)
    if len(customers) == 1:
        return decide_by_amount(
            credit,
            facts.customer_invoices.get(customers[0], ()),
            [SIGNAL_IBAN],
            [],
            facts,
            owner=f"Kunde {customers[0]}",
        )
    hints = [f"Die IBAN der Gegenseite gehoert mehreren Kunden: {', '.join(customers)}."] if customers else []
    candidates = [
        customer
        for customer, customer_name in sorted(facts.customer_names.items())
        if names_match(credit.party_name, customer_name) and (not customers or customer in customers)
    ]
    exact = [
        invoice
        for customer in candidates
        for invoice in facts.customer_invoices.get(customer, ())
        if invoice.is_open and invoice.outstanding == credit.amount
    ]
    if len(exact) == 1:
        allocations, partial = allocate_invoice(exact[0], credit.amount)
        return finish(
            credit,
            exact,
            allocations,
            partial,
            [SIGNAL_NAME, SIGNAL_AMOUNT],
            hints,
            conflict="Name und Betrag allein loesen keine Buchung aus.",
        )
    if exact:
        return leave_open(
            credit, f"Name und Betrag passen zu mehreren Rechnungen: {invoice_names(exact)}.", hints=hints
        )
    if candidates:
        return leave_open(
            credit,
            f"Der Name passt zu {', '.join(candidates)}. Der Betrag passt zu keiner offenen Rechnung. {RECHECK_HINT}",
            hints=hints,
        )
    if hints:
        return leave_open(credit, f"{hints[0]} Ohne Rechnungsnummer oder ID ordnet ERPverein nicht zu.")
    return leave_open(credit, NO_SIGNAL_REASON)


def decide_by_amount(credit, invoices, signals, hints, facts: TransferFacts, owner: str) -> TransferDecision:
    """ID or IBAN name the payer; the amount must equal the open amount of exactly one invoice (spec 16.2)."""
    invoices = [invoice for invoice in invoices if invoice.is_open]
    if not invoices:
        return leave_open(credit, f"{owner} hat keine offene Rechnung. {RECHECK_HINT}", signals, hints)
    exact = [invoice for invoice in invoices if invoice.outstanding == credit.amount]
    if len(exact) > 1:
        kinds = {invoice.billing_kind for invoice in exact if invoice.billing_kind}
        both = " Der Betrag passt zu Mitgliedsbeitrag und Miete." if len(kinds) > 1 else ""
        return review(
            credit,
            f"Der Betrag passt zu mehreren offenen Rechnungen von {owner}: {invoice_names(exact)}.{both}",
            signals,
            hints,
        )
    if exact:
        invoice = exact[0]
        allocations, partial = allocate_invoice(invoice, credit.amount)
        conflicts = []
        if is_historic(invoice, facts.go_live_date):
            conflicts.append(
                f"Die Rechnung {invoice.name} liegt vor dem Go-live-Stichtag (Historie). Ohne Rechnungsnummer "
                "bucht ERPverein sie nicht automatisch."
            )
        if invoice.created_on and credit.booking_date < invoice.created_on:
            # Money from before the invoice is a prepayment, a second payment or a gift; a person decides.
            conflicts.append(
                f"Die Gutschrift vom {credit.booking_date:%d.%m.%Y} ist aelter als die Rechnung {invoice.name}. "
                "Ohne Rechnungsnummer bucht ERPverein sie nicht automatisch."
            )
        return finish(
            credit,
            exact,
            allocations,
            partial,
            signals + [SIGNAL_AMOUNT],
            hints,
            safe=not conflicts,
            conflict=" ".join(conflicts) or None,
        )
    groups = {}
    for invoice in invoices:
        groups.setdefault(invoice.billing_kind, []).append(invoice)
    sums = [group for group in groups.values() if len(group) > 1 and total_outstanding(group) == credit.amount]
    if len(sums) > 1:
        return review(
            credit,
            f"Der Betrag ist die Summe der offenen Mitgliedsbeitraege und der offenen Mieten von {owner}.",
            signals,
        )
    if sums:
        allocations, partial = allocate_invoices(sums[0], credit.amount)
        return finish(
            credit,
            sums[0],
            allocations,
            partial,
            signals + [SIGNAL_SUM],
            hints,
            conflict=(
                "Der Betrag ist die Summe mehrerer Rechnungen. Ohne Rechnungsnummern bucht ERPverein nicht automatisch."
            ),
        )
    if len(invoices) == 1 and credit.amount < invoices[0].outstanding:
        allocations, partial = allocate_invoice(invoices[0], credit.amount)
        return finish(
            credit, invoices, allocations, partial, signals, hints, conflict="Teilzahlung ohne Rechnungsnummer."
        )
    return leave_open(
        credit, f"Der Betrag passt zu keiner offenen Rechnung von {owner}. {RECHECK_HINT}", signals, hints
    )


def finish(
    credit, invoices, allocations, partial_direct_debit, signals, hints, *, safe=False, conflict=None
) -> TransferDecision:
    decision = TransferDecision(
        credit.name, ACTION_BOOK, allocations, list(signals), [conflict] if conflict else [], list(hints)
    )
    decision.rest = credit.amount - decision.total
    used = set(decision.invoices)
    blocking = [
        (invoice.name, position)
        for invoice in invoices
        if invoice.name in used
        for position in invoice.blocking_positions
    ]
    if not allocations:
        decision.action = ACTION_REVIEW
        decision.reason = (
            f"Die Zahlungsplanzeilen von {invoice_names(invoices)} haben keinen offenen Betrag. ERPverein bucht nichts."
        )
    elif blocking:
        invoice, position = blocking[0]
        decision.action = ACTION_REVIEW
        decision.reason = (
            f"Die Rechnung {invoice} hat die aktive Einzugsposition {position}. ERPverein bucht die Ueberweisung "
            "nicht, sonst droht ein doppelter Einzug."
        )
    elif partial_direct_debit:
        decision.action = ACTION_REVIEW
        decision.reason = (
            "Die Zahlung wuerde eine Lastschriftzeile nur teilweise bezahlen. Der angekuendigte Einzugsbetrag "
            "stimmte dann nicht mehr."
        )
    elif not safe or decision.conflicts:
        decision.action = ACTION_SUGGEST
    return decision


def review(credit, reason: str, signals=(), hints=()) -> TransferDecision:
    return TransferDecision(credit.name, ACTION_REVIEW, signals=list(signals), hints=list(hints), reason=reason)


def leave_open(credit, reason: str, signals=(), hints=()) -> TransferDecision:
    return TransferDecision(credit.name, ACTION_OPEN, signals=list(signals), hints=list(hints), reason=reason)


def third_party_hints(customers: list[str], facts: TransferFacts) -> list[str]:
    if facts.iban_customers and not set(facts.iban_customers) & set(customers):
        return [
            f"Die IBAN der Gegenseite gehoert zu {', '.join(facts.iban_customers)}. Partei der Zahlung bleibt der "
            "Kunde der Rechnung."
        ]
    return []


def describe_allocations(allocations) -> str:
    parts = []
    for customer in dict.fromkeys(allocation.customer for allocation in allocations):
        rows = [
            f"{allocation.invoice} {format_amount(allocation.amount)}"
            + (f" ({allocation.payment_term})" if allocation.payment_term else "")
            for allocation in allocations
            if allocation.customer == customer
        ]
        parts.append(f"Kunde {customer}: {', '.join(rows)}")
    return "; ".join(parts)


def describe_decision(decision: TransferDecision) -> str:
    """Reason for the bank transaction: customer, invoices, amounts, signals and conflicts (spec 16.9)."""
    signals = ", ".join(decision.signals) or "keine"
    if decision.action == ACTION_SUGGEST:
        text = (
            f"Vorschlag: {describe_allocations(decision.allocations)}. Signale: {signals}. "
            f"Konflikte: {' '.join(decision.conflicts) or 'keine.'} "
            "Bestaetige oder verwirf den Vorschlag im Bankumsatz."
        )
    elif decision.action == ACTION_BOOK:
        text = f"Zugeordnet ({signals}): {describe_allocations(decision.allocations)}."
        if decision.rest > 0:
            text += (
                f" Ueberzahlung: {format_amount(decision.rest)} bleibt am Bankumsatz offen. Buche den Rest im "
                "Banking-Bereich (Vorauszahlung, Spende oder Rueckzahlung)."
            )
    else:
        text = decision.reason
        if decision.allocations:
            text += f" Betroffen: {describe_allocations(decision.allocations)}."
        if decision.signals and decision.action == ACTION_REVIEW:
            text += f" Signale: {signals}."
    return " ".join([text, *decision.hints]).strip()


def proposal_hash(decision: TransferDecision) -> str:
    """The user confirms exactly these allocations; any change in between needs a new look (spec 16.9)."""
    payload = {
        "credit": decision.credit,
        "allocations": [
            [allocation.customer, allocation.invoice, allocation.payment_term, str(allocation.amount)]
            for allocation in decision.allocations
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
