import frappe


def execute():
    frappe.reload_doc("verein_erp", "workspace", "startseite", force=True)

    if frappe.db.exists("Workspace", "verein_erp"):
        workspace = frappe.db.get_value("Workspace", "verein_erp", ["module", "title"], as_dict=True)
        if workspace and workspace.module == "verein_erp" and workspace.title == "verein_erp":
            frappe.delete_doc("Workspace", "verein_erp", force=True, ignore_permissions=True)

    frappe.clear_cache()
