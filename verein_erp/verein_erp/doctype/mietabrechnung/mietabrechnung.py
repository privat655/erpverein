from frappe.model.document import Document

from verein_erp.services.rental_subscription_generation_service import set_rental_run_defaults


class Mietabrechnung(Document):
    def before_insert(self) -> None:
        set_rental_run_defaults(self)

    def validate(self) -> None:
        set_rental_run_defaults(self)
