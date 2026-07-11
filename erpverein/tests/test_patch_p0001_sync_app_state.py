import frappe
from frappe.tests import IntegrationTestCase

from erpverein.custom_fields import get_custom_fields
from erpverein.patches.v0_1 import p0001_sync_app_state


REPORT_NAME = "Ausgetretene Mitglieder mit aktiven Abonnements"


class TestPatchP0001SyncAppState(IntegrationTestCase):
	def test_patch_can_rerun_and_syncs_current_baseline(self):
		p0001_sync_app_state.execute()
		p0001_sync_app_state.execute()

		self.assertEqual(p0001_sync_app_state.CUSTOM_FIELDS, get_custom_fields())
		for doctype, definitions in p0001_sync_app_state.CUSTOM_FIELDS.items():
			meta = frappe.get_meta(doctype, cached=False)
			for definition in definitions:
				fieldname = definition["fieldname"]
				with self.subTest(doctype=doctype, fieldname=fieldname):
					self.assertEqual(frappe.db.count("Custom Field", {"dt": doctype, "fieldname": fieldname}), 1)
					self.assertEqual(meta.get_field(fieldname).fieldtype, definition["fieldtype"])

		for salutation in ("Mr", "Ms"):
			with self.subTest(salutation=salutation):
				self.assertEqual(frappe.db.count("Salutation", {"name": salutation}), 1)
		for doctype, name in (("Customer Group", "Individual"), ("Territory", "Germany"), ("Currency", "EUR")):
			with self.subTest(doctype=doctype, name=name):
				self.assertTrue(frappe.db.exists(doctype, name))

		workspace = frappe.get_doc("Workspace", "Startseite")
		self.assertEqual(workspace.app, "erpverein")
		self.assertEqual(workspace.module, "ERPverein")
		workspace_links = {link.link_to: link for link in workspace.links if link.link_to}
		self.assertEqual(workspace_links[REPORT_NAME].link_type, "Report")

		sidebar = frappe.get_doc("Workspace Sidebar", "Startseite")
		self.assertEqual(sidebar.app, "erpverein")
		self.assertEqual(sidebar.module, "ERPverein")
		sidebar_links = {item.link_to: item for item in sidebar.items}
		self.assertEqual(sidebar_links[REPORT_NAME].link_type, "Report")
