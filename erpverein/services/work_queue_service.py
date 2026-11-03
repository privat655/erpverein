import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, formatdate, getdate, nowdate

from erpverein.custom_fields import (
    BANK_TRANSACTION_CATEGORY_FIELDNAME,
    BANK_TRANSACTION_MATCH_NOTE_FIELDNAME,
    BANK_TRANSACTION_MATCH_STATUS_FIELDNAME,
    SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME,
    SALES_INVOICE_SCHEDULE_NOTE_FIELDNAME,
)
from erpverein.services.bank_import_service import (
    IMPORT_DOCTYPE,
    RECONCILIATION_FAILED,
    STATUS_FAILED,
    STATUS_PARTIAL,
)
from erpverein.services.bank_statement_parser import (
    CATEGORY_BANK_FEE,
    CATEGORY_DIRECT_DEBIT_REJECT,
    CATEGORY_DIRECT_DEBIT_RETURN,
    mask_iban,
    mask_sensitive_text,
)
from erpverein.services.payment_schedule_service import AMOUNT_TOLERANCE, get_active_collection_positions
from erpverein.services.prenotification_service import get_prenotification_readiness
from erpverein.services.sepa_collection_service import (
    EXPORTED_RUN_STATUSES,
    POSITION_DOCTYPE,
    POSITION_STATUS_REVIEW,
    PROFILE_DOCTYPE,
    RUN_DOCTYPE,
    RUN_STATUS_REVIEW,
    earliest_collection_date,
    has_accounts_role,
    plan_collection_date,
)
from erpverein.services.sepa_export_profile_service import get_active_export_profile
from erpverein.setup_data import MODE_OF_PAYMENT_DIRECT_DEBIT


AREA_BANK_TRANSACTION = "Bankumsatz"
AREA_RUN = "Einzugslauf"
AREA_POSITION = "Einzugsposition"
AREA_BANK_IMPORT = "Bankimport"
AREA_SCHEDULE = "Zahlungsplan"
AREA_PRENOTIFICATION = "Vorabankündigung"
# Each area needs the read right on its DocType; an area without it stays hidden.
AREA_DOCTYPES = {
    AREA_BANK_TRANSACTION: "Bank Transaction",
    AREA_RUN: RUN_DOCTYPE,
    AREA_POSITION: POSITION_DOCTYPE,
    AREA_BANK_IMPORT: IMPORT_DOCTYPE,
    AREA_SCHEDULE: "Sales Invoice",
    AREA_PRENOTIFICATION: "Sales Invoice",
}
# Direct debit rows due within this many days show a missing or late pre-notification.
DEFAULT_DAYS_AHEAD = 45
MAX_TEXT_LENGTH = 140
BANK_TRANSACTION_FIELDS = [
    "name",
    "date",
    "deposit",
    "withdrawal",
    "unallocated_amount",
    "party_type",
    "party",
    "bank_party_name",
    "bank_party_iban",
    "description",
    "matched_transaction_rule",
    BANK_TRANSACTION_CATEGORY_FIELDNAME,
    BANK_TRANSACTION_MATCH_STATUS_FIELDNAME,
    BANK_TRANSACTION_MATCH_NOTE_FIELDNAME,
]
RETURN_CATEGORIES = (CATEGORY_DIRECT_DEBIT_RETURN, CATEGORY_DIRECT_DEBIT_REJECT)


def get_work_queue(filters=None) -> tuple[list, list, str]:
    """Every open case of the treasurer with reason, link and next action (spec 22.4)."""
    if not has_accounts_role():
        frappe.throw(
            _("Der Arbeitsvorrat braucht die Rolle Accounts User oder Accounts Manager."), frappe.PermissionError
        )
    filters = frappe._dict(filters or {})
    company = filters.company or frappe.defaults.get_user_default("Company")
    days_ahead = DEFAULT_DAYS_AHEAD if filters.days_ahead in (None, "") else max(0, cint(filters.days_ahead))
    rows = []
    hidden = []
    for area, collect in (
        (AREA_BANK_TRANSACTION, bank_transaction_cases),
        (AREA_RUN, run_cases),
        (AREA_POSITION, position_cases),
        (AREA_BANK_IMPORT, bank_import_cases),
        (AREA_SCHEDULE, schedule_cases),
        (AREA_PRENOTIFICATION, prenotification_cases),
    ):
        if filters.area and filters.area != area:
            continue
        if not frappe.has_permission(AREA_DOCTYPES[area], "read"):
            hidden.append(area)
            continue
        rows += collect(company, days_ahead)
    rows.sort(key=lambda row: (str(row["date"] or ""), row["area"], row["reference_name"]))
    message = _("{0} offene Faelle.").format(len(rows))
    if hidden:
        message += " " + _("Ohne Leserecht ausgeblendet: {0}.").format(", ".join(hidden))
    return get_columns(), rows, message


def case(area, date, doctype, name, amount=None, customer=None, status=None, reason=None, action=None, **extra):
    return {
        "area": area,
        "date": getdate(date) if date else None,
        "reference_doctype": doctype,
        "reference_name": name,
        "amount": flt(amount, 2) if amount is not None else None,
        "customer": customer,
        "status": status,
        # Notes may quote bank texts; only masked IBANs leave the report (spec 20.4).
        "reason": mask_sensitive_text(reason or ""),
        "next_action": action,
        "party": extra.get("party"),
        "purpose": extra.get("purpose"),
    }


def bank_transaction_cases(company, days_ahead) -> list[dict]:
    filters = {"docstatus": 1, "unallocated_amount": [">", AMOUNT_TOLERANCE]}
    if company:
        filters["company"] = company
    return [
        bank_transaction_case(row)
        for row in frappe.get_list(
            "Bank Transaction",
            filters=filters,
            fields=BANK_TRANSACTION_FIELDS,
            order_by="date asc, name asc",
            limit_page_length=0,
        )
    ]


def bank_transaction_case(row) -> dict:
    status = row.get(BANK_TRANSACTION_MATCH_STATUS_FIELDNAME) or None
    note = row.get(BANK_TRANSACTION_MATCH_NOTE_FIELDNAME)
    if status == "Vorschlag":
        reason, action = note, _("Bankumsatz oeffnen: Vorschlag pruefen, dann bestaetigen oder verwerfen.")
    elif status == "Manuelle Pruefung":
        reason = note or _("Manuelle Pruefung.")
        action = _("Im Banking-Bereich klaeren: Zahlung zuordnen, Rest buchen oder zurueckzahlen.")
    elif status == "Abgeglichen":
        reason = _(
            "ERPverein hatte den Bankumsatz abgeglichen. Er ist wieder offen (Abgleich aufgehoben oder storniert)."
        )
        action = _("Im Banking-Bereich erneut abgleichen.")
    elif status == "Offen":
        reason = note or _("Keine Zuordnung.")
        action = open_case_action(row)
    else:
        reason = _("Bankumsatz ohne ERPverein-Import.")
        action = _("Im Banking-Bereich zuordnen.")
    party = " ".join(
        value
        for value in (row.bank_party_name, mask_iban(row.bank_party_iban) if row.bank_party_iban else None)
        if value
    )
    return case(
        AREA_BANK_TRANSACTION,
        row.date,
        "Bank Transaction",
        row.name,
        flt(row.deposit) or -flt(row.withdrawal),
        row.party if row.party_type == "Customer" else None,
        status or _("ohne Status"),
        reason,
        action,
        party=party or None,
        purpose=mask_sensitive_text(row.description or "")[:MAX_TEXT_LENGTH] or None,
    )


def open_case_action(row) -> str:
    category = row.get(BANK_TRANSACTION_CATEGORY_FIELDNAME)
    if row.matched_transaction_rule:
        return _("Die Bank Transaction Rule {0} passt. Im Banking-Bereich bestaetigen.").format(
            row.matched_transaction_rule
        )
    if category in RETURN_CATEGORIES:
        return _("Ruecklastschrift im Banking-Bereich als Gegenbuchung erfassen.")
    if category == CATEGORY_BANK_FEE:
        return _("Im Banking-Bereich buchen. Fuer wiederkehrende Gebuehren eine Bank Transaction Rule anlegen.")
    if flt(row.deposit) > 0:
        return _(
            "Im Banking-Bereich zuordnen. Fehlt die Rechnung noch, prueft ERPverein beim naechsten Abgleich erneut."
        )
    return _("Im Banking-Bereich buchen. Fuer feste Vorgaenge eine Bank Transaction Rule anlegen.")


def company_runs(company) -> list[str] | None:
    if not company:
        return None
    return frappe.get_list(RUN_DOCTYPE, filters={"company": company}, pluck="name", limit_page_length=0)


def run_cases(company, days_ahead) -> list[dict]:
    filters = {"status": RUN_STATUS_REVIEW}
    if company:
        filters["company"] = company
    rows = [
        case(
            AREA_RUN,
            run.collection_date,
            RUN_DOCTYPE,
            run.name,
            run.total_amount,
            status=run.status,
            reason=run.settlement_note or _("Manuelle Pruefung."),
            action=_("Lauf oeffnen und die Positionen pruefen. Geklaerte Positionen als erledigt markieren."),
        )
        for run in frappe.get_list(
            RUN_DOCTYPE,
            filters=filters,
            fields=["name", "status", "collection_date", "total_amount", "settlement_note"],
            order_by="collection_date asc",
            limit_page_length=0,
        )
    ]
    today = getdate(nowdate())
    filters = {"status": ["in", list(EXPORTED_RUN_STATUSES)], "settlement_bank_transaction": ["is", "not set"]}
    if company:
        filters["company"] = company
    tolerances = {}
    for run in frappe.get_list(
        RUN_DOCTYPE,
        filters=filters,
        fields=["name", "status", "collection_date", "total_amount", "export_profile"],
        order_by="collection_date asc",
        limit_page_length=0,
    ):
        if run.export_profile not in tolerances:
            tolerances[run.export_profile] = cint(
                frappe.db.get_value(PROFILE_DOCTYPE, run.export_profile, "settlement_tolerance_days")
            )
        if getdate(add_days(run.collection_date, tolerances[run.export_profile])) >= today:
            continue
        rows.append(
            case(
                AREA_RUN,
                run.collection_date,
                RUN_DOCTYPE,
                run.name,
                run.total_amount,
                status=run.status,
                reason=_("Die Gutschrift zum Einzugsdatum {0} fehlt.").format(
                    formatdate(run.collection_date, "dd.MM.yyyy")
                ),
                action=_("Umsaetze abrufen. Fehlt die Gutschrift weiter, bei der Bank nachfragen."),
            )
        )
    return rows


def position_cases(company, days_ahead) -> list[dict]:
    filters = {"status": POSITION_STATUS_REVIEW}
    runs = company_runs(company)
    if runs is not None:
        if not runs:
            return []
        filters["collection_run"] = ["in", runs]
    return [
        case(
            AREA_POSITION,
            position.collection_date,
            POSITION_DOCTYPE,
            position.name,
            position.amount,
            position.customer,
            position.status,
            position.processing_error or _("Manuelle Pruefung."),
            _("Im Banking-Bereich klaeren, dann in der Position 'Als erledigt markieren'."),
        )
        for position in frappe.get_list(
            POSITION_DOCTYPE,
            filters=filters,
            fields=["name", "status", "collection_date", "amount", "customer", "processing_error"],
            order_by="collection_date asc, name asc",
            limit_page_length=0,
        )
    ]


def bank_import_cases(company, days_ahead) -> list[dict]:
    fields = [
        "name",
        "creation",
        "status",
        "error_message",
        "error_count",
        "reconciliation_status",
        "reconciliation_message",
    ]
    imports = {
        row.name: row
        for row in frappe.get_list(
            IMPORT_DOCTYPE,
            filters={"status": ["in", [STATUS_PARTIAL, STATUS_FAILED]]},
            fields=fields,
            limit_page_length=0,
        )
        + frappe.get_list(
            IMPORT_DOCTYPE,
            filters={"reconciliation_status": RECONCILIATION_FAILED},
            fields=fields,
            limit_page_length=0,
        )
    }
    rows = []
    for row in imports.values():
        if row.status in (STATUS_PARTIAL, STATUS_FAILED):
            reason = row.error_message or _("{0} Zeilen mit Fehler.").format(cint(row.error_count))
            action = _("Grund unter Fehler beheben, dann 'Erneut verarbeiten'.")
        else:
            reason = row.reconciliation_message or _("Der Abgleich ist fehlgeschlagen.")
            action = _("Grund beheben (zum Beispiel die Rollen des API-Benutzers), dann 'Abgleich starten'.")
        rows.append(
            case(
                AREA_BANK_IMPORT,
                row.creation,
                IMPORT_DOCTYPE,
                row.name,
                status=row.status,
                reason=reason,
                action=action,
            )
        )
    return rows


def schedule_cases(company, days_ahead) -> list[dict]:
    filters = {
        "docstatus": ["<", 2],
        "outstanding_amount": [">", AMOUNT_TOLERANCE],
        SALES_INVOICE_SCHEDULE_NOTE_FIELDNAME: ["is", "set"],
    }
    if company:
        filters["company"] = company
    return [
        case(
            AREA_SCHEDULE,
            invoice.posting_date,
            "Sales Invoice",
            invoice.name,
            invoice.outstanding_amount,
            invoice.customer,
            _("Entwurf") if invoice.docstatus == 0 else _("Gebucht"),
            invoice.get(SALES_INVOICE_SCHEDULE_NOTE_FIELDNAME),
            _(
                "Zahlungsplan pruefen: Mandat korrigieren und 'Zahlungsplan aus Mandat neu erzeugen' oder "
                "'Zahlungsplan bearbeiten'."
            ),
        )
        for invoice in frappe.get_list(
            "Sales Invoice",
            filters=filters,
            fields=[
                "name",
                "docstatus",
                "posting_date",
                "customer",
                "outstanding_amount",
                SALES_INVOICE_SCHEDULE_NOTE_FIELDNAME,
            ],
            order_by="posting_date asc, name asc",
            limit_page_length=0,
        )
    ]


def prenotification_cases(company, days_ahead) -> list[dict]:
    """Direct debit rows without a collection position whose pre-notification is missing or too late (spec 31.4)."""
    from erpnext.accounts.doctype.dunning.dunning import get_payment_schedule_outstanding

    profile = get_active_export_profile(company)
    if not profile or not profile.go_live_date:
        return []
    today = getdate(nowdate())
    go_live = getdate(profile.go_live_date)
    horizon = getdate(add_days(today, days_ahead))
    if horizon < go_live:
        return []
    earliest = earliest_collection_date(
        cint(frappe.db.get_value(PROFILE_DOCTYPE, profile.name, "lead_time_business_days")), today
    )
    parents = sorted(
        {
            row.parent
            for row in frappe.get_list(
                "Payment Schedule",
                parent_doctype="Sales Invoice",
                filters={
                    "parenttype": "Sales Invoice",
                    "mode_of_payment": MODE_OF_PAYMENT_DIRECT_DEBIT,
                    "due_date": ["between", [go_live, horizon]],
                },
                fields=["parent"],
                limit_page_length=0,
            )
        }
    )
    if not parents:
        return []
    rows = []
    for name in frappe.get_list(
        "Sales Invoice",
        filters={
            "name": ["in", parents],
            "docstatus": 1,
            "company": profile.company,
            "outstanding_amount": [">", AMOUNT_TOLERANCE],
        },
        pluck="name",
        limit_page_length=0,
    ):
        invoice = frappe.get_doc("Sales Invoice", name)
        collected = {position.payment_schedule_row for position in get_active_collection_positions(invoice.name)}
        outstanding = get_payment_schedule_outstanding(invoice, invoice.payment_schedule)
        for row in invoice.payment_schedule:
            due_date = getdate(row.due_date)
            amount = flt(outstanding.get(row.name, row.outstanding), 2)
            if (
                row.mode_of_payment != MODE_OF_PAYMENT_DIRECT_DEBIT
                or row.name in collected
                or not go_live <= due_date <= horizon
                or amount <= AMOUNT_TOLERANCE
            ):
                continue
            collection_date = plan_collection_date(due_date, earliest)
            ready, reason = get_prenotification_readiness(invoice, row, collection_date, profile)
            if ready:
                continue
            send_by = getdate(add_days(collection_date, -cint(profile.prenotification_days)))
            if send_by >= today:
                action = _(
                    "Rechnung bis {0} per E-Mail senden (Einzug {1}) oder die Vorabankuendigung manuell bestaetigen."
                )
            else:
                action = _("Rechnung jetzt per E-Mail senden. Der Einzug zum {1} ist nicht mehr moeglich ({0}).")
            rows.append(
                case(
                    AREA_PRENOTIFICATION,
                    due_date,
                    "Sales Invoice",
                    invoice.name,
                    amount,
                    invoice.customer,
                    invoice.get(SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME),
                    reason,
                    action.format(formatdate(send_by, "dd.MM.yyyy"), formatdate(collection_date, "dd.MM.yyyy")),
                )
            )
    return rows


def get_columns() -> list[dict]:
    return [
        {"fieldname": "area", "label": _("Bereich"), "fieldtype": "Data", "width": 130},
        {"fieldname": "date", "label": _("Datum"), "fieldtype": "Date", "width": 100},
        {"fieldname": "reference_doctype", "label": _("Belegart"), "fieldtype": "Data", "hidden": 1},
        {
            "fieldname": "reference_name",
            "label": _("Beleg"),
            "fieldtype": "Dynamic Link",
            "options": "reference_doctype",
            "width": 180,
        },
        {"fieldname": "amount", "label": _("Betrag"), "fieldtype": "Currency", "width": 110},
        {"fieldname": "customer", "label": _("Kunde"), "fieldtype": "Link", "options": "Customer", "width": 150},
        {"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 130},
        {"fieldname": "reason", "label": _("Grund"), "fieldtype": "Data", "width": 400},
        {"fieldname": "next_action", "label": _("Nächste Aktion"), "fieldtype": "Data", "width": 320},
        {"fieldname": "party", "label": _("Gegenseite"), "fieldtype": "Data", "width": 180},
        {"fieldname": "purpose", "label": _("Verwendungszweck"), "fieldtype": "Data", "width": 260},
    ]
