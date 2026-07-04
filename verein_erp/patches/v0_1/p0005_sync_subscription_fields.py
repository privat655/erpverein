from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


CUSTOM_FIELDS = {
    "Subscription": [
        {
            "fieldname": "verein_erp_managed",
            "label": "Verein ERP Managed",
            "fieldtype": "Check",
            "insert_after": "status",
            "description": "Markiert Subscriptions, die durch Verein ERP angelegt oder kontrolliert synchronisiert werden.",
            "no_copy": 1,
            "in_standard_filter": 1,
        },
        {
            "fieldname": "verein_erp_payer_mitglied",
            "label": "Verein ERP Beitragszahler",
            "fieldtype": "Link",
            "options": "Mitglied",
            "insert_after": "verein_erp_managed",
            "description": "Mitglied, dessen Customer diese Subscription traegt.",
            "no_copy": 1,
            "in_standard_filter": 1,
        },
        {
            "fieldname": "verein_erp_generation_run",
            "label": "Verein ERP Subscription Lauf",
            "fieldtype": "Link",
            "options": "Mitglied Subscription Lauf",
            "insert_after": "verein_erp_payer_mitglied",
            "description": "Erzeugungslauf, der diese Subscription angelegt hat.",
            "no_copy": 1,
            "read_only": 1,
        },
        {
            "fieldname": "verein_erp_sync_state",
            "label": "Verein ERP Sync State",
            "fieldtype": "Long Text",
            "insert_after": "verein_erp_generation_run",
            "description": "Interner Snapshot fuer feldweisen Mitglied-zu-Subscription-Sync.",
            "hidden": 1,
            "no_copy": 1,
            "read_only": 1,
        },
    ]
}


def execute() -> None:
    create_custom_fields(CUSTOM_FIELDS, update=True)
