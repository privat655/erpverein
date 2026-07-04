from frappe.custom.doctype.custom_field.custom_field import delete_custom_fields


def execute() -> None:
    delete_custom_fields(
        {
            "Subscription": [
                "verein_erp_managed",
                "verein_erp_payer_mitglied",
                "verein_erp_generation_run",
                "verein_erp_sync_state",
            ]
        }
    )
