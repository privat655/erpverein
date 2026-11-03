import json
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, fmt_money, formatdate, getdate, now_datetime, nowdate, strip_html

from erpverein.custom_fields import (
    BANK_TRANSACTION_CATEGORY_FIELDNAME,
    BANK_TRANSACTION_CUSTOMER_REFERENCE_FIELDNAME,
    BANK_TRANSACTION_END_TO_END_FIELDNAME,
    BANK_TRANSACTION_MATCH_NOTE_FIELDNAME,
    BANK_TRANSACTION_MATCH_STATUS_FIELDNAME,
    PAYMENT_ENTRY_BANK_TRANSACTION_FIELDNAME,
    PAYMENT_ENTRY_COLLECTION_ITEM_FIELDNAME,
    PAYMENT_ENTRY_TRANSFER_KEY_FIELDNAME,
)
from erpverein.services import bank_import_service
from erpverein.services.bank_statement_parser import mask_sensitive_text
from erpverein.services.billing_common import is_retryable_error
from erpverein.services.payment_schedule_service import AMOUNT_TOLERANCE
from erpverein.services.sepa_collection_service import (
    POSITION_DOCTYPE,
    POSITION_STATUS_DONE,
    POSITION_STATUS_EXPORTED,
    POSITION_STATUS_PAID,
    POSITION_STATUS_REVIEW,
    RUN_DOCTYPE,
    RUN_STATUS_BANK_REFERENCE,
    RUN_STATUS_CREDIT_RECOGNIZED,
    RUN_STATUS_EXPORTED,
    RUN_STATUS_REVIEW,
    RUN_STATUS_SETTLED,
    SETTLEMENT_RUN_STATUSES,
    UPDATE_FLAG,
    get_active_position_docs,
    get_locked_run,
    has_accounts_role,
    require_reason,
    save_position,
    save_run_totals,
)
from erpverein.services.sepa_settlement_matcher import (
    ACTION_REVIEW,
    ACTION_SETTLE,
    Credit,
    Position,
    Run,
    match_position_credit,
    match_run_credits,
    to_amount,
)
from erpverein.setup_data import MODE_OF_PAYMENT_DIRECT_DEBIT


MATCH_STATUS_OPEN = bank_import_service.MATCH_STATUS_OPEN
MATCH_STATUS_MATCHED = "Abgeglichen"
MATCH_STATUS_REVIEW = "Manuelle Pruefung"
EVALUATED_MATCH_STATUSES = (MATCH_STATUS_OPEN, MATCH_STATUS_REVIEW)
# Runs that may still receive their batch credit, and runs whose settlement has started.
BATCH_RUN_STATUSES = (RUN_STATUS_EXPORTED, RUN_STATUS_BANK_REFERENCE, RUN_STATUS_REVIEW)
RESTART_RUN_STATUSES = (RUN_STATUS_CREDIT_RECOGNIZED, RUN_STATUS_REVIEW)
OPEN_POSITION_STATUSES = (POSITION_STATUS_EXPORTED, POSITION_STATUS_REVIEW)
# A position is settled when it is paid or when the treasurer marked a review case as done.
SETTLED_POSITION_STATUSES = (POSITION_STATUS_PAID, POSITION_STATUS_DONE)
RECONCILIATION_PERMISSIONS = (
    (RUN_DOCTYPE, "write"),
    (POSITION_DOCTYPE, "write"),
    ("Bank Transaction", "write"),
    ("Bank Transaction", "submit"),
    ("Payment Entry", "create"),
    ("Payment Entry", "submit"),
    ("Sales Invoice", "read"),
    ("Customer", "read"),
    ("Bank Account", "read"),
    ("Mitglied", "read"),
    ("Mieter", "read"),
)
CREDIT_FIELDS = [
    "name",
    "date",
    "deposit",
    "description",
    "reference_number",
    "transaction_type",
    BANK_TRANSACTION_CATEGORY_FIELDNAME,
    BANK_TRANSACTION_CUSTOMER_REFERENCE_FIELDNAME,
    BANK_TRANSACTION_END_TO_END_FIELDNAME,
]
POSITION_FIELDS = [
    "name",
    "collection_run",
    "status",
    "amount",
    "collection_date",
    "payment_entry",
    "settlement_bank_transaction",
]
RECONCILIATION_TIMEOUT = 3600
SETTLEMENT_FLAG = "erpverein_sepa_settlement"
MAX_NOTE_LENGTH = 1000


def assert_reconciliation_access() -> None:
    # The job runs with the rights of the user who owns the API key, like every other ERPverein action.
    if not has_accounts_role():
        frappe.throw(_("Der Abgleich braucht die Rolle Accounts User oder Accounts Manager."), frappe.PermissionError)
    for doctype, ptype in RECONCILIATION_PERMISSIONS:
        frappe.has_permission(doctype, ptype, throw=True)


def reconciliation_job_id(key: str) -> str:
    return "erpverein-bank-reconcile-" + re.sub(r"[^a-z0-9]+", "-", key.lower()).strip("-")


def enqueue_bank_reconciliation(bank_account: str, bank_import: str | None = None) -> str:
    job_id = reconciliation_job_id(bank_import or bank_account)
    frappe.enqueue(
        "erpverein.services.sepa_settlement_service.run_bank_reconciliation",
        queue="long",
        timeout=RECONCILIATION_TIMEOUT,
        enqueue_after_commit=True,
        job_id=job_id,
        deduplicate=True,
        bank_account=bank_account,
        bank_import=bank_import,
    )
    return job_id


def start_import_reconciliation(bank_import: str) -> dict:
    assert_reconciliation_access()
    doc = bank_import_service.get_locked_import(bank_import)
    if (
        doc.status not in (bank_import_service.STATUS_IMPORTED, bank_import_service.STATUS_PARTIAL)
        or not doc.bank_account
    ):
        frappe.throw(_("Der Abgleich startet erst nach einem erfolgreichen Bankimport."))
    bank_import_service.mark_reconciliation_queued(doc)
    return {"bank_import": doc.name, "job_id": enqueue_bank_reconciliation(doc.bank_account, doc.name)}


def start_run_reconciliation(run_name: str) -> dict:
    assert_reconciliation_access()
    run = get_locked_run(run_name)
    if run.status not in SETTLEMENT_RUN_STATUSES or not run.bank_account:
        frappe.throw(_("Der Abgleich gilt nur fuer exportierte Einzugslaeufe, die noch nicht abgestimmt sind."))
    return {"run": run.name, "job_id": enqueue_bank_reconciliation(run.bank_account)}


def run_bank_reconciliation(bank_account: str, bank_import: str | None = None) -> None:
    """Background job after every bank import; it may run again at any time without creating a second payment."""
    savepoint = f"erpverein_reconciliation_{frappe.generate_hash(length=12)}"
    frappe.db.savepoint(savepoint)
    try:
        summary = reconcile_bank_account(bank_account)
    except Exception as exc:
        error_names = {cls.__name__ for cls in type(exc).mro()}
        if error_names & {"QueryDeadlockError", "QueryTimeoutError", "DeadlockError"}:
            raise frappe.RetryBackgroundJobError() from exc
        if is_retryable_error(exc):
            raise
        # Undo only this job's work; Frappe commits the recorded failure when the job returns.
        frappe.db.rollback(save_point=savepoint)
        frappe.db.release_savepoint(savepoint)
        if not isinstance(exc, (frappe.ValidationError, frappe.PermissionError)):
            frappe.log_error(
                title=f"ERPverein Abgleich {bank_import or bank_account}",
                message=mask_sensitive_text(frappe.get_traceback()),
            )
        record_result(bank_import, bank_import_service.RECONCILIATION_FAILED, failure_message(exc))
        return
    frappe.db.release_savepoint(savepoint)
    record_result(
        bank_import,
        bank_import_service.RECONCILIATION_DONE,
        _(
            "{0} Zahlungen gebucht, {1} Einzugslaeufe abgestimmt, {2} Ueberweisungen zugeordnet, "
            "{3} Vorschlaege warten auf Bestaetigung, {4} Bankumsaetze zur manuellen Pruefung, {5} Fehler. "
            "Offene Faelle zeigt der Arbeitsvorrat."
        ).format(
            summary.payments,
            summary.settled_runs,
            summary.transfers,
            summary.proposals,
            summary.review,
            summary.errors,
        ),
    )


def record_result(bank_import: str | None, status: str, message: str) -> None:
    if not bank_import:
        return
    try:
        bank_import_service.record_reconciliation_result(bank_import, status, message)
    except frappe.PermissionError:
        frappe.log_error(title=f"ERPverein Abgleich {bank_import}", message=message)


def failure_message(exc: Exception) -> str:
    if isinstance(exc, frappe.PermissionError):
        return _(
            "Der Benutzer {0} hat nicht alle Rechte fuer den Abgleich. Nutze in n8n den API-Schluessel des Kassenwarts "
            "oder gib dem Benutzer dieselben Rollen (System Manager und Accounts User). Starte danach den Abgleich "
            "im Bankimport neu."
        ).format(frappe.session.user)
    return error_reason(exc)


def error_reason(exc: Exception) -> str:
    if isinstance(exc, (frappe.ValidationError, frappe.PermissionError)):
        return truncate_note(mask_sensitive_text(strip_html(str(exc)))) or exc.__class__.__name__
    return _("Unerwarteter Fehler. Details stehen im Fehlerprotokoll.")


def truncate_note(value: str | None) -> str | None:
    return value[:MAX_NOTE_LENGTH] if value else None


def new_summary() -> frappe._dict:
    return frappe._dict(payments=0, settled_runs=0, review=0, transfers=0, proposals=0, errors=0)


def reconcile_bank_account(bank_account: str) -> frappe._dict:
    """Settle the bank credits of the club account that belong to exported SEPA collection runs."""
    assert_reconciliation_access()
    frappe.has_permission("Bank Account", "read", doc=bank_account, throw=True)
    summary = new_summary()
    for run_name in get_runs_in_settlement(bank_account):
        reason = run_step(summary, run_name, restart_run_settlement, run_name)
        if reason:
            run_step(summary, run_name, flag_failed_settlement, run_name, reason)
    batch_credits = []
    for row in get_open_credits(bank_account):
        position = find_end_to_end_position(row)
        if position:
            handle_position_credit(row, position, summary)
        else:
            batch_credits.append(row)
    for decision in match_run_credits([to_credit(row) for row in batch_credits], get_batch_runs(bank_account)):
        apply_run_decision(decision, summary)
    # Imported here to avoid a cycle: the transfer step uses the helpers of this module.
    from erpverein.services.transfer_settlement_service import settle_transfer_credits

    # Self-payers last: only credits that the direct debit settlement did not take (spec 16).
    settle_transfer_credits(bank_account, summary)
    return summary


def run_step(summary, label: str, function, *args) -> str | None:
    """One settlement step in its own savepoint; an unexpected error rolls back only this step (spec 15.7)."""
    step = new_summary()
    savepoint = f"erpverein_settlement_{frappe.generate_hash(length=12)}"
    frappe.db.savepoint(savepoint)
    message_count = len(frappe.local.message_log)
    try:
        function(*args, step)
    except Exception as exc:
        if is_retryable_error(exc):
            raise
        frappe.db.rollback(save_point=savepoint)
        frappe.db.release_savepoint(savepoint)
        frappe.local.message_log = frappe.local.message_log[:message_count]
        if not isinstance(exc, (frappe.ValidationError, frappe.PermissionError)):
            frappe.log_error(title=f"ERPverein Abgleich {label}", message=mask_sensitive_text(frappe.get_traceback()))
        return error_reason(exc)
    frappe.db.release_savepoint(savepoint)
    for key, value in step.items():
        summary[key] += value
    return None


def get_runs_in_settlement(bank_account: str) -> list[str]:
    runs = frappe.get_list(
        RUN_DOCTYPE,
        filters={"bank_account": bank_account, "status": ["in", RESTART_RUN_STATUSES]},
        pluck="name",
        order_by="name asc",
        limit_page_length=0,
    )
    started = settlement_started(runs)
    return [name for name in runs if name in started]


def settlement_started(runs: list[str]) -> set[str]:
    if not runs:
        return set()
    return set(
        frappe.get_all(
            POSITION_DOCTYPE,
            filters={
                "collection_run": ["in", runs],
                "active_key": ["is", "set"],
                "settlement_bank_transaction": ["is", "set"],
            },
            pluck="collection_run",
        )
    )


def get_open_credits(bank_account: str) -> list:
    rows = frappe.get_list(
        "Bank Transaction",
        filters={
            "bank_account": bank_account,
            "docstatus": 1,
            "deposit": [">", 0],
            "allocated_amount": ["<=", AMOUNT_TOLERANCE],
            BANK_TRANSACTION_MATCH_STATUS_FIELDNAME: ["in", EVALUATED_MATCH_STATUSES],
        },
        fields=CREDIT_FIELDS,
        order_by="date asc, name asc",
        limit_page_length=0,
    )
    if not rows:
        return []
    # A credit that a settlement already uses belongs to the restart above, not to a new assignment.
    targets = set(
        frappe.get_all(
            POSITION_DOCTYPE,
            filters={"settlement_bank_transaction": ["in", [row.name for row in rows]]},
            pluck="settlement_bank_transaction",
        )
    )
    return [row for row in rows if row.name not in targets]


def to_credit(row) -> Credit:
    return Credit(
        name=row.name,
        booking_date=getdate(row.date),
        amount=to_amount(row.deposit),
        category=row.get(BANK_TRANSACTION_CATEGORY_FIELDNAME) or "",
        texts=tuple(
            value
            for value in (
                row.description,
                row.reference_number,
                row.get(BANK_TRANSACTION_CUSTOMER_REFERENCE_FIELDNAME),
                row.transaction_type,
            )
            if value
        ),
    )


def get_batch_runs(bank_account: str) -> list[Run]:
    rows = frappe.get_list(
        RUN_DOCTYPE,
        filters={
            "bank_account": bank_account,
            "status": ["in", BATCH_RUN_STATUSES],
            "batch_booking": 1,
            "settlement_bank_transaction": ["is", "not set"],
        },
        fields=[
            "name",
            "collection_date",
            "total_amount",
            "position_count",
            "export_profile",
            "msg_id",
            "bank_order_reference",
        ],
        order_by="collection_date asc, name asc",
        limit_page_length=0,
    )
    started = settlement_started([row.name for row in rows])
    tolerances = {}
    runs = []
    for row in rows:
        if row.name in started or not cint(row.position_count):
            continue
        if row.export_profile not in tolerances:
            tolerances[row.export_profile] = cint(
                frappe.db.get_value("SEPA Exportprofil", row.export_profile, "settlement_tolerance_days")
            )
        runs.append(
            Run(
                name=row.name,
                collection_date=getdate(row.collection_date),
                total=to_amount(row.total_amount),
                position_count=cint(row.position_count),
                tolerance_days=tolerances[row.export_profile],
                references=tuple(value for value in (row.msg_id, row.bank_order_reference) if value),
            )
        )
    return runs


def find_end_to_end_position(row) -> frappe._dict | None:
    for reference in dict.fromkeys(
        value for value in (row.get(BANK_TRANSACTION_END_TO_END_FIELDNAME), row.reference_number) if value
    ):
        position = frappe.db.get_value(
            POSITION_DOCTYPE, {"end_to_end_reference": reference}, POSITION_FIELDS, as_dict=True
        )
        if position:
            return position
    return None


def handle_position_credit(row, position, summary) -> None:
    run_status = frappe.db.get_value(RUN_DOCTYPE, position.collection_run, "status")
    if (
        position.payment_entry
        or position.settlement_bank_transaction
        or position.status not in OPEN_POSITION_STATUSES
        or run_status not in SETTLEMENT_RUN_STATUSES
    ):
        reason = _(
            "Die End-to-End-Referenz gehoert zur Position {0} im Status {1}. ERPverein bucht keine Zahlung."
        ).format(position.name, _(position.status))
        review_credit(summary, row.name, reason)
        return
    decision = match_position_credit(
        to_credit(row),
        Position(position.name, position.collection_run, to_amount(position.amount), getdate(position.collection_date)),
    )
    if decision.action == ACTION_SETTLE:
        reason = run_step(summary, row.name, settle_position_with_credit, position.name, row.name)
        if reason:
            review_credit(summary, row.name, reason)
        return
    review_credit(summary, row.name, decision.reason)
    run_step(summary, position.name, flag_position_for_review, position.name, decision.reason)


def apply_run_decision(decision, summary) -> None:
    reason = decision.reason
    if decision.action == ACTION_SETTLE:
        reason = run_step(summary, decision.credit, settle_run_with_credit, decision.run, decision.credit)
        if not reason:
            return
    elif decision.action != ACTION_REVIEW:
        return
    review_credit(summary, decision.credit, reason)
    if decision.review_run:
        run_step(summary, decision.review_run, flag_unmatched_run, decision.review_run, reason)


def review_credit(summary, transaction_name: str, reason: str) -> None:
    if not run_step(summary, transaction_name, mark_credit_for_review, transaction_name, reason):
        summary.review += 1


def lock_bank_transaction(name: str):
    frappe.db.get_value("Bank Transaction", name, "name", for_update=True)
    transaction = frappe.get_doc("Bank Transaction", name)
    transaction.check_permission("write")
    return transaction


def credit_is_open(transaction) -> bool:
    return (
        transaction.docstatus == 1
        and flt(transaction.allocated_amount) <= AMOUNT_TOLERANCE
        and transaction.get(BANK_TRANSACTION_MATCH_STATUS_FIELDNAME) in EVALUATED_MATCH_STATUSES
    )


def settle_run_with_credit(run_name: str, transaction_name: str, summary) -> None:
    run = get_locked_run(run_name)
    transaction = lock_bank_transaction(transaction_name)
    positions = get_active_position_docs(run.name)
    # The matcher read without locks; check again before the run takes the credit.
    if (
        run.settlement_bank_transaction
        or run.status not in BATCH_RUN_STATUSES
        or not positions
        or any(position.payment_entry or position.settlement_bank_transaction for position in positions)
        or not credit_is_open(transaction)
        or abs(flt(transaction.deposit) - flt(run.total_amount)) > AMOUNT_TOLERANCE
    ):
        return
    run.update(
        {
            "status": RUN_STATUS_CREDIT_RECOGNIZED,
            "settlement_bank_transaction": transaction.name,
            "settlement_note": None,
        }
    )
    run.flags[UPDATE_FLAG] = True
    run.save()
    for position in positions:
        save_position(position, {"settlement_bank_transaction": transaction.name, "processing_error": None})
    process_run_settlement(run, summary)


def settle_position_with_credit(position_name: str, transaction_name: str, summary) -> None:
    run = get_locked_run(frappe.db.get_value(POSITION_DOCTYPE, position_name, "collection_run"))
    transaction = lock_bank_transaction(transaction_name)
    position = frappe.get_doc(POSITION_DOCTYPE, position_name)
    if (
        position.payment_entry
        or position.settlement_bank_transaction
        or position.status not in OPEN_POSITION_STATUSES
        or run.status not in SETTLEMENT_RUN_STATUSES
        or not credit_is_open(transaction)
    ):
        return
    save_position(position, {"settlement_bank_transaction": transaction.name, "processing_error": None})
    process_run_settlement(run, summary)


def restart_run_settlement(run_name: str, summary) -> None:
    process_run_settlement(get_locked_run(run_name), summary)


def process_run_settlement(run, summary) -> None:
    """Create the missing payments of a settled run and reconcile them; payments that exist stay as they are."""
    groups = {}
    for position in get_active_position_docs(run.name):
        if position.settlement_bank_transaction:
            groups.setdefault(position.settlement_bank_transaction, []).append(position)
    notes = [
        note for name in sorted(groups) if (note := settle_transaction_positions(run, name, groups[name], summary))
    ]
    refresh_run_settlement(run, summary, "; ".join(notes) or None)


def settle_transaction_positions(run, transaction_name: str, positions: list, summary) -> str | None:
    transaction = lock_bank_transaction(transaction_name)
    linked = {row.payment_entry for row in transaction.payment_entries if row.payment_document == "Payment Entry"}
    pending = [
        position
        for position in positions
        if position.status != POSITION_STATUS_DONE
        and (not position.payment_entry or position.payment_entry not in linked)
    ]
    if not pending:
        return None
    reason = None
    if transaction.docstatus != 1:
        reason = _("Der Bankumsatz {0} ist storniert.").format(transaction.name)
    elif (
        flt(sum(flt(position.amount) for position in pending)) - flt(transaction.unallocated_amount) > AMOUNT_TOLERANCE
    ):
        reason = _("Der Bankumsatz {0} hat nicht mehr genug offenen Betrag. Er ist anders abgeglichen.").format(
            transaction.name
        )
    if reason:
        for position in pending:
            if position.status != POSITION_STATUS_PAID:
                set_position_review(position, reason)
        return reason
    for position in pending:
        if position.status != POSITION_STATUS_PAID:
            ensure_payment(run, position, transaction, summary)
    return reconcile_payments(
        transaction, [position for position in pending if position.status == POSITION_STATUS_PAID]
    )


def ensure_payment(run, position, transaction, summary) -> None:
    existing = frappe.db.get_value(
        "Payment Entry", {PAYMENT_ENTRY_COLLECTION_ITEM_FIELDNAME: position.name}, ["name", "docstatus"], as_dict=True
    )
    if existing and existing.docstatus == 1:
        mark_position_paid(position, existing.name)
        return
    if existing:
        set_position_review(
            position,
            _(
                "Die Zahlung {0} zu dieser Position ist storniert oder nicht gebucht. Klaere den Fall im Banking-Bereich."
            ).format(existing.name),
        )
        return
    reason = get_payment_reason(run, position)
    if reason:
        set_position_review(position, reason)
        return
    savepoint = f"erpverein_settlement_payment_{frappe.generate_hash(length=12)}"
    frappe.db.savepoint(savepoint)
    message_count = len(frappe.local.message_log)
    try:
        payment = build_payment_entry(run, position, transaction)
        payment.flags[SETTLEMENT_FLAG] = True
        payment.insert()
        payment.submit()
    except Exception as exc:
        if is_retryable_error(exc):
            raise
        frappe.db.rollback(save_point=savepoint)
        frappe.db.release_savepoint(savepoint)
        frappe.local.message_log = frappe.local.message_log[:message_count]
        if not isinstance(exc, (frappe.ValidationError, frappe.PermissionError)):
            frappe.log_error(
                title=f"ERPverein Zahlung {position.name}", message=mask_sensitive_text(frappe.get_traceback())
            )
        set_position_review(position, _("ERPverein konnte die Zahlung nicht buchen: {0}").format(error_reason(exc)))
        return
    frappe.db.release_savepoint(savepoint)
    mark_position_paid(position, payment.name)
    summary.payments += 1


def get_payment_reason(run, position) -> str | None:
    invoice = frappe.db.get_value(
        "Sales Invoice",
        position.sales_invoice,
        ["docstatus", "customer", "company", "currency", "outstanding_amount"],
        as_dict=True,
    )
    if not invoice or invoice.docstatus != 1:
        return _("Die Rechnung {0} ist nicht gebucht.").format(position.sales_invoice)
    if invoice.customer != position.customer or invoice.company != run.company:
        return _("Die Rechnung {0} gehoert zu einem anderen Kunden oder Unternehmen.").format(position.sales_invoice)
    amount = flt(position.amount, 2)
    if flt(invoice.outstanding_amount, 2) + AMOUNT_TOLERANCE < amount:
        return _(
            "Die Rechnung {0} ist inzwischen anders bezahlt (offen: {1}). ERPverein bucht keine Zahlung. "
            "Klaere den Fall im Banking-Bereich."
        ).format(position.sales_invoice, fmt_money(invoice.outstanding_amount, currency=invoice.currency))
    if not position.payment_term:
        return _("Die Position hat keine Zahlungsbedingung.")
    outstanding = frappe.db.get_value(
        "Payment Schedule",
        {"parenttype": "Sales Invoice", "parent": position.sales_invoice, "payment_term": position.payment_term},
        "outstanding",
    )
    if outstanding is None:
        return _("Die Zahlungsplanzeile der Position fehlt in der Rechnung {0}.").format(position.sales_invoice)
    if flt(outstanding, 2) + AMOUNT_TOLERANCE < amount:
        return _(
            "Die Zahlungsplanzeile ist schon bezahlt (offen: {0}). ERPverein bucht keine Zahlung. "
            "Klaere den Fall im Banking-Bereich."
        ).format(fmt_money(outstanding, currency=invoice.currency))
    return None


def build_payment_entry(run, position, transaction):
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    amount = flt(position.amount, 2)
    payment = get_payment_entry(
        "Sales Invoice",
        position.sales_invoice,
        party_amount=amount,
        bank_account=frappe.db.get_value("Bank Account", run.bank_account, "account"),
        reference_date=transaction.date,
    )
    payment.update(
        {
            "posting_date": transaction.date,
            "bank_account": run.bank_account,
            "mode_of_payment": MODE_OF_PAYMENT_DIRECT_DEBIT,
            "reference_no": position.end_to_end_reference,
            "reference_date": transaction.date,
            PAYMENT_ENTRY_COLLECTION_ITEM_FIELDNAME: position.name,
            PAYMENT_ENTRY_BANK_TRANSACTION_FIELDNAME: transaction.name,
        }
    )
    payment.set_bank_account_data()
    # The payment term updates exactly this row of the payment schedule (spec 31.5).
    reference = next(
        (
            row
            for row in payment.references
            if (row.reference_doctype, row.reference_name) == ("Sales Invoice", position.sales_invoice)
        ),
        None,
    ) or payment.append("references", {"reference_doctype": "Sales Invoice", "reference_name": position.sales_invoice})
    reference.update({"payment_term": position.payment_term, "allocated_amount": amount})
    payment.set("references", [reference])
    return payment


def reconcile_payments(transaction, positions: list) -> str | None:
    from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import reconcile_vouchers

    # Explicit vouchers only: auto_reconcile_vouchers would distribute every candidate by rank.
    vouchers = [
        {"payment_doctype": "Payment Entry", "payment_name": position.payment_entry, "amount": flt(position.amount, 2)}
        for position in positions
    ]
    if not vouchers:
        return None
    savepoint = f"erpverein_settlement_reconcile_{frappe.generate_hash(length=12)}"
    frappe.db.savepoint(savepoint)
    message_count = len(frappe.local.message_log)
    try:
        reconcile_vouchers(transaction.name, json.dumps(vouchers))
    except Exception as exc:
        if is_retryable_error(exc):
            raise
        frappe.db.rollback(save_point=savepoint)
        frappe.db.release_savepoint(savepoint)
        frappe.local.message_log = frappe.local.message_log[:message_count]
        if not isinstance(exc, (frappe.ValidationError, frappe.PermissionError)):
            frappe.log_error(
                title=f"ERPverein Abgleich {transaction.name}", message=mask_sensitive_text(frappe.get_traceback())
            )
        return _("Der Abgleich mit dem Bankumsatz {0} ist fehlgeschlagen: {1}").format(
            transaction.name, error_reason(exc)
        )
    frappe.db.release_savepoint(savepoint)
    return None


def refresh_run_settlement(run, summary=None, note: str | None = None) -> None:
    """Derive the run status from its positions and their bank credits; set the match status of each credit."""
    positions = get_active_position_docs(run.name)
    transactions = sorted(
        {position.settlement_bank_transaction for position in positions if position.settlement_bank_transaction}
    )
    unreconciled = {
        name
        for name in transactions
        if flt(frappe.db.get_value("Bank Transaction", name, "unallocated_amount")) > AMOUNT_TOLERANCE
    }
    review = [position for position in positions if position.status == POSITION_STATUS_REVIEW]
    all_paid = bool(positions) and all(position.status in SETTLED_POSITION_STATUSES for position in positions)
    if all_paid and not unreconciled and not note:
        status = RUN_STATUS_SETTLED
    elif review or note or (all_paid and unreconciled):
        status = RUN_STATUS_REVIEW
        note = note or (
            _("{0} Positionen brauchen eine manuelle Pruefung. Den Grund nennt die Spalte Pruefergebnis.").format(
                len(review)
            )
            if review
            else _("Der Bankumsatz ist nicht vollstaendig abgeglichen.")
        )
    elif transactions:
        status = RUN_STATUS_CREDIT_RECOGNIZED
    else:
        return
    note = truncate_note(note)
    error_count = sum(1 for position in positions if position.processing_error)
    if (run.status, run.settlement_note or None, cint(run.error_count)) != (status, note, error_count) or (
        status == RUN_STATUS_SETTLED and not run.settled_at
    ):
        if status == RUN_STATUS_SETTLED and run.status != RUN_STATUS_SETTLED and summary is not None:
            summary.settled_runs += 1
        run.status = status
        run.settlement_note = note
        if status == RUN_STATUS_SETTLED and not run.settled_at:
            run.settled_at = now_datetime()
        save_run_totals(run)
    for name in transactions:
        group = [position for position in positions if position.settlement_bank_transaction == name]
        if name not in unreconciled and all(position.status in SETTLED_POSITION_STATUSES for position in group):
            set_credit_match(name, MATCH_STATUS_MATCHED, describe_settlement(run, group))
        else:
            set_credit_match(name, MATCH_STATUS_REVIEW, note or _("Der Bankumsatz ist nicht vollstaendig abgeglichen."))


def describe_settlement(run, positions: list) -> str:
    if run.settlement_bank_transaction:
        return _("Sammelgutschrift zum Einzugslauf {0}: {1} Zahlungen.").format(run.name, len(positions))
    return _("Einzelgutschrift zur Einzugsposition {0}.").format(", ".join(position.name for position in positions))


def set_credit_match(transaction_name: str, status: str, note: str | None) -> None:
    note = truncate_note(note)
    current = frappe.db.get_value(
        "Bank Transaction",
        transaction_name,
        [BANK_TRANSACTION_MATCH_STATUS_FIELDNAME, BANK_TRANSACTION_MATCH_NOTE_FIELDNAME],
        as_dict=True,
    )
    if not current or (
        current.get(BANK_TRANSACTION_MATCH_STATUS_FIELDNAME),
        current.get(BANK_TRANSACTION_MATCH_NOTE_FIELDNAME) or None,
    ) == (status, note):
        return
    frappe.has_permission("Bank Transaction", "write", doc=transaction_name, throw=True)
    frappe.db.set_value(
        "Bank Transaction",
        transaction_name,
        {BANK_TRANSACTION_MATCH_STATUS_FIELDNAME: status, BANK_TRANSACTION_MATCH_NOTE_FIELDNAME: note},
    )


def mark_credit_for_review(transaction_name: str, reason: str, summary=None) -> None:
    transaction = lock_bank_transaction(transaction_name)
    if credit_is_open(transaction):
        set_credit_match(transaction.name, MATCH_STATUS_REVIEW, reason)


def mark_position_paid(position, payment_entry: str) -> None:
    save_position(position, {"status": POSITION_STATUS_PAID, "payment_entry": payment_entry, "processing_error": None})


def set_position_review(position, reason: str) -> None:
    reason = truncate_note(reason)
    if (position.status, position.processing_error or None) != (POSITION_STATUS_REVIEW, reason):
        save_position(position, {"status": POSITION_STATUS_REVIEW, "processing_error": reason})


def set_run_review(run, reason: str) -> None:
    reason = truncate_note(reason)
    if (run.status, run.settlement_note or None) == (RUN_STATUS_REVIEW, reason):
        return
    run.update({"status": RUN_STATUS_REVIEW, "settlement_note": reason})
    run.flags[UPDATE_FLAG] = True
    run.save()


def flag_unmatched_run(run_name: str, reason: str, summary=None) -> None:
    run = get_locked_run(run_name)
    if not run.settlement_bank_transaction and run.status in BATCH_RUN_STATUSES:
        set_run_review(run, reason)


def flag_failed_settlement(run_name: str, reason: str, summary=None) -> None:
    run = get_locked_run(run_name)
    if run.status in RESTART_RUN_STATUSES:
        set_run_review(run, reason)


def mark_position_done(position_name: str, reason: str | None = None) -> dict:
    """The treasurer closed a review case in the banking area; the run can then be settled (spec 35.8)."""
    run_name = frappe.db.get_value(POSITION_DOCTYPE, position_name, "collection_run")
    if not run_name:
        frappe.throw(_("Die Einzugsposition {0} existiert nicht.").format(position_name))
    run = get_locked_run(run_name)
    position = frappe.get_doc(POSITION_DOCTYPE, position_name)
    position.check_permission("write")
    if position.status != POSITION_STATUS_REVIEW:
        frappe.throw(_("Nur eine Position in Manuelle Pruefung laesst sich als erledigt markieren."))
    reason = require_reason(reason)
    note = _("Erledigt von {0} am {1}: {2}").format(frappe.session.user, formatdate(nowdate(), "dd.MM.yyyy"), reason)
    if position.processing_error:
        # The review reason moves into the note: a settled run shows no open error any more.
        note += " " + _("Pruefergebnis war: {0}").format(position.processing_error)
    note = truncate_note(note)
    save_position(position, {"status": POSITION_STATUS_DONE, "resolution_note": note, "processing_error": None})
    position.add_comment("Info", note)
    run.add_comment("Info", _("Position {0} als erledigt markiert: {1}").format(position.name, reason))
    refresh_run_settlement(run)
    return {"position": position.name, "status": position.status, "run": run.name, "run_status": run.status}


def flag_position_for_review(position_name: str, reason: str, summary=None) -> None:
    run = get_locked_run(frappe.db.get_value(POSITION_DOCTYPE, position_name, "collection_run"))
    position = frappe.get_doc(POSITION_DOCTYPE, position_name)
    if position.payment_entry or position.status not in OPEN_POSITION_STATUSES:
        return
    set_position_review(position, reason)
    refresh_run_settlement(run, summary)


def validate_payment_entry_provenance(doc, method: str | None = None) -> None:
    if doc.flags.get(SETTLEMENT_FLAG):
        return
    previous = doc.get_doc_before_save()
    for fieldname in (
        PAYMENT_ENTRY_COLLECTION_ITEM_FIELDNAME,
        PAYMENT_ENTRY_BANK_TRANSACTION_FIELDNAME,
        PAYMENT_ENTRY_TRANSFER_KEY_FIELDNAME,
    ):
        if (doc.get(fieldname) or None) != ((previous.get(fieldname) if previous else None) or None):
            frappe.throw(_("Die SEPA-Felder einer Zahlung setzt nur ERPverein."))


def handle_cancelled_collection_payment(doc, method: str | None = None) -> None:
    position_name = doc.get(PAYMENT_ENTRY_COLLECTION_ITEM_FIELDNAME)
    if not position_name:
        return
    if not frappe.has_permission(POSITION_DOCTYPE, "write", doc=position_name):
        frappe.throw(
            _(
                "Die Zahlung gehoert zur Einzugsposition {0}. Stornieren darf sie nur ein Benutzer mit den Rollen des Kassenwarts."
            ).format(position_name),
            frappe.PermissionError,
        )
    run = get_locked_run(frappe.db.get_value(POSITION_DOCTYPE, position_name, "collection_run"))
    position = frappe.get_doc(POSITION_DOCTYPE, position_name)
    set_position_review(
        position, _("Die Zahlung {0} wurde storniert. Klaere den Fall im Banking-Bereich.").format(doc.name)
    )
    refresh_run_settlement(run)
