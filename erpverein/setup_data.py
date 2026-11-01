import frappe


REQUIRED_SALUTATIONS = ("Mr", "Ms")
MODE_OF_PAYMENT_DIRECT_DEBIT = "SEPA-Lastschrift"
MODE_OF_PAYMENT_TRANSFER = "Überweisung"
PAYMENT_TERM_COUNT = 60


def payment_term_name(index: int) -> str:
    return f"ERPverein Rate {index:02d}"


def sync_setup_data() -> None:
    sync_salutations()
    sync_customer_masters()
    sync_payment_masters()


def sync_salutations() -> None:
    for salutation in REQUIRED_SALUTATIONS:
        if not frappe.db.exists("Salutation", salutation):
            frappe.get_doc({"doctype": "Salutation", "salutation": salutation}).insert(ignore_permissions=True)


def sync_customer_masters() -> None:
    if frappe.db.exists("Customer Group", "All Customer Groups") and not frappe.db.exists(
        "Customer Group", "Individual"
    ):
        frappe.get_doc(
            {
                "doctype": "Customer Group",
                "customer_group_name": "Individual",
                "parent_customer_group": "All Customer Groups",
                "is_group": 0,
            }
        ).insert(ignore_permissions=True)
    if frappe.db.exists("Territory", "All Territories") and not frappe.db.exists("Territory", "Germany"):
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


def sync_payment_masters() -> None:
    for mode_of_payment in (MODE_OF_PAYMENT_DIRECT_DEBIT, MODE_OF_PAYMENT_TRANSFER):
        if not frappe.db.exists("Mode of Payment", mode_of_payment):
            frappe.get_doc(
                {"doctype": "Mode of Payment", "mode_of_payment": mode_of_payment, "type": "Bank", "enabled": 1}
            ).insert(ignore_permissions=True)
    for index in range(1, PAYMENT_TERM_COUNT + 1):
        name = payment_term_name(index)
        if not frappe.db.exists("Payment Term", name):
            frappe.get_doc(
                {
                    "doctype": "Payment Term",
                    "payment_term_name": name,
                    "description": "ERPverein-Zahlungsplanzeile",
                    "due_date_based_on": "Day(s) after invoice date",
                    "credit_days": 0,
                }
            ).insert(ignore_permissions=True)
