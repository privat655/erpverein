from frappe.custom.doctype.custom_field.custom_field import create_custom_fields, delete_custom_fields


CUSTOMER_MITGLIED_FIELDNAME = "mitglied"
CUSTOMER_SYNC_STATE_FIELDNAME = "verein_erp_sync_state"
BANK_ACCOUNT_MANAGED_FIELDNAME = "verein_erp_managed"
BANK_ACCOUNT_SYNC_STATE_FIELDNAME = "verein_erp_sync_state"
SUBSCRIPTION_MANAGED_FIELDNAME = "verein_erp_managed"
SUBSCRIPTION_PAYER_FIELDNAME = "verein_erp_payer_mitglied"
SUBSCRIPTION_GENERATION_RUN_FIELDNAME = "verein_erp_generation_run"
SUBSCRIPTION_SYNC_STATE_FIELDNAME = "verein_erp_sync_state"


def get_custom_fields() -> dict:
    return {
        "Customer": [
            {
                "fieldname": CUSTOMER_MITGLIED_FIELDNAME,
                "label": "Mitglied",
                "fieldtype": "Link",
                "options": "Mitglied",
                "insert_after": "customer_name",
                "description": "1:1 verknuepfter Mitglied-Datensatz aus der Verein ERP App.",
                "no_copy": 1,
                "in_standard_filter": 1,
                "search_index": 1,
                "unique": 1,
            },
            {
                "fieldname": CUSTOMER_SYNC_STATE_FIELDNAME,
                "label": "Verein ERP Sync State",
                "fieldtype": "Long Text",
                "insert_after": CUSTOMER_MITGLIED_FIELDNAME,
                "description": "Interner Snapshot fuer feldweisen Mitglied-zu-Customer-Sync.",
                "hidden": 1,
                "no_copy": 1,
                "read_only": 1,
            }
        ],
        "Bank Account": [
            {
                "fieldname": BANK_ACCOUNT_MANAGED_FIELDNAME,
                "label": "Verein ERP Managed",
                "fieldtype": "Check",
                "insert_after": "party",
                "description": "Markiert Bankkonten, die durch Verein ERP angelegt oder kontrolliert synchronisiert werden.",
                "no_copy": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": BANK_ACCOUNT_SYNC_STATE_FIELDNAME,
                "label": "Verein ERP Sync State",
                "fieldtype": "Long Text",
                "insert_after": BANK_ACCOUNT_MANAGED_FIELDNAME,
                "description": "Interner Snapshot fuer feldweisen SEPA-Mandat-zu-Bank-Account-Sync.",
                "hidden": 1,
                "no_copy": 1,
                "read_only": 1,
            },
        ],
        "Subscription": [
            {
                "fieldname": SUBSCRIPTION_MANAGED_FIELDNAME,
                "label": "Verein ERP Managed",
                "fieldtype": "Check",
                "insert_after": "status",
                "description": "Markiert Subscriptions, die durch Verein ERP angelegt oder kontrolliert synchronisiert werden.",
                "no_copy": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": SUBSCRIPTION_PAYER_FIELDNAME,
                "label": "Verein ERP Beitragszahler",
                "fieldtype": "Link",
                "options": "Mitglied",
                "insert_after": SUBSCRIPTION_MANAGED_FIELDNAME,
                "description": "Mitglied, dessen Customer diese Subscription traegt.",
                "no_copy": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": SUBSCRIPTION_GENERATION_RUN_FIELDNAME,
                "label": "Verein ERP Subscription Lauf",
                "fieldtype": "Link",
                "options": "Mitglied Subscription Lauf",
                "insert_after": SUBSCRIPTION_PAYER_FIELDNAME,
                "description": "Erzeugungslauf, der diese Subscription angelegt hat.",
                "no_copy": 1,
                "read_only": 1,
            },
            {
                "fieldname": SUBSCRIPTION_SYNC_STATE_FIELDNAME,
                "label": "Verein ERP Sync State",
                "fieldtype": "Long Text",
                "insert_after": SUBSCRIPTION_GENERATION_RUN_FIELDNAME,
                "description": "Interner Snapshot fuer feldweisen Mitglied-zu-Subscription-Sync.",
                "hidden": 1,
                "no_copy": 1,
                "read_only": 1,
            },
        ],
    }


def sync_custom_fields() -> None:
    create_custom_fields(get_custom_fields(), update=True)


def remove_custom_fields() -> None:
    delete_custom_fields(
        {
            "Customer": [CUSTOMER_MITGLIED_FIELDNAME, CUSTOMER_SYNC_STATE_FIELDNAME],
            "Bank Account": [BANK_ACCOUNT_MANAGED_FIELDNAME, BANK_ACCOUNT_SYNC_STATE_FIELDNAME],
            "Subscription": [
                SUBSCRIPTION_MANAGED_FIELDNAME,
                SUBSCRIPTION_PAYER_FIELDNAME,
                SUBSCRIPTION_GENERATION_RUN_FIELDNAME,
                SUBSCRIPTION_SYNC_STATE_FIELDNAME,
            ],
        }
    )
