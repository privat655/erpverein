from frappe.model.document import Document

from erpverein.services.sepa_export_profile_service import get_accounts_settings_warnings, validate_sepa_export_profile


class SEPAExportprofil(Document):
    def onload(self) -> None:
        self.set_onload("accounts_settings_warnings", get_accounts_settings_warnings())

    def validate(self) -> None:
        validate_sepa_export_profile(self)
