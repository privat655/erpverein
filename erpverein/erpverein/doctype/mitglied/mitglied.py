from frappe.model.document import Document
from frappe.model.naming import make_autoname

from erpverein.services.mitglied_service import (
    MITGLIED_NAMING_SERIES,
    clear_mitglied_link_from_customer,
    sync_mitglied_to_customer,
    validate_mitglied,
)


class Mitglied(Document):
    def autoname(self) -> None:
        self.name = make_autoname(MITGLIED_NAMING_SERIES, doc=self)
        self.mitglied_id = self.name

    def validate(self) -> None:
        validate_mitglied(self)

    def on_update(self) -> None:
        sync_mitglied_to_customer(self)

    def on_trash(self) -> None:
        clear_mitglied_link_from_customer(self)
