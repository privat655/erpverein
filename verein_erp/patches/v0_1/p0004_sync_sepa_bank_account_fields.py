from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {
    "Bank Account": [
        {
            "fieldname": "verein_erp_managed",
            "label": "Verein ERP Managed",
            "fieldtype": "Check",
            "insert_after": "party",
            "description": "Markiert Bankkonten, die durch Verein ERP angelegt oder kontrolliert synchronisiert werden.",
            "no_copy": 1,
            "in_standard_filter": 1,
        },
        {
            "fieldname": "verein_erp_sync_state",
            "label": "Verein ERP Sync State",
            "fieldtype": "Long Text",
            "insert_after": "verein_erp_managed",
            "description": "Interner Snapshot fuer feldweisen SEPA-Mandat-zu-Bank-Account-Sync.",
            "hidden": 1,
            "no_copy": 1,
            "read_only": 1,
        },
    ]
}


def execute() -> None:
    create_custom_fields(CUSTOM_FIELDS, update=True)
