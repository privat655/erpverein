from frappe.model.document import Document

from verein_erp.services.subscription_generation_service import set_subscription_run_defaults


class MitgliedSubscriptionLauf(Document):
    def before_insert(self) -> None:
        set_subscription_run_defaults(self)

    def validate(self) -> None:
        set_subscription_run_defaults(self)
