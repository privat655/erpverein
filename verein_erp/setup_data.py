import frappe


REQUIRED_SALUTATIONS = ("Mr", "Ms")


def sync_setup_data() -> None:
    sync_salutations()


def sync_salutations() -> None:
    for salutation in REQUIRED_SALUTATIONS:
        if not frappe.db.exists("Salutation", salutation):
            frappe.get_doc({"doctype": "Salutation", "salutation": salutation}).insert(ignore_permissions=True)
