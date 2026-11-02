import hashlib

import frappe
from frappe import _
from frappe.utils import cint, flt, fmt_money, now_datetime

from erpverein.services.pain008_writer import (
    BIC_PATTERN,
    MAX_NAME_LENGTH,
    Pain008Error,
    build_pain008,
    is_sepa_reference,
    to_sepa_text,
)
from erpverein.services.sepa_collection_service import (
    POSITION_STATUS_EXPORTED,
    RUN_DOCTYPE,
    RUN_STATUS_CHECKED,
    RUN_STATUS_EXPORTED,
    UPDATE_FLAG,
    get_active_position_docs,
    get_locked_run,
    get_run_profile,
    save_position,
    store_check_result,
    validate_run_positions,
)
from erpverein.services.sepa_mandat_service import normalize_bic, normalize_iban, validate_iban


def export_collection_run(run_name: str) -> dict:
    run = get_locked_run(run_name)
    if run.status != RUN_STATUS_CHECKED:
        frappe.throw(_("Nur ein gepruefter Einzugslauf laesst sich exportieren. Pruefe den Lauf zuerst."))
    profile = get_run_profile(run)
    frappe.has_permission("Bank Account", "read", doc=run.bank_account, throw=True)
    results = validate_run_positions(run, profile)
    if any(results.values()):
        store_check_result(run, results)
        return {
            "run": run.name,
            "exported": False,
            "errors": [{"position": name, "reason": reason} for name, reason in results.items() if reason],
        }

    positions = get_active_position_docs(run.name)
    exported_at = now_datetime()
    message = build_export_message(run, profile, positions, exported_at)
    try:
        xml = build_pain008(message)
    except Pain008Error as exc:
        frappe.throw(_("Die pain.008-Datei ist ungueltig: {0}").format(str(exc)))
    sha256 = hashlib.sha256(xml).hexdigest()
    file_doc = save_export_file(run, xml)

    for position in positions:
        save_position(position, {"status": POSITION_STATUS_EXPORTED, "processing_error": None})
    run.update(
        {
            "status": RUN_STATUS_EXPORTED,
            "msg_id": message["msg_id"],
            "pmtinf_id": message["pmtinf_id"],
            "export_format": profile.export_format,
            "export_profile_version": cint(profile.profile_version),
            "export_file": file_doc.file_url,
            "export_file_sha256": sha256,
            "exported_at": exported_at,
            "exported_by": frappe.session.user,
        }
    )
    run.flags[UPDATE_FLAG] = True
    run.save()
    run.add_comment(
        "Info",
        _("pain.008-Datei erzeugt: {0} Lastschriften, Summe {1}, SHA-256 {2}.").format(
            len(positions), fmt_money(run.total_amount, currency=run.currency), sha256
        ),
    )
    return {
        "run": run.name,
        "exported": True,
        "file_url": file_doc.file_url,
        "sha256": sha256,
        "count": len(positions),
        "total": flt(run.total_amount, 2),
    }


def build_export_message(run, profile, positions, exported_at) -> dict:
    account = frappe.db.get_value("Bank Account", run.bank_account, ["iban", "branch_code"], as_dict=True)
    creditor_iban = normalize_iban(account.iban) if account else None
    if not validate_iban(creditor_iban):
        frappe.throw(_("Die IBAN des Vereinskontos fehlt oder ist ungueltig."))
    creditor_bic = normalize_bic(account.branch_code)
    transactions = []
    for position in positions:
        mandate = frappe.db.get_value("SEPA Mandat", position.sepa_mandate, ["kontoinhaber", "iban"], as_dict=True)
        transactions.append(
            {
                "end_to_end_id": position.end_to_end_reference,
                "amount": flt(position.amount, 2),
                "mandate_id": position.mandate_reference_snapshot,
                "mandate_date": position.mandate_date_snapshot,
                "debtor_name": to_sepa_text(mandate.kontoinhaber, MAX_NAME_LENGTH),
                "debtor_iban": normalize_iban(mandate.iban),
                "purpose": position.purpose,
            }
        )
    return {
        "msg_id": make_msg_id(run.name, exported_at),
        "created_at": exported_at,
        "creditor_name": to_sepa_text(profile.creditor_name, MAX_NAME_LENGTH),
        "creditor_iban": creditor_iban,
        "creditor_bic": creditor_bic if creditor_bic and BIC_PATTERN.fullmatch(creditor_bic) else None,
        "creditor_id": profile.creditor_identifier,
        "pmtinf_id": run.name,
        "collection_date": run.collection_date,
        "sequence_type": run.sequence_type,
        "batch_booking": cint(run.batch_booking),
        "transactions": transactions,
    }


def make_msg_id(run_name: str, exported_at) -> str:
    msg_id = f"{run_name}-{exported_at:%y%m%d%H%M%S}"
    if is_sepa_reference(msg_id):
        return msg_id
    return "ERPV-" + hashlib.sha256(msg_id.encode()).hexdigest()[:30]


def save_export_file(run, xml: bytes):
    file_doc = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": f"{run.name}.xml",
            "attached_to_doctype": RUN_DOCTYPE,
            "attached_to_name": run.name,
            "attached_to_field": "export_file",
            "is_private": 1,
            "content": xml,
        }
    )
    file_doc.insert()
    return file_doc
