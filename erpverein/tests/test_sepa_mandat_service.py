from unittest.mock import patch

from frappe.tests import UnitTestCase

from erpverein.services.sepa_mandat_service import (
    lock_mandate_references,
    mask_iban,
    normalize_bic,
    normalize_iban,
    validate_iban,
)


class TestSEPAMandatService(UnitTestCase):
    def test_normalize_iban_removes_spaces_and_uppercases(self):
        self.assertEqual(normalize_iban(" de89 3704 0044 0532 0130 00 "), "DE89370400440532013000")

    def test_validate_iban_accepts_valid_iban(self):
        self.assertTrue(validate_iban("DE89370400440532013000"))

    def test_validate_iban_rejects_invalid_check_digits(self):
        self.assertFalse(validate_iban("DE00370400440532013000"))

    def test_normalize_bic_removes_spaces_and_uppercases(self):
        self.assertEqual(normalize_bic(" coba de ff xxx "), "COBADEFFXXX")

    def test_mask_iban_never_returns_full_value(self):
        iban = "DE89370400440532013000"
        masked = mask_iban(iban)

        self.assertNotEqual(masked, iban)
        self.assertTrue(masked.startswith("DE"))
        self.assertTrue(masked.endswith("3000"))

    def test_reference_locks_use_deterministic_order(self):
        class Mandate:
            bezugs_doctype = "Mieter"
            bezugs_name = "MIE-2"

            @staticmethod
            def get_value_before_save(fieldname):
                return {"bezugs_doctype": "Mitglied", "bezugs_name": "MIT-1"}[fieldname]

        with patch("erpverein.services.sepa_mandat_service.frappe.db.get_value") as get_value:
            lock_mandate_references(Mandate())

        self.assertEqual(
            [call.args[:2] for call in get_value.call_args_list],
            [("Mieter", "MIE-2"), ("Mitglied", "MIT-1")],
        )
        self.assertTrue(all(call.kwargs["for_update"] for call in get_value.call_args_list))
