from frappe.tests import UnitTestCase

from verein_erp.services.sepa_mandat_service import normalize_iban, validate_iban


class TestSEPAMandatService(UnitTestCase):
    def test_normalize_iban_removes_spaces_and_uppercases(self):
        self.assertEqual(normalize_iban(" de89 3704 0044 0532 0130 00 "), "DE89370400440532013000")

    def test_validate_iban_accepts_valid_iban(self):
        self.assertTrue(validate_iban("DE89370400440532013000"))

    def test_validate_iban_rejects_invalid_check_digits(self):
        self.assertFalse(validate_iban("DE00370400440532013000"))
