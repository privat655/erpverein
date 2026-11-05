import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


PROFILE_DEFAULT_V2 = "aqbanking-listtrans-default-v2"
PROFILE_TEMPLATE_V3 = "aqbanking-listtrans-template-v3"
PARSER_VERSION = "erpverein-bank-parser-1"
SCHEMA_PROFILES = {
    "erpverein.aqbanking.listtrans-default.v2": PROFILE_DEFAULT_V2,
    "erpverein.aqbanking.listtrans-template.v3": PROFILE_TEMPLATE_V3,
}
# Standard template of aqbanking-cli 6.9.2 listtrans (src/tools/aqbanking-cli/listtrans.c), no header line.
DEFAULT_V2_COLUMNS = (
    "dateOrValutaDateAsString",
    "valueAsString",
    "localBankcode",
    "localAccountNumber",
    "localIban",
    "remoteName",
    "remoteIban",
    "purposeInOneLine",
)
# Extended template proposed for the Banking Webtop (O7); the file starts with these names as header line.
TEMPLATE_V3_COLUMNS = (
    "fiId",
    "dateAsString",
    "valutaDateAsString",
    "valueAsString",
    "localIban",
    "remoteName",
    "remoteIban",
    "remoteBic",
    "remoteAccountNumber",
    "purposeInOneLine",
    "endToEndReference",
    "mandateId",
    "creditorSchemeId",
    "customerReference",
    "bankReference",
    "transactionCode",
    "transactionText",
    "transactionKey",
    "textKey",
    "primanota",
    "ultimateDebtor",
    "ultimateCreditor",
)
V3_TEMPLATE = "\t".join(f"$({column})" for column in TEMPLATE_V3_COLUMNS)
MAX_MANIFEST_BYTES = 64 * 1024
MAX_STATEMENT_BYTES = 5 * 1024 * 1024
MAX_STATEMENT_ROWS = 5000
FINGERPRINT_VERSION = "erpverein-bank-row-v1"
EMPTY_REFERENCES = {"NOTPROVIDED", "NONREF"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,139}$")
ACCOUNT_ALIAS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
IBAN_PATTERN = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
DOT_AMOUNT_PATTERN = re.compile(r"^\d+(\.\d{1,2})?$")
GERMAN_AMOUNT_PATTERN = re.compile(r"^(\d{1,3}(\.\d{3})+|\d+)(,\d{1,2})?$")
GERMAN_DATE_PATTERN = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?$")
ISO_DATE_PATTERN = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$"
)
COMPACT_DATE_PATTERN = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
MASKABLE_IBAN = re.compile(r"\b[A-Z]{2}[0-9]{2}(?: ?[A-Z0-9]){11,30}\b")
UMLAUTS = str.maketrans({"Ä": "AE", "Ö": "OE", "Ü": "UE", "ß": "SS", "ẞ": "SS"})

CATEGORY_INCOMING_TRANSFER = "Ueberweisung eingehend"
CATEGORY_INCOMING_STANDING_ORDER = "Dauerauftrag eingehend"
CATEGORY_INCOMING_INSTANT = "Echtzeitueberweisung eingehend"
CATEGORY_OUTGOING_TRANSFER = "Ueberweisung ausgehend"
CATEGORY_OUTGOING_STANDING_ORDER = "Dauerauftrag ausgehend"
CATEGORY_OUTGOING_DIRECT_DEBIT = "Lastschrift ausgehend"
CATEGORY_DIRECT_DEBIT_CREDIT = "Lastschrift-Gutschrift"
CATEGORY_DIRECT_DEBIT_REJECT = "Lastschrift-Rueckgabe (Reject)"
CATEGORY_DIRECT_DEBIT_RETURN = "Ruecklastschrift"
CATEGORY_BANK_FEE = "Bankgebuehr"
CATEGORY_OTHER = "Sonstiges"
CATEGORY_MANUAL_REVIEW = "Manuelle Pruefung"
CATEGORIES = (
    CATEGORY_INCOMING_TRANSFER,
    CATEGORY_INCOMING_STANDING_ORDER,
    CATEGORY_INCOMING_INSTANT,
    CATEGORY_OUTGOING_TRANSFER,
    CATEGORY_OUTGOING_STANDING_ORDER,
    CATEGORY_OUTGOING_DIRECT_DEBIT,
    CATEGORY_DIRECT_DEBIT_CREDIT,
    CATEGORY_DIRECT_DEBIT_REJECT,
    CATEGORY_DIRECT_DEBIT_RETURN,
    CATEGORY_BANK_FEE,
    CATEGORY_OTHER,
    CATEGORY_MANUAL_REVIEW,
)
# Geschaeftsvorfallcodes (DK) that are unambiguous for this club; unknown codes fall back to the other signals.
DIRECT_DEBIT_CREDIT_CODES = {"171", "174", "192", "196"}
DIRECT_DEBIT_RETURN_CODES = {"181", "184"}
OUTGOING_DIRECT_DEBIT_CODES = {"104", "105", "106", "107", "109"}
INCOMING_STANDING_ORDER_CODES = {"152"}
INCOMING_TRANSFER_CODES = {"151", "153", "154", "166", "167", "169"}
OUTGOING_STANDING_ORDER_CODES = {"117"}
OUTGOING_TRANSFER_CODES = {"116", "118", "119", "191"}
BANK_FEE_CODES = {"805", "806", "807", "808", "809"}
RETURN_SUBFAMILIES = {"UPDD", "RRTN", "PRDD", "RPCR"}
FEE_SUBFAMILIES = {"CHRG", "FEES", "COMM", "COMI"}
RETURN_WORDS = ("RUECKLASTSCHRIFT", "RUECKRECHNUNG", "RUECKBELASTUNG", "LASTSCHRIFT-RUECK", "LASTSCHRIFTRUECK")
FEE_WORDS = ("ENTGELT", "GEBUEHR", "KONTOFUEHRUNG", "ABSCHLUSS", "PREIS FUER", "PORTO")
BATCH_PATTERN = re.compile(r"\bPOSTEN\s*\d+\b")


class BankStatementError(ValueError):
    """A whole file or manifest cannot be imported."""


class BankRowError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass
class StatementRow:
    source_row_number: int
    booking_date: date
    value_date: date | None
    amount: Decimal
    purpose: str = ""
    party_name: str = ""
    party_iban: str = ""
    party_account: str = ""
    party_bic: str = ""
    local_iban: str = ""
    end_to_end_reference: str = ""
    mandate_reference: str = ""
    creditor_identifier: str = ""
    customer_reference: str = ""
    bank_reference: str = ""
    bank_transaction_id: str = ""
    transaction_code: str = ""
    transaction_text: str = ""
    transaction_key: str = ""
    text_key: str = ""
    primanota: str = ""
    ultimate_debtor: str = ""
    ultimate_creditor: str = ""
    fingerprint: str = ""
    category: str = ""


@dataclass
class ParsedStatement:
    profile: str
    encoding: str
    rows: list[StatementRow] = field(default_factory=list)
    errors: list[tuple[int, str, str]] = field(default_factory=list)
    # Filled values per column, counts only: shows what the bank delivers without showing content.
    fill_counts: dict[str, int] = field(default_factory=dict)

    @property
    def row_count(self) -> int:
        return len(self.rows) + len(self.errors)


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    text = "".join(" " if unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp", "Zs"} else char for char in text)
    return " ".join(text.split())


def normalize_reference(value: object) -> str:
    text = normalize_text(value)
    return "" if text.upper() in EMPTY_REFERENCES else text


def normalize_code(value: object) -> str:
    text = normalize_text(value)
    return "" if text.strip("0") == "" else text


def normalize_iban(value: object) -> str:
    return "".join(normalize_text(value).split()).upper()


def is_valid_iban(value: object) -> bool:
    iban = normalize_iban(value)
    if not IBAN_PATTERN.fullmatch(iban):
        return False
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1


def mask_iban(value: object) -> str:
    iban = normalize_iban(value)
    return f"**** {iban[-4:]}" if len(iban) >= 4 else "****"


def mask_sensitive_text(value: object) -> str:
    """Mask IBAN-like values in messages before they reach logs or the import record."""
    return MASKABLE_IBAN.sub(lambda match: mask_iban(match.group(0)), str(value or ""))


def parse_amount(value: object) -> Decimal:
    text = "".join(normalize_text(value).split()).replace("EUR", "").replace("€", "")
    sign = 1
    if text.endswith("-") or text.startswith("-"):
        sign = -1
        text = text.strip("-")
    elif text.startswith("+"):
        text = text[1:]
    if "," in text:
        if not GERMAN_AMOUNT_PATTERN.fullmatch(text):
            raise BankRowError("betrag", "Der Betrag hat kein gueltiges Format.")
        text = text.replace(".", "").replace(",", ".")
    elif not DOT_AMOUNT_PATTERN.fullmatch(text):
        raise BankRowError("betrag", "Der Betrag hat kein gueltiges Format.")
    try:
        return (sign * Decimal(text)).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise BankRowError("betrag", "Der Betrag hat kein gueltiges Format.") from None


def parse_date(value: object) -> date:
    text = normalize_text(value)
    for pattern, order in (
        (GERMAN_DATE_PATTERN, (3, 2, 1)),
        (ISO_DATE_PATTERN, (1, 2, 3)),
        (COMPACT_DATE_PATTERN, (1, 2, 3)),
    ):
        match = pattern.fullmatch(text)
        if match:
            try:
                return date(*(int(match.group(index)) for index in order))
            except ValueError:
                break
    raise BankRowError("datum", "Das Datum hat kein gueltiges Format.")


def decode_statement(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"


def parse_statement(data: bytes, profile: str) -> ParsedStatement:
    if profile not in (PROFILE_DEFAULT_V2, PROFILE_TEMPLATE_V3):
        raise BankStatementError("Das Importprofil ist unbekannt.")
    if not data:
        raise BankStatementError("Die Datei ist leer.")
    if len(data) > MAX_STATEMENT_BYTES:
        raise BankStatementError("Die Datei ist groesser als 5 MB.")
    text, encoding = decode_statement(data)
    # AqBanking ends every row with a newline; other line separators may occur inside texts.
    lines = [(number, line.rstrip("\r")) for number, line in enumerate(text.split("\n"), start=1) if line.strip()]
    if profile == PROFILE_TEMPLATE_V3:
        if not lines:
            raise BankStatementError("Die Datei hat keine Kopfzeile.")
        columns = [normalize_text(name) for name in lines[0][1].split("\t")]
        missing = {"valueAsString"} - set(columns)
        if missing or not {"dateAsString", "valutaDateAsString"} & set(columns):
            raise BankStatementError("Die Kopfzeile nennt weder Datum noch Betrag.")
        lines = lines[1:]
    else:
        columns = list(DEFAULT_V2_COLUMNS)
    if len(lines) > MAX_STATEMENT_ROWS:
        raise BankStatementError(f"Die Datei hat mehr als {MAX_STATEMENT_ROWS} Zeilen.")
    result = ParsedStatement(profile=profile, encoding=encoding, fill_counts=dict.fromkeys(filter(None, columns), 0))
    for number, line in lines:
        values = line.split("\t")
        try:
            if len(values) != len(columns):
                raise BankRowError("spaltenzahl", f"Die Zeile hat {len(values)} statt {len(columns)} Spalten.")
            row_values = dict(zip(columns, values, strict=True))
            for column in result.fill_counts:
                result.fill_counts[column] += bool(normalize_code(row_values[column]))
            result.rows.append(build_row(number, row_values))
        except BankRowError as exc:
            result.errors.append((number, exc.code, str(exc)))
    return result


def build_row(number: int, values: dict[str, str]) -> StatementRow:
    booking = values.get("dateAsString") or values.get("dateOrValutaDateAsString") or values.get("valutaDateAsString")
    value_date = values.get("valutaDateAsString")
    remote_iban = normalize_iban(values.get("remoteIban"))
    remote_account = normalize_text(values.get("remoteAccountNumber"))
    if remote_iban and not is_valid_iban(remote_iban):
        remote_account = remote_account or remote_iban
        remote_iban = ""
    return StatementRow(
        source_row_number=number,
        booking_date=parse_date(booking),
        value_date=parse_date(value_date) if normalize_text(value_date) else None,
        amount=parse_amount(values.get("valueAsString")),
        purpose=normalize_text(values.get("purposeInOneLine")),
        party_name=normalize_text(values.get("remoteName")),
        party_iban=remote_iban,
        party_account=remote_account,
        party_bic=normalize_iban(values.get("remoteBic")),
        local_iban=normalize_iban(values.get("localIban")),
        end_to_end_reference=normalize_reference(values.get("endToEndReference")),
        mandate_reference=normalize_reference(values.get("mandateId")),
        creditor_identifier=normalize_iban(values.get("creditorSchemeId")),
        customer_reference=normalize_reference(values.get("customerReference")),
        bank_reference=normalize_reference(values.get("bankReference")),
        bank_transaction_id=normalize_reference(values.get("fiId")),
        transaction_code=normalize_code(values.get("transactionCode")),
        transaction_text=normalize_text(values.get("transactionText")),
        transaction_key=normalize_text(values.get("transactionKey")).upper(),
        text_key=normalize_code(values.get("textKey")),
        primanota=normalize_code(values.get("primanota")),
        ultimate_debtor=normalize_text(values.get("ultimateDebtor")),
        ultimate_creditor=normalize_text(values.get("ultimateCreditor")),
    )


def fingerprint_identity(row: StatementRow, account_iban: str) -> str:
    # Only fields of both profiles: a v3 file that overlaps the last v2 file finds the same transactions.
    return json.dumps(
        {
            "account": normalize_iban(account_iban),
            "booking_date": row.booking_date.isoformat(),
            "amount": str(row.amount),
            "party_iban": row.party_iban,
            "party_name": row.party_name.casefold(),
            "purpose": row.purpose.casefold(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def assign_fingerprints(rows: list[StatementRow], account_iban: str) -> None:
    occurrences = Counter()
    for row in rows:
        identity = fingerprint_identity(row, account_iban)
        occurrences[identity] += 1
        payload = f"{FINGERPRINT_VERSION}|{identity}|{occurrences[identity]}"
        row.fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()


# AqBanking reads localIban from the booking's parties (debit: debtor account, credit: creditor account) and ignores
# the reversal flag. A return or refund keeps the original parties, so localIban is the other party and remoteIban the
# club. Such a row gets the other party as counterpart; a row without the club on either side is foreign.
def align_reversed_parties(rows: list[StatementRow], account_iban: str) -> list[int]:
    account_iban = normalize_iban(account_iban)
    foreign = []
    for row in rows:
        if not row.local_iban or row.local_iban == account_iban:
            continue
        if row.party_iban != account_iban:
            foreign.append(row.source_row_number)
            continue
        # The remote name and BIC are the club's own; the export holds no name of the other party.
        row.party_iban, row.party_name, row.party_bic, row.local_iban = row.local_iban, "", "", account_iban
    return foreign


def classify_row(row: StatementRow, own_creditor_ids=frozenset()) -> str:
    if not row.amount:
        return CATEGORY_OTHER
    incoming = row.amount > 0
    code = row.transaction_code or row.text_key
    parts = (row.transaction_key.split("-") + ["", "", ""])[:3]
    family, subfamily = parts[1], parts[2]
    swift_key = row.transaction_key[-3:] if len(row.transaction_key) == 4 else ""
    text = " ".join((row.transaction_text, row.transaction_key)).upper().translate(UMLAUTS)
    purpose = row.purpose.upper().translate(UMLAUTS)
    own_collection = bool(row.creditor_identifier) and row.creditor_identifier in own_creditor_ids

    if incoming:
        if "REJECT" in text or "RUECKGABE" in text or any(word in text for word in RETURN_WORDS):
            return CATEGORY_MANUAL_REVIEW
        if own_collection or code in DIRECT_DEBIT_CREDIT_CODES or family == "IDDT":
            return CATEGORY_DIRECT_DEBIT_CREDIT
        if "LASTSCHRIFT" in text or swift_key == "DDT" or BATCH_PATTERN.search(purpose):
            return CATEGORY_DIRECT_DEBIT_CREDIT
        if code in INCOMING_STANDING_ORDER_CODES or subfamily == "STDO" or swift_key == "STO" or "DAUERAUFTRAG" in text:
            return CATEGORY_INCOMING_STANDING_ORDER
        if family == "RRCT" or "ECHTZEIT" in text or "INSTANT" in text:
            return CATEGORY_INCOMING_INSTANT
        if code in INCOMING_TRANSFER_CODES or family == "RCDT" or "UEBERWEISUNG" in text or "GUTSCHRIFT" in text:
            return CATEGORY_INCOMING_TRANSFER
        return CATEGORY_INCOMING_TRANSFER if row.party_iban else CATEGORY_OTHER

    if "REJECT" in text or "REJECT" in purpose:
        return CATEGORY_DIRECT_DEBIT_REJECT
    if (
        code in DIRECT_DEBIT_RETURN_CODES
        or (family == "IDDT" and subfamily in RETURN_SUBFAMILIES)
        or swift_key == "RTI"
        or any(word in text or word in purpose for word in RETURN_WORDS)
        or own_collection
    ):
        return CATEGORY_DIRECT_DEBIT_RETURN
    if (
        code in BANK_FEE_CODES
        or subfamily in FEE_SUBFAMILIES
        or family in FEE_SUBFAMILIES
        or swift_key in {"CHG", "COM"}
    ):
        return CATEGORY_BANK_FEE
    if any(word in text for word in FEE_WORDS) or (not row.party_iban and any(word in purpose for word in FEE_WORDS)):
        return CATEGORY_BANK_FEE
    if (
        code in OUTGOING_DIRECT_DEBIT_CODES
        or family == "RDDT"
        or swift_key == "DDT"
        or row.mandate_reference
        or "LASTSCHRIFT" in text
    ):
        return CATEGORY_OUTGOING_DIRECT_DEBIT
    if code in OUTGOING_STANDING_ORDER_CODES or subfamily == "STDO" or swift_key == "STO" or "DAUERAUFTRAG" in text:
        return CATEGORY_OUTGOING_STANDING_ORDER
    if code in OUTGOING_TRANSFER_CODES or family in {"ICDT", "IRCT"} or "UEBERWEISUNG" in text:
        return CATEGORY_OUTGOING_TRANSFER
    return CATEGORY_OTHER


def parse_manifest(data: bytes | str) -> dict:
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data or b"")
    if not raw:
        raise BankStatementError("Das Manifest fehlt.")
    if len(raw) > MAX_MANIFEST_BYTES:
        raise BankStatementError("Das Manifest ist zu gross.")
    try:
        manifest = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise BankStatementError("Das Manifest ist kein gueltiges JSON.") from None
    if not isinstance(manifest, dict):
        raise BankStatementError("Das Manifest ist kein JSON-Objekt.")
    schema = normalize_text(manifest.get("schema"))
    if schema not in SCHEMA_PROFILES:
        raise BankStatementError("Das Schema des Manifests ist unbekannt.")
    request_id = normalize_text(manifest.get("request_id"))
    if not REQUEST_ID_PATTERN.fullmatch(request_id):
        raise BankStatementError("Die Anfrage-ID (request_id) fehlt oder ist ungueltig.")
    idempotency_key = normalize_text(manifest.get("idempotency_key")).lower()
    if not SHA256_PATTERN.fullmatch(idempotency_key):
        raise BankStatementError("Der Idempotenzschluessel (idempotency_key) fehlt oder ist ungueltig.")
    account_alias = normalize_text(manifest.get("account_alias")).lower()
    if not ACCOUNT_ALIAS_PATTERN.fullmatch(account_alias):
        raise BankStatementError("Der Kontoalias (account_alias) fehlt oder ist ungueltig.")
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    transactions = artifacts.get("transactions_csv") if isinstance(artifacts.get("transactions_csv"), dict) else {}
    sha256 = normalize_text(transactions.get("sha256")).lower()
    if not SHA256_PATTERN.fullmatch(sha256):
        raise BankStatementError("Die Pruefsumme der Umsatzdatei fehlt im Manifest.")
    period = manifest.get("period") if isinstance(manifest.get("period"), dict) else {}
    return {
        "schema": schema,
        "profile": SCHEMA_PROFILES[schema],
        "request_id": request_id,
        "idempotency_key": idempotency_key,
        "account_alias": account_alias,
        "sha256": sha256,
        "created_at": parse_manifest_datetime(manifest.get("created_at")),
        "period_from": parse_optional_date(period.get("from")),
        "period_to": parse_optional_date(period.get("to")),
        "overlap_days": parse_optional_int(period.get("overlap_days")),
        "manifest_json": json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1),
    }


def parse_manifest_datetime(value: object) -> datetime | None:
    text = normalize_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise BankStatementError("Der Zeitpunkt created_at im Manifest ist ungueltig.") from None
    return parsed


def parse_optional_date(value: object) -> date | None:
    if not normalize_text(value):
        return None
    try:
        return parse_date(value)
    except BankRowError:
        raise BankStatementError("Der Zeitraum im Manifest ist ungueltig.") from None


def parse_optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not isinstance(value, int | str) or not str(value).isdigit():
        raise BankStatementError("Die Ueberlappung im Manifest ist ungueltig.")
    return int(value)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
