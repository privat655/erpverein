from frappe.model.document import Document

from verein_erp.services.sepa_mandat_service import (
    clear_mandat_link_from_reference,
    sync_sepa_mandat,
    validate_sepa_mandat,
)


class SEPAMandat(Document):
    def validate(self) -> None:
        validate_sepa_mandat(self)

    def on_update(self) -> None:
        sync_sepa_mandat(self)

    def on_trash(self) -> None:
        clear_mandat_link_from_reference(self)
