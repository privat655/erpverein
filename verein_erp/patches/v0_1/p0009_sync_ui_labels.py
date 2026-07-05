import frappe
from frappe.modules.import_file import import_file_by_path


INVOICE_TIMING_RENAMES = {
    "End of the current subscription period": "Periodenende",
    "Beginning of the current subscription period": "Periodenbeginn",
    "Days before the current subscription period": "Tage vor Periodenbeginn",
}


def execute():
    reload_doctypes()
    sync_workspace()
    migrate_invoice_timing_values()
    frappe.clear_cache()


def reload_doctypes() -> None:
    for doctype in (
        "mitglied",
        "sepa_mandat",
        "beitragsabrechnung",
        "beitragsabrechnung_periode",
        "beitragsabrechnung_vorschau",
    ):
        frappe.reload_doc("verein_erp", "doctype", doctype, force=True)


def sync_workspace() -> None:
    frappe.reload_doc("verein_erp", "workspace", "startseite", force=True)
    import_file_by_path(frappe.get_app_path("verein_erp", "workspace_sidebar", "startseite.json"), force=True)


def migrate_invoice_timing_values() -> None:
    if not frappe.db.exists("DocType", "Beitragsabrechnung"):
        return

    for old_value, new_value in INVOICE_TIMING_RENAMES.items():
        frappe.db.sql(
            """
            update `tabBeitragsabrechnung`
            set generate_invoice_at = %s
            where generate_invoice_at = %s
            """,
            (new_value, old_value),
        )
