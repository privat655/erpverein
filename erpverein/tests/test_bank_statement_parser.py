import json
from datetime import date
from decimal import Decimal

from frappe.tests import UnitTestCase

from erpverein.services.bank_statement_parser import (
    CATEGORY_BANK_FEE,
    CATEGORY_DIRECT_DEBIT_CREDIT,
    CATEGORY_DIRECT_DEBIT_REJECT,
    CATEGORY_DIRECT_DEBIT_RETURN,
    CATEGORY_INCOMING_STANDING_ORDER,
    CATEGORY_INCOMING_TRANSFER,
    CATEGORY_MANUAL_REVIEW,
    CATEGORY_OTHER,
    CATEGORY_OUTGOING_DIRECT_DEBIT,
    CATEGORY_OUTGOING_TRANSFER,
    PROFILE_DEFAULT_V2,
    PROFILE_TEMPLATE_V3,
    TEMPLATE_V3_COLUMNS,
    V3_TEMPLATE,
    BankRowError,
    BankStatementError,
    StatementRow,
    align_reversed_parties,
    assign_fingerprints,
    classify_row,
    mask_sensitive_text,
    parse_amount,
    parse_date,
    parse_manifest,
    parse_statement,
    sha256_hex,
)


# Example IBANs from public documentation, never real accounts.
CLUB_IBAN = "DE89370400440532013000"
PAYER_IBAN = "DE02120300000000202051"
OTHER_IBAN = "DE02100100100006820101"
CREDITOR_ID = "DE98ZZZ09999999999"


def v2_line(*values: str) -> str:
    booking, amount, name, iban, purpose = values
    return "\t".join([booking, amount, "", "", CLUB_IBAN, name, iban, purpose])


def v3_file(rows: list[dict], columns=TEMPLATE_V3_COLUMNS) -> bytes:
    lines = ["\t".join(columns)]
    lines += ["\t".join(row.get(column, "") for column in columns) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def statement_row(amount: str, **values) -> StatementRow:
    return StatementRow(
        source_row_number=1, booking_date=date(2027, 2, 1), value_date=None, amount=Decimal(amount), **values
    )


def manifest(**overrides) -> dict:
    data = {
        "schema": "erpverein.aqbanking.listtrans-default.v2",
        "request_id": "bank-2027-02-03-0123456789abcdef",
        "idempotency_key": "a" * 64,
        "created_at": "2027-02-03T07:30:00Z",
        "account_alias": "verein-main",
        "period": {"from": "2027-01-20", "to": "2027-02-03", "overlap_days": 14},
        "artifacts": {"transactions_csv": {"filename": "transactions.csv", "sha256": "b" * 64}},
    }
    data.update(overrides)
    return data


class TestBankStatementParser(UnitTestCase):
    def test_default_template_rows_are_parsed(self):
        content = "\n".join(
            [
                v2_line("01.02.2027", "350.00", "Erika Mustermann", PAYER_IBAN, "Beitrag 2027 ACC-SINV-2027-00001"),
                v2_line("02.02.2027", "-12.50", "", "", "Entgelt Kontofuehrung"),
                "",
            ]
        ).encode()

        statement = parse_statement(content, PROFILE_DEFAULT_V2)

        self.assertEqual((statement.encoding, statement.row_count, statement.errors), ("utf-8-sig", 2, []))
        first, second = statement.rows
        self.assertEqual(
            (first.source_row_number, first.booking_date, first.amount), (1, date(2027, 2, 1), Decimal("350.00"))
        )
        self.assertEqual(
            (first.party_name, first.party_iban, first.local_iban), ("Erika Mustermann", PAYER_IBAN, CLUB_IBAN)
        )
        self.assertEqual(first.purpose, "Beitrag 2027 ACC-SINV-2027-00001")
        self.assertIsNone(first.value_date)
        self.assertEqual((second.amount, second.party_iban, second.end_to_end_reference), (Decimal("-12.50"), "", ""))

    def test_template_v3_reads_columns_by_header(self):
        columns = ("valueAsString", "", "dateAsString", "valutaDateAsString", "remoteIban", "endToEndReference")
        columns += (
            "mandateId",
            "creditorSchemeId",
            "customerReference",
            "transactionKey",
            "transactionCode",
            "unknown",
        )
        content = v3_file(
            [
                {
                    "valueAsString": "-175.00",
                    "": "ignored",
                    "dateAsString": "03.02.2027",
                    "valutaDateAsString": "01.02.2027",
                    "remoteIban": PAYER_IBAN,
                    "endToEndReference": "SEPA-POS-2027-0000001",
                    "mandateId": "MR-0001",
                    "creditorSchemeId": "de98 zzz0 9999 9999 99",
                    "customerReference": "NOTPROVIDED",
                    "transactionKey": "pmnt-iddt-updd",
                    "transactionCode": "0",
                    "unknown": "x",
                }
            ],
            columns,
        )

        row = parse_statement(content, PROFILE_TEMPLATE_V3).rows[0]

        self.assertEqual(
            (row.source_row_number, row.booking_date, row.value_date), (2, date(2027, 2, 3), date(2027, 2, 1))
        )
        self.assertEqual((row.end_to_end_reference, row.mandate_reference), ("SEPA-POS-2027-0000001", "MR-0001"))
        self.assertEqual((row.creditor_identifier, row.customer_reference), (CREDITOR_ID, ""))
        self.assertEqual((row.transaction_key, row.transaction_code), ("PMNT-IDDT-UPDD", ""))
        fill_counts = parse_statement(content, PROFILE_TEMPLATE_V3).fill_counts
        self.assertEqual(
            (fill_counts["endToEndReference"], fill_counts["transactionCode"], fill_counts["unknown"]), (1, 0, 1)
        )
        self.assertNotIn("", fill_counts)

    def test_amounts_accept_aqbanking_and_german_formats(self):
        cases = {
            "1234.56": "1234.56",
            "-12.5": "-12.50",
            "+5.00": "5.00",
            "1.234,56": "1234.56",
            "-1.234,56": "-1234.56",
            "12,50-": "-12.50",
            "1 234,56 EUR": "1234.56",
            "0.00": "0.00",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_amount(text), Decimal(expected))
        for text in ("", "abc", "12.345", "1,2,3", "12.34.56"):
            with self.subTest(text=text), self.assertRaises(BankRowError):
                parse_amount(text)

    def test_dates_accept_german_iso_and_datetime_values(self):
        for text in ("02.01.2027", "2.1.2027", "2027-01-02", "2027-01-02T10:15:00Z", "02.01.2027 10:15", "20270102"):
            with self.subTest(text=text):
                self.assertEqual(parse_date(text), date(2027, 1, 2))
        for text in ("", "31.02.2027", "2027/01/02", "02.01.27"):
            with self.subTest(text=text), self.assertRaises(BankRowError):
                parse_date(text)

    def test_broken_rows_become_errors_and_valid_rows_stay(self):
        content = "\n".join(
            [
                v2_line("01.02.2027", "10.00", "A", PAYER_IBAN, "eins"),
                "01.02.2027\t10.00\tzu wenig",
                v2_line("01.02.2027", "zehn", "B", PAYER_IBAN, "drei"),
                v2_line("30.02.2027", "10.00", "C", PAYER_IBAN, "vier"),
            ]
        ).encode()

        statement = parse_statement(content, PROFILE_DEFAULT_V2)

        self.assertEqual([row.source_row_number for row in statement.rows], [1])
        self.assertEqual(
            [(number, code) for number, code, _message in statement.errors],
            [(2, "spaltenzahl"), (3, "betrag"), (4, "datum")],
        )
        self.assertEqual(statement.row_count, 4)
        self.assertNotIn(PAYER_IBAN, json.dumps(statement.errors))

    def test_texts_are_normalized_and_invalid_ibans_move_to_the_account_number(self):
        content = v2_line(
            "01.02.2027", "5.00", " Erika​  Mustermann ", "DE00 1234 5678", "Beitrag  2027  Haus A"
        ).encode()

        row = parse_statement(content, PROFILE_DEFAULT_V2).rows[0]

        self.assertEqual((row.party_name, row.purpose), ("Erika Mustermann", "Beitrag 2027 Haus A"))
        self.assertEqual((row.party_iban, row.party_account), ("", "DE0012345678"))

    def test_file_level_problems_stop_the_import(self):
        with self.assertRaises(BankStatementError):
            parse_statement(b"", PROFILE_DEFAULT_V2)
        with self.assertRaises(BankStatementError):
            parse_statement(b"x", "csv-default")
        with self.assertRaises(BankStatementError):
            parse_statement(b"fiId\tremoteName\n1\tA\n", PROFILE_TEMPLATE_V3)

    def test_windows_encoded_files_are_read(self):
        content = v2_line("01.02.2027", "5.00", "Jürgen", PAYER_IBAN, "Beitrag Müller").encode("cp1252")

        statement = parse_statement(content, PROFILE_DEFAULT_V2)

        self.assertEqual(statement.encoding, "cp1252")
        self.assertEqual(statement.rows[0].purpose, "Beitrag Müller")

    def test_fingerprint_is_stable_across_profiles_and_counts_identical_rows(self):
        twin = v2_line("01.02.2027", "25.00", "Erika Mustermann", PAYER_IBAN, "Miete Februar")
        first = parse_statement(f"{twin}\n{twin}\n".encode(), PROFILE_DEFAULT_V2).rows
        later_rows = [
            {
                "dateAsString": "01.02.2027",
                "valutaDateAsString": "01.02.2027",
                "valueAsString": "25.00",
                "remoteName": "erika  mustermann",
                "remoteIban": PAYER_IBAN,
                "purposeInOneLine": "Miete  Februar",
                "endToEndReference": f"E2E-{index}",
            }
            for index in range(3)
        ]
        later = parse_statement(v3_file(later_rows), PROFILE_TEMPLATE_V3).rows
        other_account = parse_statement(f"{twin}\n".encode(), PROFILE_DEFAULT_V2).rows

        assign_fingerprints(first, CLUB_IBAN)
        assign_fingerprints(later, CLUB_IBAN)
        assign_fingerprints(other_account, OTHER_IBAN)

        self.assertNotEqual(first[0].fingerprint, first[1].fingerprint)
        self.assertEqual([row.fingerprint for row in later[:2]], [row.fingerprint for row in first])
        self.assertNotIn(later[2].fingerprint, {row.fingerprint for row in first})
        self.assertNotEqual(other_account[0].fingerprint, first[0].fingerprint)
        self.assertEqual(len(first[0].fingerprint), 64)

    def test_reversed_bookings_get_the_payer_as_party_and_foreign_rows_are_listed(self):
        # A return keeps the collection's parties: AqBanking puts the payer in localIban and the club in remoteIban.
        returned, credit, fee, foreign = parse_statement(
            v3_file(
                [
                    {
                        "dateAsString": "04.02.2027",
                        "valueAsString": "-345.67",
                        "localIban": PAYER_IBAN,
                        "remoteName": "Verein",
                        "remoteIban": CLUB_IBAN,
                        "remoteBic": "HYVEDEMMXXX",
                        "transactionText": "SEPA Lastschrift-Rueckrechn",
                        "transactionKey": "PMNT-IDDT-UPDD",
                    },
                    {
                        "dateAsString": "04.02.2027",
                        "valueAsString": "30.00",
                        "localIban": CLUB_IBAN,
                        "remoteName": "Erika Mustermann",
                        "remoteIban": PAYER_IBAN,
                    },
                    {"dateAsString": "04.02.2027", "valueAsString": "-34.20", "localIban": CLUB_IBAN},
                    {
                        "dateAsString": "04.02.2027",
                        "valueAsString": "12.00",
                        "localIban": OTHER_IBAN,
                        "remoteIban": PAYER_IBAN,
                    },
                ]
            ),
            PROFILE_TEMPLATE_V3,
        ).rows

        self.assertEqual(align_reversed_parties([returned, credit, fee, foreign], CLUB_IBAN), [5])
        self.assertEqual(
            (returned.party_iban, returned.party_name, returned.party_bic, returned.local_iban),
            (PAYER_IBAN, "", "", CLUB_IBAN),
        )
        self.assertEqual((credit.party_iban, credit.party_name), (PAYER_IBAN, "Erika Mustermann"))
        self.assertEqual((fee.party_iban, fee.local_iban), ("", CLUB_IBAN))
        self.assertEqual((foreign.party_iban, foreign.local_iban), (PAYER_IBAN, OTHER_IBAN))
        self.assertEqual(classify_row(returned), CATEGORY_DIRECT_DEBIT_RETURN)

    def test_classification_uses_codes_keywords_and_sign(self):
        cases = [
            (statement_row("350.00", party_iban=PAYER_IBAN), CATEGORY_INCOMING_TRANSFER),
            (statement_row("350.00", transaction_key="PMNT-RCDT-STDO"), CATEGORY_INCOMING_STANDING_ORDER),
            (statement_row("700.00", transaction_text="SEPA Basislastschrift E.v."), CATEGORY_DIRECT_DEBIT_CREDIT),
            (statement_row("700.00", purpose="POSTEN 12 DATEI 0815"), CATEGORY_DIRECT_DEBIT_CREDIT),
            (statement_row("700.00", transaction_code="192"), CATEGORY_DIRECT_DEBIT_CREDIT),
            (statement_row("175.00", creditor_identifier=CREDITOR_ID), CATEGORY_DIRECT_DEBIT_CREDIT),
            (statement_row("-178.00", transaction_text="SEPA Lastschrift-Rückrechnung"), CATEGORY_DIRECT_DEBIT_RETURN),
            (statement_row("-175.00", transaction_key="PMNT-IDDT-UPDD"), CATEGORY_DIRECT_DEBIT_RETURN),
            (statement_row("-175.00", transaction_text="SEPA Reject"), CATEGORY_DIRECT_DEBIT_REJECT),
            (
                statement_row("-175.00", creditor_identifier=CREDITOR_ID, mandate_reference="MR-1"),
                CATEGORY_DIRECT_DEBIT_RETURN,
            ),
            (statement_row("-12.50", purpose="Entgelt Kontofuehrung"), CATEGORY_BANK_FEE),
            (statement_row("-12.50", transaction_key="NCHG"), CATEGORY_BANK_FEE),
            (
                statement_row("-49.90", mandate_reference="FREMD-1", party_iban=OTHER_IBAN),
                CATEGORY_OUTGOING_DIRECT_DEBIT,
            ),
            (
                statement_row("-120.00", transaction_key="PMNT-ICDT-ESCT", party_iban=OTHER_IBAN),
                CATEGORY_OUTGOING_TRANSFER,
            ),
            (statement_row("-120.00", purpose="Entgelt Hallenmiete", party_iban=OTHER_IBAN), CATEGORY_OTHER),
            (statement_row("20.00", transaction_text="SEPA Lastschrift Rückgabe"), CATEGORY_MANUAL_REVIEW),
            (statement_row("20.00"), CATEGORY_OTHER),
        ]
        for row, expected in cases:
            with self.subTest(row=row):
                self.assertEqual(classify_row(row, frozenset({CREDITOR_ID})), expected)

    def test_manifest_is_validated_and_normalized(self):
        result = parse_manifest(json.dumps(manifest(account_alias="Verein-Main", idempotency_key="A" * 64)))

        self.assertEqual(
            (result["profile"], result["account_alias"], result["idempotency_key"]),
            (PROFILE_DEFAULT_V2, "verein-main", "a" * 64),
        )
        self.assertEqual(
            (result["period_from"], result["period_to"], result["overlap_days"]),
            (date(2027, 1, 20), date(2027, 2, 3), 14),
        )
        self.assertEqual(result["created_at"].utcoffset().total_seconds(), 0)
        self.assertEqual(result["sha256"], "b" * 64)
        invalid = [
            b"",
            b"[]",
            b"{no json",
            json.dumps(manifest(schema="erpverein.aqbanking.listtrans-default.v1")),
            json.dumps(manifest(idempotency_key="1234")),
            json.dumps(manifest(account_alias="Verein Main")),
            json.dumps(manifest(request_id="")),
            json.dumps(manifest(artifacts={})),
            json.dumps(manifest(period={"from": "gestern"})),
        ]
        for data in invalid:
            with self.subTest(data=data), self.assertRaises(BankStatementError):
                parse_manifest(data)

    def test_sensitive_values_are_masked(self):
        message = f"'{PAYER_IBAN}' is not a valid IBAN, account DE02 1203 0000 0000 2020 51"

        masked = mask_sensitive_text(message)

        self.assertNotIn(PAYER_IBAN, masked.replace(" ", ""))
        self.assertIn("**** 2051", masked)

    def test_v3_template_lists_every_header_column(self):
        self.assertEqual(V3_TEMPLATE.split("\t"), [f"$({column})" for column in TEMPLATE_V3_COLUMNS])
        self.assertEqual(len(sha256_hex(b"")), 64)
