from frappe.model.document import Document

from erpverein.services.billing_common import RUN_STATUS_DRAFT
from erpverein.services.billing_run_service import prevent_frozen_run_deletion, validate_run_freeze
from erpverein.services.rental_subscription_generation_service import RUN_INPUT_FIELDS, set_rental_run_defaults, validate_rental_periods


class Mietabrechnung(Document):
    def before_insert(self) -> None:
        set_rental_run_defaults(self)
        self.status = RUN_STATUS_DRAFT

    def validate(self) -> None:
        set_rental_run_defaults(self)
        validate_rental_periods(self)
        validate_run_freeze(self, RUN_INPUT_FIELDS)

    def on_trash(self) -> None:
        prevent_frozen_run_deletion(self)
