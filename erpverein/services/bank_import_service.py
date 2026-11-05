import json

import frappe
from frappe import _
from frappe.utils import cint, convert_utc_to_system_timezone, flt, getdate, now_datetime, strip_html

from erpverein.custom_fields import (
    BANK_ACCOUNT_IMPORT_ALIAS_FIELDNAME,
    BANK_TRANSACTION_CATEGORY_FIELDNAME,
    BANK_TRANSACTION_CREDITOR_FIELDNAME,
    BANK_TRANSACTION_CUSTOMER_REFERENCE_FIELDNAME,
    BANK_TRANSACTION_END_TO_END_FIELDNAME,
    BANK_TRANSACTION_FINGERPRINT_FIELDNAME,
    BANK_TRANSACTION_IMPORT_BATCH_FIELDNAME,
    BANK_TRANSACTION_MANDATE_FIELDNAME,
    BANK_TRANSACTION_MATCH_STATUS_FIELDNAME,
    BANK_TRANSACTION_PRIMANOTA_FIELDNAME,
    BANK_TRANSACTION_SOURCE_ROW_FIELDNAME,
    BANK_TRANSACTION_TEXT_KEY_FIELDNAME,
    BANK_TRANSACTION_VALUE_DATE_FIELDNAME,
)
from erpverein.services.bank_statement_parser import (
    ACCOUNT_ALIAS_PATTERN,
    MAX_STATEMENT_BYTES,
    PARSER_VERSION,
    BankStatementError,
    align_reversed_parties,
    assign_fingerprints,
    classify_row,
    is_valid_iban,
    mask_sensitive_text,
    normalize_iban,
    normalize_text,
    parse_manifest,
    parse_statement,
    sha256_hex,
)
from erpverein.services.billing_common import is_retryable_error
from erpverein.services.sepa_collection_service import changed_fields
from erpverein.services.sepa_export_profile_service import get_active_export_profile


IMPORT_DOCTYPE = "ERPverein Bankimport"
STATUS_DRAFT = "Entwurf"
STATUS_IMPORTED = "Importiert"
STATUS_PARTIAL = "Teilfehler"
STATUS_FAILED = "Fehler"
RECONCILIATION_QUEUED = "Eingereiht"
RECONCILIATION_DONE = "Abgeschlossen"
RECONCILIATION_FAILED = "Fehler"
MATCH_STATUS_OPEN = "Offen"
UPDATE_FLAG = "erpverein_bank_import_update"
EDITABLE_FIELDS = {"notes"}
MAX_STORED_ERRORS = 200
MAX_DATA_LENGTH = 140


def assert_import_access() -> None:
    frappe.has_permission(IMPORT_DOCTYPE, "create", throw=True)
    for ptype in ("create", "submit"):
        frappe.has_permission("Bank Transaction", ptype, throw=True)


def import_bank_statement(manifest_data, statement_data: bytes | None) -> dict:
    """Import one AqBanking export sent by n8n: manifest and file unchanged, one call per retrieval."""
    assert_import_access()
    try:
        manifest = parse_manifest(manifest_data)
        if not statement_data:
            raise BankStatementError("Die Umsatzdatei fehlt.")
        if len(statement_data) > MAX_STATEMENT_BYTES:
            raise BankStatementError("Die Datei ist groesser als 5 MB.")
    except BankStatementError as exc:
        frappe.throw(_(str(exc)), title=_("Bankimport abgelehnt"))
    sha256 = sha256_hex(statement_data)
    if sha256 != manifest["sha256"]:
        frappe.throw(_("Die Pruefsumme der Umsatzdatei passt nicht zum Manifest. ERPverein hat nichts importiert."))
    existing = find_existing_import(manifest["idempotency_key"], sha256)
    if not existing:
        doc = create_import_record(manifest, sha256)
        if doc:
            save_source_file(doc, statement_data)
            return process_bank_import(doc, statement_data)
        # A locking read sees the record that a parallel call has just committed.
        existing = find_existing_import(manifest["idempotency_key"], sha256, for_update=True)
    return continue_existing_import(existing)


def find_existing_import(idempotency_key: str, sha256: str, for_update: bool = False) -> str | None:
    return frappe.db.get_value(
        IMPORT_DOCTYPE, {"idempotency_key": idempotency_key}, "name", for_update=for_update
    ) or frappe.db.get_value(IMPORT_DOCTYPE, {"source_file_sha256": sha256}, "name", for_update=for_update)


def continue_existing_import(name: str | None) -> dict:
    if not name:
        frappe.throw(_("Der Bankimport konnte nicht angelegt werden. Bitte erneut senden."))
    doc = get_locked_import(name, "read")
    if doc.status == STATUS_IMPORTED:
        return import_summary(doc, known=True)
    doc.check_permission("write")
    return process_bank_import(doc, read_source_file(doc))


def get_locked_import(name: str, ptype: str = "write"):
    if not frappe.db.get_value(IMPORT_DOCTYPE, name, "name", for_update=True):
        frappe.throw(_("Der Bankimport {0} existiert nicht.").format(name))
    doc = frappe.get_doc(IMPORT_DOCTYPE, name)
    doc.check_permission(ptype)
    return doc


def create_import_record(manifest: dict, sha256: str):
    savepoint = f"erpverein_bank_import_{frappe.generate_hash(length=12)}"
    frappe.db.savepoint(savepoint)
    message_count = len(frappe.local.message_log)
    doc = frappe.get_doc(
        {
            "doctype": IMPORT_DOCTYPE,
            "status": STATUS_DRAFT,
            "account_alias": manifest["account_alias"],
            "request_id": manifest["request_id"],
            "idempotency_key": manifest["idempotency_key"],
            "manifest_created_at": to_system_datetime(manifest["created_at"]),
            "period_from": manifest["period_from"],
            "period_to": manifest["period_to"],
            "overlap_days": manifest["overlap_days"],
            "import_profile": manifest["profile"],
            "parser_version": PARSER_VERSION,
            "source_file_sha256": sha256,
            "manifest_json": manifest["manifest_json"],
        }
    )
    doc.flags[UPDATE_FLAG] = True
    try:
        doc.insert()
    except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
        # A parallel call with the same file or key won the race; its record answers this call.
        frappe.db.rollback(save_point=savepoint)
        frappe.db.release_savepoint(savepoint)
        frappe.local.message_log = frappe.local.message_log[:message_count]
        return None
    frappe.db.release_savepoint(savepoint)
    return doc


def to_system_datetime(value):
    if not value:
        return None
    if value.tzinfo:
        return convert_utc_to_system_timezone(value).replace(tzinfo=None)
    return value


def save_source_file(doc, statement_data: bytes) -> None:
    file_doc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": f"{doc.name}-umsaetze.csv",
            "attached_to_doctype": IMPORT_DOCTYPE,
            "attached_to_name": doc.name,
            "attached_to_field": "source_file",
            "is_private": 1,
            "content": statement_data,
        }
    )
    file_doc.insert()
    doc.source_file = file_doc.file_url


def read_source_file(doc) -> bytes:
    file_name = frappe.db.get_value(
        "File",
        {"file_url": doc.source_file, "attached_to_doctype": IMPORT_DOCTYPE, "attached_to_name": doc.name},
        "name",
    )
    if not doc.source_file or not file_name:
        frappe.throw(_("Die Originaldatei des Bankimports {0} fehlt.").format(doc.name))
    # Read the raw bytes: File.get_content() decodes text and would change the checksum.
    with open(frappe.get_doc("File", file_name).get_full_path(), "rb") as handle:
        data = handle.read()
    if sha256_hex(data) != doc.source_file_sha256:
        frappe.throw(_("Die Originaldatei des Bankimports {0} wurde veraendert.").format(doc.name))
    return data


def process_bank_import(doc, statement_data: bytes) -> dict:
    doc.started_at = now_datetime()
    counters = frappe._dict(rows=0, duplicates=0, before_go_live=0, zero_amount=0)
    errors = []
    try:
        context = get_import_context(doc.account_alias)
        doc.update(
            {
                "company": context.company,
                "bank_account": context.bank_account,
                "currency": context.currency,
                "go_live_date": context.go_live_date,
            }
        )
        statement = parse_statement(statement_data, doc.import_profile)
        doc.source_encoding = statement.encoding
        doc.column_fill = json.dumps(statement.fill_counts, indent=1)
        counters.rows = statement.row_count
        errors += [(number, code, message) for number, code, message in statement.errors]
        foreign_rows = align_reversed_parties(statement.rows, context.iban)
        if foreign_rows:
            raise BankStatementError(
                _(
                    "Die Datei enthaelt Umsaetze eines anderen Kontos (Zeile {0}). ERPverein hat nichts importiert."
                ).format(", ".join(map(str, foreign_rows[:10])))
            )
    except BankStatementError as exc:
        return finish_import(doc, counters, errors, fatal=str(exc))

    assign_fingerprints(statement.rows, context.iban)
    for row in statement.rows:
        row.category = classify_row(row, context.creditor_ids)
        if row.booking_date < context.go_live_date:
            counters.before_go_live += 1
            continue
        if not row.amount:
            counters.zero_amount += 1
            continue
        outcome, message = import_row(doc, row, context)
        if outcome == "duplicate":
            counters.duplicates += 1
        elif outcome == "error":
            errors.append((row.source_row_number, "bankumsatz", message))
    return finish_import(doc, counters, errors)


def get_import_context(alias: str | None) -> frappe._dict:
    alias = normalize_text(alias).lower()
    names = (
        frappe.get_list("Bank Account", filters={BANK_ACCOUNT_IMPORT_ALIAS_FIELDNAME: alias}, pluck="name")
        if alias
        else []
    )
    if len(names) != 1:
        raise BankStatementError(
            _("Kein Firmen-Bankkonto hat den Bankimport-Alias {0}. Trage ihn am Vereinskonto ein.").format(alias or "-")
        )
    account = frappe.get_doc("Bank Account", names[0])
    account.check_permission("read")
    if not cint(account.is_company_account) or cint(account.disabled) or not account.account or not account.company:
        raise BankStatementError(
            _("Das Bankkonto {0} ist kein aktives Firmen-Bankkonto mit Hauptbuchkonto.").format(account.name)
        )
    iban = normalize_iban(account.iban)
    if not is_valid_iban(iban):
        raise BankStatementError(_("Das Bankkonto {0} braucht eine gueltige IBAN.").format(account.name))
    profile = get_active_export_profile(account.company)
    if not profile or not profile.go_live_date:
        raise BankStatementError(
            _("Fuer das Unternehmen {0} gibt es kein aktives SEPA-Exportprofil mit Go-live-Stichtag.").format(
                account.company
            )
        )
    return frappe._dict(
        bank_account=account.name,
        company=account.company,
        currency=frappe.db.get_value("Account", account.account, "account_currency"),
        iban=iban,
        go_live_date=getdate(profile.go_live_date),
        creditor_ids=frozenset({profile.creditor_identifier} - {None, ""}),
    )


def import_row(doc, row, context) -> tuple[str, str | None]:
    existing = frappe.db.get_value(
        "Bank Transaction",
        {BANK_TRANSACTION_FINGERPRINT_FIELDNAME: row.fingerprint},
        BANK_TRANSACTION_IMPORT_BATCH_FIELDNAME,
        as_dict=True,
    )
    if existing:
        return ("own" if existing.get(BANK_TRANSACTION_IMPORT_BATCH_FIELDNAME) == doc.name else "duplicate", None)
    savepoint = f"erpverein_bank_row_{frappe.generate_hash(length=12)}"
    frappe.db.savepoint(savepoint)
    message_count = len(frappe.local.message_log)
    try:
        transaction = frappe.get_doc(build_bank_transaction(doc, row, context))
        transaction.insert()
        transaction.submit()
    except Exception as exc:
        if is_retryable_error(exc):
            raise
        frappe.db.rollback(save_point=savepoint)
        frappe.db.release_savepoint(savepoint)
        frappe.local.message_log = frappe.local.message_log[:message_count]
        if isinstance(exc, (frappe.UniqueValidationError, frappe.DuplicateEntryError)):
            return "duplicate", None
        if not isinstance(exc, frappe.ValidationError):
            frappe.log_error(
                title=f"ERPverein Bankimport {doc.name}: Zeile {row.source_row_number}",
                message=mask_sensitive_text(frappe.get_traceback()),
            )
        return "error", mask_sensitive_text(strip_html(str(exc))) or exc.__class__.__name__
    frappe.db.release_savepoint(savepoint)
    return "created", None


def build_bank_transaction(doc, row, context) -> dict:
    amount = flt(row.amount, 2)
    return {
        "doctype": "Bank Transaction",
        "date": row.booking_date,
        "bank_account": context.bank_account,
        "company": context.company,
        "currency": context.currency,
        "deposit": amount if amount > 0 else 0,
        "withdrawal": -amount if amount < 0 else 0,
        "description": row.purpose or row.transaction_text or None,
        "reference_number": row.end_to_end_reference or row.customer_reference or None,
        "transaction_id": truncate(row.bank_transaction_id or row.bank_reference),
        "transaction_type": truncate(row.transaction_text or row.transaction_key),
        "bank_party_name": truncate(row.party_name),
        "bank_party_iban": row.party_iban or None,
        "bank_party_account_number": truncate(row.party_account),
        BANK_TRANSACTION_CATEGORY_FIELDNAME: row.category,
        BANK_TRANSACTION_MATCH_STATUS_FIELDNAME: MATCH_STATUS_OPEN,
        BANK_TRANSACTION_VALUE_DATE_FIELDNAME: row.value_date,
        BANK_TRANSACTION_END_TO_END_FIELDNAME: truncate(row.end_to_end_reference),
        BANK_TRANSACTION_MANDATE_FIELDNAME: truncate(row.mandate_reference),
        BANK_TRANSACTION_CREDITOR_FIELDNAME: truncate(row.creditor_identifier),
        BANK_TRANSACTION_CUSTOMER_REFERENCE_FIELDNAME: truncate(row.customer_reference),
        BANK_TRANSACTION_PRIMANOTA_FIELDNAME: truncate(row.primanota),
        BANK_TRANSACTION_TEXT_KEY_FIELDNAME: truncate(row.transaction_code or row.text_key),
        BANK_TRANSACTION_IMPORT_BATCH_FIELDNAME: doc.name,
        BANK_TRANSACTION_SOURCE_ROW_FIELDNAME: row.source_row_number,
        BANK_TRANSACTION_FINGERPRINT_FIELDNAME: row.fingerprint,
    }


def truncate(value: str | None) -> str | None:
    return value[:MAX_DATA_LENGTH] if value else None


def finish_import(doc, counters, errors: list[tuple], fatal: str | None = None) -> dict:
    transactions = frappe.get_list(
        "Bank Transaction",
        filters={BANK_TRANSACTION_IMPORT_BATCH_FIELDNAME: doc.name, "docstatus": 1},
        fields=["deposit", "withdrawal", "status"],
        limit_page_length=0,
    )
    doc.update(
        {
            "row_count": counters.rows,
            "new_transaction_count": len(transactions),
            "duplicate_count": counters.duplicates,
            "before_go_live_count": counters.before_go_live,
            "zero_amount_count": counters.zero_amount,
            "error_count": len(errors),
            "deposit_total": flt(sum(flt(row.deposit) for row in transactions), 2),
            "withdrawal_total": flt(sum(flt(row.withdrawal) for row in transactions), 2),
            "reconciled_count": sum(1 for row in transactions if row.status == "Reconciled"),
            "unreconciled_count": sum(1 for row in transactions if row.status == "Unreconciled"),
            "error_message": fatal,
            "completed_at": now_datetime(),
        }
    )
    doc.set(
        "import_errors",
        [
            {"row_number": number, "error_code": code, "message": message}
            for number, code, message in errors[:MAX_STORED_ERRORS]
        ],
    )
    if fatal or (
        errors and not (transactions or counters.duplicates or counters.before_go_live or counters.zero_amount)
    ):
        doc.status = STATUS_FAILED
    elif errors:
        doc.status = STATUS_PARTIAL
    else:
        doc.status = STATUS_IMPORTED
    queue_reconciliation = doc.status != STATUS_FAILED and bool(doc.bank_account)
    if queue_reconciliation:
        doc.update({"reconciliation_status": RECONCILIATION_QUEUED, "reconciliation_message": None, "reconciled_at": None})
    doc.flags[UPDATE_FLAG] = True
    doc.save()
    if queue_reconciliation:
        # Imported here to avoid a cycle: the settlement service records its result on the bank import.
        from erpverein.services.sepa_settlement_service import enqueue_bank_reconciliation

        enqueue_bank_reconciliation(doc.bank_account, doc.name)
    return import_summary(doc)


def import_summary(doc, known: bool = False) -> dict:
    # Counters only: the answer travels through n8n and must not carry bank data.
    return {
        "bank_import": doc.name,
        "status": doc.status,
        "request_id": doc.request_id,
        "known": known,
        "rows": cint(doc.row_count),
        "new": cint(doc.new_transaction_count),
        "duplicates": cint(doc.duplicate_count),
        "before_go_live": cint(doc.before_go_live_count),
        "zero_amount": cint(doc.zero_amount_count),
        "errors": cint(doc.error_count),
        "reconciled": cint(doc.reconciled_count),
        "open": cint(doc.unreconciled_count),
        "reconciliation": doc.reconciliation_status or None,
    }


def mark_reconciliation_queued(doc) -> None:
    doc.update({"reconciliation_status": RECONCILIATION_QUEUED, "reconciliation_message": None, "reconciled_at": None})
    doc.flags[UPDATE_FLAG] = True
    doc.save()


def record_reconciliation_result(name: str, status: str, message: str | None) -> None:
    doc = get_locked_import(name)
    transactions = frappe.get_list(
        "Bank Transaction",
        filters={BANK_TRANSACTION_IMPORT_BATCH_FIELDNAME: doc.name, "docstatus": 1},
        fields=["status"],
        limit_page_length=0,
    )
    doc.update(
        {
            "reconciliation_status": status,
            "reconciliation_message": message,
            "reconciled_at": now_datetime(),
            "reconciled_count": sum(1 for row in transactions if row.status == "Reconciled"),
            "unreconciled_count": sum(1 for row in transactions if row.status == "Unreconciled"),
        }
    )
    doc.flags[UPDATE_FLAG] = True
    doc.save()


def reprocess_bank_import(name: str) -> dict:
    assert_import_access()
    doc = get_locked_import(name)
    if doc.status == STATUS_IMPORTED:
        frappe.throw(_("Der Bankimport {0} ist bereits vollstaendig importiert.").format(doc.name))
    return process_bank_import(doc, read_source_file(doc))


def validate_bank_import(doc) -> None:
    if doc.flags.get(UPDATE_FLAG):
        return
    previous = doc.get_doc_before_save()
    if not previous:
        frappe.throw(_("Bankimporte entstehen nur ueber die Schnittstelle fuer n8n."))
    changed = changed_fields(previous, doc, EDITABLE_FIELDS)
    if changed:
        frappe.throw(_("Diese Felder aendert nur ERPverein: {0}").format(", ".join(changed)))


def validate_bank_import_alias(doc, method: str | None = None) -> None:
    alias = normalize_text(doc.get(BANK_ACCOUNT_IMPORT_ALIAS_FIELDNAME)).lower()
    doc.set(BANK_ACCOUNT_IMPORT_ALIAS_FIELDNAME, alias or None)
    if not alias:
        return
    if not ACCOUNT_ALIAS_PATTERN.fullmatch(alias):
        frappe.throw(
            _("Der Bankimport-Alias darf nur Kleinbuchstaben, Ziffern, Punkt, Bindestrich und Unterstrich enthalten.")
        )
    if not cint(doc.is_company_account):
        frappe.throw(_("Nur ein Firmen-Bankkonto bekommt einen Bankimport-Alias."))
