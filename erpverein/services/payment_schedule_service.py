import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_datetime, getdate, now_datetime

from erpverein.custom_fields import (
    PAYMENT_SCHEDULE_CHANGED_AT_FIELDNAME,
    SALES_INVOICE_BILLING_KIND_FIELDNAME,
    SALES_INVOICE_PAYER_DOCTYPE_FIELDNAME,
    SALES_INVOICE_PAYER_FIELDNAME,
    SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME,
    SALES_INVOICE_SCHEDULE_FINGERPRINT_FIELDNAME,
    SALES_INVOICE_SCHEDULE_NOTE_FIELDNAME,
    SALES_INVOICE_SCHEDULE_ORIGIN_FIELDNAME,
    SALES_INVOICE_SEPA_MANDATE_FIELDNAME,
    SUBSCRIPTION_BILLING_KIND_FIELDNAME,
    SUBSCRIPTION_MANAGED_FIELDNAME,
    SUBSCRIPTION_SOURCES_FIELDNAME,
)
from erpverein.services.billing_common import is_retryable_error, stable_hash
from erpverein.services.sepa_collection_schedule_service import (
    INTERVAL_MONTHLY,
    INTERVAL_WEEKLY,
    clamp_month_day,
    get_schedule_config,
    nominal_occurrences,
)
from erpverein.services.sepa_export_profile_service import is_prenotification_required
from erpverein.services.sepa_mandat_service import MANDATE_STATUS_ACTIVE
from erpverein.setup_data import (
    MODE_OF_PAYMENT_DIRECT_DEBIT,
    MODE_OF_PAYMENT_TRANSFER,
    PAYMENT_TERM_COUNT,
    payment_term_name,
)


ORIGIN_MANDATE = "Mandat"
ORIGIN_STANDARD = "Standard"
ORIGIN_EDITED = "Bearbeitet"
PRENOTIFICATION_NOT_REQUIRED = "Nicht erforderlich"
PRENOTIFICATION_PENDING = "Ausstehend"
PRENOTIFICATION_SENT = "Versendet"
PRENOTIFICATION_RESEND = "Erneut senden"
PRENOTIFICATION_MANUAL = "Manuell bestaetigt"
PAYER_DOCTYPES_BY_ROLE = {"Beitragszahler": "Mitglied", "Mieter": "Mieter"}
BILLING_TYPE_DIRECT_DEBIT = "Lastschrift"
ALLOWED_MODES = (MODE_OF_PAYMENT_DIRECT_DEBIT, MODE_OF_PAYMENT_TRANSFER)
AMOUNT_TOLERANCE = 0.005
COLLECTION_POSITION_DOCTYPE = "SEPA Einzugsposition"


def plan_mandate_rows(
    config: dict, period_from, period_to, posting_date, total: float, standard_due_date=None
) -> tuple[list[dict], str | None]:
    period_from, period_to, posting_date = getdate(period_from), getdate(period_to), getdate(posting_date)
    years_ahead = max(0, period_to.year - period_from.year) + 2
    dates = sorted(value for value in nominal_occurrences(config, period_from, years_ahead=years_ahead) if value <= period_to)
    if not dates:
        return [], _("Kein Mandatstermin liegt im Leistungszeitraum.")
    if dates[0] < posting_date:
        return [], _("Ein Mandatstermin liegt vor dem Rechnungsdatum.")

    rows = [
        {"due_date": value, "payment_amount": flt(mandate_amount(config, value), 2), "mode_of_payment": MODE_OF_PAYMENT_DIRECT_DEBIT}
        for value in dates
    ]
    if any(row["payment_amount"] <= 0 for row in rows):
        return [], _("Der Mandatsplan enthaelt einen Termin ohne Betrag.")

    total = flt(total, 2)
    mandate_total = flt(sum(row["payment_amount"] for row in rows), 2)
    if mandate_total - total > AMOUNT_TOLERANCE:
        return [], _("Die Summe des Mandatsplans ({0}) ist groesser als der Rechnungsbetrag ({1}).").format(
            mandate_total, total
        )
    if total - mandate_total > AMOUNT_TOLERANCE:
        rows.append(
            {
                "due_date": first_free_date(standard_due_date or posting_date, {row["due_date"] for row in rows}),
                "payment_amount": flt(total - mandate_total, 2),
                "mode_of_payment": MODE_OF_PAYMENT_TRANSFER,
            }
        )
    return sorted(rows, key=lambda row: row["due_date"]), None


def mandate_amount(config: dict, nominal) -> float:
    if config["interval"] in {INTERVAL_WEEKLY, INTERVAL_MONTHLY}:
        return flt(config.get("regular_amount"))
    for template in config.get("annual_dates") or []:
        if clamp_month_day(nominal.year, template["month"], template["day"]) == nominal:
            return flt(template["amount"])
    return 0.0


def first_free_date(start, taken: set):
    value = getdate(start)
    while value in taken:
        value = getdate(add_days(value, 1))
    return value


def assign_payment_terms(rows, reserved=()) -> None:
    terms = [row.get("payment_term") for row in rows if row.get("payment_term")]
    if len(terms) != len(set(terms)):
        frappe.throw(_("Jede Zeile im Zahlungsplan braucht eine eigene Zahlungsbedingung."))
    used = set(terms) | set(reserved)
    available = (
        payment_term_name(index) for index in range(1, PAYMENT_TERM_COUNT + 1) if payment_term_name(index) not in used
    )
    for row in sorted(
        (row for row in rows if not row.get("payment_term")), key=lambda row: (getdate(row.get("due_date")), cint(row.get("idx")))
    ):
        term = next(available, None)
        if not term:
            frappe.throw(_("Ein Zahlungsplan darf hoechstens {0} Zeilen haben.").format(PAYMENT_TERM_COUNT))
        row.update({"payment_term": term})


def schedule_fingerprint(rows) -> str:
    return stable_hash(
        [
            {
                "due_date": str(getdate(row.get("due_date"))),
                "invoice_portion": flt(row.get("invoice_portion"), 6),
                "payment_amount": None if flt(row.get("invoice_portion")) else flt(row.get("payment_amount"), 2),
                "mode_of_payment": row.get("mode_of_payment") or None,
                "payment_term": row.get("payment_term") or None,
            }
            for row in sorted(rows, key=lambda row: (getdate(row.get("due_date")), cint(row.get("idx"))))
        ]
    )


def next_prenotification_status(
    current: str | None, has_direct_debit: bool, direct_debit_changed: bool, required: bool = True
) -> str:
    if not has_direct_debit or not required:
        return PRENOTIFICATION_NOT_REQUIRED
    if current in {PRENOTIFICATION_SENT, PRENOTIFICATION_MANUAL}:
        return PRENOTIFICATION_RESEND if direct_debit_changed else current
    if current == PRENOTIFICATION_RESEND:
        return current
    return PRENOTIFICATION_PENDING


def apply_payment_schedule(doc, method: str | None = None) -> None:
    if not is_managed_invoice(doc):
        return
    context = get_billing_context(doc)
    set_billing_provenance(doc, context)
    previous = doc.get_doc_before_save()

    if doc.get(SALES_INVOICE_SCHEDULE_ORIGIN_FIELDNAME) == ORIGIN_EDITED or schedule_was_edited(doc):
        mandate = context.mandate.name if context.mandate else None
        finalize_draft_schedule(doc, previous, origin=ORIGIN_EDITED, mandate=mandate, note=None)
        return

    plan = build_schedule_plan(doc, context)
    if plan.rows:
        replace_schedule_rows(doc, plan.rows)
        run_erpnext_schedule_checks(doc)
        origin = ORIGIN_MANDATE
    else:
        if doc.get(SALES_INVOICE_SCHEDULE_ORIGIN_FIELDNAME) == ORIGIN_MANDATE:
            replace_schedule_rows(doc, [standard_schedule_row(doc)])
            run_erpnext_schedule_checks(doc)
        for row in doc.payment_schedule:
            row.mode_of_payment = MODE_OF_PAYMENT_TRANSFER
        origin = ORIGIN_STANDARD
    finalize_draft_schedule(doc, previous, origin=origin, mandate=plan.mandate, note=plan.note)


def is_managed_invoice(doc) -> bool:
    if doc.doctype != "Sales Invoice" or not doc.get("subscription"):
        return False
    if cint(doc.get("is_return")) or cint(doc.get("is_pos")) or cint(doc.get("is_debit_note")) or doc.get("is_opening") == "Yes":
        return False
    return bool(cint(frappe.db.get_value("Subscription", doc.subscription, SUBSCRIPTION_MANAGED_FIELDNAME)))


def get_billing_context(doc) -> frappe._dict:
    context = frappe._dict(
        billing_kind=frappe.db.get_value("Subscription", doc.subscription, SUBSCRIPTION_BILLING_KIND_FIELDNAME),
        payer_doctype=None,
        payer=None,
        direct_debit=False,
        mandate=None,
        conflict=None,
    )
    sources = frappe.get_all(
        "ERPverein Subscription Source",
        filters={"parenttype": "Subscription", "parent": doc.subscription, "parentfield": SUBSCRIPTION_SOURCES_FIELDNAME},
        fields=["source_doctype", "source_name", "source_role"],
    )
    payers = [row for row in sources if PAYER_DOCTYPES_BY_ROLE.get(row.source_role) == row.source_doctype]
    if len(payers) != 1:
        context.conflict = _("Der Zahler der Subscription ist nicht eindeutig.")
        return context

    context.payer_doctype, context.payer = payers[0].source_doctype, payers[0].source_name
    payer = frappe.db.get_value(context.payer_doctype, context.payer, ["abrechnungsart", "sepa_mandat"], as_dict=True)
    context.direct_debit = bool(payer and payer.abrechnungsart == BILLING_TYPE_DIRECT_DEBIT)
    if context.direct_debit:
        context.mandate = get_active_mandate(context.payer_doctype, context.payer, payer.sepa_mandat)
    return context


def get_active_mandate(payer_doctype: str, payer: str, mandate_name: str | None):
    if not mandate_name or not frappe.db.exists("SEPA Mandat", mandate_name):
        return None
    mandate = frappe.get_doc("SEPA Mandat", mandate_name)
    if mandate.status != MANDATE_STATUS_ACTIVE or mandate.bezugs_doctype != payer_doctype or mandate.bezugs_name != payer:
        return None
    return mandate


def set_billing_provenance(doc, context) -> None:
    doc.set(SALES_INVOICE_BILLING_KIND_FIELDNAME, context.billing_kind)
    doc.set(SALES_INVOICE_PAYER_DOCTYPE_FIELDNAME, context.payer_doctype)
    doc.set(SALES_INVOICE_PAYER_FIELDNAME, context.payer)


def schedule_was_edited(doc) -> bool:
    stored = doc.get(SALES_INVOICE_SCHEDULE_FINGERPRINT_FIELDNAME)
    return bool(stored) and stored != schedule_fingerprint(doc.payment_schedule)


def build_schedule_plan(doc, context) -> frappe._dict:
    if context.conflict:
        return frappe._dict(rows=None, mandate=None, note=context.conflict)
    if not context.direct_debit:
        return frappe._dict(rows=None, mandate=None, note=None)
    if not context.mandate:
        return frappe._dict(rows=None, mandate=None, note=_("Abrechnungsart Lastschrift ohne aktives SEPA-Mandat."))

    config, complete = get_schedule_config(context.mandate)
    if not complete:
        note = _("Der Mandatsplan ist unvollstaendig.")
    elif doc.get("currency") != config.get("currency"):
        note = _("Die Waehrung der Rechnung passt nicht zum Mandat.")
    elif flt(doc.get("total_advance")):
        note = _("Die Rechnung enthaelt verrechnete Anzahlungen.")
    elif not doc.get("from_date") or not doc.get("to_date"):
        note = _("Der Leistungszeitraum der Rechnung fehlt.")
    else:
        rows, note = plan_mandate_rows(
            config,
            doc.from_date,
            doc.to_date,
            doc.posting_date,
            schedule_total(doc),
            standard_due_date=standard_due_date(doc),
        )
        if rows:
            return frappe._dict(rows=rows, mandate=context.mandate.name, note=None)
    return frappe._dict(rows=None, mandate=None, note=note)


def standard_due_date(doc):
    from erpnext.accounts.party import get_due_date

    days_until_due = cint(frappe.db.get_value("Subscription", doc.subscription, "days_until_due"))
    if days_until_due:
        return getdate(add_days(doc.posting_date, days_until_due))
    return getdate(get_due_date(doc.posting_date, "Customer", doc.customer, doc.company) or doc.posting_date)


def schedule_total(doc) -> float:
    total = flt(doc.get("rounded_total") or doc.get("grand_total")) - flt(doc.get("write_off_amount"))
    return flt(total, doc.precision("grand_total"))


def replace_schedule_rows(doc, plan_rows: list[dict]) -> None:
    conversion_rate = flt(doc.get("conversion_rate")) or 1
    precision = doc.precision("grand_total")
    doc.payment_terms_template = None
    doc.set("payment_schedule", [])
    for row in plan_rows:
        amount = flt(row["payment_amount"], precision)
        base_amount = flt(amount * conversion_rate, precision)
        doc.append(
            "payment_schedule",
            {
                "due_date": row["due_date"],
                "invoice_portion": 0,
                "payment_amount": amount,
                "base_payment_amount": base_amount,
                "outstanding": amount,
                "base_outstanding": base_amount,
                "paid_amount": 0,
                "base_paid_amount": 0,
                "discounted_amount": 0,
                "mode_of_payment": row["mode_of_payment"],
                "payment_term": row.get("payment_term"),
            },
        )


def run_erpnext_schedule_checks(doc) -> None:
    doc.validate_payment_schedule_dates()
    doc.set_due_date()
    doc.validate_payment_schedule_amount()
    doc.validate_due_date()


def finalize_draft_schedule(doc, previous, *, origin: str, mandate: str | None, note: str | None) -> None:
    for row in doc.payment_schedule:
        row.mode_of_payment = row.mode_of_payment or MODE_OF_PAYMENT_TRANSFER
        row.outstanding = flt(row.payment_amount)
        row.base_outstanding = flt(row.base_payment_amount)
    validate_schedule_modes(doc)
    assign_payment_terms(doc.payment_schedule)
    has_direct_debit = has_direct_debit_rows(doc)
    if has_direct_debit and not mandate:
        frappe.throw(_("Eine Lastschriftzeile braucht ein aktives SEPA-Mandat des Zahlers."))
    direct_debit_changed = stamp_changed_rows(doc, previous.payment_schedule if previous else [])
    set_schedule_state(doc, origin=origin, mandate=mandate, note=note, direct_debit_changed=direct_debit_changed)
    if doc.docstatus == 1:
        doc.set_status()


def validate_schedule_modes(doc) -> None:
    for row in doc.payment_schedule:
        if row.mode_of_payment not in ALLOWED_MODES:
            frappe.throw(
                _("Zeile {0}: Zahlungsweise muss {1} oder {2} sein.").format(
                    row.idx, MODE_OF_PAYMENT_DIRECT_DEBIT, MODE_OF_PAYMENT_TRANSFER
                )
            )


def has_direct_debit_rows(doc) -> bool:
    return any(row.mode_of_payment == MODE_OF_PAYMENT_DIRECT_DEBIT for row in doc.payment_schedule)


def row_key(row) -> tuple:
    return (str(getdate(row.get("due_date"))), flt(row.get("payment_amount"), 2), row.get("mode_of_payment") or None)


def stamp_changed_rows(doc, previous_rows) -> bool:
    previous_stamps = {row_key(row): row.get(PAYMENT_SCHEDULE_CHANGED_AT_FIELDNAME) for row in previous_rows}
    now = now_datetime()
    direct_debit_changed = False
    for row in doc.payment_schedule:
        stamp = previous_stamps.get(row_key(row))
        if stamp:
            row.set(PAYMENT_SCHEDULE_CHANGED_AT_FIELDNAME, stamp)
            continue
        row.set(PAYMENT_SCHEDULE_CHANGED_AT_FIELDNAME, now)
        direct_debit_changed = direct_debit_changed or row.mode_of_payment == MODE_OF_PAYMENT_DIRECT_DEBIT
    removed = set(previous_stamps) - {row_key(row) for row in doc.payment_schedule}
    return direct_debit_changed or any(key[2] == MODE_OF_PAYMENT_DIRECT_DEBIT for key in removed)


def set_schedule_state(doc, *, origin: str, mandate: str | None, note: str | None, direct_debit_changed: bool) -> None:
    has_direct_debit = has_direct_debit_rows(doc)
    doc.set(SALES_INVOICE_SCHEDULE_ORIGIN_FIELDNAME, origin)
    doc.set(SALES_INVOICE_SEPA_MANDATE_FIELDNAME, mandate if has_direct_debit else None)
    doc.set(SALES_INVOICE_SCHEDULE_NOTE_FIELDNAME, note)
    doc.set(SALES_INVOICE_SCHEDULE_FINGERPRINT_FIELDNAME, schedule_fingerprint(doc.payment_schedule))
    doc.set(
        SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME,
        next_prenotification_status(
            doc.get(SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME),
            has_direct_debit,
            direct_debit_changed,
            required=is_prenotification_required(doc),
        ),
    )


def get_locked_rows(doc) -> dict[str, str]:
    from erpnext.accounts.doctype.dunning.dunning import get_payment_schedule_outstanding

    locked = {}
    outstanding = get_payment_schedule_outstanding(doc, doc.payment_schedule)
    for row in doc.payment_schedule:
        row_outstanding = flt(outstanding.get(row.name, row.outstanding))
        if flt(row.paid_amount) > AMOUNT_TOLERANCE or flt(row.payment_amount) - row_outstanding > AMOUNT_TOLERANCE:
            locked[row.name] = _("Die Zeile ist ganz oder teilweise bezahlt.")
    dunned = frappe.get_all(
        "Overdue Payment",
        filters={"parenttype": "Dunning", "sales_invoice": doc.name, "docstatus": ["<", 2]},
        pluck="payment_schedule",
    )
    for row_name in dunned:
        if row_name:
            locked.setdefault(row_name, _("Die Zeile steht in einer Mahnung."))
    for position in get_active_collection_positions(doc.name):
        if position.payment_schedule_row:
            locked.setdefault(
                position.payment_schedule_row, _("Die Zeile steht in der Einzugsposition {0}.").format(position.name)
            )
    return locked


def get_active_collection_positions(invoice: str) -> list[frappe._dict]:
    return frappe.get_all(
        COLLECTION_POSITION_DOCTYPE,
        filters={"sales_invoice": invoice, "active_key": ["is", "set"]},
        fields=["name", "payment_schedule_row"],
        order_by="name asc",
    )


def get_schedule_editor(invoice: str) -> dict:
    doc = frappe.get_doc("Sales Invoice", invoice)
    doc.check_permission("read")
    locked = get_locked_rows(doc) if doc.docstatus == 1 else {}
    return {
        "invoice": doc.name,
        "modified": str(doc.modified),
        "currency": doc.currency,
        "total": schedule_total(doc),
        "modes": list(ALLOWED_MODES),
        "rows": [
            {
                "name": row.name,
                "due_date": str(getdate(row.due_date)),
                "payment_amount": flt(row.payment_amount, 2),
                "mode_of_payment": row.mode_of_payment,
                "payment_term": row.payment_term,
                "paid_amount": flt(row.paid_amount, 2),
                "locked": row.name in locked,
                "lock_reason": locked.get(row.name),
            }
            for row in sorted(doc.payment_schedule, key=lambda row: (getdate(row.due_date), row.idx))
        ],
    }


def update_submitted_schedule(invoice: str, rows, modified: str | None = None) -> dict:
    doc = get_submitted_managed_invoice(invoice, modified)
    locked = get_locked_rows(doc)
    direct_debit_changed = apply_editor_rows(doc, normalize_editor_rows(rows), locked)
    mandate = require_mandate_for_direct_debit(doc, get_billing_context(doc))
    finalize_submitted_schedule(
        doc,
        origin=ORIGIN_EDITED,
        mandate=mandate,
        note=None,
        direct_debit_changed=direct_debit_changed,
        comment=_("Zahlungsplan bearbeitet."),
    )
    return {"invoice": doc.name, "prenotification_status": doc.get(SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME)}


def get_submitted_managed_invoice(invoice: str, modified: str | None = None):
    doc = frappe.get_doc("Sales Invoice", invoice)
    doc.check_permission("write")
    doc.check_permission("submit")
    if doc.docstatus != 1:
        frappe.throw(_("Der Zahlungsplan eines Entwurfs wird im Formular bearbeitet."))
    if not is_managed_invoice(doc):
        frappe.throw(_("Die Rechnung stammt nicht aus einer ERPverein-Subscription."))
    if modified and get_datetime(modified) != get_datetime(doc.modified):
        frappe.throw(_("Die Rechnung wurde inzwischen geaendert. Bitte neu laden."))
    return doc


def normalize_editor_rows(rows) -> list[frappe._dict]:
    if isinstance(rows, str):
        try:
            rows = json.loads(rows)
        except (TypeError, ValueError):
            frappe.throw(_("Die Zeilen des Zahlungsplans sind ungueltig."))
    if not isinstance(rows, list) or not rows or len(rows) > PAYMENT_TERM_COUNT:
        frappe.throw(_("Der Zahlungsplan braucht 1 bis {0} Zeilen.").format(PAYMENT_TERM_COUNT))
    normalized = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or not row.get("due_date"):
            frappe.throw(_("Zeile {0}: Die Faelligkeit fehlt.").format(index))
        amount = flt(row.get("payment_amount"), 2)
        if amount <= 0:
            frappe.throw(_("Zeile {0}: Der Betrag muss groesser als null sein.").format(index))
        if row.get("mode_of_payment") not in ALLOWED_MODES:
            frappe.throw(
                _("Zeile {0}: Zahlungsweise muss {1} oder {2} sein.").format(
                    index, MODE_OF_PAYMENT_DIRECT_DEBIT, MODE_OF_PAYMENT_TRANSFER
                )
            )
        normalized.append(
            frappe._dict(
                name=row.get("name") or None,
                due_date=getdate(row["due_date"]),
                payment_amount=amount,
                mode_of_payment=row["mode_of_payment"],
            )
        )
    return normalized


def apply_editor_rows(doc, desired: list[frappe._dict], locked: dict[str, str]) -> bool:
    existing = {row.name: row for row in doc.payment_schedule}
    desired_names = {row.name for row in desired if row.name}
    if desired_names - set(existing):
        frappe.throw(_("Der Zahlungsplan enthaelt eine unbekannte Zeile. Bitte neu laden."))
    if set(locked) - desired_names:
        frappe.throw(_("Eine bezahlte oder gemahnte Zeile darf nicht entfernt werden."))
    due_dates = [row.due_date for row in desired]
    if len(due_dates) != len(set(due_dates)):
        frappe.throw(_("Jede Faelligkeit darf im Zahlungsplan nur einmal vorkommen."))
    desired_total = flt(sum(row.payment_amount for row in desired), 2)
    if abs(desired_total - flt(schedule_total(doc), 2)) > AMOUNT_TOLERANCE:
        frappe.throw(
            _("Die Summe des Zahlungsplans ({0}) muss dem Rechnungsbetrag ({1}) entsprechen.").format(
                desired_total, schedule_total(doc)
            )
        )

    conversion_rate = flt(doc.get("conversion_rate")) or 1
    precision = doc.precision("grand_total")
    now = now_datetime()
    direct_debit_changed = False
    rows = []
    for row in desired:
        current = existing.get(row.name) if row.name else None
        if current and row_key(current) == row_key(row):
            rows.append(current)
            continue
        if current and current.name in locked:
            frappe.throw(_("Eine bezahlte oder gemahnte Zeile darf nicht geaendert werden."))
        target = current or frappe._dict()
        direct_debit_changed = direct_debit_changed or MODE_OF_PAYMENT_DIRECT_DEBIT in {
            target.get("mode_of_payment"),
            row.mode_of_payment,
        }
        amount = flt(row.payment_amount, precision)
        base_amount = flt(amount * conversion_rate, precision)
        target.update(
            {
                "due_date": row.due_date,
                "invoice_portion": 0,
                "payment_amount": amount,
                "base_payment_amount": base_amount,
                "outstanding": flt(amount - flt(target.get("paid_amount")), precision),
                "base_outstanding": flt(base_amount - flt(target.get("base_paid_amount")), precision),
                "mode_of_payment": row.mode_of_payment,
                PAYMENT_SCHEDULE_CHANGED_AT_FIELDNAME: now,
            }
        )
        rows.append(target)
    removed = [row for name, row in existing.items() if name not in desired_names]
    direct_debit_changed = direct_debit_changed or any(
        row.mode_of_payment == MODE_OF_PAYMENT_DIRECT_DEBIT for row in removed
    )
    doc.payment_terms_template = None
    doc.set("payment_schedule", sorted(rows, key=lambda row: getdate(row.get("due_date"))))
    for index, row in enumerate(doc.payment_schedule, start=1):
        row.idx = index
    assign_payment_terms(doc.payment_schedule)
    return direct_debit_changed


def require_mandate_for_direct_debit(doc, context) -> str | None:
    if not has_direct_debit_rows(doc):
        return None
    if not context.mandate:
        frappe.throw(_("Eine Lastschriftzeile braucht ein aktives SEPA-Mandat des Zahlers."))
    if not frappe.has_permission("SEPA Mandat", "read", doc=context.mandate.name):
        frappe.throw(_("Keine Berechtigung fuer das SEPA-Mandat des Zahlers."), frappe.PermissionError)
    return context.mandate.name


def finalize_submitted_schedule(
    doc, *, origin: str, mandate: str | None, note: str | None, direct_debit_changed: bool, comment: str
) -> None:
    previous_mandate = doc.get(SALES_INVOICE_SEPA_MANDATE_FIELDNAME)
    if has_direct_debit_rows(doc) and previous_mandate and mandate and previous_mandate != mandate:
        # The sent invoice names the old mandate; the direct debit rows need a new pre-notification.
        now = now_datetime()
        for row in doc.payment_schedule:
            if row.mode_of_payment == MODE_OF_PAYMENT_DIRECT_DEBIT:
                row.set(PAYMENT_SCHEDULE_CHANGED_AT_FIELDNAME, now)
        direct_debit_changed = True
    set_schedule_state(doc, origin=origin, mandate=mandate, note=note, direct_debit_changed=direct_debit_changed)
    run_erpnext_schedule_checks(doc)
    doc.flags.ignore_validate_update_after_submit = True
    doc.save()
    doc.set_status(update=True)
    doc.add_comment("Info", comment)


def preview_schedule_regeneration(invoice: str) -> dict:
    doc = frappe.get_doc("Sales Invoice", invoice)
    doc.check_permission("read")
    return build_regeneration_preview(doc)


def build_regeneration_preview(doc) -> dict:
    preview = {"invoice": doc.name, "allowed": False, "reason": None}
    if doc.docstatus != 1 or not is_managed_invoice(doc):
        preview["reason"] = _("Nur gebuchte Rechnungen aus ERPverein-Subscriptions lassen sich neu planen.")
        return preview
    if get_locked_rows(doc):
        preview["reason"] = _("Mindestens eine Zeile ist bezahlt oder gemahnt.")
        return preview

    plan = build_schedule_plan(doc, get_billing_context(doc))
    rows = plan.rows or [standard_schedule_row(doc)]
    preview.update(
        {
            "allowed": True,
            "origin": ORIGIN_MANDATE if plan.rows else ORIGIN_STANDARD,
            "mandate": plan.mandate,
            "note": plan.note,
            "rows": [
                {
                    "due_date": str(getdate(row["due_date"])),
                    "payment_amount": flt(row["payment_amount"], 2),
                    "mode_of_payment": row["mode_of_payment"],
                }
                for row in rows
            ],
            "current_rows": [
                {
                    "due_date": str(getdate(row.due_date)),
                    "payment_amount": flt(row.payment_amount, 2),
                    "mode_of_payment": row.mode_of_payment,
                }
                for row in sorted(doc.payment_schedule, key=lambda row: getdate(row.due_date))
            ],
        }
    )
    preview["preview_hash"] = stable_hash(
        {"invoice": doc.name, "modified": str(doc.modified), "origin": preview["origin"], "rows": preview["rows"]}
    )
    return preview


def standard_schedule_row(doc) -> dict:
    return {
        "due_date": standard_due_date(doc),
        "payment_amount": schedule_total(doc),
        "mode_of_payment": MODE_OF_PAYMENT_TRANSFER,
    }


def apply_schedule_regeneration(invoice: str, preview_hash: str, comment: str | None = None) -> dict:
    doc = get_submitted_managed_invoice(invoice)
    preview = build_regeneration_preview(doc)
    if not preview["allowed"]:
        frappe.throw(preview["reason"])
    if preview["preview_hash"] != preview_hash:
        frappe.throw(_("Die Vorschau ist veraltet. Bitte neu erzeugen."))

    previous_rows = list(doc.payment_schedule)
    replace_schedule_rows(doc, preview["rows"])
    assign_payment_terms(doc.payment_schedule)
    finalize_submitted_schedule(
        doc,
        origin=preview["origin"],
        mandate=preview["mandate"],
        note=preview["note"],
        direct_debit_changed=stamp_changed_rows(doc, previous_rows),
        comment=comment or _("Zahlungsplan aus dem Mandat neu erzeugt."),
    )
    return {"invoice": doc.name, "prenotification_status": doc.get(SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME)}


def preview_schedule_backfill(limit: int = 200) -> dict:
    candidates = frappe.get_list(
        "Sales Invoice",
        filters={
            "docstatus": 1,
            "subscription": ["is", "set"],
            SALES_INVOICE_SCHEDULE_ORIGIN_FIELDNAME: ["is", "not set"],
            "outstanding_amount": [">", 0],
        },
        fields=["name"],
        order_by="posting_date asc, name asc",
        limit_page_length=max(1, min(cint(limit) or 200, 500)),
    )
    previews = []
    for candidate in candidates:
        doc = frappe.get_doc("Sales Invoice", candidate.name)
        if is_managed_invoice(doc):
            previews.append(build_regeneration_preview(doc))
    return {
        "invoices": previews,
        "preview_hash": stable_hash([preview.get("preview_hash") or preview["invoice"] for preview in previews]),
    }


def apply_schedule_backfill(preview_hash: str, limit: int = 200) -> dict:
    preview = preview_schedule_backfill(limit)
    if preview["preview_hash"] != preview_hash:
        frappe.throw(_("Die Vorschau ist veraltet. Bitte neu erzeugen."))

    result = {"applied": 0, "skipped": 0, "failed": 0}
    for item in preview["invoices"]:
        if not item["allowed"]:
            result["skipped"] += 1
            continue
        savepoint = f"erpverein_schedule_{frappe.generate_hash(length=12)}"
        frappe.db.savepoint(savepoint)
        try:
            apply_schedule_regeneration(item["invoice"], item["preview_hash"], comment=_("Zahlungsplan nachgetragen."))
            frappe.db.release_savepoint(savepoint)
            result["applied"] += 1
        except Exception as exc:
            if is_retryable_error(exc):
                raise
            frappe.db.rollback(save_point=savepoint)
            frappe.db.release_savepoint(savepoint)
            frappe.log_error(title=f"ERPverein schedule backfill failed: {item['invoice']}", message=frappe.get_traceback())
            result["failed"] += 1
    return result
