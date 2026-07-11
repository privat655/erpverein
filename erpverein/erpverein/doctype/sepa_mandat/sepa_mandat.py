from frappe.model.document import Document

from erpverein.services.sepa_mandat_service import (
    delete_sepa_mandat,
    prepare_bank_account_for_mandat,
    sync_sepa_mandat,
    validate_sepa_mandat,
)


class SEPAMandat(Document):
    def validate(self) -> None:
        validate_sepa_mandat(self)

    def before_save(self) -> None:
        prepare_bank_account_for_mandat(self)

    def on_update(self) -> None:
        sync_sepa_mandat(self)

    def on_trash(self) -> None:
        delete_sepa_mandat(self)
