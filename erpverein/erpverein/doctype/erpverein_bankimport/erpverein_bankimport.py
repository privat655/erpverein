from frappe.model.document import Document

from erpverein.services.bank_import_service import validate_bank_import


class ERPvereinBankimport(Document):
    def validate(self) -> None:
        validate_bank_import(self)
