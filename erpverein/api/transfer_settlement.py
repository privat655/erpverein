import frappe

from erpverein.services.transfer_settlement_service import (
    confirm_transfer_proposal,
    get_transfer_proposal,
    reject_transfer_proposal,
)


@frappe.whitelist()
def get_proposal(bank_transaction: str) -> dict:
    return get_transfer_proposal(bank_transaction)


@frappe.whitelist(methods=["POST"])
def confirm_proposal(bank_transaction: str, proposal_hash: str) -> dict:
    return confirm_transfer_proposal(bank_transaction, proposal_hash)


@frappe.whitelist(methods=["POST"])
def reject_proposal(bank_transaction: str, reason: str) -> dict:
    return reject_transfer_proposal(bank_transaction, reason)
