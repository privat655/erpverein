from frappe.tests import UnitTestCase

from verein_erp.services.mitglied_service import get_mitglied_display_name, normalize_email, normalize_text


class TestMitgliedService(UnitTestCase):
    def test_normalize_text_collapses_whitespace(self):
        self.assertEqual(normalize_text("  Erika   Musterfrau  "), "Erika Musterfrau")

    def test_normalize_email_lowercases(self):
        self.assertEqual(normalize_email(" ERIKA@EXAMPLE.ORG "), "erika@example.org")

    def test_get_mitglied_display_name_joins_available_parts(self):
        self.assertEqual(get_mitglied_display_name(" Erika ", " Musterfrau "), "Erika Musterfrau")
