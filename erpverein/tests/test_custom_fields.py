import frappe
from frappe.tests import IntegrationTestCase

from erpverein.custom_fields import get_custom_fields, sync_custom_fields


EXPECTED_CUSTOM_FIELDS = {
	"Customer": [
		{
			"fieldname": "erpverein_mitglied",
			"label": "ERPverein Mitglied",
			"fieldtype": "Link",
			"options": "Mitglied",
			"insert_after": "customer_name",
			"description": "1:1 verknuepfter Mitglied-Datensatz aus ERPverein.",
			"no_copy": 1,
			"in_standard_filter": 1,
			"search_index": 1,
			"unique": 1,
		},
		{
			"fieldname": "erpverein_sync_state",
			"label": "ERPverein Sync-Status",
			"fieldtype": "Long Text",
			"insert_after": "erpverein_mitglied",
			"description": "Interner ERPverein-Snapshot fuer kontrollierte Synchronisation.",
			"hidden": 1,
			"no_copy": 1,
			"read_only": 1,
		},
		{
			"fieldname": "erpverein_mieter",
			"label": "ERPverein Mieter",
			"fieldtype": "Link",
			"options": "Mieter",
			"insert_after": "erpverein_sync_state",
			"description": "1:1 verknuepfter Mieter-Datensatz aus ERPverein.",
			"no_copy": 1,
			"in_standard_filter": 1,
			"search_index": 1,
			"unique": 1,
		},
	],
	"Bank Account": [
		{
			"fieldname": "erpverein_managed",
			"label": "ERPverein verwaltet",
			"fieldtype": "Check",
			"insert_after": "party",
			"description": "Markiert ausschliesslich durch ERPverein angelegte Bankkonten.",
			"no_copy": 1,
			"read_only": 1,
			"in_standard_filter": 1,
		},
		{
			"fieldname": "erpverein_sync_state",
			"label": "ERPverein Sync-Status",
			"fieldtype": "Long Text",
			"insert_after": "erpverein_managed",
			"description": "Interner Snapshot fuer kontrollierte Bankkonto-Synchronisation.",
			"hidden": 1,
			"no_copy": 1,
			"read_only": 1,
		},
	],
	"Subscription": [
		{
			"fieldname": "erpverein_managed",
			"label": "ERPverein verwaltet",
			"fieldtype": "Check",
			"insert_after": "party",
			"no_copy": 1,
			"read_only": 1,
			"in_standard_filter": 1,
		},
		{
			"fieldname": "erpverein_billing_kind",
			"label": "ERPverein Abrechnungsart",
			"fieldtype": "Select",
			"options": "\nMitgliedsbeitrag\nMiete",
			"insert_after": "erpverein_managed",
			"no_copy": 1,
			"read_only": 1,
			"in_standard_filter": 1,
		},
		{
			"fieldname": "erpverein_generation_key",
			"label": "ERPverein Generierungsschluessel",
			"fieldtype": "Data",
			"insert_after": "erpverein_billing_kind",
			"hidden": 1,
			"no_copy": 1,
			"read_only": 1,
			"unique": 1,
		},
		{
			"fieldname": "erpverein_generation_payload",
			"label": "ERPverein Generierungsdaten",
			"fieldtype": "Long Text",
			"insert_after": "erpverein_generation_key",
			"hidden": 1,
			"no_copy": 1,
			"read_only": 1,
		},
		{
			"fieldname": "erpverein_generation_run_doctype",
			"label": "ERPverein Lauf-Typ",
			"fieldtype": "Link",
			"options": "DocType",
			"insert_after": "erpverein_generation_payload",
			"hidden": 1,
			"no_copy": 1,
			"read_only": 1,
		},
		{
			"fieldname": "erpverein_generation_run",
			"label": "ERPverein Abrechnungslauf",
			"fieldtype": "Dynamic Link",
			"options": "erpverein_generation_run_doctype",
			"insert_after": "erpverein_generation_run_doctype",
			"no_copy": 1,
			"read_only": 1,
		},
		{
			"fieldname": "erpverein_sources",
			"label": "ERPverein Quellen",
			"fieldtype": "Table",
			"options": "ERPverein Subscription Source",
			"insert_after": "erpverein_generation_run",
			"no_copy": 1,
			"read_only": 1,
		},
	],
}


class TestCustomFields(IntegrationTestCase):
	def test_custom_field_definitions_match_reset_baseline(self):
		self.assertEqual(get_custom_fields(), EXPECTED_CUSTOM_FIELDS)

	def test_every_owned_field_uses_erpverein_prefix(self):
		for doctype, fields in get_custom_fields().items():
			for field in fields:
				with self.subTest(doctype=doctype, fieldname=field["fieldname"]):
					self.assertTrue(field["fieldname"].startswith("erpverein_"))

	def test_sync_can_rerun_and_creates_each_owned_field_once(self):
		sync_custom_fields()
		sync_custom_fields()

		for doctype, fields in EXPECTED_CUSTOM_FIELDS.items():
			meta = frappe.get_meta(doctype, cached=False)
			for definition in fields:
				fieldname = definition["fieldname"]
				with self.subTest(doctype=doctype, fieldname=fieldname):
					self.assertEqual(frappe.db.count("Custom Field", {"dt": doctype, "fieldname": fieldname}), 1)
					field = meta.get_field(fieldname)
					self.assertIsNotNone(field)
					self.assertEqual(field.fieldtype, definition["fieldtype"])
					self.assertEqual(field.options or None, definition.get("options"))
					for property_name in ("read_only", "hidden", "unique", "search_index", "in_standard_filter", "no_copy"):
						if property_name in definition:
							self.assertEqual(field.get(property_name), definition[property_name])

	def test_generic_member_and_tenant_fields_are_not_defined(self):
		defined = {
			(doctype, field["fieldname"])
			for doctype, fields in get_custom_fields().items()
			for field in fields
		}
		for fieldname in ("mitglied", "mieter", "member", "tenant"):
			self.assertNotIn(("Customer", fieldname), defined)
			self.assertFalse(frappe.db.exists("Custom Field", {"dt": "Customer", "fieldname": fieldname}))
