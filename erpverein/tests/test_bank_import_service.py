import hashlib
import json
from unittest.mock import patch

import frappe
from frappe.core.doctype.file.file import has_permission as file_has_permission
from frappe.tests import IntegrationTestCase
from frappe.utils import cint

from erpverein.custom_fields import sync_custom_fields
from erpverein.services import bank_import_service
from erpverein.services.bank_import_service import IMPORT_DOCTYPE, import_bank_statement, reprocess_bank_import
from erpverein.services.bank_statement_parser import TEMPLATE_V3_COLUMNS
from erpverein.setup_data import sync_setup_data
from erpverein.tests.collection_fixtures import (
    NEXT_YEAR,
    SYNTHETIC_CREDITOR_ID,
    ensure_export_profile,
    make_company_bank_account,
)
from erpverein.tests.test_payment_schedule_service import make_eur_customer, make_price_list, make_user_with_roles
from erpverein.tests.test_sepa_mandat_doctype import make_bank, make_german_iban


ALIAS = "verein-test"
V2_SCHEMA = "erpverein.aqbanking.listtrans-default.v2"
V3_SCHEMA = "erpverein.aqbanking.listtrans-template.v3"
JANUARY = f"15.01.{NEXT_YEAR}"
FEBRUARY = f"01.02.{NEXT_YEAR}"
BEFORE_GO_LIVE = f"30.12.{NEXT_YEAR - 1}"
TRANSACTION_FIELDS = [
    "name",
    "docstatus",
    "status",
    "date",
    "deposit",
    "withdrawal",
    "description",
    "reference_number",
    "transaction_id",
    "transaction_type",
    "party_type",
    "party",
    "bank_party_name",
    "bank_party_iban",
    "erpverein_bank_category",
    "erpverein_match_status",
    "erpverein_value_date",
    "erpverein_sepa_end_to_end_reference",
    "erpverein_sepa_mandate_reference",
    "erpverein_sepa_creditor_identifier",
    "erpverein_customer_reference",
    "erpverein_primanota",
    "erpverein_text_key",
    "erpverein_source_row_number",
    "erpverein_source_fingerprint",
]


class TestBankImportService(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()
        sync_setup_data()
        make_price_list()
        ensure_export_profile()
        cls.bank_account = make_company_bank_account()
        frappe.db.set_value("Bank Account", cls.bank_account, "erpverein_bank_import_alias", ALIAS)
        cls.club_iban = frappe.db.get_value("Bank Account", cls.bank_account, "iban")

    def test_same_statement_twice_creates_no_new_transactions_or_payments(self):
        tag = token()
        content = v2_file(
            v2_row(JANUARY, "350.00", f"Beitrag {tag}", make_german_iban()),
            v2_row(FEBRUARY, "-12.50", f"Entgelt Kontofuehrung {tag}"),
            v2_row(BEFORE_GO_LIVE, "99.00", f"Altjahr {tag}", make_german_iban()),
        )
        first = send(content)
        counts = (
            frappe.db.count("Bank Transaction"),
            frappe.db.count("Payment Entry"),
            frappe.db.count(IMPORT_DOCTYPE),
        )

        again = send(content)
        other_key = send(content, key=hashlib.sha256(f"other|{tag}".encode()).hexdigest())
        doc = frappe.get_doc(IMPORT_DOCTYPE, first["bank_import"])
        file_doc = frappe.get_doc("File", {"file_url": doc.source_file, "attached_to_name": doc.name})

        self.assertEqual(
            (
                first["status"],
                first["rows"],
                first["new"],
                first["before_go_live"],
                first["duplicates"],
                first["known"],
            ),
            ("Importiert", 3, 2, 1, 0, False),
        )
        self.assertEqual((again["bank_import"], again["known"], again["new"]), (doc.name, True, 2))
        self.assertEqual((other_key["bank_import"], other_key["known"]), (doc.name, True))
        self.assertEqual(
            (frappe.db.count("Bank Transaction"), frappe.db.count("Payment Entry"), frappe.db.count(IMPORT_DOCTYPE)),
            counts,
        )
        self.assertEqual((float(doc.deposit_total), float(doc.withdrawal_total)), (350.0, 12.5))
        self.assertEqual(
            (doc.bank_account, doc.import_profile, str(doc.go_live_date)),
            (self.bank_account, "aqbanking-listtrans-default-v2", f"{NEXT_YEAR}-01-01"),
        )
        self.assertEqual(doc.source_file_sha256, hashlib.sha256(content).hexdigest())
        self.assertTrue(cint(file_doc.is_private))
        self.assertEqual(bank_import_service.read_source_file(doc), content)
        self.assertEqual({row.status for row in transactions_for(doc.name)}, {"Unreconciled"})

    def test_overlapping_files_and_profile_switch_import_each_transaction_once(self):
        tag = token()
        payer = make_german_iban()
        twin = v2_row(JANUARY, "25.00", f"Miete {tag}", payer)
        first = send(v2_file(twin, twin))
        later_rows = [
            {
                "dateAsString": JANUARY,
                "valutaDateAsString": JANUARY,
                "valueAsString": "25.00",
                "remoteName": "Testzahler",
                "remoteIban": payer,
                "purposeInOneLine": f"Miete {tag}",
                "endToEndReference": f"E2E-{index}-{tag}",
            }
            for index in range(2)
        ]
        later_rows.append({**later_rows[0], "valueAsString": "30.00", "purposeInOneLine": f"Spende {tag}"})

        later = send(v3_file(later_rows), schema=V3_SCHEMA)
        created = transactions_for(later["bank_import"])

        self.assertEqual((first["new"], first["duplicates"]), (2, 0))
        self.assertEqual((later["status"], later["new"], later["duplicates"]), ("Importiert", 1, 2))
        self.assertEqual(len(created), 1)
        self.assertEqual((float(created[0].deposit), created[0].erpverein_source_row_number), (30.0, 4))
        self.assertEqual(created[0].reference_number, f"E2E-0-{tag}")

    def test_transactions_carry_bank_data_category_and_party(self):
        customer = make_eur_customer("Bankimport Partei")
        payer = make_german_iban()
        frappe.get_doc(
            {
                "doctype": "Bank Account",
                "account_name": f"Zahler {token()}",
                "bank": make_bank().name,
                "party_type": "Customer",
                "party": customer.name,
                "iban": payer,
            }
        ).insert(ignore_permissions=True)
        tag = token()
        rows = [
            {
                "fiId": f"FI-{tag}",
                "dateAsString": FEBRUARY,
                "valutaDateAsString": f"02.02.{NEXT_YEAR}",
                "valueAsString": "175.00",
                "localIban": self.club_iban,
                "remoteName": "Erika Test",
                "remoteIban": payer,
                "purposeInOneLine": f"Beitrag {tag}",
                "endToEndReference": f"E2E-{tag}",
                "customerReference": f"KREF-{tag}",
                "transactionKey": "PMNT-RCDT-ESCT",
                "transactionCode": "166",
                "primanota": "9301",
            },
            {
                "dateAsString": FEBRUARY,
                "valueAsString": "-175.00",
                "remoteIban": payer,
                "purposeInOneLine": f"Rueckgabe {tag}",
                "endToEndReference": f"SEPA-POS-{tag}",
                "mandateId": f"MR-{tag}",
                "creditorSchemeId": SYNTHETIC_CREDITOR_ID,
                "transactionText": "SEPA Lastschrift-Rückrechnung",
            },
        ]
        previous = frappe.db.get_single_value("Accounts Settings", "enable_party_matching")
        frappe.db.set_single_value("Accounts Settings", "enable_party_matching", 1)
        try:
            result = send(v3_file(rows), schema=V3_SCHEMA)
        finally:
            frappe.db.set_single_value("Accounts Settings", "enable_party_matching", previous)
        credit, debit = transactions_for(result["bank_import"])
        fill = json.loads(frappe.db.get_value(IMPORT_DOCTYPE, result["bank_import"], "column_fill"))

        self.assertEqual(
            (result["status"], result["new"], result["open"], result["reconciled"]), ("Importiert", 2, 2, 0)
        )
        self.assertEqual((fill["endToEndReference"], fill["mandateId"], fill["fiId"], fill["textKey"]), (2, 1, 1, 0))
        self.assertEqual((credit.docstatus, credit.status, float(credit.deposit)), (1, "Unreconciled", 175.0))
        self.assertEqual((credit.party_type, credit.party), ("Customer", customer.name))
        self.assertEqual((credit.bank_party_name, credit.bank_party_iban), ("Erika Test", payer))
        self.assertEqual((credit.transaction_id, credit.transaction_type), (f"FI-{tag}", "PMNT-RCDT-ESCT"))
        self.assertEqual((credit.reference_number, credit.description), (f"E2E-{tag}", f"Beitrag {tag}"))
        self.assertEqual(str(credit.erpverein_value_date), f"{NEXT_YEAR}-02-02")
        self.assertEqual(
            (credit.erpverein_bank_category, credit.erpverein_match_status), ("Ueberweisung eingehend", "Offen")
        )
        self.assertEqual(
            (credit.erpverein_customer_reference, credit.erpverein_primanota, credit.erpverein_text_key),
            (f"KREF-{tag}", "9301", "166"),
        )
        self.assertEqual((credit.erpverein_source_row_number, len(credit.erpverein_source_fingerprint)), (2, 64))
        self.assertEqual((float(debit.withdrawal), debit.erpverein_bank_category), (175.0, "Ruecklastschrift"))
        self.assertEqual(
            (
                debit.erpverein_sepa_end_to_end_reference,
                debit.erpverein_sepa_mandate_reference,
                debit.erpverein_sepa_creditor_identifier,
            ),
            (f"SEPA-POS-{tag}", f"MR-{tag}", SYNTHETIC_CREDITOR_ID),
        )
        self.assertEqual(debit.transaction_type, "SEPA Lastschrift-Rückrechnung")

    def test_broken_rows_are_reported_and_valid_rows_are_kept(self):
        tag = token()
        payer = make_german_iban()
        content = v2_file(
            v2_row(JANUARY, "10.00", f"Eins {tag}", payer),
            v2_row(JANUARY, "zehn", f"Zwei {tag}", payer),
            v2_row(JANUARY, "30.00", f"Drei {tag}", payer),
            v2_row(JANUARY, "0.00", f"Null {tag}", payer),
        )
        build = bank_import_service.build_bank_transaction

        def fail_third_row(doc, row, context):
            if row.purpose.startswith("Drei"):
                frappe.throw(f"Testfehler fuer {payer}")
            return build(doc, row, context)

        with patch.object(bank_import_service, "build_bank_transaction", side_effect=fail_third_row):
            result = send(content)
        doc = frappe.get_doc(IMPORT_DOCTYPE, result["bank_import"])
        messages = [row.message for row in doc.import_errors]

        self.assertEqual(
            (result["status"], result["new"], result["errors"], result["zero_amount"]), ("Teilfehler", 1, 2, 1)
        )
        self.assertEqual(
            [(row.row_number, row.error_code) for row in doc.import_errors], [(2, "betrag"), (3, "bankumsatz")]
        )
        self.assertNotIn(payer, json.dumps(messages))
        self.assertIn("**** ", messages[1])

        retried = reprocess_bank_import(doc.name)

        self.assertEqual(
            (retried["status"], retried["new"], retried["errors"], retried["duplicates"]), ("Teilfehler", 2, 1, 0)
        )
        self.assertEqual(len(transactions_for(doc.name)), 2)

    def test_wrong_checksum_or_manifest_is_rejected_without_a_record(self):
        content = v2_file(v2_row(JANUARY, "10.00", f"Pruefsumme {token()}", make_german_iban()))
        imports = frappe.db.count(IMPORT_DOCTYPE)

        with self.assertRaises(frappe.ValidationError):
            send(content, sha="0" * 64)
        with self.assertRaises(frappe.ValidationError):
            import_bank_statement("{}", content)
        with self.assertRaises(frappe.ValidationError):
            send(b"")

        self.assertEqual(frappe.db.count(IMPORT_DOCTYPE), imports)

    def test_returns_with_the_club_as_creditor_import_the_payer_as_party(self):
        # On a return AqBanking keeps the parties of the collection: the payer in localIban, the club in remoteIban.
        payer = make_german_iban()
        tag = token()
        row = {
            "dateAsString": FEBRUARY,
            "valueAsString": "-345.67",
            "localIban": payer,
            "remoteName": "Verein Test",
            "remoteIban": self.club_iban,
            "remoteBic": "HYVEDEMMXXX",
            "purposeInOneLine": f"Miete {tag}",
            "endToEndReference": f"E2E-R-{tag}",
            "mandateId": f"MR-{tag}",
            "transactionText": "SEPA Lastschrift-Rueckrechn",
            "transactionKey": "PMNT-IDDT-UPDD",
        }

        result = send(v3_file([row]), schema=V3_SCHEMA)
        (returned,) = transactions_for(result["bank_import"])

        self.assertEqual((result["status"], result["new"], result["errors"]), ("Importiert", 1, 0))
        self.assertEqual((returned.bank_party_iban, returned.bank_party_name), (payer, None))
        self.assertEqual(returned.erpverein_bank_category, "Ruecklastschrift")

    def test_unknown_alias_or_foreign_account_fail_and_can_be_reprocessed(self):
        tag = token()
        content = v2_file(v2_row(JANUARY, "10.00", f"Alias {tag}", make_german_iban()))
        unknown = send(content, alias="verein-unbekannt")
        resent = send(content, alias="verein-unbekannt")
        foreign = send(
            v2_file(v2_row(JANUARY, "10.00", f"Fremd {tag}", make_german_iban(), local_iban=make_german_iban()))
        )

        self.assertEqual((unknown["status"], unknown["new"]), ("Fehler", 0))
        self.assertEqual((resent["bank_import"], resent["status"]), (unknown["bank_import"], "Fehler"))
        self.assertIn("verein-unbekannt", frappe.db.get_value(IMPORT_DOCTYPE, unknown["bank_import"], "error_message"))
        self.assertEqual((foreign["status"], foreign["new"]), ("Fehler", 0))
        self.assertIn("anderen Kontos", frappe.db.get_value(IMPORT_DOCTYPE, foreign["bank_import"], "error_message"))

        frappe.db.set_value("Bank Account", self.bank_account, "erpverein_bank_import_alias", "verein-unbekannt")
        try:
            retried = reprocess_bank_import(unknown["bank_import"])
        finally:
            frappe.db.set_value("Bank Account", self.bank_account, "erpverein_bank_import_alias", ALIAS)

        self.assertEqual((retried["status"], retried["new"]), ("Importiert", 1))
        self.assertIsNone(frappe.db.get_value(IMPORT_DOCTYPE, unknown["bank_import"], "error_message"))
        with self.assertRaises(frappe.ValidationError):
            reprocess_bank_import(unknown["bank_import"])

    def test_user_without_accounts_role_cannot_import_or_read(self):
        result = send(v2_file(v2_row(JANUARY, "10.00", f"Lesen {token()}", make_german_iban())))
        content = v2_file(v2_row(JANUARY, "10.00", f"Rechte {token()}", make_german_iban()))
        imports = frappe.db.count(IMPORT_DOCTYPE)
        doc = frappe.get_doc(IMPORT_DOCTYPE, result["bank_import"])
        file_doc = frappe.get_doc("File", {"file_url": doc.source_file, "attached_to_name": doc.name})
        user = make_user_with_roles("System Manager", "Sales User", "Sales Master Manager")
        try:
            frappe.set_user(user.name)
            with self.assertRaises(frappe.PermissionError):
                send(content)
            with self.assertRaises(frappe.PermissionError):
                reprocess_bank_import(doc.name)
        finally:
            frappe.set_user("Administrator")

        self.assertEqual(frappe.db.count(IMPORT_DOCTYPE), imports)
        self.assertFalse(frappe.has_permission(IMPORT_DOCTYPE, "read", doc=doc.name, user=user.name))
        self.assertFalse(file_has_permission(file_doc, "read", user=user.name))

    def test_n8n_user_with_accounts_user_role_imports(self):
        content = v2_file(v2_row(FEBRUARY, "42.00", f"API {token()}", make_german_iban()))
        user = make_user_with_roles("Accounts User")
        try:
            frappe.set_user(user.name)
            result = send(content)
            again = send(content)
        finally:
            frappe.set_user("Administrator")
        doc = frappe.get_doc(IMPORT_DOCTYPE, result["bank_import"])

        self.assertEqual((result["status"], result["new"], again["known"]), ("Importiert", 1, True))
        self.assertEqual(doc.owner, user.name)
        self.assertEqual(
            frappe.db.get_value("Bank Transaction", transactions_for(doc.name)[0].name, "owner"), user.name
        )

    def test_bank_import_alias_is_validated(self):
        account = frappe.get_doc("Bank Account", self.bank_account)
        for alias in ("Verein Main", "verein/main", "ä-konto"):
            with self.subTest(alias=alias), self.assertRaises(frappe.ValidationError):
                account.erpverein_bank_import_alias = alias
                account.save()
        account.reload()
        party_account = frappe.get_doc(
            {
                "doctype": "Bank Account",
                "account_name": f"Zahler {token()}",
                "bank": make_bank().name,
                "party_type": "Customer",
                "party": make_eur_customer("Bankimport Alias").name,
                "iban": make_german_iban(),
                "erpverein_bank_import_alias": "zahler-konto",
            }
        )
        with self.assertRaises(frappe.ValidationError):
            party_account.insert(ignore_permissions=True)

        account.erpverein_bank_import_alias = " VEREIN-TEST "
        account.save()

        self.assertEqual(frappe.db.get_value("Bank Account", self.bank_account, "erpverein_bank_import_alias"), ALIAS)

    def test_bank_imports_are_changed_only_by_erpverein(self):
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc({"doctype": IMPORT_DOCTYPE, "account_alias": ALIAS}).insert()
        result = send(v2_file(v2_row(JANUARY, "10.00", f"Schutz {token()}", make_german_iban())))
        doc = frappe.get_doc(IMPORT_DOCTYPE, result["bank_import"])

        doc.notes = "Abruf geprueft"
        doc.save()
        doc.new_transaction_count = 99
        with self.assertRaises(frappe.ValidationError):
            doc.save()


def token() -> str:
    return frappe.generate_hash(length=10)


def v2_row(booking: str, amount: str, purpose: str, iban: str | None = None, local_iban: str = "") -> str:
    return "\t".join([booking, amount, "", "", local_iban, "Testzahler" if iban else "", iban or "", purpose])


def v2_file(*rows: str) -> bytes:
    return ("\n".join(rows) + "\n").encode("utf-8")


def v3_file(rows: list[dict]) -> bytes:
    lines = ["\t".join(TEMPLATE_V3_COLUMNS)]
    lines += ["\t".join(row.get(column, "") for column in TEMPLATE_V3_COLUMNS) for row in rows]
    return ("\n".join(lines) + "\n").encode("utf-8")


def send(
    content: bytes, schema: str = V2_SCHEMA, alias: str = ALIAS, key: str | None = None, sha: str | None = None
) -> dict:
    sha = sha or hashlib.sha256(content).hexdigest()
    key = key or hashlib.sha256(f"{schema}|{alias}|{sha}".encode()).hexdigest()
    manifest = {
        "schema": schema,
        "request_id": f"bank-test-{key[:16]}",
        "idempotency_key": key,
        "created_at": f"{NEXT_YEAR}-02-03T07:30:00Z",
        "account_alias": alias,
        "period": {"from": f"{NEXT_YEAR}-01-01", "to": f"{NEXT_YEAR}-02-03", "overlap_days": 14},
        "artifacts": {"transactions_csv": {"filename": "transactions.csv", "sha256": sha}},
    }
    return import_bank_statement(json.dumps(manifest), content)


def transactions_for(bank_import: str) -> list:
    return frappe.get_all(
        "Bank Transaction",
        filters={"erpverein_import_batch": bank_import},
        fields=TRANSACTION_FIELDS,
        order_by="erpverein_source_row_number asc",
    )
