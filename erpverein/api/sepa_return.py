import frappe

from erpverein.services.sepa_return_service import confirm_return_assignment


@frappe.whitelist(methods=["POST"])
def confirm_return(bank_transaction: str, position: str, note: str | None = None) -> dict:
    return confirm_return_assignment(bank_transaction, position, note)
