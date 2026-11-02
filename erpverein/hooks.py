app_name = "erpverein"
app_title = "ERPverein"
app_publisher = "ERPverein"
app_description = "Durable ERPNext customizations for Vereinsverwaltung"
app_email = "admin@example.com"
app_license = "MIT"

required_apps = ["erpnext"]

after_install = "erpverein.install.after_install"
after_migrate = "erpverein.install.after_migrate"
before_uninstall = "erpverein.uninstall.before_uninstall"
before_tests = "erpverein.tests.before_tests.before_tests"

doctype_js = {"Sales Invoice": "public/js/sales_invoice.js"}
doctype_list_js = {"Sales Invoice": "public/js/sales_invoice_list.js"}

jinja = {
    "methods": [
        "erpverein.services.prenotification_service.erpverein_payment_schedule_rows",
        "erpverein.services.prenotification_service.erpverein_payment_details",
    ]
}

has_permission = {
    "SEPA Einzugslauf": "erpverein.services.sepa_collection_service.collection_has_permission",
    "SEPA Einzugsposition": "erpverein.services.sepa_collection_service.collection_has_permission",
}

permission_query_conditions = {
    "SEPA Einzugslauf": "erpverein.services.sepa_collection_service.collection_query_conditions",
    "SEPA Einzugsposition": "erpverein.services.sepa_collection_service.collection_query_conditions",
}

doc_events = {
    "Bank Account": {
        "validate": [
            "erpverein.services.sepa_mandat_service.validate_bank_account_provenance",
            "erpverein.services.bank_import_service.validate_bank_import_alias",
        ],
    },
    "Customer": {
        "validate": [
            "erpverein.services.mitglied_service.validate_customer_membership_link",
            "erpverein.services.mieter_service.validate_customer_rental_link",
        ],
        "on_update": [
            "erpverein.services.mitglied_service.sync_customer_to_mitglied",
            "erpverein.services.mieter_service.sync_customer_to_mieter",
        ],
        "on_trash": [
            "erpverein.services.mitglied_service.clear_customer_link_from_mitglied",
            "erpverein.services.mieter_service.clear_customer_link_from_mieter",
        ],
    },
    "Subscription": {
        "validate": "erpverein.services.billing_common.validate_subscription_provenance",
    },
    "Sales Invoice": {
        "validate": "erpverein.services.payment_schedule_service.apply_payment_schedule",
        "before_cancel": "erpverein.services.sepa_collection_service.prevent_invoice_cancel_with_active_positions",
    },
    "Payment Entry": {
        "validate": "erpverein.services.sepa_settlement_service.validate_payment_entry_provenance",
        "on_cancel": "erpverein.services.sepa_settlement_service.handle_cancelled_collection_payment",
    },
}

scheduler_events = {
    "daily": ["erpverein.services.sepa_collection_schedule_service.refresh_collection_schedule_projections"],
    "hourly": [
        "erpverein.services.billing_job_service.reconcile_stale_billing_jobs",
        "erpverein.services.prenotification_service.refresh_prenotification_status",
    ],
}
