from frappe.model.document import Document

from erpverein.services.sepa_collection_service import prevent_collection_deletion, validate_collection_position


class SEPAEinzugsposition(Document):
    def validate(self) -> None:
        validate_collection_position(self)

    def on_trash(self) -> None:
        prevent_collection_deletion(self)
