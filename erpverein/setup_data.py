import frappe


REQUIRED_SALUTATIONS = ("Mr", "Ms")


def sync_setup_data() -> None:
    sync_salutations()
    sync_customer_masters()


def sync_salutations() -> None:
    for salutation in REQUIRED_SALUTATIONS:
        if not frappe.db.exists("Salutation", salutation):
            frappe.get_doc({"doctype": "Salutation", "salutation": salutation}).insert(ignore_permissions=True)


def sync_customer_masters() -> None:
    if not frappe.db.exists("Customer Group", "Individual"):
        frappe.get_doc(
            {
                "doctype": "Customer Group",
                "customer_group_name": "Individual",
                "parent_customer_group": "All Customer Groups",
                "is_group": 0,
            }
        ).insert(ignore_permissions=True)
    if not frappe.db.exists("Territory", "Germany"):
        frappe.get_doc(
            {
                "doctype": "Territory",
                "territory_name": "Germany",
                "parent_territory": "All Territories",
                "is_group": 0,
            }
        ).insert(ignore_permissions=True)
    if not frappe.db.exists("Currency", "EUR"):
        frappe.get_doc({"doctype": "Currency", "currency_name": "EUR", "enabled": 1}).insert(ignore_permissions=True)
