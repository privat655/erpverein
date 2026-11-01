import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, cstr, flt, fmt_money, formatdate, get_datetime, getdate, now_datetime

from erpverein.custom_fields import (
    PAYMENT_SCHEDULE_CHANGED_AT_FIELDNAME,
    SALES_INVOICE_PRENOTIFICATION_SENT_AT_FIELDNAME,
    SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME,
    SALES_INVOICE_SEPA_MANDATE_FIELDNAME,
)
from erpverein.services.payment_schedule_service import (
    PRENOTIFICATION_MANUAL,
    PRENOTIFICATION_PENDING,
    PRENOTIFICATION_RESEND,
    PRENOTIFICATION_SENT,
    has_direct_debit_rows,
)
from erpverein.services.sepa_collection_schedule_service import next_target_business_day
from erpverein.services.sepa_export_profile_service import get_active_export_profile, get_prenotification_days
from erpverein.services.sepa_mandat_service import normalize_iban
from erpverein.setup_data import MODE_OF_PAYMENT_DIRECT_DEBIT


PRINT_FORMAT_NAME = "ERPverein Rechnung DIN 5008"
EMAIL_QUEUE_STATUS_SENT = "Sent"
REFRESH_BATCH_SIZE = 500


def refresh_prenotification_status() -> None:
    invoices = frappe.get_all(
        "Sales Invoice",
        filters={
            "docstatus": 1,
            SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME: ["in", [PRENOTIFICATION_PENDING, PRENOTIFICATION_RESEND]],
        },
        pluck="name",
        order_by="modified asc",
        limit_page_length=REFRESH_BATCH_SIZE,
    )
    for invoice in invoices:
        update_prenotification_status(invoice)


def update_prenotification_status(invoice: str) -> str | None:
    doc = frappe.get_doc("Sales Invoice", invoice)
    status = doc.get(SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME)
    if status not in {PRENOTIFICATION_PENDING, PRENOTIFICATION_RESEND}:
        return status
    sent_at = get_last_prenotification_sent_at(invoice)
    changed_at = last_direct_debit_change(doc)
    if not sent_at or (changed_at and sent_at <= changed_at):
        return status
    frappe.db.set_value(
        "Sales Invoice",
        invoice,
        {
            SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME: PRENOTIFICATION_SENT,
            SALES_INVOICE_PRENOTIFICATION_SENT_AT_FIELDNAME: sent_at,
        },
        update_modified=False,
    )
    return PRENOTIFICATION_SENT


def get_last_prenotification_sent_at(invoice: str):
    communications = frappe.get_all(
        "Communication",
        filters={
            "reference_doctype": "Sales Invoice",
            "reference_name": invoice,
            "communication_medium": "Email",
            "sent_or_received": "Sent",
        },
        pluck="name",
    )
    if not communications:
        return None
    queues = frappe.get_all(
        "Email Queue",
        filters={"communication": ["in", communications], "status": EMAIL_QUEUE_STATUS_SENT},
        fields=["modified", "attachments"],
    )
    sent = [get_datetime(queue.modified) for queue in queues if has_prenotification_attachment(queue.attachments, invoice)]
    return max(sent) if sent else None


def has_prenotification_attachment(attachments: str | None, invoice: str) -> bool:
    try:
        values = json.loads(attachments or "[]")
    except (TypeError, ValueError):
        return False
    return isinstance(values, list) and any(
        isinstance(value, dict)
        and cint(value.get("print_format_attachment"))
        and value.get("print_format") == PRINT_FORMAT_NAME
        and value.get("doctype") == "Sales Invoice"
        and value.get("name") == invoice
        for value in values
    )


def last_direct_debit_change(doc):
    stamps = [
        get_datetime(row.get(PAYMENT_SCHEDULE_CHANGED_AT_FIELDNAME))
        for row in doc.payment_schedule
        if row.mode_of_payment == MODE_OF_PAYMENT_DIRECT_DEBIT and row.get(PAYMENT_SCHEDULE_CHANGED_AT_FIELDNAME)
    ]
    return max(stamps) if stamps else None


def confirm_prenotification_manually(invoice: str) -> dict:
    doc = frappe.get_doc("Sales Invoice", invoice)
    doc.check_permission("write")
    if doc.docstatus != 1:
        frappe.throw(_("Nur gebuchte Rechnungen koennen eine Vorabankuendigung haben."))
    if not has_direct_debit_rows(doc):
        frappe.throw(_("Die Rechnung enthaelt keine Lastschriftzeile."))
    confirmed_at = now_datetime()
    frappe.db.set_value(
        "Sales Invoice",
        invoice,
        {
            SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME: PRENOTIFICATION_MANUAL,
            SALES_INVOICE_PRENOTIFICATION_SENT_AT_FIELDNAME: confirmed_at,
        },
    )
    doc.add_comment("Info", _("Vorabankuendigung manuell bestaetigt."))
    return {"invoice": invoice, "status": PRENOTIFICATION_MANUAL, "confirmed_at": str(confirmed_at)}


def get_prenotification_readiness(doc, row, collection_date, prenotification_days: int | None = None) -> tuple[bool, str | None]:
    sent_at = doc.get(SALES_INVOICE_PRENOTIFICATION_SENT_AT_FIELDNAME)
    if doc.get(SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME) not in {PRENOTIFICATION_SENT, PRENOTIFICATION_MANUAL} or not sent_at:
        return False, _("Die Vorabankuendigung fehlt.")
    changed_at = row.get(PAYMENT_SCHEDULE_CHANGED_AT_FIELDNAME)
    if changed_at and get_datetime(sent_at) <= get_datetime(changed_at):
        return False, _("Die Zeile wurde nach der Vorabankuendigung geaendert.")
    days = get_prenotification_days(doc.company) if prenotification_days is None else cint(prenotification_days)
    if getdate(sent_at) > getdate(add_days(collection_date, -days)):
        return False, _("Die Vorabankuendigung kommt zu spaet.")
    return True, None


def erpverein_payment_schedule_rows(doc) -> list[dict]:
    rows = []
    for row in sorted(doc.get("payment_schedule") or [], key=lambda row: (getdate(row.due_date), cint(row.idx))):
        is_direct_debit = row.mode_of_payment == MODE_OF_PAYMENT_DIRECT_DEBIT
        collection_date = next_target_business_day(row.due_date) if is_direct_debit else None
        rows.append(
            {
                "due_date": getdate(row.due_date),
                "due_date_display": format_print_date(row.due_date),
                "amount": flt(row.payment_amount),
                "amount_display": fmt_money(row.payment_amount, currency=doc.get("currency")),
                "mode_of_payment": row.mode_of_payment,
                "is_direct_debit": is_direct_debit,
                "collection_date": collection_date,
                "collection_date_display": format_print_date(collection_date) if collection_date else None,
            }
        )
    return rows


def erpverein_payment_details(doc) -> dict:
    profile = get_active_export_profile(doc.get("company"))
    mandate = None
    if doc.get(SALES_INVOICE_SEPA_MANDATE_FIELDNAME):
        mandate = frappe.db.get_value(
            "SEPA Mandat", doc.get(SALES_INVOICE_SEPA_MANDATE_FIELDNAME), ["mandatsreferenz", "mandatsdatum", "iban"], as_dict=True
        )
    bank_account = profile.bank_account if profile else frappe.db.get_value(
        "Bank Account", {"company": doc.get("company"), "is_company_account": 1, "is_default": 1, "disabled": 0}, "name"
    )
    club_account = frappe.db.get_value("Bank Account", bank_account, ["iban", "branch_code"], as_dict=True) if bank_account else None
    return {
        "posting_date_display": format_print_date(doc.get("posting_date")),
        "period_display": (
            f"{format_print_date(doc.get('from_date'))} – {format_print_date(doc.get('to_date'))}"
            if doc.get("from_date") and doc.get("to_date")
            else None
        ),
        "creditor_identifier": profile.creditor_identifier if profile else None,
        "mandate_reference": mandate.mandatsreferenz if mandate else None,
        "mandate_date_display": format_print_date(mandate.mandatsdatum) if mandate and mandate.mandatsdatum else None,
        "debtor_iban_masked": mask_iban_last_four(mandate.iban) if mandate else None,
        "club_iban": format_iban(club_account.iban) if club_account else None,
        "club_bic": club_account.branch_code if club_account else None,
    }


def format_print_date(value) -> str | None:
    return formatdate(value, "dd.MM.yyyy") if value else None


def mask_iban_last_four(value: object) -> str | None:
    iban = normalize_iban(value)
    return f"**** {iban[-4:]}" if iban and len(iban) >= 4 else None


def format_iban(value: object) -> str | None:
    iban = normalize_iban(value)
    return " ".join(iban[index : index + 4] for index in range(0, len(iban), 4)) if iban else cstr(value) or None
