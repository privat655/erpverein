from unittest.mock import patch

import frappe
from frappe.tests import UnitTestCase

from erpverein.services.sepa_export_profile_service import (
    get_accounts_settings_warnings,
    set_profile_version,
    validate_company_bank_account,
    validate_single_active_profile,
)


class ProfileDoc(frappe._dict):
    def get_doc_before_save(self):
        return self.get("_previous")


class TestSEPAExportProfile(UnitTestCase):
    @patch("erpverein.services.sepa_export_profile_service.frappe.db.get_value")
    def test_bank_account_must_be_active_company_account_of_profile_company(self, get_value):
        doc = ProfileDoc(company="Verein", bank_account="Vereinskonto")
        valid = frappe._dict(is_company_account=1, company="Verein", account="18000 - V", disabled=0)

        get_value.return_value = valid
        validate_company_bank_account(doc)

        for values in (
            frappe._dict(valid, is_company_account=0),
            frappe._dict(valid, company="Andere"),
            frappe._dict(valid, account=None),
            frappe._dict(valid, disabled=1),
            None,
        ):
            get_value.return_value = values
            with self.subTest(values=values), self.assertRaises(frappe.ValidationError):
                validate_company_bank_account(doc)

    @patch("erpverein.services.sepa_export_profile_service.frappe.db.get_value", return_value="Anderes Profil")
    def test_only_one_active_profile_per_company(self, _get_value):
        with self.assertRaises(frappe.ValidationError):
            validate_single_active_profile(ProfileDoc(name="Neu", company="Verein", active=1))

        validate_single_active_profile(ProfileDoc(name="Neu", company="Verein", active=0))

    def test_profile_version_increments_only_on_relevant_change(self):
        previous = ProfileDoc(profile_version=3, prenotification_days=14, notes="alt")
        unchanged = ProfileDoc(previous, notes="neu", _previous=previous)
        changed = ProfileDoc(previous, prenotification_days=10, _previous=previous)
        new = ProfileDoc(profile_version=None)

        set_profile_version(unchanged)
        set_profile_version(changed)
        set_profile_version(new)

        self.assertEqual(unchanged.profile_version, 3)
        self.assertEqual(changed.profile_version, 4)
        self.assertEqual(new.profile_version, 1)

    @patch("erpverein.services.sepa_export_profile_service.frappe.db.get_single_value")
    def test_accounts_settings_warnings_name_each_deviation(self, get_single_value):
        recommended = {
            "enable_party_matching": 1,
            "enable_fuzzy_matching": 0,
            "automatically_run_rules_on_unreconciled_transactions": 1,
            "auto_reconcile_payments": 0,
        }
        get_single_value.side_effect = lambda doctype, fieldname: recommended[fieldname]
        self.assertEqual(get_accounts_settings_warnings(), [])

        get_single_value.side_effect = lambda doctype, fieldname: {**recommended, "auto_reconcile_payments": 1}[fieldname]
        warnings = get_accounts_settings_warnings()
        self.assertEqual(len(warnings), 1)
        self.assertIn("auto_reconcile_payments", warnings[0])
