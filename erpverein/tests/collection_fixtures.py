import frappe
from frappe.utils import getdate, nowdate

from erpverein.tests.test_sepa_mandat_doctype import make_bank, make_german_iban


TEST_COMPANY = "_Test Company 2"
SYNTHETIC_CREDITOR_ID = "DE98ZZZ09999999999"
NEXT_YEAR = getdate(nowdate()).year + 1
BANK_ACCOUNT_NAME = "ERPverein Vereinskonto Test"
FEE_ACCOUNT_NAME = "ERPverein Nebenkosten des Geldverkehrs Test"


def ensure_export_profile(company: str = TEST_COMPANY, **overrides):
    values = {
        "active": 1,
        "company": company,
        "bank_account": make_company_bank_account(company),
        "creditor_identifier": SYNTHETIC_CREDITOR_ID,
        "creditor_name": "Testverein e.V.",
        "scheme": "CORE",
        "export_format": "pain.008.001.08",
        "currency": "EUR",
        "go_live_date": f"{NEXT_YEAR}-01-01",
        "prenotification_days": 14,
        "rent_prenotification_required": 0,
        "lead_time_business_days": 1,
        "earliest_submission_business_days": 15,
        "batch_booking_default": 1,
        "settlement_tolerance_days": 5,
        "max_positions_per_file": 1000,
        "default_purpose_template": "Rechnung {invoice}",
    }
    values.update(overrides)
    name = frappe.db.get_value("SEPA Exportprofil", {"company": company, "active": 1}, "name")
    if name:
        profile = frappe.get_doc("SEPA Exportprofil", name)
    else:
        profile = frappe.get_doc({"doctype": "SEPA Exportprofil", "profile_name": f"ERPverein Test {company}"})
    profile.update(values)
    profile.save(ignore_permissions=True)
    return profile


def make_company_bank_account(company: str = TEST_COMPANY) -> str:
    existing = frappe.db.get_value(
        "Bank Account", {"company": company, "is_company_account": 1, "account_name": BANK_ACCOUNT_NAME}, "name"
    )
    if existing:
        return existing
    account = frappe.db.get_value("Account", {"company": company, "account_name": BANK_ACCOUNT_NAME, "is_group": 0}, "name")
    if not account:
        parent = frappe.db.get_value("Account", {"company": company, "is_group": 1, "account_type": "Bank"}, "name")
        parent = parent or frappe.db.get_value("Account", {"company": company, "is_group": 1, "root_type": "Asset"}, "name")
        account = (
            frappe.get_doc(
                {
                    "doctype": "Account",
                    "account_name": BANK_ACCOUNT_NAME,
                    "parent_account": parent,
                    "company": company,
                    "account_type": "Bank",
                    "account_currency": frappe.db.get_value("Company", company, "default_currency"),
                    "is_group": 0,
                }
            )
            .insert(ignore_permissions=True)
            .name
        )
    return (
        frappe.get_doc(
            {
                "doctype": "Bank Account",
                "account_name": BANK_ACCOUNT_NAME,
                "bank": make_bank().name,
                "is_company_account": 1,
                "company": company,
                "account": account,
                "iban": make_german_iban(),
                "branch_code": "COBADEFFXXX",
            }
        )
        .insert(ignore_permissions=True)
        .name
    )


def make_fee_account(company: str = TEST_COMPANY) -> str:
    """Expense account for return fees, like 68550 "Nebenkosten des Geldverkehrs" on the site."""
    account = frappe.db.get_value(
        "Account", {"company": company, "account_name": FEE_ACCOUNT_NAME, "is_group": 0}, "name"
    )
    if account:
        return account
    parent = frappe.db.get_value("Account", {"company": company, "is_group": 1, "root_type": "Expense"}, "name")
    return (
        frappe.get_doc(
            {
                "doctype": "Account",
                "account_name": FEE_ACCOUNT_NAME,
                "parent_account": parent,
                "company": company,
                "account_currency": frappe.db.get_value("Company", company, "default_currency"),
                "is_group": 0,
            }
        )
        .insert(ignore_permissions=True)
        .name
    )
