import frappe
from frappe.tests import UnitTestCase

from erpverein.services.billing_common import (
    BILLING_KIND_MEMBERSHIP,
    canonical_json,
    generation_key,
    parse_selection_json,
    periods_overlap,
)


class TestBillingCommon(UnitTestCase):
    def test_generation_key_normalizes_dates_and_is_deterministic(self):
        first = generation_key(BILLING_KIND_MEMBERSHIP, "MIT-0001", "2026-01-01", "2026-12-31")
        second = generation_key(BILLING_KIND_MEMBERSHIP, "MIT-0001", "2026-01-01", "2026-12-31")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_canonical_json_sorts_nested_keys(self):
        self.assertEqual(canonical_json({"b": 2, "a": {"d": 4, "c": 3}}), '{"a":{"c":3,"d":4},"b":2}')

    def test_open_period_overlaps_future_finite_period(self):
        self.assertTrue(periods_overlap("2026-01-01", None, "2030-01-01", "2030-12-31"))
        self.assertFalse(periods_overlap("2026-01-01", "2026-12-31", "2027-01-01", None))

    def test_selection_json_requires_a_list_of_strings(self):
        with self.assertRaises(frappe.ValidationError):
            parse_selection_json('{"name":"MIT-0001"}', "Auswahl")
        with self.assertRaises(frappe.ValidationError):
            parse_selection_json('["MIT-0001", 2]', "Auswahl")

        self.assertEqual(parse_selection_json('["MIT-0002", "MIT-0001", "MIT-0001"]', "Auswahl"), ["MIT-0001", "MIT-0002"])
