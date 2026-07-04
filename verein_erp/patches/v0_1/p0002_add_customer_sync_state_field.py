from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {
    "Customer": [
        {
            "fieldname": "verein_erp_sync_state",
            "label": "Verein ERP Sync State",
            "fieldtype": "Long Text",
            "insert_after": "mitglied",
            "description": "Interner Snapshot fuer feldweisen Mitglied-zu-Customer-Sync.",
            "hidden": 1,
            "no_copy": 1,
            "read_only": 1,
        }
    ]
}


def execute() -> None:
    create_custom_fields(CUSTOM_FIELDS, update=True)
