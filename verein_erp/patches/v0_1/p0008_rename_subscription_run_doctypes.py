import frappe


DOCTYPE_RENAMES = (
    ("Mitglied Subscription Lauf Vorschau", "Beitragsabrechnung Vorschau"),
    ("Mitglied Subscription Lauf Periode", "Beitragsabrechnung Periode"),
    ("Mitglied Subscription Lauf", "Beitragsabrechnung"),
)


def execute():
    for old_name, new_name in DOCTYPE_RENAMES:
        rename_doctype_if_needed(old_name, new_name)
        rename_table_if_needed(old_name, new_name)

    update_parenttype_references()
    frappe.clear_cache()


def rename_doctype_if_needed(old_name: str, new_name: str) -> None:
    if frappe.db.exists("DocType", old_name) and not frappe.db.exists("DocType", new_name):
        frappe.rename_doc("DocType", old_name, new_name, force=True, merge=False)


def rename_table_if_needed(old_name: str, new_name: str) -> None:
    old_table = f"tab{old_name}"
    new_table = f"tab{new_name}"
    if table_exists(old_table) and not table_exists(new_table):
        frappe.db.sql(f"rename table `{old_table}` to `{new_table}`")


def table_exists(table_name: str) -> bool:
    return frappe.db.sql("show tables like %s", table_name, as_list=True)


def update_parenttype_references() -> None:
    for child_doctype in ("Beitragsabrechnung Vorschau", "Beitragsabrechnung Periode"):
        table = f"tab{child_doctype}"
        if table_exists(table):
            frappe.db.sql(
                f"update `{table}` set parenttype = %s where parenttype = %s",
                ("Beitragsabrechnung", "Mitglied Subscription Lauf"),
            )
