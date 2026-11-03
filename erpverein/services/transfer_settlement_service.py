import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from erpverein.custom_fields import (
    BANK_TRANSACTION_CATEGORY_FIELDNAME,
    BANK_TRANSACTION_CUSTOMER_REFERENCE_FIELDNAME,
    BANK_TRANSACTION_END_TO_END_FIELDNAME,
    BANK_TRANSACTION_MATCH_STATUS_FIELDNAME,
    PAYMENT_ENTRY_BANK_TRANSACTION_FIELDNAME,
    PAYMENT_ENTRY_TRANSFER_KEY_FIELDNAME,
    SALES_INVOICE_BILLING_KIND_FIELDNAME,
    SALES_INVOICE_PAYER_DOCTYPE_FIELDNAME,
    SALES_INVOICE_PAYER_FIELDNAME,
)
from erpverein.services.bank_statement_parser import normalize_iban
from erpverein.services.billing_common import stable_hash
from erpverein.services.payment_schedule_service import AMOUNT_TOLERANCE
from erpverein.services.sepa_collection_service import (
    POSITION_DOCTYPE,
    POSITION_STATUS_RETURNED,
    has_accounts_role,
    require_reason,
)
from erpverein.services.sepa_export_profile_service import get_active_export_profile
from erpverein.services.sepa_settlement_matcher import to_amount
from erpverein.services.sepa_settlement_service import (
    MATCH_STATUS_MATCHED,
    MATCH_STATUS_OPEN,
    MATCH_STATUS_REVIEW,
    SETTLED_POSITION_STATUSES,
    SETTLEMENT_FLAG,
    assert_reconciliation_access,
    lock_bank_transaction,
    new_summary,
    run_step,
    set_credit_match,
)
from erpverein.services.transfer_settlement_matcher import (
    ACTION_BOOK,
    ACTION_OPEN,
    ACTION_REVIEW,
    ACTION_SKIP,
    ACTION_SUGGEST,
    RECHECK_HINT,
    Invoice,
    Payer,
    ScheduleRow,
    TransferCredit,
    TransferDecision,
    TransferFacts,
    decide_transfer,
    describe_decision,
    invoice_numbers_in,
    is_transfer_credit,
    payer_ids_in,
    payer_kind,
    proposal_hash,
)
from erpverein.setup_data import MODE_OF_PAYMENT_DIRECT_DEBIT, MODE_OF_PAYMENT_TRANSFER


MATCH_STATUS_PROPOSAL = "Vorschlag"
# Credits in "Manuelle Pruefung" wait for a person: ERPverein never books them on its own afterwards.
TRANSFER_MATCH_STATUSES = (MATCH_STATUS_OPEN, MATCH_STATUS_PROPOSAL)
MATCH_STATUS_BY_ACTION = {
    ACTION_SUGGEST: MATCH_STATUS_PROPOSAL,
    ACTION_REVIEW: MATCH_STATUS_REVIEW,
    ACTION_OPEN: MATCH_STATUS_OPEN,
}
BOOKABLE_ACTIONS = (ACTION_BOOK, ACTION_SUGGEST)
INVOICE_FIELDS = [
    "name",
    "creation",
    "customer",
    "customer_name",
    "currency",
    "party_account_currency",
    "outstanding_amount",
    "posting_date",
    "due_date",
    SALES_INVOICE_BILLING_KIND_FIELDNAME,
    SALES_INVOICE_PAYER_DOCTYPE_FIELDNAME,
    SALES_INVOICE_PAYER_FIELDNAME,
]
SCHEDULE_FIELDS = ["name", "parent", "idx", "payment_term", "due_date", "outstanding", "mode_of_payment"]
MAX_REFERENCE_LENGTH = 140
# A cancelled invoice is amended as <name>-1, -2 ...; ERPverein follows at most this many amendments.
MAX_AMENDMENTS = 9


class TransferContext:
    """Open invoices of the bank account's company, read once per job and refreshed after every booking."""

    def __init__(self, bank_account: str):
        account = frappe.db.get_value("Bank Account", bank_account, ["company", "account"], as_dict=True)
        if not account or not account.company or not account.account:
            frappe.throw(_("Das Bankkonto {0} braucht ein Unternehmen und ein Hauptbuchkonto.").format(bank_account))
        self.bank_account = bank_account
        self.company = account.company
        self.currency = frappe.db.get_value("Account", account.account, "account_currency")
        profile = get_active_export_profile(self.company)
        self.go_live_date = getdate(profile.go_live_date) if profile and profile.get("go_live_date") else None
        self.reload()

    def reload(self) -> None:
        self.invoices = {}
        self.details = {}
        self.ibans = {}
        self.load_invoices()

    def load_invoices(self, names=None) -> None:
        filters = {
            "docstatus": 1,
            "company": self.company,
            "is_return": 0,
            "outstanding_amount": [">", AMOUNT_TOLERANCE],
        }
        if names is not None:
            names = list(names)
            for name in names:
                self.invoices.pop(name, None)
                self.details.pop(name, None)
            if not names:
                return
            filters["name"] = ["in", names]
        rows = [
            row
            for row in frappe.get_list("Sales Invoice", filters=filters, fields=INVOICE_FIELDS, limit_page_length=0)
            if row.currency == self.currency
        ]
        if not rows:
            return
        names = [row.name for row in rows]
        schedules = {}
        for item in frappe.get_list(
            "Payment Schedule",
            parent_doctype="Sales Invoice",
            filters={"parenttype": "Sales Invoice", "parent": ["in", names]},
            fields=SCHEDULE_FIELDS,
            order_by="idx asc",
            limit_page_length=0,
        ):
            schedules.setdefault(item.parent, []).append(item)
        blocking = {}
        waiting = {}
        for position in frappe.get_list(
            POSITION_DOCTYPE,
            filters={
                "sales_invoice": ["in", names],
                "active_key": ["is", "set"],
                "status": ["not in", SETTLED_POSITION_STATUSES],
            },
            fields=["name", "sales_invoice", "status"],
            order_by="name asc",
            limit_page_length=0,
        ):
            # A returned position without decision keeps its row; the transfer waits for the treasurer.
            target = waiting if position.status == POSITION_STATUS_RETURNED else blocking
            target.setdefault(position.sales_invoice, []).append(position.name)
        for row in rows:
            self.invoices[row.name] = to_invoice(
                row, schedules.get(row.name, []), blocking.get(row.name, []), waiting.get(row.name, [])
            )
            self.details[row.name] = row

    def customer_invoices(self, customer: str) -> tuple[Invoice, ...]:
        return tuple(invoice for invoice in self.invoices.values() if invoice.customer == customer)

    def facts_for_customers(self) -> tuple[dict, dict]:
        invoices = {}
        names = {}
        for invoice in self.invoices.values():
            invoices.setdefault(invoice.customer, []).append(invoice)
            names[invoice.customer] = self.details[invoice.name].customer_name or invoice.customer
        return {customer: tuple(rows) for customer, rows in invoices.items()}, names

    def named_invoice(self, number: str) -> Invoice | None:
        if number in self.invoices:
            return self.invoices[number]
        number = current_amendment(number)
        if number in self.invoices:
            return self.invoices[number]
        values = frappe.db.get_value(
            "Sales Invoice",
            number,
            ["name", "docstatus", "company", "currency", "is_return", "outstanding_amount", "customer", "posting_date"],
            as_dict=True,
        )
        if not values:
            return None
        if not frappe.has_permission("Sales Invoice", "read", doc=number):
            reason = _("keine Leseberechtigung")
        else:
            reason = closed_reason(values, self)
            if not reason:
                # Submitted after the job started: read it like every other open invoice.
                self.load_invoices([number])
                if number in self.invoices:
                    return self.invoices[number]
                reason = _("nicht offen")
        return Invoice(
            values.name,
            values.customer,
            to_amount(values.outstanding_amount),
            getdate(values.posting_date),
            closed_reason=reason,
        )

    def payer(self, reference: str) -> Payer | None:
        doctype, billing_kind = payer_kind(reference)
        if not frappe.db.exists(doctype, reference) or not frappe.has_permission(doctype, "read", doc=reference):
            return None
        customer = frappe.db.get_value(doctype, reference, "customer")
        if not customer:
            return Payer(reference)
        invoices = tuple(
            invoice
            for invoice in self.customer_invoices(customer)
            if invoice.billing_kind == billing_kind
            and payer_of(self.details[invoice.name]) in ((doctype, reference), (None, None))
        )
        return Payer(reference, customer, invoices)

    def iban_customers(self, iban: object) -> tuple[str, ...]:
        iban = normalize_iban(iban)
        if not iban:
            return ()
        if iban not in self.ibans:
            # ERPNext stores the IBAN as typed; ERPverein mandates store it without spaces.
            grouped = " ".join(iban[index : index + 4] for index in range(0, len(iban), 4))
            parties = frappe.get_list(
                "Bank Account",
                filters={
                    "iban": ["in", [iban, grouped]],
                    "is_company_account": 0,
                    "party_type": "Customer",
                    "party": ["is", "set"],
                },
                pluck="party",
                limit_page_length=0,
            )
            self.ibans[iban] = tuple(sorted(set(parties)))
        return self.ibans[iban]


def current_amendment(number: str) -> str:
    """The payer writes the number of the invoice they got; after cancel and amend, its amendment takes the money."""
    name = number
    for _attempt in range(MAX_AMENDMENTS):
        if frappe.db.get_value("Sales Invoice", name, "docstatus") != 2:
            break
        amendment = frappe.db.get_value("Sales Invoice", {"amended_from": name}, "name")
        if not amendment:
            break
        name = amendment
    return name


def to_invoice(row, schedule: list, blocking: list, waiting: list = ()) -> Invoice:
    from erpnext.accounts.doctype.dunning.dunning import get_payment_schedule_outstanding

    # Payments without payment term reduce the oldest rows first, like the ERPNext dunning (spec 31.5).
    outstanding = get_payment_schedule_outstanding(row, schedule) if schedule else {}
    return Invoice(
        name=row.name,
        customer=row.customer,
        outstanding=to_amount(row.outstanding_amount),
        posting_date=getdate(row.posting_date),
        due_date=getdate(row.due_date) if row.due_date else None,
        billing_kind=row.get(SALES_INVOICE_BILLING_KIND_FIELDNAME) or "",
        rows=tuple(
            ScheduleRow(
                item.payment_term or "",
                getdate(item.due_date),
                to_amount(outstanding.get(item.name, item.outstanding)),
                item.mode_of_payment == MODE_OF_PAYMENT_DIRECT_DEBIT,
                cint(item.idx),
            )
            for item in schedule
        ),
        blocking_positions=tuple(blocking),
        waiting_returns=tuple(waiting),
        created_on=getdate(row.creation) if row.get("creation") else None,
    )


def payer_of(row) -> tuple:
    return (row.get(SALES_INVOICE_PAYER_DOCTYPE_FIELDNAME) or None, row.get(SALES_INVOICE_PAYER_FIELDNAME) or None)


def closed_reason(values, context: TransferContext) -> str | None:
    if values.docstatus == 0:
        return _("nicht gebucht")
    if values.docstatus == 2:
        return _("storniert")
    if cint(values.is_return):
        return _("Gutschrift")
    if values.company != context.company:
        return _("gehoert zu einem anderen Unternehmen")
    if values.currency != context.currency:
        return _("andere Waehrung")
    if flt(values.outstanding_amount) <= AMOUNT_TOLERANCE:
        return _("bezahlt")
    return None


def to_transfer_credit(transaction) -> TransferCredit:
    return TransferCredit(
        name=transaction.name,
        booking_date=getdate(transaction.date),
        amount=to_amount(transaction.deposit),
        category=transaction.get(BANK_TRANSACTION_CATEGORY_FIELDNAME) or "",
        texts=tuple(
            value
            for value in (
                transaction.description,
                transaction.reference_number,
                transaction.get(BANK_TRANSACTION_CUSTOMER_REFERENCE_FIELDNAME),
                transaction.get(BANK_TRANSACTION_END_TO_END_FIELDNAME),
                transaction.transaction_type,
            )
            if value
        ),
        party_name=transaction.bank_party_name or "",
        rule=transaction.get("matched_transaction_rule") or "",
    )


def evaluate_transfer(transaction, context: TransferContext) -> TransferDecision:
    credit = to_transfer_credit(transaction)
    if not is_transfer_credit(credit):
        return TransferDecision(credit.name, ACTION_SKIP)
    # Named invoices first: an invoice submitted after the job started joins the open invoices.
    invoices = {number: context.named_invoice(number) for number in invoice_numbers_in(credit.texts)}
    payers = {reference: context.payer(reference) for reference in payer_ids_in(credit.texts)}
    customer_invoices, customer_names = context.facts_for_customers()
    facts = TransferFacts(
        go_live_date=context.go_live_date,
        invoices=invoices,
        payers=payers,
        iban_customers=context.iban_customers(transaction.bank_party_iban),
        customer_invoices=customer_invoices,
        customer_names=customer_names,
    )
    return decide_transfer(credit, facts)


def get_transfer_credits(bank_account: str) -> list[str]:
    names = frappe.get_list(
        "Bank Transaction",
        filters={
            "bank_account": bank_account,
            "docstatus": 1,
            "deposit": [">", 0],
            "allocated_amount": ["<=", AMOUNT_TOLERANCE],
            BANK_TRANSACTION_MATCH_STATUS_FIELDNAME: ["in", TRANSFER_MATCH_STATUSES],
        },
        pluck="name",
        order_by="date asc, name asc",
        limit_page_length=0,
    )
    if not names:
        return []
    taken = set(
        frappe.get_all(
            POSITION_DOCTYPE,
            filters={"settlement_bank_transaction": ["in", names]},
            pluck="settlement_bank_transaction",
        )
    )
    return [name for name in names if name not in taken]


def settle_transfer_credits(bank_account: str, summary) -> None:
    """Job step after the direct debit settlement: one savepoint per bank transaction (spec 22.1)."""
    names = get_transfer_credits(bank_account)
    if not names:
        return
    context = TransferContext(bank_account)
    for name in names:
        reason = run_step(summary, name, settle_transfer_credit, name, context)
        if reason:
            # The rolled back step may have refreshed invoices that are open again now.
            context.reload()
            run_step(summary, name, record_transfer_failure, name, reason)


def transfer_is_open(transaction) -> bool:
    return (
        transaction.docstatus == 1
        and flt(transaction.deposit) > 0
        and flt(transaction.allocated_amount) <= AMOUNT_TOLERANCE
        and transaction.get(BANK_TRANSACTION_MATCH_STATUS_FIELDNAME) in TRANSFER_MATCH_STATUSES
        and not frappe.db.exists(POSITION_DOCTYPE, {"settlement_bank_transaction": transaction.name})
    )


def settle_transfer_credit(transaction_name: str, context: TransferContext, summary) -> None:
    transaction = lock_bank_transaction(transaction_name)
    if not transfer_is_open(transaction):
        return
    decision = evaluate_transfer(transaction, context)
    if decision.action == ACTION_BOOK:
        decision = recheck_under_lock(transaction, decision, context)
    if decision.action == ACTION_SKIP:
        return
    if decision.action == ACTION_BOOK:
        book_transfer(transaction, decision, context, summary)
        return
    status = MATCH_STATUS_BY_ACTION[decision.action]
    set_credit_match(transaction.name, status, describe_decision(decision))
    if status == MATCH_STATUS_PROPOSAL:
        summary.proposals += 1
    elif status == MATCH_STATUS_REVIEW:
        summary.review += 1


def recheck_under_lock(transaction, decision: TransferDecision, context: TransferContext) -> TransferDecision:
    """Lock the invoices of a booking, read them again and decide again (spec 22.3)."""
    for invoice in sorted(decision.invoices):
        frappe.db.get_value("Sales Invoice", invoice, "name", for_update=True)
    context.load_invoices(decision.invoices)
    return evaluate_transfer(transaction, context)


def book_transfer(transaction, decision: TransferDecision, context: TransferContext, summary, confirmed_by=None):
    payments = [
        ensure_transfer_payment(
            transaction,
            customer,
            [allocation for allocation in decision.allocations if allocation.customer == customer],
        )
        for customer in decision.customers
    ]
    linked = {row.payment_entry for row in transaction.payment_entries if row.payment_document == "Payment Entry"}
    vouchers = [
        {"payment_doctype": "Payment Entry", "payment_name": payment.name, "amount": flt(payment.paid_amount, 2)}
        for payment in payments
        if payment.name not in linked
    ]
    if vouchers:
        from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import reconcile_vouchers

        # Explicit vouchers only: auto_reconcile_vouchers would distribute every candidate by rank (spec 31.7).
        reconcile_vouchers(transaction.name, json.dumps(vouchers))
    booked = TransferDecision(
        decision.credit,
        ACTION_BOOK,
        decision.allocations,
        decision.signals,
        decision.conflicts,
        decision.hints,
        rest=decision.rest,
    )
    note = _("{0} Zahlung: {1}.").format(describe_decision(booked), ", ".join(payment.name for payment in payments))
    if confirmed_by:
        note = _("Vorschlag bestaetigt von {0}. {1}").format(confirmed_by, note)
    unallocated = flt(frappe.db.get_value("Bank Transaction", transaction.name, "unallocated_amount"))
    # An overpayment leaves the rest open on the bank transaction; a person decides about it (spec 16.7).
    status = MATCH_STATUS_MATCHED if unallocated <= AMOUNT_TOLERANCE else MATCH_STATUS_REVIEW
    set_credit_match(transaction.name, status, note)
    summary.payments += sum(1 for payment in payments if payment.created)
    summary.transfers += 1
    if status == MATCH_STATUS_REVIEW:
        summary.review += 1
    context.load_invoices(decision.invoices)
    return payments


def transfer_key(transaction_name: str, customer: str) -> str:
    # One payment per bank transaction and customer; the unique key stops a second payment (spec 22.2).
    return stable_hash({"bank_transaction": transaction_name, "party_type": "Customer", "party": customer})


def ensure_transfer_payment(transaction, customer: str, allocations: list) -> frappe._dict:
    key = transfer_key(transaction.name, customer)
    existing = frappe.db.get_value(
        "Payment Entry", {PAYMENT_ENTRY_TRANSFER_KEY_FIELDNAME: key}, ["name", "docstatus", "paid_amount"], as_dict=True
    )
    if existing and existing.docstatus == 1:
        return frappe._dict(name=existing.name, paid_amount=existing.paid_amount, created=False)
    if existing:
        frappe.throw(
            _(
                "Die Zahlung {0} zu diesem Bankumsatz ist storniert oder nicht gebucht. ERPverein bucht keine zweite "
                "Zahlung. Klaere den Fall im Banking-Bereich."
            ).format(existing.name)
        )
    payment = build_transfer_payment(transaction, customer, allocations, key)
    payment.flags[SETTLEMENT_FLAG] = True
    payment.insert()
    payment.submit()
    return frappe._dict(name=payment.name, paid_amount=payment.paid_amount, created=True)


def build_transfer_payment(transaction, customer: str, allocations: list, key: str):
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

    total = flt(sum(allocation.amount for allocation in allocations), 2)
    payment = get_payment_entry(
        "Sales Invoice",
        allocations[0].invoice,
        party_amount=total,
        bank_account=frappe.db.get_value("Bank Account", transaction.bank_account, "account"),
        reference_date=transaction.date,
    )
    if payment.party != customer:
        frappe.throw(_("Die Rechnung {0} gehoert nicht zum Kunden {1}.").format(allocations[0].invoice, customer))
    payment.update(
        {
            "posting_date": transaction.date,
            "bank_account": transaction.bank_account,
            "mode_of_payment": MODE_OF_PAYMENT_TRANSFER,
            "reference_no": transfer_reference(transaction),
            "reference_date": transaction.date,
            PAYMENT_ENTRY_BANK_TRANSACTION_FIELDNAME: transaction.name,
            PAYMENT_ENTRY_TRANSFER_KEY_FIELDNAME: key,
        }
    )
    payment.set_bank_account_data()
    payment.set("references", [])
    # One reference per schedule row with its payment term, so ERPNext updates exactly these rows (spec 31.5).
    for allocation in allocations:
        payment.append(
            "references",
            {
                "reference_doctype": "Sales Invoice",
                "reference_name": allocation.invoice,
                "payment_term": allocation.payment_term or None,
                "allocated_amount": flt(allocation.amount, 2),
            },
        )
    return payment


def transfer_reference(transaction) -> str:
    return (transaction.reference_number or transaction.transaction_id or transaction.name)[:MAX_REFERENCE_LENGTH]


def record_transfer_failure(transaction_name: str, reason: str, summary) -> None:
    transaction = lock_bank_transaction(transaction_name)
    if not transfer_is_open(transaction):
        return
    # The status stays: the next job tries again, and a suggestion can still be confirmed.
    set_credit_match(
        transaction.name,
        transaction.get(BANK_TRANSACTION_MATCH_STATUS_FIELDNAME),
        _("Automatische Zuordnung fehlgeschlagen: {0} {1}").format(reason, RECHECK_HINT),
    )
    summary.errors += 1


def get_transfer_proposal(transaction_name: str) -> dict:
    if not has_accounts_role():
        frappe.throw(_("Vorschlaege brauchen die Rolle Accounts User oder Accounts Manager."), frappe.PermissionError)
    transaction = frappe.get_doc("Bank Transaction", transaction_name)
    transaction.check_permission("read")
    if transaction.get(BANK_TRANSACTION_MATCH_STATUS_FIELDNAME) != MATCH_STATUS_PROPOSAL:
        frappe.throw(_("Der Bankumsatz {0} hat keinen Vorschlag.").format(transaction.name))
    return proposal_response(transaction, evaluate_transfer(transaction, TransferContext(transaction.bank_account)))


def proposal_response(transaction, decision: TransferDecision) -> dict:
    return {
        "bank_transaction": transaction.name,
        "bookable": decision.action in BOOKABLE_ACTIONS,
        "note": describe_decision(decision),
        "amount": flt(transaction.deposit, 2),
        "rest": flt(decision.rest, 2),
        "allocations": [
            {
                "customer": allocation.customer,
                "invoice": allocation.invoice,
                "payment_term": allocation.payment_term or None,
                "amount": flt(allocation.amount, 2),
            }
            for allocation in decision.allocations
        ],
        "proposal_hash": proposal_hash(decision),
    }


def confirm_transfer_proposal(transaction_name: str, proposal: str | None = None) -> dict:
    """Book exactly the suggestion the treasurer saw; ERPverein checks it again first (spec 16.9)."""
    assert_reconciliation_access()
    transaction = lock_bank_transaction(transaction_name)
    if transaction.get(BANK_TRANSACTION_MATCH_STATUS_FIELDNAME) != MATCH_STATUS_PROPOSAL or not transfer_is_open(
        transaction
    ):
        frappe.throw(
            _("Der Bankumsatz {0} hat keinen offenen Vorschlag mehr. Bitte neu laden.").format(transaction.name)
        )
    context = TransferContext(transaction.bank_account)
    decision = evaluate_transfer(transaction, context)
    if decision.action in BOOKABLE_ACTIONS:
        decision = recheck_under_lock(transaction, decision, context)
    if decision.action not in BOOKABLE_ACTIONS:
        frappe.throw(_("ERPverein bucht den Vorschlag nicht: {0}").format(describe_decision(decision)))
    if not proposal or proposal != proposal_hash(decision):
        frappe.throw(_("Der Vorschlag hat sich geaendert. Bitte neu laden."))
    summary = new_summary()
    payments = book_transfer(transaction, decision, context, summary, confirmed_by=frappe.session.user)
    transaction.add_comment("Info", _("Vorschlag bestaetigt. Zahlung: {0}").format(", ".join(p.name for p in payments)))
    return {
        "bank_transaction": transaction.name,
        "payments": [payment.name for payment in payments],
        "match_status": frappe.db.get_value(
            "Bank Transaction", transaction.name, BANK_TRANSACTION_MATCH_STATUS_FIELDNAME
        ),
    }


def reject_transfer_proposal(transaction_name: str, reason: str | None = None) -> dict:
    if not has_accounts_role():
        frappe.throw(_("Vorschlaege brauchen die Rolle Accounts User oder Accounts Manager."), frappe.PermissionError)
    transaction = lock_bank_transaction(transaction_name)
    if transaction.get(BANK_TRANSACTION_MATCH_STATUS_FIELDNAME) != MATCH_STATUS_PROPOSAL:
        frappe.throw(_("Der Bankumsatz {0} hat keinen Vorschlag.").format(transaction.name))
    reason = require_reason(reason)
    note = _("Vorschlag verworfen von {0}: {1}. ERPverein ordnet diesen Bankumsatz nicht mehr selbst zu.").format(
        frappe.session.user, reason
    )
    set_credit_match(transaction.name, MATCH_STATUS_REVIEW, note)
    transaction.add_comment("Info", note)
    return {"bank_transaction": transaction.name, "match_status": MATCH_STATUS_REVIEW}


def handle_cancelled_transfer_payment(doc, method: str | None = None) -> None:
    """ERPNext removes a cancelled payment from the bank transaction; ERPverein books it never again (spec 22.2)."""
    transaction_name = doc.get(PAYMENT_ENTRY_BANK_TRANSACTION_FIELDNAME)
    if not doc.get(PAYMENT_ENTRY_TRANSFER_KEY_FIELDNAME) or not transaction_name:
        return
    if not frappe.db.exists("Bank Transaction", transaction_name):
        return
    if not frappe.has_permission("Bank Transaction", "write", doc=transaction_name):
        frappe.throw(
            _(
                "Die Zahlung gehoert zum Bankumsatz {0}. Stornieren darf sie nur ein Benutzer mit Schreibrecht darauf."
            ).format(transaction_name),
            frappe.PermissionError,
        )
    set_credit_match(
        transaction_name,
        MATCH_STATUS_REVIEW,
        _(
            "Die Zahlung {0} wurde storniert. ERPverein bucht diesen Bankumsatz nicht erneut. "
            "Klaere den Fall im Banking-Bereich."
        ).format(doc.name),
    )
