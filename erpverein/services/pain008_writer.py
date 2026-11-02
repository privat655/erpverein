import re
import unicodedata
import warnings
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


PAIN_008_SCHEMA = "pain.008.001.08"
PAIN_008_NAMESPACE = f"urn:iso:std:iso:20022:tech:xsd:{PAIN_008_SCHEMA}"
XML_DECLARATION = b'<?xml version="1.0" encoding="UTF-8"?>\n'
MAX_NAME_LENGTH = 70
MAX_PURPOSE_LENGTH = 140
MAX_REFERENCE_LENGTH = 35
MAX_AMOUNT = Decimal("999999999.99")
SEQUENCE_TYPES = ("FRST", "RCUR", "OOFF", "FNAL")
SEPA_TEXT_CHARACTER = re.compile(r"[A-Za-z0-9/\-?:().,'+ ]")
SEPA_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9/\-?:().,'+]{1,35}$")
BIC_PATTERN = re.compile(r"^[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?$")
IBAN_PATTERN = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
GERMAN_TRANSLITERATION = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "Ä": "Ae",
    "Ö": "Oe",
    "Ü": "Ue",
    "ß": "ss",
    "&": "+",
}
EEA_COUNTRY_CODES = frozenset(
    {
        "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR", "GR", "HR", "HU", "IE",
        "IS", "IT", "LI", "LT", "LU", "LV", "MT", "NL", "NO", "PL", "PT", "RO", "SE", "SI", "SK",
    }
)


class Pain008Error(Exception):
    pass


def to_sepa_text(value: object, max_length: int) -> str:
    text = "".join(GERMAN_TRANSLITERATION.get(char, char) for char in str(value or ""))
    text = "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))
    text = "".join(char if SEPA_TEXT_CHARACTER.fullmatch(char) else " " for char in text)
    return " ".join(text.split())[:max_length].strip()


def is_sepa_reference(value: object) -> bool:
    value = str(value or "")
    return bool(SEPA_REFERENCE_PATTERN.fullmatch(value)) and not value.startswith("/") and not value.endswith("/") and "//" not in value


def is_sepa_text(value: object, max_length: int) -> bool:
    value = str(value or "")
    return bool(value) and len(value) <= max_length and value == to_sepa_text(value, max_length)


def is_eea_iban(iban: object) -> bool:
    return str(iban or "")[:2] in EEA_COUNTRY_CODES


def amount_to_cents(value: object) -> int:
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        raise Pain008Error("Ein Betrag ist ungueltig.") from None
    if amount <= 0 or amount > MAX_AMOUNT:
        raise Pain008Error("Ein Betrag muss groesser als 0 und hoechstens 999.999.999,99 EUR sein.")
    return int(amount * 100)


def build_pain008(message: dict) -> bytes:
    """Build and validate one pain.008.001.08 file with exactly one PmtInf block.

    message keys: msg_id, created_at, creditor_name, creditor_iban, creditor_bic (optional), creditor_id,
    pmtinf_id, collection_date, sequence_type, batch_booking and transactions (end_to_end_id, amount,
    mandate_id, mandate_date, debtor_name, debtor_iban, purpose).
    """
    validate_message(message)
    from sepaxml import SepaDD

    config = {
        "name": message["creditor_name"],
        "IBAN": message["creditor_iban"],
        "batch": True,
        "creditor_id": message["creditor_id"],
        "currency": "EUR",
        "instrument": "CORE",
    }
    if message.get("creditor_bic"):
        config["BIC"] = message["creditor_bic"]
    with warnings.catch_warnings():
        # sepaxml 2.7.0 tests ElementTree truth values, which Python deprecates.
        warnings.simplefilter("ignore", DeprecationWarning)
        sepa = SepaDD(config, schema=PAIN_008_SCHEMA, clean=False)
        for transaction in message["transactions"]:
            sepa.add_payment(
                {
                    "name": transaction["debtor_name"],
                    "IBAN": transaction["debtor_iban"],
                    "amount": amount_to_cents(transaction["amount"]),
                    "type": message["sequence_type"],
                    "collection_date": as_date(message["collection_date"]),
                    "mandate_id": transaction["mandate_id"],
                    "mandate_date": as_date(transaction["mandate_date"]),
                    "description": transaction["purpose"],
                    "endtoend_id": transaction["end_to_end_id"],
                }
            )
        raw = sepa.export(validate=False)

    root = ET.fromstring(raw)
    apply_message_details(root, message)
    ET.register_namespace("", PAIN_008_NAMESPACE)
    ET.indent(root, space="  ")
    xml = XML_DECLARATION + ET.tostring(root, encoding="utf-8")
    validate_pain008(xml, expected=message)
    return xml


def validate_message(message: dict) -> None:
    for key in ("msg_id", "pmtinf_id"):
        if not is_sepa_reference(message.get(key)):
            raise Pain008Error(f"{key} ist keine gueltige SEPA-Referenz.")
    if message.get("sequence_type") not in SEQUENCE_TYPES:
        raise Pain008Error("Der Sequenztyp ist ungueltig.")
    if not is_sepa_text(message.get("creditor_name"), MAX_NAME_LENGTH):
        raise Pain008Error("Der Name des Glaeubigers ist leer oder enthaelt unzulaessige Zeichen.")
    if not IBAN_PATTERN.fullmatch(str(message.get("creditor_iban") or "")):
        raise Pain008Error("Die IBAN des Vereinskontos ist ungueltig.")
    if message.get("creditor_bic") and not BIC_PATTERN.fullmatch(message["creditor_bic"]):
        raise Pain008Error("Die BIC des Vereinskontos ist ungueltig.")
    if not message.get("creditor_id") or not is_sepa_reference(message["creditor_id"]):
        raise Pain008Error("Die Glaeubiger-ID fehlt oder ist ungueltig.")
    transactions = message.get("transactions") or []
    if not transactions:
        raise Pain008Error("Die Datei braucht mindestens eine Lastschrift.")
    end_to_end_ids = [transaction.get("end_to_end_id") for transaction in transactions]
    if len(end_to_end_ids) != len(set(end_to_end_ids)):
        raise Pain008Error("Eine End-to-End-Referenz kommt mehrfach vor.")
    for index, transaction in enumerate(transactions, start=1):
        for key in ("end_to_end_id", "mandate_id"):
            if not is_sepa_reference(transaction.get(key)):
                raise Pain008Error(f"Lastschrift {index}: {key} ist keine gueltige SEPA-Referenz.")
        if not is_sepa_text(transaction.get("debtor_name"), MAX_NAME_LENGTH):
            raise Pain008Error(f"Lastschrift {index}: Der Name des Zahlers ist leer oder enthaelt unzulaessige Zeichen.")
        if not is_sepa_text(transaction.get("purpose"), MAX_PURPOSE_LENGTH):
            raise Pain008Error(f"Lastschrift {index}: Der Verwendungszweck ist leer oder enthaelt unzulaessige Zeichen.")
        iban = str(transaction.get("debtor_iban") or "")
        if not IBAN_PATTERN.fullmatch(iban) or not is_eea_iban(iban):
            raise Pain008Error(f"Lastschrift {index}: Die IBAN des Zahlers ist ungueltig oder liegt ausserhalb des EWR.")
        amount_to_cents(transaction.get("amount"))
        if as_date(transaction.get("mandate_date")) > as_date(message.get("collection_date")):
            raise Pain008Error(f"Lastschrift {index}: Das Mandatsdatum liegt nach dem Einzugsdatum.")


def apply_message_details(root, message: dict) -> None:
    initiation = root.find(qualified("CstmrDrctDbtInitn"))
    header = initiation.find(qualified("GrpHdr"))
    header.find(qualified("MsgId")).text = message["msg_id"]
    header.find(qualified("CreDtTm")).text = as_datetime(message["created_at"]).strftime("%Y-%m-%dT%H:%M:%S")
    initiating_party = header.find(qualified("InitgPty"))
    for child in list(initiating_party):
        if child.tag == qualified("Id"):
            initiating_party.remove(child)

    payment_info = initiation.findall(qualified("PmtInf"))
    if len(payment_info) != 1:
        raise Pain008Error("Die Datei muss genau einen PmtInf-Block enthalten.")
    payment_info[0].find(qualified("PmtInfId")).text = message["pmtinf_id"]
    payment_info[0].find(qualified("BtchBookg")).text = "true" if message["batch_booking"] else "false"
    for mandate in payment_info[0].iter(qualified("MndtRltdInf")):
        signature_date = mandate.find(qualified("DtOfSgntr"))
        amendment = ET.Element(qualified("AmdmntInd"))
        amendment.text = "false"
        mandate.insert(list(mandate).index(signature_date) + 1, amendment)
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag == qualified("PstlAdr") and not len(child):
                parent.remove(child)


def validate_pain008(xml: bytes, expected: dict | None = None) -> dict:
    from sepaxml.validation import ValidationError, try_valid_xml

    try:
        try_valid_xml(xml, PAIN_008_SCHEMA)
    except ValidationError as exc:
        raise Pain008Error("Die Datei entspricht nicht dem Schema pain.008.001.08.") from exc

    root = ET.fromstring(xml)
    initiation = root.find(qualified("CstmrDrctDbtInitn"))
    header = initiation.find(qualified("GrpHdr"))
    payment_info = initiation.findall(qualified("PmtInf"))
    if len(payment_info) != 1:
        raise Pain008Error("Die Datei muss genau einen PmtInf-Block enthalten.")
    transactions = payment_info[0].findall(qualified("DrctDbtTxInf"))
    amounts = [Decimal(node.text) for node in payment_info[0].iter(qualified("InstdAmt"))]
    total = sum(amounts, Decimal("0.00"))
    end_to_end_ids = [node.text for node in payment_info[0].iter(qualified("EndToEndId"))]
    for node, expected_count in ((header, len(transactions)), (payment_info[0], len(transactions))):
        if int(node.find(qualified("NbOfTxs")).text) != expected_count:
            raise Pain008Error("NbOfTxs stimmt nicht mit der Zahl der Lastschriften ueberein.")
        if Decimal(node.find(qualified("CtrlSum")).text) != total:
            raise Pain008Error("CtrlSum stimmt nicht mit der Summe der Lastschriften ueberein.")
    if len(end_to_end_ids) != len(set(end_to_end_ids)) or not all(is_sepa_reference(value) for value in end_to_end_ids):
        raise Pain008Error("Die End-to-End-Referenzen sind nicht eindeutig oder ungueltig.")
    if any(node.get("Ccy") != "EUR" for node in payment_info[0].iter(qualified("InstdAmt"))):
        raise Pain008Error("Alle Betraege muessen in EUR sein.")
    # The ISO schema leaves these optional; the DK rules (GBIC) require them.
    expected_codes = {
        "PmtMtd": "DD",
        "PmtTpInf/SvcLvl/Cd": "SEPA",
        "PmtTpInf/LclInstrm/Cd": "CORE",
        "ChrgBr": "SLEV",
        "CdtrSchmeId/Id/PrvtId/Othr/SchmeNm/Prtry": "SEPA",
    }
    for path, code in expected_codes.items():
        if required_text(payment_info[0], path) != code:
            raise Pain008Error(f"{path} muss {code} sein.")
    if required_text(payment_info[0], "PmtTpInf/SeqTp") not in SEQUENCE_TYPES:
        raise Pain008Error("Der Sequenztyp ist ungueltig.")
    for path in ("ReqdColltnDt", "Cdtr/Nm", "CdtrAcct/Id/IBAN", "CdtrSchmeId/Id/PrvtId/Othr/Id"):
        required_text(payment_info[0], path)
    for transaction in transactions:
        for path in ("DrctDbtTx/MndtRltdInf/MndtId", "DrctDbtTx/MndtRltdInf/DtOfSgntr", "Dbtr/Nm", "RmtInf/Ustrd"):
            required_text(transaction, path)
        if not is_sepa_reference(required_text(transaction, "DrctDbtTx/MndtRltdInf/MndtId")):
            raise Pain008Error("Eine Mandatsreferenz enthaelt unzulaessige Zeichen.")
        if not is_eea_iban(required_text(transaction, "DbtrAcct/Id/IBAN")):
            raise Pain008Error("Eine IBAN liegt ausserhalb des EWR.")
    if any(node.text != "false" for node in payment_info[0].iter(qualified("AmdmntInd"))) or len(
        list(payment_info[0].iter(qualified("AmdmntInd")))
    ) != len(transactions):
        raise Pain008Error("Jede Lastschrift braucht AmdmntInd = false.")
    for node in root.iter(qualified("Nm")):
        if not is_sepa_text(node.text, MAX_NAME_LENGTH):
            raise Pain008Error("Ein Name ist leer, zu lang oder enthaelt unzulaessige Zeichen.")
    for node in root.iter(qualified("Ustrd")):
        if not is_sepa_text(node.text, MAX_PURPOSE_LENGTH):
            raise Pain008Error("Ein Verwendungszweck ist leer, zu lang oder enthaelt unzulaessige Zeichen.")

    summary = {
        "msg_id": header.find(qualified("MsgId")).text,
        "pmtinf_id": payment_info[0].find(qualified("PmtInfId")).text,
        "batch_booking": payment_info[0].find(qualified("BtchBookg")).text == "true",
        "collection_date": payment_info[0].find(qualified("ReqdColltnDt")).text,
        "count": len(transactions),
        "total": total,
        "end_to_end_ids": end_to_end_ids,
    }
    if expected:
        expected_total = sum(
            (Decimal(amount_to_cents(transaction["amount"])) / 100 for transaction in expected["transactions"]),
            Decimal("0.00"),
        )
        if (
            summary["msg_id"] != expected["msg_id"]
            or summary["pmtinf_id"] != expected["pmtinf_id"]
            or summary["batch_booking"] != bool(expected["batch_booking"])
            or summary["collection_date"] != str(as_date(expected["collection_date"]))
            or summary["count"] != len(expected["transactions"])
            or summary["total"] != expected_total
            or summary["end_to_end_ids"] != [transaction["end_to_end_id"] for transaction in expected["transactions"]]
        ):
            raise Pain008Error("Die Datei weicht von den Daten des Einzugslaufs ab.")
    return summary


def qualified(tag: str) -> str:
    return f"{{{PAIN_008_NAMESPACE}}}{tag}"


def required_text(node, path: str) -> str:
    child = node.find("/".join(qualified(part) for part in path.split("/")))
    if child is None or not (child.text or "").strip():
        raise Pain008Error(f"In der Datei fehlt {path}.")
    return child.text.strip()


def as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def as_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
