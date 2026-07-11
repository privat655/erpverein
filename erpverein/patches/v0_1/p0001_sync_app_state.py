import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.modules.import_file import import_file_by_path


CUSTOM_FIELDS = {
    "Customer": [
        {
            "fieldname": "erpverein_mitglied",
            "label": "ERPverein Mitglied",
            "fieldtype": "Link",
            "options": "Mitglied",
            "insert_after": "customer_name",
            "description": "1:1 verknuepfter Mitglied-Datensatz aus ERPverein.",
            "no_copy": 1,
            "in_standard_filter": 1,
            "search_index": 1,
            "unique": 1,
        },
        {
            "fieldname": "erpverein_sync_state",
            "label": "ERPverein Sync-Status",
            "fieldtype": "Long Text",
            "insert_after": "erpverein_mitglied",
            "description": "Interner ERPverein-Snapshot fuer kontrollierte Synchronisation.",
            "hidden": 1,
            "no_copy": 1,
            "read_only": 1,
        },
        {
            "fieldname": "erpverein_mieter",
            "label": "ERPverein Mieter",
            "fieldtype": "Link",
            "options": "Mieter",
            "insert_after": "erpverein_sync_state",
            "description": "1:1 verknuepfter Mieter-Datensatz aus ERPverein.",
            "no_copy": 1,
            "in_standard_filter": 1,
            "search_index": 1,
            "unique": 1,
        },
    ],
    "Bank Account": [
        {
            "fieldname": "erpverein_managed",
            "label": "ERPverein verwaltet",
            "fieldtype": "Check",
            "insert_after": "party",
            "description": "Markiert ausschliesslich durch ERPverein angelegte Bankkonten.",
            "no_copy": 1,
            "read_only": 1,
            "in_standard_filter": 1,
        },
        {
            "fieldname": "erpverein_sync_state",
            "label": "ERPverein Sync-Status",
            "fieldtype": "Long Text",
            "insert_after": "erpverein_managed",
            "description": "Interner Snapshot fuer kontrollierte Bankkonto-Synchronisation.",
            "hidden": 1,
            "no_copy": 1,
            "read_only": 1,
        },
    ],
    "Subscription": [
        {"fieldname": "erpverein_managed", "label": "ERPverein verwaltet", "fieldtype": "Check", "insert_after": "party", "no_copy": 1, "read_only": 1, "in_standard_filter": 1},
        {"fieldname": "erpverein_billing_kind", "label": "ERPverein Abrechnungsart", "fieldtype": "Select", "options": "\nMitgliedsbeitrag\nMiete", "insert_after": "erpverein_managed", "no_copy": 1, "read_only": 1, "in_standard_filter": 1},
        {"fieldname": "erpverein_generation_key", "label": "ERPverein Generierungsschluessel", "fieldtype": "Data", "insert_after": "erpverein_billing_kind", "hidden": 1, "no_copy": 1, "read_only": 1, "unique": 1},
        {"fieldname": "erpverein_generation_payload", "label": "ERPverein Generierungsdaten", "fieldtype": "Long Text", "insert_after": "erpverein_generation_key", "hidden": 1, "no_copy": 1, "read_only": 1},
        {"fieldname": "erpverein_generation_run_doctype", "label": "ERPverein Lauf-Typ", "fieldtype": "Link", "options": "DocType", "insert_after": "erpverein_generation_payload", "hidden": 1, "no_copy": 1, "read_only": 1},
        {"fieldname": "erpverein_generation_run", "label": "ERPverein Abrechnungslauf", "fieldtype": "Dynamic Link", "options": "erpverein_generation_run_doctype", "insert_after": "erpverein_generation_run_doctype", "no_copy": 1, "read_only": 1},
        {"fieldname": "erpverein_sources", "label": "ERPverein Quellen", "fieldtype": "Table", "options": "ERPverein Subscription Source", "insert_after": "erpverein_generation_run", "no_copy": 1, "read_only": 1},
    ],
}


def execute() -> None:
    create_custom_fields(CUSTOM_FIELDS, update=True)
    for salutation in ("Mr", "Ms"):
        if not frappe.db.exists("Salutation", salutation):
            frappe.get_doc({"doctype": "Salutation", "salutation": salutation}).insert(ignore_permissions=True)

    if not frappe.db.exists("Customer Group", "Individual"):
        frappe.get_doc(
            {
                "doctype": "Customer Group",
                "customer_group_name": "Individual",
                "parent_customer_group": "All Customer Groups",
                "is_group": 0,
            }
        ).insert(ignore_permissions=True)
    if not frappe.db.exists("Territory", "Germany"):
        frappe.get_doc(
            {
                "doctype": "Territory",
                "territory_name": "Germany",
                "parent_territory": "All Territories",
                "is_group": 0,
            }
        ).insert(ignore_permissions=True)
    if not frappe.db.exists("Currency", "EUR"):
        frappe.get_doc({"doctype": "Currency", "currency_name": "EUR", "enabled": 1}).insert(ignore_permissions=True)

    frappe.reload_doc("erpverein", "workspace", "startseite", force=True)
    import_file_by_path(frappe.get_app_path("erpverein", "workspace_sidebar", "startseite.json"), force=True)
    frappe.clear_cache()
