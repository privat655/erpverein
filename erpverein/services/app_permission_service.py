import frappe

# The app's own non-table DocTypes. Whoever may read one of them has work in ERPverein, so the
# desk's apps screen and app switcher offer them the app.
APP_DOCTYPES = (
    "Beitragsabrechnung",
    "ERPverein Bankimport",
    "Mietabrechnung",
    "Mieter",
    "Mitglied",
    "SEPA Einzugslauf",
    "SEPA Einzugsposition",
    "SEPA Exportprofil",
    "SEPA Mandat",
)


def has_app_permission() -> bool:
    # Called by Frappe for the `add_to_apps_screen` hook without arguments, for the session user.
    return any(frappe.has_permission(doctype, "read") for doctype in APP_DOCTYPES)
