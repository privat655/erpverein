from frappe.model.document import Document
from frappe.model.naming import make_autoname

from erpverein.services.mieter_service import (
    MIETER_NAMING_SERIES,
    clear_mieter_link_from_customer,
    sync_mieter_to_customer,
    validate_mieter,
)


class Mieter(Document):
    def autoname(self) -> None:
        self.name = make_autoname(MIETER_NAMING_SERIES, doc=self)
        self.mieter_id = self.name

    def validate(self) -> None:
        validate_mieter(self)

    def on_update(self) -> None:
        sync_mieter_to_customer(self)

    def on_trash(self) -> None:
        clear_mieter_link_from_customer(self)
