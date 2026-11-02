import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal

from frappe.tests import UnitTestCase

from erpverein.services.pain008_writer import (
    PAIN_008_NAMESPACE,
    Pain008Error,
    amount_to_cents,
    build_pain008,
    is_sepa_reference,
    qualified,
    to_sepa_text,
    validate_pain008,
)


SYNTHETIC_CREDITOR_ID = "DE98ZZZ09999999999"
SYNTHETIC_CREDITOR_IBAN = "DE89370400440532013000"
SYNTHETIC_DEBTOR_IBAN = "DE02120300000000202051"


def make_message(**overrides) -> dict:
    message = {
        "msg_id": "SEPA-LAUF-2027-00001-270125101500",
        "created_at": datetime(2027, 1, 25, 10, 15),
        "creditor_name": "Testverein e.V.",
        "creditor_iban": SYNTHETIC_CREDITOR_IBAN,
        "creditor_bic": None,
        "creditor_id": SYNTHETIC_CREDITOR_ID,
        "pmtinf_id": "SEPA-LAUF-2027-00001",
        "collection_date": date(2027, 2, 1),
        "sequence_type": "RCUR",
        "batch_booking": 1,
        "transactions": [
            {
                "end_to_end_id": "SEPA-POS-2027-0000001",
                "amount": 175.0,
                "mandate_id": "MR-TEST-0001",
                "mandate_date": date(2016, 1, 1),
                "debtor_name": "Erika Mustermann",
                "debtor_iban": SYNTHETIC_DEBTOR_IBAN,
                "purpose": "Rechnung ACC-SINV-2027-00001",
            },
            {
                "end_to_end_id": "SEPA-POS-2027-0000002",
                "amount": 0.1 + 0.2,
                "mandate_id": "MR-TEST-0002",
                "mandate_date": "2020-05-01",
                "debtor_name": "Max Mustermann",
                "debtor_iban": SYNTHETIC_DEBTOR_IBAN,
                "purpose": "Rechnung ACC-SINV-2027-00002",
            },
        ],
    }
    message.update(overrides)
    return message


def texts(root, tag: str) -> list[str]:
    return [node.text for node in root.iter(qualified(tag))]


class TestPain008Writer(UnitTestCase):
    def test_text_is_converted_to_sepa_character_set(self):
        self.assertEqual(to_sepa_text("Jürgen Müller-Straße & Söhne", 70), "Juergen Mueller-Strasse + Soehne")
        self.assertEqual(to_sepa_text("Café  Ödön_€@", 70), "Cafe Oedoen")
        self.assertEqual(to_sepa_text("x" * 200, 140), "x" * 140)
        self.assertEqual(to_sepa_text(None, 70), "")

    def test_references_follow_sepa_rules(self):
        self.assertTrue(is_sepa_reference("SEPA-POS-2027-0000001"))
        self.assertTrue(is_sepa_reference("MR/2016/0001"))
        for value in ("", "MR 0001", "/MR-0001", "MR-0001/", "MR//0001", "MR_0001", "Ä-1", "X" * 36):
            with self.subTest(value=value):
                self.assertFalse(is_sepa_reference(value))

    def test_amounts_are_converted_to_cents(self):
        self.assertEqual(amount_to_cents(175), 17500)
        self.assertEqual(amount_to_cents(0.1 + 0.2), 30)
        self.assertEqual(amount_to_cents("12.345"), 1235)
        for value in (0, -1, "abc", Decimal("1000000000")):
            with self.subTest(value=value), self.assertRaises(Pain008Error):
                amount_to_cents(value)

    def test_file_is_schema_valid_and_matches_the_run(self):
        xml = build_pain008(make_message())
        root = ET.fromstring(xml)
        summary = validate_pain008(xml)

        self.assertTrue(xml.startswith(b'<?xml version="1.0" encoding="UTF-8"?>'))
        self.assertEqual(root.tag, f"{{{PAIN_008_NAMESPACE}}}Document")
        self.assertEqual(len(root.findall(f"{qualified('CstmrDrctDbtInitn')}/{qualified('PmtInf')}")), 1)
        self.assertEqual(texts(root, "MsgId"), ["SEPA-LAUF-2027-00001-270125101500"])
        self.assertEqual(texts(root, "CreDtTm"), ["2027-01-25T10:15:00"])
        self.assertEqual(texts(root, "PmtInfId"), ["SEPA-LAUF-2027-00001"])
        self.assertEqual(texts(root, "NbOfTxs"), ["2", "2"])
        self.assertEqual(texts(root, "CtrlSum"), ["175.30", "175.30"])
        self.assertEqual(texts(root, "BtchBookg"), ["true"])
        self.assertEqual(texts(root, "SeqTp"), ["RCUR"])
        self.assertEqual(root.find(f".//{qualified('LclInstrm')}/{qualified('Cd')}").text, "CORE")
        self.assertEqual(texts(root, "ReqdColltnDt"), ["2027-02-01"])
        self.assertEqual(texts(root, "EndToEndId"), ["SEPA-POS-2027-0000001", "SEPA-POS-2027-0000002"])
        self.assertEqual(texts(root, "AmdmntInd"), ["false", "false"])
        self.assertEqual(texts(root, "DtOfSgntr"), ["2016-01-01", "2020-05-01"])
        self.assertEqual(texts(root, "ChrgBr"), ["SLEV"])
        self.assertIn(SYNTHETIC_CREDITOR_ID, texts(root, "Id"))
        self.assertIsNone(root.find(f".//{qualified('InitgPty')}/{qualified('Id')}"))
        self.assertEqual(texts(root, "BICFI"), [])
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["total"], Decimal("175.30"))

    def test_single_booking_and_creditor_bic(self):
        xml = build_pain008(make_message(batch_booking=0, creditor_bic="COBADEFFXXX"))
        root = ET.fromstring(xml)

        self.assertEqual(texts(root, "BtchBookg"), ["false"])
        self.assertEqual(texts(root, "BICFI"), ["COBADEFFXXX"])
        self.assertEqual(len(root.findall(f".//{qualified('PmtInf')}")), 1)

    def test_invalid_input_is_rejected(self):
        message = make_message()
        duplicate = dict(message["transactions"][0])
        cases = {
            "duplicate end-to-end": make_message(transactions=[message["transactions"][0], duplicate]),
            "non-EEA IBAN": make_message(
                transactions=[{**message["transactions"][0], "debtor_iban": "CH9300762011623852957"}]
            ),
            "umlaut in name": make_message(transactions=[{**message["transactions"][0], "debtor_name": "Jürgen"}]),
            "space in mandate": make_message(transactions=[{**message["transactions"][0], "mandate_id": "MR 1"}]),
            "long purpose": make_message(transactions=[{**message["transactions"][0], "purpose": "x" * 141}]),
            "mandate after collection": make_message(
                transactions=[{**message["transactions"][0], "mandate_date": date(2027, 2, 2)}]
            ),
            "no transactions": make_message(transactions=[]),
            "bad sequence": make_message(sequence_type="XXXX"),
            "missing creditor id": make_message(creditor_id=None),
            "long msg id": make_message(msg_id="X" * 36),
        }
        for label, value in cases.items():
            with self.subTest(label=label), self.assertRaises(Pain008Error):
                build_pain008(value)

    def test_validation_rejects_schema_errors_and_wrong_totals(self):
        xml = build_pain008(make_message())

        without_mandate = xml.replace(b"<MndtId>MR-TEST-0001</MndtId>", b"")
        with self.assertRaises(Pain008Error):
            validate_pain008(without_mandate)
        wrong_sum = xml.replace(b"<CtrlSum>175.30</CtrlSum>", b"<CtrlSum>175.31</CtrlSum>", 1)
        with self.assertRaises(Pain008Error):
            validate_pain008(wrong_sum)
        with self.assertRaises(Pain008Error):
            validate_pain008(xml, expected=make_message(pmtinf_id="SEPA-LAUF-2027-00002"))
        with self.assertRaises(Pain008Error):
            validate_pain008(xml.replace(b"Max Mustermann", "Jürgen Müller".encode()))
        with self.assertRaises(Pain008Error):
            validate_pain008(xml.replace(b"<Cd>CORE</Cd>", b"<Cd>B2B</Cd>"))
