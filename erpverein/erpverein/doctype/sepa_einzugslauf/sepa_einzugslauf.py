from frappe.model.document import Document

from erpverein.services.sepa_collection_service import get_run_onload, prevent_collection_deletion, validate_collection_run


class SEPAEinzugslauf(Document):
    def onload(self) -> None:
        for key, value in get_run_onload(self).items():
            self.set_onload(key, value)

    def validate(self) -> None:
        validate_collection_run(self)

    def on_trash(self) -> None:
        prevent_collection_deletion(self)
