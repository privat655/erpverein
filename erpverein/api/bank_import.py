import frappe

from erpverein.services.bank_import_service import import_bank_statement, reprocess_bank_import
from erpverein.services.bank_statement_parser import MAX_MANIFEST_BYTES, MAX_STATEMENT_BYTES
from erpverein.services.sepa_settlement_service import start_import_reconciliation


@frappe.whitelist(methods=["POST"])
def import_statement(manifest: str | None = None) -> dict:
    """n8n sends the Webtop's multipart parts unchanged: `manifest` (file or text) and `transactions` (file)."""
    files = frappe.request.files if frappe.request else {}
    if not manifest and files.get("manifest"):
        manifest = files["manifest"].stream.read(MAX_MANIFEST_BYTES + 1)
    transactions = files.get("transactions")
    return import_bank_statement(manifest, transactions.stream.read(MAX_STATEMENT_BYTES + 1) if transactions else None)


@frappe.whitelist(methods=["POST"])
def reprocess(bank_import: str) -> dict:
    return reprocess_bank_import(bank_import)


@frappe.whitelist(methods=["POST"])
def start_reconciliation(bank_import: str) -> dict:
    return start_import_reconciliation(bank_import)
