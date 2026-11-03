import json

import frappe
from frappe import _
from frappe.utils import add_days, cstr, flt, formatdate, getdate, nowdate

from erpverein.custom_fields import (
    BANK_TRANSACTION_CATEGORY_FIELDNAME,
    BANK_TRANSACTION_END_TO_END_FIELDNAME,
    BANK_TRANSACTION_MANDATE_FIELDNAME,
    BANK_TRANSACTION_MATCH_STATUS_FIELDNAME,
    JOURNAL_ENTRY_COLLECTION_ITEM_FIELDNAME,
    JOURNAL_ENTRY_RETURN_BANK_TRANSACTION_FIELDNAME,
    JOURNAL_ENTRY_RETURN_FEE_FIELDNAME,
    JOURNAL_ENTRY_RETURN_FIELDNAMES,
    JOURNAL_ENTRY_RETURN_PRINCIPAL_FIELDNAME,
    JOURNAL_ENTRY_RETURN_REASON_FIELDNAME,
    SALES_INVOICE_BILLING_KIND_FIELDNAME,
)
from erpverein.services.payment_schedule_service import AMOUNT_TOLERANCE, adjust_row_payment, change_schedule_row
from erpverein.services.sepa_collection_service import (
    POSITION_DOCTYPE,
    POSITION_STATUS_DONE,
    POSITION_STATUS_EXPORTED,
    POSITION_STATUS_PAID,
    POSITION_STATUS_RETURNED,
    POSITION_STATUS_REVIEW,
    RETURN_DECISION_RECOLLECT,
    RETURN_DECISIONS,
    RUN_DOCTYPE,
    RUN_STATUS_PARTLY_RETURNED,
    RUN_STATUS_SETTLED,
    earliest_collection_date,
    get_locked_run,
    iban_hash,
    require_reason,
    save_position,
)
from erpverein.services.sepa_export_profile_service import get_active_export_profile, is_prenotification_required
from erpverein.services.sepa_return_matcher import (
    ACTION_BOOK,
    ACTION_REVIEW,
    ACTION_SKIP,
    ACTION_WAIT,
    REFUND_PERIOD_DAYS,
    RETURN_CATEGORIES,
    STATE_DONE,
    STATE_PAID,
    STATE_RETURNED,
    STATE_REVIEW,
    STATE_UNPAID,
    Debit,
    ReturnPosition,
    confirm_return,
    decide_return,
)
from erpverein.services.sepa_settlement_matcher import format_amount, to_amount
from erpverein.services.sepa_settlement_service import (
    MATCH_STATUS_MATCHED,
    MATCH_STATUS_OPEN,
    MATCH_STATUS_REVIEW,
    assert_reconciliation_access,
    lock_bank_transaction,
    new_summary,
    refresh_run_settlement,
    run_step,
    set_credit_match,
    set_position_review,
    truncate_note,
)
from erpverein.setup_data import MODE_OF_PAYMENT_DIRECT_DEBIT, MODE_OF_PAYMENT_TRANSFER


RETURN_FLAG = "erpverein_sepa_return"
DEBIT_FIELDS = [
    "name",
    "date",
    "withdrawal",
    "company",
    "currency",
    "bank_account",
    "description",
    "reference_number",
    "transaction_type",
    "bank_party_iban",
    BANK_TRANSACTION_CATEGORY_FIELDNAME,
    BANK_TRANSACTION_END_TO_END_FIELDNAME,
    BANK_TRANSACTION_MANDATE_FIELDNAME,
]
# Positions whose file went to the bank; draft, checked and cancelled positions never moved money.
EXPORTED_POSITION_STATUSES = (
    POSITION_STATUS_EXPORTED,
    POSITION_STATUS_PAID,
    POSITION_STATUS_REVIEW,
    POSITION_STATUS_DONE,
    POSITION_STATUS_RETURNED,
)
POSITION_FIELDS = [
    "name",
    "status",
    "amount",
    "collection_date",
    "end_to_end_reference",
    "mandate_reference_snapshot",
    "debtor_iban_hash",
    "payment_entry",
    "return_bank_transaction",
]
MAX_CHEQUE_LENGTH = 140


def settle_return_debits(bank_account: str, summary) -> None:
    """Job step after the batch credits: book every bank debit that returns a paid collection (spec 17.4).

    Each debit runs in its own savepoint; the journal entry, its reconciliation and the new statuses come together
    or not at all."""
    for row in get_open_debits(bank_account):
        debit = to_debit(row)
        if decide_return(debit, load_candidates(debit)).action == ACTION_SKIP:
            continue
        reason = run_step(summary, row.name, settle_return_debit, row.name)
        if reason:
            run_step(summary, row.name, record_return_failure, row.name, reason)


def get_open_debits(bank_account: str) -> list:
    # Debits in "Manuelle Pruefung" wait for a person: ERPverein never books them on its own afterwards.
    return frappe.get_list(
        "Bank Transaction",
        filters={
            "bank_account": bank_account,
            "docstatus": 1,
            "withdrawal": [">", 0],
            "allocated_amount": ["<=", AMOUNT_TOLERANCE],
            BANK_TRANSACTION_MATCH_STATUS_FIELDNAME: MATCH_STATUS_OPEN,
        },
        fields=DEBIT_FIELDS,
        order_by="date asc, name asc",
        limit_page_length=0,
    )


def to_debit(row) -> Debit:
    return Debit(
        name=row.name,
        booking_date=getdate(row.date),
        amount=to_amount(row.withdrawal),
        category=row.get(BANK_TRANSACTION_CATEGORY_FIELDNAME) or "",
        references=tuple(
            dict.fromkeys(
                value for value in (row.get(BANK_TRANSACTION_END_TO_END_FIELDNAME), row.reference_number) if value
            )
        ),
        mandate_reference=row.get(BANK_TRANSACTION_MANDATE_FIELDNAME) or "",
        iban_hash=iban_hash(row.bank_party_iban) or "",
        texts=tuple(value for value in (row.description, row.transaction_type) if value),
    )


def load_candidates(debit: Debit) -> list[ReturnPosition]:
    """Positions the debit may return: its End-to-End reference, otherwise its mandate or the IBAN of the payer."""
    queries = []
    if debit.references:
        queries.append({"end_to_end_reference": ["in", list(debit.references)]})
    if debit.category in RETURN_CATEGORIES:
        if debit.mandate_reference:
            queries.append({"mandate_reference_snapshot": debit.mandate_reference})
        elif debit.iban_hash:
            queries.append({"debtor_iban_hash": debit.iban_hash})
    rows = {}
    for filters in queries:
        for row in frappe.get_list(
            POSITION_DOCTYPE,
            filters={**filters, "status": ["in", EXPORTED_POSITION_STATUSES]},
            fields=POSITION_FIELDS,
            limit_page_length=0,
        ):
            rows[row.name] = row
    return [to_return_position(row) for row in rows.values()]


def to_return_position(row) -> ReturnPosition:
    return ReturnPosition(
        name=row.name,
        state=position_state(row),
        amount=to_amount(row.amount),
        collection_date=getdate(row.collection_date),
        end_to_end=row.end_to_end_reference or "",
        mandate_reference=row.mandate_reference_snapshot or "",
        iban_hash=row.debtor_iban_hash or "",
        return_transaction=row.return_bank_transaction or "",
    )


def position_state(row) -> str:
    if row.status == POSITION_STATUS_RETURNED:
        return STATE_RETURNED
    if row.status == POSITION_STATUS_DONE:
        return STATE_DONE
    if row.status == POSITION_STATUS_REVIEW:
        return STATE_REVIEW
    return STATE_PAID if row.status == POSITION_STATUS_PAID and row.payment_entry else STATE_UNPAID


def debit_is_open(transaction) -> bool:
    return (
        transaction.docstatus == 1
        and flt(transaction.withdrawal) > 0
        and flt(transaction.allocated_amount) <= AMOUNT_TOLERANCE
        and transaction.get(BANK_TRANSACTION_MATCH_STATUS_FIELDNAME) == MATCH_STATUS_OPEN
    )


def settle_return_debit(transaction_name: str, summary) -> None:
    debit = to_debit(frappe.db.get_value("Bank Transaction", transaction_name, DEBIT_FIELDS, as_dict=True))
    first = decide_return(debit, load_candidates(debit))
    run = None
    if first.action == ACTION_BOOK:
        # Run before bank transaction, like the batch credit settlement: parallel jobs lock in the same order.
        run = get_locked_run(frappe.db.get_value(POSITION_DOCTYPE, first.position, "collection_run"))
    transaction = lock_bank_transaction(transaction_name)
    if not debit_is_open(transaction):
        return
    # The first decision read without locks; decide again before anything is booked (spec 22.3).
    debit = to_debit(transaction)
    decision = decide_return(debit, load_candidates(debit))
    if decision.action == ACTION_SKIP:
        return
    if decision.action == ACTION_WAIT:
        set_credit_match(transaction.name, MATCH_STATUS_OPEN, decision.note)
        return
    if decision.action == ACTION_REVIEW:
        set_credit_match(transaction.name, MATCH_STATUS_REVIEW, decision.note)
        summary.review += 1
        return
    if run is None or decision.position != first.position:
        # The data changed between both reads; the next job decides with the new state.
        return
    reason = book_return(run, transaction, decision, summary)
    if reason:
        set_credit_match(transaction.name, MATCH_STATUS_REVIEW, reason)
        summary.review += 1


def book_return(run, transaction, decision, summary) -> str | None:
    """Journal entry, reconciliation and statuses of one return; returns the reason when a person has to decide."""
    position = frappe.get_doc(POSITION_DOCTYPE, decision.position)
    position.check_permission("write")
    entry_name, reason = find_return_entry(position, transaction)
    if not entry_name and not reason:
        reason = get_return_reason(run, position, transaction, decision)
    if reason:
        return reason
    if not entry_name:
        check_return_setup(run, return_invoice(position), decision)
        entry = build_return_entry(run, position, transaction, decision)
        entry.flags[RETURN_FLAG] = True
        entry.insert()
        entry.submit()
        entry_name = entry.name
    if not frappe.db.exists(
        "Bank Transaction Payments",
        {"parent": transaction.name, "payment_document": "Journal Entry", "payment_entry": entry_name},
    ):
        from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import reconcile_vouchers

        # Abgleichsart "Matched": "Unreconcile" in the banking area never cancels the journal entry.
        vouchers = [
            {"payment_doctype": "Journal Entry", "payment_name": entry_name, "amount": flt(transaction.withdrawal, 2)}
        ]
        reconcile_vouchers(transaction.name, json.dumps(vouchers))
    save_position(
        position,
        {
            "status": POSITION_STATUS_RETURNED,
            "return_bank_transaction": transaction.name,
            "return_journal_entry": entry_name,
            "return_principal_amount": flt(decision.principal, 2),
            "return_fee_amount": flt(decision.fee, 2),
            "return_reason_code": decision.reason_code or None,
            "return_reason_text": decision.reason_text or None,
            "processing_error": None,
        },
    )
    note = _("{0} Gegenbuchung: {1}.").format(decision.note, entry_name)
    if flt(frappe.db.get_value("Bank Transaction", transaction.name, "unallocated_amount")) > AMOUNT_TOLERANCE:
        set_credit_match(
            transaction.name, MATCH_STATUS_REVIEW, _("{0} Der Bankumsatz ist nicht ganz abgeglichen.").format(note)
        )
    else:
        set_credit_match(transaction.name, MATCH_STATUS_MATCHED, note)
    position.add_comment("Info", note)
    refresh_run_settlement(run, summary)
    summary.returns += 1
    return None


def find_return_entry(position, transaction) -> tuple[str | None, str | None]:
    """Anchors of spec 17.8: one journal entry per position and per bank transaction, cancelled ones included."""
    for filters in (
        {JOURNAL_ENTRY_COLLECTION_ITEM_FIELDNAME: position.name},
        {JOURNAL_ENTRY_RETURN_BANK_TRANSACTION_FIELDNAME: transaction.name},
    ):
        existing = frappe.db.get_value(
            "Journal Entry",
            filters,
            [
                "name",
                "docstatus",
                JOURNAL_ENTRY_COLLECTION_ITEM_FIELDNAME,
                JOURNAL_ENTRY_RETURN_BANK_TRANSACTION_FIELDNAME,
            ],
            as_dict=True,
        )
        if not existing:
            continue
        if (
            existing.docstatus == 1
            and existing.get(JOURNAL_ENTRY_COLLECTION_ITEM_FIELDNAME) == position.name
            and existing.get(JOURNAL_ENTRY_RETURN_BANK_TRANSACTION_FIELDNAME) == transaction.name
        ):
            return existing.name, None
        return None, _(
            "Zur Einzugsposition {0} oder zu diesem Bankumsatz gibt es schon die Gegenbuchung {1} (storniert oder "
            "zu einem anderen Vorgang). ERPverein bucht keine zweite. Klaere den Fall im Banking-Bereich."
        ).format(position.name, existing.name)
    return None, None


def get_return_reason(run, position, transaction, decision) -> str | None:
    """Checks before the journal entry; a failed check sends the debit to manual review with its reason."""
    invoice = return_invoice(position)
    if not invoice or invoice.docstatus != 1:
        return _("Die Rechnung {0} ist nicht gebucht.").format(position.sales_invoice)
    if invoice.customer != position.customer or invoice.company != run.company:
        return _("Die Rechnung {0} gehoert zu einem anderen Kunden oder Unternehmen.").format(invoice.name)
    if transaction.bank_account != run.bank_account or transaction.company != run.company:
        return _("Die Ruecklastschrift ist nicht auf dem Vereinskonto des Einzugslaufs {0} gebucht.").format(run.name)
    currency = frappe.get_cached_value("Company", run.company, "default_currency")
    if {invoice.currency, invoice.party_account_currency or invoice.currency, transaction.currency or currency} != {
        currency
    }:
        return _("ERPverein bucht Ruecklastschriften nur in {0}.").format(currency)
    if frappe.db.get_value("Payment Entry", position.payment_entry, "docstatus") != 1:
        return _("Die urspruengliche Zahlung {0} ist nicht gebucht.").format(position.payment_entry)
    paid = frappe.db.get_value(
        "Payment Schedule",
        {"parenttype": "Sales Invoice", "parent": invoice.name, "payment_term": position.payment_term},
        "paid_amount",
    )
    if paid is None or flt(paid) + AMOUNT_TOLERANCE < flt(decision.principal):
        return _("Die Zahlungsplanzeile {0} der Rechnung {1} ist nicht bezahlt. ERPverein oeffnet sie nicht.").format(
            position.payment_term, invoice.name
        )
    return None


def check_return_setup(run, invoice, decision) -> None:
    """Setup the treasurer can fix. The step fails, the debit stays Offen and the next job tries again."""
    if not invoice.cost_center:
        frappe.throw(_("Weder die Rechnung {0} noch das Unternehmen hat eine Kostenstelle.").format(invoice.name))
    if decision.fee and not return_fee_account(run):
        frappe.throw(
            _("Im SEPA-Exportprofil {0} fehlt das Gebuehrenkonto oder es gehoert zu einem anderen Unternehmen.").format(
                run.export_profile
            )
        )


def return_invoice(position):
    invoice = frappe.db.get_value(
        "Sales Invoice",
        position.sales_invoice,
        ["name", "docstatus", "customer", "company", "currency", "party_account_currency", "debit_to", "cost_center"],
        as_dict=True,
    )
    if invoice:
        # The fee follows the invoice: rent and membership fee keep their cost centers (user decision Phase 6).
        invoice.cost_center = invoice.cost_center or frappe.get_cached_value("Company", invoice.company, "cost_center")
    return invoice


def return_fee_account(run) -> str | None:
    account = frappe.db.get_value("SEPA Exportprofil", run.export_profile, "default_fee_account")
    if account and frappe.db.get_value("Account", account, "company") == run.company:
        return account
    return None


def build_return_entry(run, position, transaction, decision):
    """Journal entry of spec 17.6: debit customer with the invoice, debit fee, credit bank."""
    invoice = return_invoice(position)
    principal = flt(decision.principal, 2)
    fee = flt(decision.fee, 2)
    remark = _("Ruecklastschrift zur Einzugsposition {0}, Rechnung {1}, Bankumsatz {2}.").format(
        position.name, invoice.name, transaction.name
    )
    if decision.reason_text:
        remark += " " + _("Grund: {0} ({1}).").format(decision.reason_text, decision.reason_code)
    entry = frappe.new_doc("Journal Entry")
    entry.update(
        {
            "voucher_type": "Bank Entry",
            "company": run.company,
            "posting_date": transaction.date,
            "cheque_no": (position.end_to_end_reference or transaction.name)[:MAX_CHEQUE_LENGTH],
            "cheque_date": transaction.date,
            "mode_of_payment": MODE_OF_PAYMENT_DIRECT_DEBIT,
            "user_remark": remark,
            JOURNAL_ENTRY_COLLECTION_ITEM_FIELDNAME: position.name,
            JOURNAL_ENTRY_RETURN_BANK_TRANSACTION_FIELDNAME: transaction.name,
            JOURNAL_ENTRY_RETURN_PRINCIPAL_FIELDNAME: principal,
            JOURNAL_ENTRY_RETURN_FEE_FIELDNAME: fee,
            JOURNAL_ENTRY_RETURN_REASON_FIELDNAME: decision.reason_code or None,
        }
    )
    entry.append(
        "accounts",
        {
            "account": invoice.debit_to,
            "party_type": "Customer",
            "party": invoice.customer,
            "debit_in_account_currency": principal,
            "reference_type": "Sales Invoice",
            "reference_name": invoice.name,
            "cost_center": invoice.cost_center,
        },
    )
    if fee:
        # The fee stays with the club; it changes neither the invoice nor the payer's account (spec 17.10, 17.11).
        entry.append(
            "accounts",
            {"account": return_fee_account(run), "debit_in_account_currency": fee, "cost_center": invoice.cost_center},
        )
    entry.append(
        "accounts",
        {
            "account": frappe.db.get_value("Bank Account", transaction.bank_account, "account"),
            "bank_account": transaction.bank_account,
            "credit_in_account_currency": flt(principal + fee, 2),
            "cost_center": invoice.cost_center,
        },
    )
    return entry


def record_return_failure(transaction_name: str, reason: str, summary) -> None:
    transaction = lock_bank_transaction(transaction_name)
    if not debit_is_open(transaction):
        return
    # The status stays "Offen": the next job tries again.
    set_credit_match(
        transaction.name,
        MATCH_STATUS_OPEN,
        _("Ruecklastschrift nicht gebucht: {0} ERPverein prueft beim naechsten Abgleich erneut.").format(reason),
    )
    summary.errors += 1


def confirm_return_assignment(transaction_name: str, position_name: str, note: str | None = None) -> dict:
    """The treasurer assigns a return in manual review to a paid position; ERPverein books it like the job."""
    assert_reconciliation_access()
    run_name = frappe.db.get_value(POSITION_DOCTYPE, position_name, "collection_run")
    if not run_name:
        frappe.throw(_("Die Einzugsposition {0} existiert nicht.").format(position_name))
    run = get_locked_run(run_name)
    transaction = lock_bank_transaction(transaction_name)
    if (
        transaction.get(BANK_TRANSACTION_MATCH_STATUS_FIELDNAME) != MATCH_STATUS_REVIEW
        or transaction.docstatus != 1
        or flt(transaction.withdrawal) <= 0
        or flt(transaction.allocated_amount) > AMOUNT_TOLERANCE
    ):
        frappe.throw(_("Der Bankumsatz {0} ist keine offene Belastung in Manueller Pruefung.").format(transaction.name))
    position = frappe.get_doc(POSITION_DOCTYPE, position_name)
    position.check_permission("write")
    note = require_reason(note) if cstr(note).strip() else None
    decision = confirm_return(to_debit(transaction), to_return_position(position), frappe.session.user)
    if decision.action != ACTION_BOOK:
        frappe.throw(decision.note)
    if note:
        decision.note += " " + _("Notiz: {0}").format(note)
    summary = new_summary()
    reason = book_return(run, transaction, decision, summary)
    if reason:
        frappe.throw(reason)
    transaction.add_comment(
        "Info", _("Ruecklastschrift zugeordnet von {0}: {1}").format(frappe.session.user, position.name)
    )
    return {
        "bank_transaction": transaction.name,
        "position": position.name,
        "journal_entry": frappe.db.get_value(POSITION_DOCTYPE, position.name, "return_journal_entry"),
        "match_status": frappe.db.get_value(
            "Bank Transaction", transaction.name, BANK_TRANSACTION_MATCH_STATUS_FIELDNAME
        ),
    }


def validate_journal_entry_provenance(doc, method: str | None = None) -> None:
    if doc.flags.get(RETURN_FLAG):
        return
    previous = doc.get_doc_before_save()
    if previous is None and doc.get("amended_from"):
        # Frappe copies read-only fields into an amendment; the amendment is the treasurer's own journal entry.
        for fieldname in JOURNAL_ENTRY_RETURN_FIELDNAMES:
            doc.set(fieldname, None)
    for fieldname in JOURNAL_ENTRY_RETURN_FIELDNAMES:
        if (doc.get(fieldname) or None) != ((previous.get(fieldname) if previous else None) or None):
            frappe.throw(_("Die Felder einer Ruecklastschrift setzt nur ERPverein."))


def open_row_for_return(doc, method: str | None = None) -> None:
    """Journal entry of a return submitted: its payment schedule row is open again (spec 31.6)."""
    position_name = doc.get(JOURNAL_ENTRY_COLLECTION_ITEM_FIELDNAME)
    if not position_name:
        return
    position = frappe.db.get_value(POSITION_DOCTYPE, position_name, ["sales_invoice", "payment_term"], as_dict=True)
    adjust_row_payment(
        position.sales_invoice, position.payment_term, -flt(doc.get(JOURNAL_ENTRY_RETURN_PRINCIPAL_FIELDNAME))
    )


def handle_cancelled_return_entry(doc, method: str | None = None) -> None:
    """A cancelled journal entry of a return closes the row again and sends position and debit to review."""
    position_name = doc.get(JOURNAL_ENTRY_COLLECTION_ITEM_FIELDNAME)
    if not position_name:
        return
    if not frappe.has_permission(POSITION_DOCTYPE, "write", doc=position_name):
        frappe.throw(
            _(
                "Der Buchungssatz gehoert zur Einzugsposition {0}. Stornieren darf ihn nur ein Benutzer mit den Rollen "
                "des Kassenwarts."
            ).format(position_name),
            frappe.PermissionError,
        )
    run = get_locked_run(frappe.db.get_value(POSITION_DOCTYPE, position_name, "collection_run"))
    position = frappe.get_doc(POSITION_DOCTYPE, position_name)
    if position.return_decision:
        frappe.throw(
            _(
                "Zur Ruecklastschrift der Position {0} ist schon entschieden ({1}). Storniere die Gegenbuchung nicht; "
                "korrigiere den Fall im Banking-Bereich."
            ).format(position.name, _(position.return_decision))
        )
    adjust_row_payment(
        position.sales_invoice, position.payment_term, flt(doc.get(JOURNAL_ENTRY_RETURN_PRINCIPAL_FIELDNAME))
    )
    reason = _(
        "Die Gegenbuchung {0} wurde storniert. ERPverein bucht diese Ruecklastschrift nicht erneut. Klaere den Fall "
        "im Banking-Bereich und markiere die Position dann als erledigt."
    ).format(doc.name)
    set_position_review(position, reason)
    transaction = doc.get(JOURNAL_ENTRY_RETURN_BANK_TRANSACTION_FIELDNAME)
    if transaction and frappe.db.exists("Bank Transaction", transaction):
        set_credit_match(transaction, MATCH_STATUS_REVIEW, reason)
    refresh_run_settlement(run)


def decide_returned_position(position_name: str, decision: str, due_date=None, note: str | None = None) -> dict:
    """The treasurer decides after a return: collect again with a new due date, or wait for a transfer.

    Both go through 'Zahlungsplan bearbeiten' (Phase 1). ERPverein never collects again by itself."""
    run_name = frappe.db.get_value(POSITION_DOCTYPE, position_name, "collection_run")
    if not run_name:
        frappe.throw(_("Die Einzugsposition {0} existiert nicht.").format(position_name))
    run = get_locked_run(run_name)
    position = frappe.get_doc(POSITION_DOCTYPE, position_name)
    position.check_permission("write")
    if position.status != POSITION_STATUS_RETURNED or position.return_decision:
        frappe.throw(_("Entscheiden laesst sich nur eine zurueckbelastete Position ohne Entscheidung."))
    if decision not in RETURN_DECISIONS:
        frappe.throw(_("Die Entscheidung muss {0} sein.").format(" oder ".join(RETURN_DECISIONS)))
    if frappe.db.get_value("Journal Entry", position.return_journal_entry, "docstatus") != 1:
        frappe.throw(_("Die Gegenbuchung {0} ist nicht gebucht.").format(position.return_journal_entry))
    note = require_reason(note) if cstr(note).strip() else None
    if decision == RETURN_DECISION_RECOLLECT:
        due_date = check_recollection_date(position, due_date)
        mode_of_payment = None
    else:
        due_date = getdate(due_date) if due_date else None
        if due_date and due_date < getdate(nowdate()):
            frappe.throw(_("Die Faelligkeit darf nicht in der Vergangenheit liegen."))
        mode_of_payment = MODE_OF_PAYMENT_TRANSFER
    text = _("{0}: entschieden von {1} am {2}").format(
        _(decision), frappe.session.user, formatdate(nowdate(), "dd.MM.yyyy")
    )
    if due_date:
        text += ", " + _("neue Faelligkeit {0}").format(formatdate(due_date, "dd.MM.yyyy"))
    if note:
        text += ": " + note
    text = truncate_note(text)
    # The decision frees the row first; the schedule change then runs the checks of Phase 1.
    save_position(position, {"return_decision": decision, "return_decision_note": text})
    result = change_schedule_row(
        position.sales_invoice,
        position.payment_term,
        due_date=due_date,
        mode_of_payment=mode_of_payment,
        comment=_("Ruecklastschrift der Einzugsposition {0}. {1}").format(position.name, text),
    )
    position.add_comment("Info", text)
    run.add_comment("Info", _("Position {0}: {1}").format(position.name, text))
    refresh_run_settlement(run)
    return {
        "position": position.name,
        "decision": decision,
        "run": run.name,
        "run_status": run.status,
        "invoice": result["invoice"],
        "prenotification_status": result["prenotification_status"],
    }


def check_recollection_date(position, due_date):
    if not due_date:
        frappe.throw(_("Fuer einen neuen Einzug braucht die Zeile eine neue Faelligkeit."))
    due_date = getdate(due_date)
    invoice = frappe.db.get_value(
        "Sales Invoice", position.sales_invoice, ["name", "company", SALES_INVOICE_BILLING_KIND_FIELDNAME], as_dict=True
    )
    profile = get_active_export_profile(invoice.company)
    if not profile:
        frappe.throw(_("Es gibt kein aktives SEPA-Exportprofil."))
    earliest = getdate(
        earliest_collection_date(frappe.db.get_value("SEPA Exportprofil", profile.name, "lead_time_business_days"))
    )
    if is_prenotification_required(invoice, profile):
        # The invoice is the pre-notification; it needs the notice period before the new due date (E2, E13).
        earliest = max(earliest, getdate(add_days(nowdate(), profile.prenotification_days)))
    if due_date < earliest:
        frappe.throw(
            _(
                "Die neue Faelligkeit muss am oder nach dem {0} liegen (Vorlauf und Frist der Vorabankuendigung)."
            ).format(formatdate(earliest, "dd.MM.yyyy"))
        )
    return due_date


def close_settled_runs(today=None) -> None:
    """Daily job: a settled run is closed when the refund period of 8 weeks is over (spec 37.5)."""
    today = getdate(today or nowdate())
    summary = new_summary()
    for name in frappe.get_list(
        RUN_DOCTYPE,
        filters={
            "status": ["in", [RUN_STATUS_SETTLED, RUN_STATUS_PARTLY_RETURNED]],
            "collection_date": ["<=", add_days(today, -REFUND_PERIOD_DAYS)],
        },
        pluck="name",
        order_by="collection_date asc, name asc",
        limit_page_length=0,
    ):
        run_step(summary, name, close_run, name, today)


def close_run(run_name: str, today, summary=None) -> None:
    refresh_run_settlement(get_locked_run(run_name), today=today)


def describe_return(position) -> str:
    """Reason of a returned position in the work queue: bank transaction, journal entry, amounts and reason."""
    text = _("Ruecklastschrift (Bankumsatz {0}, Gegenbuchung {1}): Hauptbetrag {2}, Gebuehr {3}.").format(
        position.return_bank_transaction,
        position.return_journal_entry,
        format_amount(position.return_principal_amount),
        format_amount(position.return_fee_amount),
    )
    if position.return_reason_text:
        text += " " + _("Grund: {0} ({1}).").format(position.return_reason_text, position.return_reason_code)
    return text
