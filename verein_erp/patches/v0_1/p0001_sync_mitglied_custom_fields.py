from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {
    "Customer": [
        {
            "fieldname": "mitglied",
            "label": "Mitglied",
            "fieldtype": "Link",
            "options": "Mitglied",
            "insert_after": "customer_name",
            "description": "1:1 verknuepfter Mitglied-Datensatz aus der Verein ERP App.",
            "no_copy": 1,
            "in_standard_filter": 1,
            "search_index": 1,
            "unique": 1,
        }
    ]
}


def execute() -> None:
    create_custom_fields(CUSTOM_FIELDS, update=True)
