import frappe
from frappe.modules.import_file import import_file_by_path


def execute() -> None:
    frappe.reload_doc("verein_erp", "workspace", "startseite", force=True)
    import_file_by_path(frappe.get_app_path("verein_erp", "workspace_sidebar", "startseite.json"), force=True)
    frappe.clear_cache()
