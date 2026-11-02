import hashlib
import json
from itertools import groupby

import frappe
from frappe import _
from frappe.model import no_value_fields
from frappe.utils import add_days, cint, cstr, flt, formatdate, get_datetime, getdate, now_datetime, nowdate

from erpverein.custom_fields import (
    SALES_INVOICE_BILLING_KIND_FIELDNAME,
    SALES_INVOICE_PAYER_DOCTYPE_FIELDNAME,
    SALES_INVOICE_PAYER_FIELDNAME,
    SALES_INVOICE_PRENOTIFICATION_SENT_AT_FIELDNAME,
    SALES_INVOICE_SEPA_MANDATE_FIELDNAME,
)
from erpverein.services.billing_common import is_retryable_error, stable_hash
from erpverein.services.pain008_writer import (
    MAX_NAME_LENGTH,
    MAX_PURPOSE_LENGTH,
    is_eea_iban,
    is_sepa_reference,
    is_sepa_text,
    to_sepa_text,
)
from erpverein.services.payment_schedule_service import (
    AMOUNT_TOLERANCE,
    BILLING_TYPE_DIRECT_DEBIT,
    get_active_collection_positions,
)
from erpverein.services.prenotification_service import get_prenotification_readiness, mask_iban_last_four
from erpverein.services.sepa_collection_schedule_service import is_target_business_day, next_target_business_day
from erpverein.services.sepa_export_profile_service import DEFAULT_PURPOSE_TEMPLATE
from erpverein.services.sepa_mandat_service import MANDATE_STATUS_ACTIVE, normalize_iban, validate_iban
from erpverein.setup_data import MODE_OF_PAYMENT_DIRECT_DEBIT


RUN_DOCTYPE = "SEPA Einzugslauf"
POSITION_DOCTYPE = "SEPA Einzugsposition"
PROFILE_DOCTYPE = "SEPA Exportprofil"
RUN_STATUS_DRAFT = "Entwurf"
RUN_STATUS_CHECKED = "Geprueft"
RUN_STATUS_EXPORTED = "Exportiert"
RUN_STATUS_BANK_REFERENCE = "Bankreferenz erfasst"
RUN_STATUS_CREDIT_RECOGNIZED = "Bankgutschrift erkannt"
RUN_STATUS_SETTLED = "Abgestimmt"
RUN_STATUS_REVIEW = "Manuelle Pruefung"
RUN_STATUS_CANCELLED = "Storniert"
POSITION_STATUS_DRAFT = "Entwurf"
POSITION_STATUS_CHECKED = "Geprueft"
POSITION_STATUS_EXPORTED = "Exportiert"
POSITION_STATUS_PAID = "Bezahlt"
POSITION_STATUS_REVIEW = "Manuelle Pruefung"
POSITION_STATUS_CANCELLED = "Storniert"
OPEN_RUN_STATUSES = {RUN_STATUS_DRAFT, RUN_STATUS_CHECKED}
EXPORTED_RUN_STATUSES = {RUN_STATUS_EXPORTED, RUN_STATUS_BANK_REFERENCE}
# After the export a run waits for its bank credit; these statuses still allow a settlement (Phase 4).
SETTLEMENT_RUN_STATUSES = EXPORTED_RUN_STATUSES | {RUN_STATUS_CREDIT_RECOGNIZED, RUN_STATUS_REVIEW}
INACTIVE_POSITION_STATUSES = {POSITION_STATUS_CANCELLED}
SEQUENCE_TYPE_RECURRING = "RCUR"
ACCOUNTS_ROLES = frozenset({"Accounts User", "Accounts Manager"})
ROW_READY = "Bereit"
ROW_CONFLICT = "Konflikt"
ROW_EXISTING = "Vorhanden"
PAYER_FIELDS = {
    "Mitglied": frappe._dict(end="austrittsdatum", end_label="Austritt", source_id="mitglied_id"),
    "Mieter": frappe._dict(end="mietende", end_label="Mietende", source_id="mieter_id"),
}
MANDATE_FIELDS = [
    "name",
    "status",
    "bezugs_doctype",
    "bezugs_name",
    "customer",
    "mandatsreferenz",
    "mandatsdatum",
    "iban",
    "kontoinhaber",
    "einzugsplan_bis",
]
RUN_EDITABLE_FIELDS = {"notes"}
POSITION_EDITABLE_FIELDS = {"purpose"}
MAX_REASON_LENGTH = 500
MAX_BANK_REFERENCE_LENGTH = 140
UPDATE_FLAG = "erpverein_collection_update"


def has_accounts_role(user: str | None = None) -> bool:
    user = user or frappe.session.user
    return user == "Administrator" or bool(ACCOUNTS_ROLES & set(frappe.get_roles(user)))


def collection_has_permission(doc=None, ptype=None, user=None, debug=False) -> bool:
    # Collection runs show bank data: the DocType permission (System Manager) needs an accounts role as well.
    return has_accounts_role(user)


def collection_query_conditions(user=None, doctype=None) -> str:
    return "" if has_accounts_role(user) else "1=0"


def assert_collection_access(ptype: str) -> None:
    frappe.has_permission(RUN_DOCTYPE, ptype, throw=True)
    if not has_accounts_role():
        frappe.throw(_("SEPA-Einzuege brauchen die Rolle Accounts User oder Accounts Manager."), frappe.PermissionError)
    for doctype in ("Sales Invoice", "SEPA Mandat", "Mitglied", "Mieter"):
        frappe.has_permission(doctype, "read", throw=True)


def add_target_business_days(value, days: int):
    value = getdate(value)
    step = 1 if cint(days) >= 0 else -1
    remaining = abs(cint(days))
    while remaining:
        value = getdate(add_days(value, step))
        if is_target_business_day(value):
            remaining -= 1
    return value


def earliest_collection_date(lead_time_business_days: int, today=None):
    # One business day more than the lead time keeps the upload deadline (07:00 on the day before) ahead.
    return add_target_business_days(today or nowdate(), cint(lead_time_business_days) + 1)


def plan_collection_date(due_date, earliest):
    return max(next_target_business_day(due_date), getdate(earliest))


def upload_window(collection_date, profile) -> tuple:
    return (
        add_target_business_days(collection_date, -cint(profile.earliest_submission_business_days)),
        add_target_business_days(collection_date, -cint(profile.lead_time_business_days)),
    )


def collection_generation_key(invoice: str, payment_term: str | None, due_date) -> str:
    return stable_hash({"invoice": invoice, "payment_term": payment_term, "due_date": str(getdate(due_date))})


def iban_hash(iban: object) -> str | None:
    iban = normalize_iban(iban)
    return hashlib.sha256(f"erpverein-iban:{iban}".encode()).hexdigest() if iban else None


def render_purpose(template: str | None, values: dict) -> str:
    try:
        text = (template or DEFAULT_PURPOSE_TEMPLATE).format(**values)
    except (KeyError, IndexError, ValueError):
        text = DEFAULT_PURPOSE_TEMPLATE.format(**values)
    return to_sepa_text(text, MAX_PURPOSE_LENGTH) or to_sepa_text(
        DEFAULT_PURPOSE_TEMPLATE.format(**values), MAX_PURPOSE_LENGTH
    )


def group_rows_into_runs(rows: list[dict], max_positions: int) -> list[list[dict]]:
    ordered = sorted(rows, key=lambda row: (row["collection_date"], row["billing_kind"] or "", row["invoice"], row["due_date"]))
    groups = []
    for _collection_date, items in groupby(ordered, key=lambda row: row["collection_date"]):
        items = list(items)
        size = max(1, cint(max_positions))
        groups += [items[index : index + size] for index in range(0, len(items), size)]
    return groups


def get_collection_profile(company: str | None = None):
    company = company or frappe.defaults.get_user_default("Company")
    if not company:
        frappe.throw(_("Bitte ein Unternehmen waehlen."))
    name = frappe.db.get_value(PROFILE_DOCTYPE, {"company": company, "active": 1}, "name")
    if not name:
        frappe.throw(_("Fuer das Unternehmen {0} gibt es kein aktives SEPA-Exportprofil.").format(frappe.bold(company)))
    profile = frappe.get_doc(PROFILE_DOCTYPE, name)
    profile.check_permission("read")
    return profile


def get_run_profile(run):
    profile = frappe.get_doc(PROFILE_DOCTYPE, run.export_profile)
    profile.check_permission("read")
    if not cint(profile.active) or profile.company != run.company:
        frappe.throw(_("Das Exportprofil {0} des Laufs ist nicht mehr aktiv.").format(frappe.bold(profile.name)))
    return profile


def build_collection_preview(company: str | None = None, cutoff_date=None, batch_booking=None) -> dict:
    assert_collection_access("create")
    if not cutoff_date:
        frappe.throw(_("Bitte einen Stichtag angeben."))
    profile = get_collection_profile(company)
    cutoff = getdate(cutoff_date)
    go_live = getdate(profile.go_live_date)
    today = getdate(nowdate())
    earliest = earliest_collection_date(profile.lead_time_business_days, today)
    batch = cint(profile.batch_booking_default) if batch_booking in (None, "") else cint(batch_booking)
    cache = {}
    rows = []
    modified = {}
    if cutoff >= go_live:
        for invoice_name in get_candidate_invoices(profile.company, go_live, cutoff):
            invoice = frappe.get_doc("Sales Invoice", invoice_name)
            modified[invoice.name] = str(invoice.modified)
            rows += evaluate_invoice_rows(invoice, profile, go_live, cutoff, earliest, cache)
    rows.sort(key=lambda row: (row["collection_date"], row["invoice"], row["due_date"]))
    ready = [row for row in rows if row["status"] == ROW_READY]
    runs = []
    for group in group_rows_into_runs(ready, profile.max_positions_per_file):
        upload_earliest, upload_deadline = upload_window(group[0]["collection_date"], profile)
        runs.append(
            {
                "collection_date": group[0]["collection_date"],
                "count": len(group),
                "total": flt(sum(row["amount"] for row in group), 2),
                "upload_earliest": str(upload_earliest),
                "upload_deadline": str(upload_deadline),
            }
        )
    preview = {
        "company": profile.company,
        "export_profile": profile.name,
        "cutoff_date": str(cutoff),
        "go_live_date": str(go_live),
        "earliest_collection_date": str(earliest),
        "batch_booking": batch,
        "rows": rows,
        "runs": runs,
        "ready_count": len(ready),
        "conflict_count": sum(1 for row in rows if row["status"] == ROW_CONFLICT),
        "existing_count": sum(1 for row in rows if row["status"] == ROW_EXISTING),
    }
    preview["preview_hash"] = stable_hash(
        {
            "preview": preview,
            "profile_version": cint(profile.profile_version),
            "today": str(today),
            "invoices": modified,
        }
    )
    return preview


def get_candidate_invoices(company: str, go_live, cutoff) -> list[str]:
    rows = frappe.get_list(
        "Payment Schedule",
        parent_doctype="Sales Invoice",
        filters={
            "parenttype": "Sales Invoice",
            "mode_of_payment": MODE_OF_PAYMENT_DIRECT_DEBIT,
            "due_date": ["between", [go_live, cutoff]],
        },
        fields=["parent"],
    )
    parents = sorted({row.parent for row in rows})
    if not parents:
        return []
    return sorted(
        frappe.get_list(
            "Sales Invoice",
            filters={"name": ["in", parents], "docstatus": 1, "company": company, "outstanding_amount": [">", 0]},
            pluck="name",
        )
    )


def evaluate_invoice_rows(invoice, profile, go_live, cutoff, earliest, cache) -> list[dict]:
    from erpnext.accounts.doctype.dunning.dunning import get_payment_schedule_outstanding

    rows = [
        row
        for row in invoice.payment_schedule
        if row.mode_of_payment == MODE_OF_PAYMENT_DIRECT_DEBIT and go_live <= getdate(row.due_date) <= cutoff
    ]
    if not rows:
        return []
    outstanding = get_payment_schedule_outstanding(invoice, invoice.payment_schedule)
    context = get_invoice_context(invoice, cache)
    invoice_reason = get_invoice_reason(invoice, profile) or context.reason
    results = []
    for row in sorted(rows, key=lambda row: (getdate(row.due_date), row.idx)):
        amount = flt(outstanding.get(row.name, row.outstanding), 2)
        if amount <= AMOUNT_TOLERANCE:
            continue
        collection_date = plan_collection_date(row.due_date, earliest)
        key = collection_generation_key(invoice.name, row.payment_term, row.due_date)
        mandate = context.mandate or frappe._dict()
        item = {
            "invoice": invoice.name,
            "customer": invoice.customer,
            "customer_name": invoice.customer_name,
            "billing_kind": invoice.get(SALES_INVOICE_BILLING_KIND_FIELDNAME),
            "payer_doctype": invoice.get(SALES_INVOICE_PAYER_DOCTYPE_FIELDNAME),
            "payer": invoice.get(SALES_INVOICE_PAYER_FIELDNAME),
            "source_id": context.source_id,
            "period_from": str(getdate(invoice.from_date)) if invoice.get("from_date") else None,
            "period_to": str(getdate(invoice.to_date)) if invoice.get("to_date") else None,
            "schedule_row": row.name,
            "payment_term": row.payment_term,
            "due_date": str(getdate(row.due_date)),
            "collection_date": str(collection_date),
            "amount": amount,
            "mandate": invoice.get(SALES_INVOICE_SEPA_MANDATE_FIELDNAME),
            "mandate_reference": mandate.get("mandatsreferenz"),
            "mandate_date": str(getdate(mandate.mandatsdatum)) if mandate.get("mandatsdatum") else None,
            "iban_masked": mask_iban_last_four(mandate.get("iban")),
            "generation_key": key,
            "position": None,
            "status": ROW_READY,
            "reason": None,
        }
        existing = frappe.db.get_value(POSITION_DOCTYPE, {"active_key": key}, "name")
        if existing:
            item.update(
                position=existing,
                status=ROW_EXISTING,
                reason=_("Die Einzugsposition {0} existiert schon.").format(existing),
            )
        else:
            reason = invoice_reason or get_row_reason(invoice, row, context, profile, collection_date, amount)
            if reason:
                item.update(status=ROW_CONFLICT, reason=reason)
        results.append(item)
    return results


def get_invoice_reason(invoice, profile) -> str | None:
    if invoice.docstatus != 1:
        return _("Die Rechnung ist nicht gebucht.")
    if cint(invoice.get("is_return")):
        return _("Die Rechnung ist eine Gutschrift.")
    if invoice.company != profile.company:
        return _("Die Rechnung gehoert zu einem anderen Unternehmen.")
    if invoice.currency != profile.currency:
        return _("Die Rechnung ist nicht in {0}.").format(profile.currency)
    if flt(invoice.outstanding_amount) <= AMOUNT_TOLERANCE:
        return _("Die Rechnung ist bezahlt.")
    return None


def get_invoice_context(invoice, cache: dict) -> frappe._dict:
    key = (
        invoice.get(SALES_INVOICE_PAYER_DOCTYPE_FIELDNAME),
        invoice.get(SALES_INVOICE_PAYER_FIELDNAME),
        invoice.get(SALES_INVOICE_SEPA_MANDATE_FIELDNAME),
        invoice.customer,
    )
    if key not in cache:
        cache[key] = load_payer_context(*key)
    return cache[key]


def load_payer_context(payer_doctype: str | None, payer: str | None, mandate_name: str | None, customer: str) -> frappe._dict:
    context = frappe._dict(reason=None, mandate=None, end_date=None, end_label=None, source_id=payer)
    fields = PAYER_FIELDS.get(payer_doctype)
    if not fields or not payer:
        context.reason = _("Die Rechnung nennt keinen Zahler (Mitglied oder Mieter).")
        return context
    values = frappe.db.get_value(
        payer_doctype, payer, ["abrechnungsart", "sepa_mandat", fields.end, fields.source_id], as_dict=True
    )
    if not values:
        context.reason = _("Der Zahler {0} existiert nicht mehr.").format(payer)
        return context
    context.update(end_date=values.get(fields.end), end_label=fields.end_label, source_id=values.get(fields.source_id) or payer)
    mandate = frappe.db.get_value("SEPA Mandat", mandate_name, MANDATE_FIELDS, as_dict=True) if mandate_name else None
    iban = normalize_iban(mandate.iban) if mandate else None
    if values.abrechnungsart != BILLING_TYPE_DIRECT_DEBIT:
        context.reason = _("Die Abrechnungsart des Zahlers ist nicht mehr Lastschrift.")
    elif not mandate:
        context.reason = _("Die Rechnung nennt kein SEPA-Mandat.")
    elif mandate.status != MANDATE_STATUS_ACTIVE or (mandate.bezugs_doctype, mandate.bezugs_name) != (payer_doctype, payer):
        context.reason = _("Das SEPA-Mandat der Rechnung ist nicht mehr aktiv.")
    elif values.sepa_mandat != mandate.name:
        context.reason = _("Der Zahler hat inzwischen ein anderes SEPA-Mandat. Erzeuge den Zahlungsplan neu.")
    elif mandate.customer != customer:
        context.reason = _("Das SEPA-Mandat gehoert zu einem anderen Kunden.")
    elif not is_sepa_reference(mandate.mandatsreferenz):
        context.reason = _("Die Mandatsreferenz fehlt oder enthaelt unzulaessige Zeichen.")
    elif not mandate.mandatsdatum:
        context.reason = _("Das Mandatsdatum fehlt.")
    elif not validate_iban(iban):
        context.reason = _("Die IBAN des SEPA-Mandats ist ungueltig.")
    elif not is_eea_iban(iban):
        context.reason = _("Die IBAN liegt ausserhalb des EWR. ERPverein zieht dorthin nicht ein.")
    elif not to_sepa_text(mandate.kontoinhaber, MAX_NAME_LENGTH):
        context.reason = _("Der Kontoinhaber des SEPA-Mandats fehlt.")
    context.mandate = mandate
    return context


def get_row_reason(invoice, row, context, profile, collection_date, amount: float) -> str | None:
    mandate = context.mandate
    if getdate(mandate.mandatsdatum) > getdate(collection_date):
        return _("Das Mandatsdatum liegt nach dem Einzugsdatum.")
    if mandate.einzugsplan_bis and getdate(collection_date) > getdate(mandate.einzugsplan_bis):
        return _("Das Einzugsdatum liegt nach dem Ende des Mandatsplans.")
    if context.end_date and getdate(collection_date) > getdate(context.end_date):
        return _("Das Einzugsdatum liegt nach dem {0} ({1}).").format(
            _(context.end_label), formatdate(context.end_date, "dd.MM.yyyy")
        )
    if flt(amount) - flt(invoice.outstanding_amount) > AMOUNT_TOLERANCE:
        return _("Der Betrag ist groesser als der offene Betrag der Rechnung.")
    _ready, reason = get_prenotification_readiness(invoice, row, collection_date, profile)
    return reason


def create_collection_runs(
    company: str | None = None, cutoff_date=None, preview_hash: str | None = None, batch_booking=None, selected_rows=None
) -> dict:
    preview = build_collection_preview(company, cutoff_date, batch_booking)
    if not preview_hash or preview_hash != preview["preview_hash"]:
        frappe.throw(_("Die Vorschau ist veraltet. Bitte neu erzeugen."))
    rows = select_ready_rows(preview["rows"], selected_rows)
    profile = get_collection_profile(preview["company"])
    result = {"runs": [], "created": 0, "skipped": 0, "failed": 0}
    for group in group_rows_into_runs(rows, profile.max_positions_per_file):
        create_run_with_positions(profile, group, preview["batch_booking"], result)
    return result


def select_ready_rows(rows: list[dict], selected_rows) -> list[dict]:
    ready = [row for row in rows if row["status"] == ROW_READY]
    if selected_rows not in (None, ""):
        selection = parse_name_list(selected_rows, _("Auswahl"))
        unknown = selection - {row["schedule_row"] for row in ready}
        if unknown:
            frappe.throw(_("Die Auswahl enthaelt Zeilen, die nicht einzugsbereit sind. Bitte die Vorschau neu erzeugen."))
        ready = [row for row in ready if row["schedule_row"] in selection]
    if not ready:
        frappe.throw(_("Es gibt keine einzugsbereite Lastschriftzeile."))
    return ready


def parse_name_list(value, label: str) -> set[str]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            frappe.throw(_("{0} ist ungueltig.").format(label))
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        frappe.throw(_("{0} muss eine Liste von Namen sein.").format(label))
    return set(value)


def create_run_with_positions(profile, rows: list[dict], batch_booking: int, result: dict) -> None:
    collection_date = getdate(rows[0]["collection_date"])
    upload_earliest, upload_deadline = upload_window(collection_date, profile)
    savepoint = f"erpverein_collection_run_{frappe.generate_hash(length=12)}"
    frappe.db.savepoint(savepoint)
    run = frappe.get_doc(
        {
            "doctype": RUN_DOCTYPE,
            "status": RUN_STATUS_DRAFT,
            "company": profile.company,
            "export_profile": profile.name,
            "bank_account": profile.bank_account,
            "collection_date": collection_date,
            "sequence_type": SEQUENCE_TYPE_RECURRING,
            "batch_booking": cint(batch_booking),
            "currency": profile.currency,
            "upload_earliest": upload_earliest,
            "upload_deadline": upload_deadline,
        }
    )
    run.flags[UPDATE_FLAG] = True
    run.insert()
    created = 0
    for row in rows:
        outcome = create_position(run, row, profile)
        result[outcome] += 1
        created += outcome == "created"
    if not created:
        frappe.db.rollback(save_point=savepoint)
        frappe.db.release_savepoint(savepoint)
        return
    frappe.db.release_savepoint(savepoint)
    save_run_totals(run)
    result["runs"].append(run.name)


def create_position(run, row: dict, profile) -> str:
    savepoint = f"erpverein_collection_position_{frappe.generate_hash(length=12)}"
    frappe.db.savepoint(savepoint)
    try:
        frappe.db.get_value("Sales Invoice", row["invoice"], "name", for_update=True)
        if frappe.db.get_value(POSITION_DOCTYPE, {"active_key": row["generation_key"]}, "name"):
            frappe.db.release_savepoint(savepoint)
            return "skipped"
        position = frappe.get_doc(
            {
                "doctype": POSITION_DOCTYPE,
                "collection_run": run.name,
                "status": POSITION_STATUS_DRAFT,
                "billing_kind": row["billing_kind"],
                "source_doctype": row["payer_doctype"],
                "source_name": row["payer"],
                "customer": row["customer"],
                "sepa_mandate": row["mandate"],
                "sales_invoice": row["invoice"],
                "payment_schedule_row": row["schedule_row"],
                "payment_term": row["payment_term"],
                "due_date": row["due_date"],
                "collection_date": run.collection_date,
                "amount": row["amount"],
                "currency": run.currency,
                "purpose": render_purpose(profile.default_purpose_template, purpose_values(row)),
                "mandate_reference_snapshot": row["mandate_reference"],
                "mandate_date_snapshot": row["mandate_date"],
                "debtor_iban_masked": row["iban_masked"],
                "debtor_iban_hash": iban_hash(frappe.db.get_value("SEPA Mandat", row["mandate"], "iban")),
                "prenotification_sent_at": frappe.db.get_value(
                    "Sales Invoice", row["invoice"], SALES_INVOICE_PRENOTIFICATION_SENT_AT_FIELDNAME
                ),
                "sequence_type": run.sequence_type,
                "generation_key": row["generation_key"],
            }
        )
        position.flags[UPDATE_FLAG] = True
        position.insert()
        frappe.db.release_savepoint(savepoint)
        return "created"
    except Exception as exc:
        if is_retryable_error(exc):
            raise
        frappe.db.rollback(save_point=savepoint)
        frappe.db.release_savepoint(savepoint)
        if isinstance(exc, (frappe.UniqueValidationError, frappe.DuplicateEntryError)):
            frappe.clear_last_message()
            return "skipped"
        frappe.log_error(title=f"ERPverein collection position failed: {row['invoice']}", message=frappe.get_traceback())
        return "failed"


def purpose_values(row: dict) -> dict:
    return {
        "invoice": row["invoice"],
        "customer": row.get("customer_name") or row["customer"],
        "source_id": row.get("source_id") or row["payer"],
        "period_from": formatdate(row["period_from"], "dd.MM.yyyy") if row.get("period_from") else "",
        "period_to": formatdate(row["period_to"], "dd.MM.yyyy") if row.get("period_to") else "",
        "due_date": formatdate(row["due_date"], "dd.MM.yyyy"),
        "collection_date": formatdate(row["collection_date"], "dd.MM.yyyy"),
    }


def get_locked_run(run_name: str):
    if not frappe.db.get_value(RUN_DOCTYPE, run_name, "name", for_update=True):
        frappe.throw(_("Der Einzugslauf {0} existiert nicht.").format(run_name))
    run = frappe.get_doc(RUN_DOCTYPE, run_name)
    run.check_permission("write")
    if not has_accounts_role():
        frappe.throw(_("SEPA-Einzuege brauchen die Rolle Accounts User oder Accounts Manager."), frappe.PermissionError)
    return run


def get_active_position_docs(run_name: str) -> list:
    names = frappe.get_all(
        POSITION_DOCTYPE,
        filters={"collection_run": run_name, "active_key": ["is", "set"]},
        pluck="name",
        order_by="name asc",
    )
    return [frappe.get_doc(POSITION_DOCTYPE, name) for name in names]


def save_run_totals(run) -> None:
    positions = frappe.get_all(
        POSITION_DOCTYPE,
        filters={"collection_run": run.name, "active_key": ["is", "set"]},
        fields=["amount", "processing_error"],
    )
    run.position_count = len(positions)
    run.total_amount = flt(sum(flt(position.amount) for position in positions), 2)
    run.error_count = sum(1 for position in positions if position.processing_error)
    run.flags[UPDATE_FLAG] = True
    run.save()


def save_position(position, values: dict) -> None:
    position.update(values)
    position.flags[UPDATE_FLAG] = True
    position.save()


def check_collection_run(run_name: str) -> dict:
    run = get_locked_run(run_name)
    if run.status not in OPEN_RUN_STATUSES:
        frappe.throw(_("Nur ein Lauf im Status Entwurf oder Geprueft laesst sich pruefen."))
    results = validate_run_positions(run, get_run_profile(run))
    store_check_result(run, results)
    return {"run": run.name, "status": run.status, "positions": run.position_count, "errors": run.error_count}


def validate_run_positions(run, profile) -> dict[str, str | None]:
    positions = get_active_position_docs(run.name)
    if not positions:
        frappe.throw(_("Der Einzugslauf hat keine aktive Position."))
    earliest = earliest_collection_date(profile.lead_time_business_days)
    cache = {}
    return {position.name: get_position_reason(position, run, profile, earliest, cache) for position in positions}


def get_position_reason(position, run, profile, earliest, cache: dict) -> str | None:
    from erpnext.accounts.doctype.dunning.dunning import get_payment_schedule_outstanding

    if getdate(run.collection_date) < getdate(earliest):
        return _("Das Einzugsdatum ist zu nah. Storniere den Lauf und bereite die Lastschriften neu vor.")
    if not frappe.has_permission("Sales Invoice", "read", doc=position.sales_invoice):
        return _("Keine Leseberechtigung fuer die Rechnung.")
    invoice = frappe.get_doc("Sales Invoice", position.sales_invoice)
    reason = get_invoice_reason(invoice, profile)
    if reason:
        return reason
    row = next((row for row in invoice.payment_schedule if row.name == position.payment_schedule_row), None)
    if (
        not row
        or row.mode_of_payment != MODE_OF_PAYMENT_DIRECT_DEBIT
        or row.payment_term != position.payment_term
        or getdate(row.due_date) != getdate(position.due_date)
    ):
        return _("Die Zahlungsplanzeile hat sich geaendert.")
    outstanding = flt(get_payment_schedule_outstanding(invoice, invoice.payment_schedule).get(row.name, row.outstanding), 2)
    if abs(outstanding - flt(position.amount, 2)) > AMOUNT_TOLERANCE:
        return _("Der offene Betrag der Zeile ({0}) passt nicht mehr zum Betrag der Position.").format(outstanding)
    if invoice.get(SALES_INVOICE_SEPA_MANDATE_FIELDNAME) != position.sepa_mandate:
        return _("Die Rechnung nennt inzwischen ein anderes SEPA-Mandat.")
    context = get_invoice_context(invoice, cache)
    if context.reason:
        return context.reason
    mandate = context.mandate
    if (
        mandate.mandatsreferenz != position.mandate_reference_snapshot
        or str(getdate(mandate.mandatsdatum)) != str(getdate(position.mandate_date_snapshot))
        or iban_hash(mandate.iban) != position.debtor_iban_hash
    ):
        return _("Das SEPA-Mandat wurde seit der Vorbereitung geaendert. Storniere die Position und bereite sie neu vor.")
    reason = get_row_reason(invoice, row, context, profile, run.collection_date, position.amount)
    if reason:
        return reason
    if not is_sepa_text(position.purpose, MAX_PURPOSE_LENGTH):
        return _("Der Verwendungszweck ist leer, zu lang oder enthaelt unzulaessige Zeichen.")
    return None


def store_check_result(run, results: dict[str, str | None]) -> None:
    failed = any(results.values())
    status = POSITION_STATUS_DRAFT if failed else POSITION_STATUS_CHECKED
    for position in get_active_position_docs(run.name):
        reason = results.get(position.name)
        if position.processing_error != reason or position.status != status:
            save_position(position, {"processing_error": reason, "status": status})
    run.status = RUN_STATUS_DRAFT if failed else RUN_STATUS_CHECKED
    save_run_totals(run)


def require_reason(reason) -> str:
    reason = " ".join(cstr(reason).split())
    if not reason:
        frappe.throw(_("Bitte einen Grund angeben."))
    if len(reason) > MAX_REASON_LENGTH:
        frappe.throw(_("Der Grund darf hoechstens {0} Zeichen haben.").format(MAX_REASON_LENGTH))
    return reason


def cancel_collection_positions(run_name: str, positions=None, reason: str | None = None, only_errors=False) -> dict:
    run = get_locked_run(run_name)
    if run.status not in OPEN_RUN_STATUSES:
        frappe.throw(_("Positionen eines exportierten oder stornierten Laufs lassen sich nicht einzeln stornieren."))
    reason = require_reason(reason)
    active = get_active_position_docs(run.name)
    if cint(only_errors):
        targets = [position for position in active if position.processing_error]
    else:
        names = parse_name_list(positions, _("Positionen"))
        if names - {position.name for position in active}:
            frappe.throw(_("Eine Position gehoert nicht zu den aktiven Positionen dieses Laufs."))
        targets = [position for position in active if position.name in names]
    if not targets:
        frappe.throw(_("Es gibt keine Position zum Stornieren."))
    for position in targets:
        save_position(position, {"status": POSITION_STATUS_CANCELLED, "cancel_reason": reason})
    remaining = [position for position in active if position not in targets]
    for position in remaining:
        if position.status != POSITION_STATUS_DRAFT:
            save_position(position, {"status": POSITION_STATUS_DRAFT})
    run.status = RUN_STATUS_DRAFT if remaining else RUN_STATUS_CANCELLED
    if not remaining:
        run.cancel_reason = reason
    save_run_totals(run)
    run.add_comment("Info", _("{0} Positionen storniert. Grund: {1}").format(len(targets), reason))
    return {"run": run.name, "status": run.status, "cancelled": len(targets), "positions": run.position_count}


def cancel_collection_position(position_name: str, reason: str | None = None) -> dict:
    run_name = frappe.db.get_value(POSITION_DOCTYPE, position_name, "collection_run")
    if not run_name:
        frappe.throw(_("Die Einzugsposition {0} existiert nicht.").format(position_name))
    return cancel_collection_positions(run_name, positions=[position_name], reason=reason)


def cancel_collection_run(run_name: str, reason: str | None = None, confirm_not_submitted=False) -> dict:
    run = get_locked_run(run_name)
    if run.status == RUN_STATUS_CANCELLED:
        frappe.throw(_("Der Einzugslauf ist bereits storniert."))
    positions = get_active_position_docs(run.name)
    if (
        run.status in {RUN_STATUS_CREDIT_RECOGNIZED, RUN_STATUS_SETTLED}
        or run.settlement_bank_transaction
        or any(position.payment_entry or position.settlement_bank_transaction for position in positions)
    ):
        frappe.throw(
            _("Zu diesem Einzugslauf gibt es schon eine Bankgutschrift oder Zahlungen. Er laesst sich nicht stornieren.")
        )
    exported = run.status in SETTLEMENT_RUN_STATUSES
    if exported and not cint(confirm_not_submitted):
        frappe.throw(
            _("Bestaetige, dass die Bank die Datei nicht ausfuehrt (nicht hochgeladen, abgelehnt oder nicht freigegeben).")
        )
    reason = require_reason(reason)
    for position in positions:
        save_position(position, {"status": POSITION_STATUS_CANCELLED, "cancel_reason": reason})
    run.status = RUN_STATUS_CANCELLED
    run.cancel_reason = reason
    save_run_totals(run)
    run.add_comment(
        "Info",
        _("Einzugslauf storniert ({0} Positionen). Grund: {1}").format(len(positions), reason)
        + (" " + _("Die Exportdatei bleibt zur Nachverfolgung erhalten.") if exported else ""),
    )
    return {"run": run.name, "status": run.status, "cancelled": len(positions)}


def set_collection_bank_reference(run_name: str, reference: str | None = None) -> dict:
    run = get_locked_run(run_name)
    if run.status not in EXPORTED_RUN_STATUSES:
        frappe.throw(_("Eine Bankreferenz gibt es erst nach dem Export."))
    reference = " ".join(cstr(reference).split())
    if not reference or len(reference) > MAX_BANK_REFERENCE_LENGTH:
        frappe.throw(_("Die Bankreferenz muss 1 bis {0} Zeichen haben.").format(MAX_BANK_REFERENCE_LENGTH))
    run.bank_order_reference = reference
    run.bank_reference_at = now_datetime()
    run.status = RUN_STATUS_BANK_REFERENCE
    run.flags[UPDATE_FLAG] = True
    run.save()
    run.add_comment("Info", _("Bankreferenz erfasst: {0}").format(reference))
    return {"run": run.name, "status": run.status}


def get_run_positions_for_display(run_name: str) -> list[dict]:
    if not has_accounts_role():
        return []
    return frappe.get_list(
        POSITION_DOCTYPE,
        filters={"collection_run": run_name},
        fields=[
            "name",
            "status",
            "billing_kind",
            "source_doctype",
            "source_name",
            "sales_invoice",
            "due_date",
            "amount",
            "mandate_reference_snapshot",
            "debtor_iban_masked",
            "payment_entry",
            "processing_error",
        ],
        order_by="name asc",
    )


def get_run_onload(run) -> dict:
    deadline = getdate(run.upload_deadline) if run.upload_deadline else None
    return {
        "positions": get_run_positions_for_display(run.name),
        "upload_deadline_passed": bool(deadline and getdate(nowdate()) > deadline),
    }


def validate_collection_run(doc) -> None:
    if doc.flags.get(UPDATE_FLAG):
        return
    previous = doc.get_doc_before_save()
    if not previous:
        frappe.throw(_("Einzugslaeufe entstehen ueber 'Faellige Lastschriften vorbereiten'."))
    changed = changed_fields(previous, doc, RUN_EDITABLE_FIELDS)
    if changed:
        frappe.throw(_("Diese Felder aendert nur ERPverein: {0}").format(", ".join(changed)))


def validate_collection_position(doc) -> None:
    doc.end_to_end_reference = doc.end_to_end_reference or doc.name
    doc.active_key = None if doc.status in INACTIVE_POSITION_STATUSES else doc.generation_key
    previous = doc.get_doc_before_save()
    if doc.flags.get(UPDATE_FLAG):
        if previous and previous.end_to_end_reference != doc.end_to_end_reference:
            frappe.throw(_("Die End-to-End-Referenz ist unveraenderlich."))
        return
    if not previous:
        frappe.throw(_("Einzugspositionen entstehen ueber 'Faellige Lastschriften vorbereiten'."))
    changed = changed_fields(previous, doc, POSITION_EDITABLE_FIELDS)
    if changed:
        frappe.throw(_("Diese Felder aendert nur ERPverein: {0}").format(", ".join(changed)))
    if cstr(previous.purpose) != cstr(doc.purpose):
        if doc.status != POSITION_STATUS_DRAFT or frappe.db.get_value(RUN_DOCTYPE, doc.collection_run, "status") != RUN_STATUS_DRAFT:
            frappe.throw(_("Den Verwendungszweck kannst du nur aendern, solange Lauf und Position im Status Entwurf sind."))
        doc.purpose = to_sepa_text(doc.purpose, MAX_PURPOSE_LENGTH)
        if not doc.purpose:
            frappe.throw(_("Der Verwendungszweck darf nicht leer sein."))


def changed_fields(previous, doc, editable: set[str]) -> list[str]:
    return [
        df.label or df.fieldname
        for df in doc.meta.fields
        if df.fieldtype not in no_value_fields
        and df.fieldname not in editable
        and comparable_value(df, previous.get(df.fieldname)) != comparable_value(df, doc.get(df.fieldname))
    ]


def comparable_value(df, value):
    if df.fieldtype in ("Currency", "Float", "Percent"):
        return flt(value, 6)
    if df.fieldtype in ("Int", "Check"):
        return cint(value)
    if value in (None, ""):
        return None
    if df.fieldtype == "Date":
        return str(getdate(value))
    if df.fieldtype == "Datetime":
        return str(get_datetime(value))
    return cstr(value)


def prevent_collection_deletion(doc) -> None:
    frappe.throw(_("Einzugslaeufe und Einzugspositionen werden storniert, nicht geloescht."))


def prevent_invoice_cancel_with_active_positions(doc, method: str | None = None) -> None:
    positions = [position.name for position in get_active_collection_positions(doc.name)]
    if positions:
        frappe.throw(
            _("Die Rechnung steht in aktiven Einzugspositionen ({0}). Storniere zuerst die Position oder den Einzugslauf.").format(
                ", ".join(positions)
            )
        )
