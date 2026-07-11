app_name = "erpverein"
app_title = "ERPverein"
app_publisher = "ERPverein"
app_description = "Durable ERPNext customizations for Vereinsverwaltung"
app_email = "admin@example.com"
app_license = "MIT"

required_apps = ["erpnext"]

after_install = "erpverein.install.after_install"
before_uninstall = "erpverein.uninstall.before_uninstall"
before_tests = "erpverein.tests.before_tests.before_tests"

doc_events = {
    "Bank Account": {
        "validate": "erpverein.services.sepa_mandat_service.validate_bank_account_provenance",
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
}

scheduler_events = {
    "hourly": ["erpverein.services.billing_job_service.reconcile_stale_billing_jobs"],
}
