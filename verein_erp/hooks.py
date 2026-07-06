app_name = "verein_erp"
app_title = "ERPverein"
app_publisher = "ERPverein"
app_description = "Durable ERPNext customizations for Vereinsverwaltung"
app_email = "admin@example.com"
app_license = "MIT"

required_apps = ["erpnext"]

after_install = "verein_erp.install.after_install"
before_uninstall = "verein_erp.uninstall.before_uninstall"
before_tests = "verein_erp.tests.before_tests.before_tests"

doc_events = {
    "Customer": {
        "validate": [
            "verein_erp.services.mitglied_service.validate_customer_membership_link",
            "verein_erp.services.mieter_service.validate_customer_rental_link",
        ],
        "on_update": [
            "verein_erp.services.mitglied_service.sync_customer_to_mitglied",
            "verein_erp.services.mieter_service.sync_customer_to_mieter",
        ],
        "on_trash": [
            "verein_erp.services.mitglied_service.clear_customer_link_from_mitglied",
            "verein_erp.services.mieter_service.clear_customer_link_from_mieter",
        ],
    }
}
