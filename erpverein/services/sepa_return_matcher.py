import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from erpverein.services.bank_statement_parser import (
    CATEGORY_DIRECT_DEBIT_REJECT,
    CATEGORY_DIRECT_DEBIT_RETURN,
    UMLAUTS,
)
from erpverein.services.sepa_settlement_matcher import format_amount, to_amount


# Every rule that decides whether a bank debit returns a SEPA collection lives in this module, without Frappe
# (spec 17). A real bank fetch may need other categories, words or periods; change them here (spec 37.8).
# A debit without our End-to-End reference counts only in these categories (bank_statement_parser.classify_row).
RETURN_CATEGORIES = frozenset({CATEGORY_DIRECT_DEBIT_RETURN, CATEGORY_DIRECT_DEBIT_REJECT})
# HVB names the fees of a return in the purpose, for example "Eigengebuehren - 3,00 Fremdgebuehren - 7,67";
# other banks write "Entgelt" or "Fremdentgelt". Every word with GEBUEHR or ENTGELT names one fee amount.
FEE_PATTERN = re.compile(
    r"[A-Z]*(?:GEBUEHR|ENTGELT)[A-Z]*\s*(?:[-:=]\s*)?(?:EUR\s*)?(\d{1,3}(?:\.\d{3})*,\d{2}|\d+\.\d{2})(?!\d)"
)
# ISO 20022 return reasons that a bank may quote; the text is short and free of personal data.
ISO_REASONS = {
    "AC01": "IBAN fehlerhaft",
    "AC04": "Konto aufgeloest",
    "AC06": "Konto gesperrt",
    "AC13": "Zahler ist kein Verbraucher",
    "AG01": "Lastschrift fuer dieses Konto nicht erlaubt",
    "AG02": "Ungueltiger Vorgang",
    "AM04": "Deckung fehlt",
    "AM05": "Doppelte Lastschrift",
    "BE05": "Glaeubiger unbekannt",
    "CNOR": "Bank des Glaeubigers nicht erreichbar",
    "DNOR": "Bank des Zahlers nicht erreichbar",
    "FF01": "Ungueltiges Dateiformat",
    "FOCR": "Rueckruf",
    "MD01": "Kein gueltiges Mandat",
    "MD02": "Mandatsdaten fehlen oder sind falsch",
    "MD06": "Widerspruch des Zahlers",
    "MD07": "Zahler verstorben",
    "MS02": "Grund des Zahlers",
    "MS03": "Sonstige Gruende",
    "RC01": "BIC fehlerhaft",
    "RR01": "Regulatorischer Grund",
    "RR02": "Regulatorischer Grund",
    "RR03": "Regulatorischer Grund",
    "RR04": "Regulatorischer Grund",
    "SL01": "Dienst der Bank des Zahlers",
}
ISO_REASON_PATTERN = re.compile(r"(?<![A-Z0-9])(" + "|".join(ISO_REASONS) + r")(?![A-Z0-9])")
# German return texts without a code (HVB: "Sonstige Gruende", "Konto aufgeloest", "Deckung fehlt").
REASON_PHRASES = (
    ("AC04", re.compile(r"KONTO\s+(?:AUFGELOEST|ERLOSCHEN|GESCHLOSSEN|SALDIERT)")),
    ("AC06", re.compile(r"KONTO\s+GESPERRT")),
    ("AC01", re.compile(r"(?:IBAN|KONTONUMMER|KONTO)\s+(?:FEHLERHAFT|UNGUELTIG|FALSCH|UNBEKANNT)")),
    ("AM04", re.compile(r"DECKUNG|NICHT\s+GEDECKT")),
    ("MD01", re.compile(r"KEIN\w*\s+MANDAT|MANDAT\s+(?:FEHLT|UNGUELTIG|UNBEKANNT|WIDERRUFEN)")),
    ("MD06", re.compile(r"WIDERSPRUCH|ERSTATTUNG")),
    ("MD07", re.compile(r"VERSTORBEN")),
    ("MS02", re.compile(r"AUF\s+(?:WUNSCH|VERANLASSUNG)\s+DES\s+(?:ZAHLERS|ZAHLUNGSPFLICHTIGEN|KUNDEN)")),
    ("MS03", re.compile(r"SONSTIGE\s+GRUENDE|OHNE\s+ANGABE")),
)
# Without End-to-End reference the closest collection counts only within this many days; a return of the debtor
# bank arrives a few days after the collection, a refund request may come weeks later (spec 17.2).
PROXIMITY_DAYS = 14
# A payer may ask for a refund of an unauthorised collection for 13 months; older collections are not candidates.
MAX_RETURN_DAYS = 400
# The payer may ask for a refund of an authorised SEPA core direct debit for 8 weeks; then a run is closed.
REFUND_PERIOD_DAYS = 56
ZERO = Decimal("0.00")

# States of a position for the matcher; the service derives them from status and links.
STATE_PAID = "paid"
STATE_UNPAID = "unpaid"
STATE_REVIEW = "review"
STATE_DONE = "done"
STATE_RETURNED = "returned"

ACTION_BOOK = "book"
ACTION_WAIT = "wait"
ACTION_REVIEW = "review"
ACTION_SKIP = "skip"

SIGNAL_END_TO_END = "End-to-End-Referenz"
SIGNAL_MANDATE = "Mandatsreferenz"
SIGNAL_IBAN = "IBAN der Gegenseite"
SIGNAL_AMOUNT = "Betrag"
SIGNAL_PROXIMITY = "naechstes Einzugsdatum"


@dataclass(frozen=True)
class Debit:
    name: str
    booking_date: date
    # Total debit of the bank transaction (withdrawal), positive.
    amount: Decimal
    category: str = ""
    # End-to-End reference and reference number of the bank transaction.
    references: tuple[str, ...] = ()
    mandate_reference: str = ""
    # Hash of the counterparty IBAN, built like SEPA Einzugsposition.debtor_iban_hash.
    iban_hash: str = ""
    # Purpose and booking text: return reason and fees.
    texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReturnPosition:
    name: str
    state: str
    amount: Decimal
    collection_date: date
    end_to_end: str = ""
    mandate_reference: str = ""
    iban_hash: str = ""
    # Bank transaction of the return that ERPverein booked for this position.
    return_transaction: str = ""


@dataclass
class ReturnDecision:
    debit: str
    action: str
    position: str | None = None
    principal: Decimal = ZERO
    fee: Decimal = ZERO
    signals: tuple[str, ...] = ()
    reason_code: str = ""
    reason_text: str = ""
    note: str = ""


def fold_text(value: object) -> str:
    return " ".join(str(value or "").upper().translate(UMLAUTS).split())


def normalize_reference(value: object) -> str:
    return "".join(str(value or "").split()).upper()


def parse_text_amount(value: str) -> Decimal:
    if "," in value:
        value = value.replace(".", "").replace(",", ".")
    return to_amount(value)


def fee_amounts_in(texts) -> list[Decimal]:
    """Fees the bank names in the purpose; they only check the fee, they never decide it (spec 17.7)."""
    return [parse_text_amount(amount) for text in texts for amount in FEE_PATTERN.findall(fold_text(text))]


def return_reason_in(texts) -> tuple[str, str]:
    """Normalized return reason: ISO code and a short German text, never the bank text itself."""
    text = " ".join(fold_text(value) for value in texts)
    match = ISO_REASON_PATTERN.search(text)
    if match:
        return match.group(1), ISO_REASONS[match.group(1)]
    for code, pattern in REASON_PHRASES:
        if pattern.search(text):
            return code, ISO_REASONS[code]
    return "", ""


def refund_period_over(collection_date: date, today: date) -> bool:
    """The payer can no longer ask for a refund of an authorised collection (8 weeks)."""
    return today >= collection_date + timedelta(days=REFUND_PERIOD_DAYS)


def decide_return(debit: Debit, positions: list[ReturnPosition]) -> ReturnDecision:
    """Find the collection position a debit returns and split it into principal and fee (spec 17.2 and 17.7).

    ERPverein books only when exactly one position fits. Name and amount alone never book."""
    reason_code, reason_text = return_reason_in(debit.texts)
    decision = ReturnDecision(debit.name, ACTION_SKIP, reason_code=reason_code, reason_text=reason_text)
    references = {normalize_reference(value) for value in debit.references if normalize_reference(value)}
    named = [
        position
        for position in positions
        if position.end_to_end and normalize_reference(position.end_to_end) in references
    ]
    if len(named) > 1:
        return review(decision, f"Mehrere Einzugspositionen tragen die End-to-End-Referenz: {names(named)}.")
    if named:
        return check_position(decision, debit, named[0], (SIGNAL_END_TO_END,))
    if debit.category not in RETURN_CATEGORIES:
        return decision
    if not debit.mandate_reference and not debit.iban_hash:
        return review(
            decision,
            "Die Ruecklastschrift nennt weder eine bekannte End-to-End-Referenz noch Mandat oder IBAN. "
            "Buche sie im Banking-Bereich.",
        )
    signals = (SIGNAL_MANDATE,) if debit.mandate_reference else ()
    signals += (SIGNAL_IBAN,) if debit.iban_hash else ()
    payer = [position for position in positions if same_payer(debit, position)]
    window = [
        position
        for position in payer
        if position.collection_date <= debit.booking_date <= position.collection_date + timedelta(days=MAX_RETURN_DAYS)
    ]
    if not window:
        return review(
            decision,
            f"Keine Einzugsposition passt zu {' und '.join(signals)} der Ruecklastschrift. "
            "Buche sie im Banking-Bereich.",
        )
    # Without the End-to-End reference the amount names the collection: debit = position + fees named in the text.
    fees = sum(fee_amounts_in(debit.texts), ZERO)
    fitting = [position for position in window if position.amount + fees == debit.amount]
    if not fitting:
        fee_text = f" abzueglich Gebuehren laut Text {format_amount(fees)}" if fees else ""
        return review(
            decision,
            f"Die Belastung {format_amount(debit.amount)}{fee_text} passt zu keiner Einzugsposition von "
            f"{' und '.join(signals)} ({names(window)}). Ohne End-to-End-Referenz muss der Betrag aufgehen.",
        )
    signals += (SIGNAL_AMOUNT,)
    if len(fitting) > 1:
        latest = max(position.collection_date for position in fitting)
        nearest = [position for position in fitting if position.collection_date == latest]
        if len(nearest) > 1 or (debit.booking_date - latest).days > PROXIMITY_DAYS:
            return review(decision, f"Mehrere Einzugspositionen passen: {names(fitting)}. ERPverein bucht nichts.")
        fitting = nearest
        signals += (SIGNAL_PROXIMITY,)
    return check_position(decision, debit, fitting[0], signals)


def same_payer(debit: Debit, position: ReturnPosition) -> bool:
    if debit.mandate_reference and normalize_reference(position.mandate_reference) != normalize_reference(
        debit.mandate_reference
    ):
        return False
    return not debit.iban_hash or position.iban_hash == debit.iban_hash


def confirm_return(debit: Debit, position: ReturnPosition, confirmed_by: str) -> ReturnDecision:
    """A person assigns a debit in manual review to a position (spec 37.7).

    The amount rules stay: the position must be paid and the debit at least its amount. A fee text that contradicts
    the debit only informs; the person confirmed the assignment."""
    reason_code, reason_text = return_reason_in(debit.texts)
    decision = ReturnDecision(debit.name, ACTION_SKIP, reason_code=reason_code, reason_text=reason_text)
    decision = check_position(decision, debit, position, (f"bestaetigt von {confirmed_by}",), confirmed=True)
    if decision.action == ACTION_WAIT:
        return review(decision, f"Die Zahlung der Position {position.name} ist noch nicht gebucht.")
    return decision


def check_position(
    decision: ReturnDecision, debit: Debit, position: ReturnPosition, signals, confirmed: bool = False
) -> ReturnDecision:
    decision.position = position.name
    decision.signals = tuple(signals)
    if debit.booking_date < position.collection_date:
        return review(decision, f"Das Buchungsdatum liegt vor dem Einzugsdatum der Position {position.name}.")
    if position.state == STATE_RETURNED and position.return_transaction != debit.name:
        return review(
            decision,
            f"Die Position {position.name} ist schon zurueckbelastet (Bankumsatz {position.return_transaction}). "
            "ERPverein bucht keine zweite Gegenbuchung.",
        )
    if position.state == STATE_DONE:
        return review(
            decision,
            f"Die Position {position.name} ist als erledigt markiert. Buche die Ruecklastschrift im Banking-Bereich.",
        )
    if position.state in (STATE_UNPAID, STATE_REVIEW):
        decision.action = ACTION_WAIT
        decision.note = (
            f"Die Ruecklastschrift gehoert zur Position {position.name}. Deren Zahlung ist noch nicht gebucht. "
            "ERPverein bucht die Gegenbuchung, sobald die Zahlung gebucht ist."
        )
        return decision
    principal = position.amount
    fee = debit.amount - principal
    if fee < 0:
        return review(
            decision,
            f"Die Belastung {format_amount(debit.amount)} ist kleiner als der Betrag {format_amount(principal)} der "
            f"Position {position.name}. ERPverein bucht keine Gegenbuchung.",
        )
    fees_in_text = fee_amounts_in(debit.texts)
    text_fees = sum(fees_in_text, ZERO) if fees_in_text else None
    contradiction = text_fees is not None and text_fees != fee
    if contradiction and not confirmed:
        return review(
            decision,
            f"Widerspruch bei der Gebuehr: laut Text {format_amount(text_fees)}, Belastung minus "
            f"Betrag der Position {position.name} {format_amount(fee)}. ERPverein bucht keine Gegenbuchung.",
        )
    decision.action = ACTION_BOOK
    decision.principal = principal
    decision.fee = fee
    decision.note = describe_booking(decision)
    if contradiction:
        decision.note += f" Laut Text {format_amount(text_fees)}; gebucht ist die Belastung."
    return decision


def review(decision: ReturnDecision, reason: str) -> ReturnDecision:
    decision.action = ACTION_REVIEW
    decision.note = reason
    return decision


def names(positions) -> str:
    return ", ".join(position.name for position in positions)


def describe_booking(decision: ReturnDecision) -> str:
    text = (
        f"Ruecklastschrift zur Einzugsposition {decision.position} ({', '.join(decision.signals)}): "
        f"Hauptbetrag {format_amount(decision.principal)}, Gebuehr {format_amount(decision.fee)}."
    )
    if decision.reason_text:
        text += f" Grund: {decision.reason_text} ({decision.reason_code})."
    return text
