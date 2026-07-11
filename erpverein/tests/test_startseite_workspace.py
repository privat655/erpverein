import json
from pathlib import Path

from frappe.tests import UnitTestCase


REPORT_NAME = "Ausgetretene Mitglieder mit aktiven Abonnements"


class TestStartseiteWorkspace(UnitTestCase):
	def test_workspace_json_defines_current_startseite_for_app(self):
		workspace_path = (
			Path(__file__).resolve().parents[1]
			/ "erpverein"
			/ "workspace"
			/ "startseite"
			/ "startseite.json"
		)
		workspace = json.loads(workspace_path.read_text())

		self.assertEqual(workspace["name"], "Startseite")
		self.assertEqual(workspace["title"], "Startseite")
		self.assertEqual(workspace["label"], "Startseite")
		self.assertEqual(workspace["app"], "erpverein")
		self.assertEqual(workspace["module"], "ERPverein")
		links = {link["link_to"]: link for link in workspace["links"] if link.get("link_to")}
		self.assertEqual(links["Mitglied"]["label"], "Mitglieder")
		self.assertEqual(links["Mieter"]["label"], "Mieter")
		self.assertEqual(links["SEPA Mandat"]["label"], "SEPA-Mandate")
		self.assertEqual(links["Beitragsabrechnung"]["label"], "Beitragsabrechnungen")
		self.assertEqual(links["Mietabrechnung"]["label"], "Mietabrechnungen")
		self.assertEqual(links[REPORT_NAME]["link_type"], "Report")
		self.assertEqual(links[REPORT_NAME]["is_query_report"], 1)

	def test_workspace_sidebar_json_defines_current_startseite_for_app(self):
		sidebar_path = Path(__file__).resolve().parents[1] / "workspace_sidebar" / "startseite.json"
		sidebar = json.loads(sidebar_path.read_text())

		self.assertEqual(sidebar["name"], "Startseite")
		self.assertEqual(sidebar["title"], "Startseite")
		self.assertEqual(sidebar["app"], "erpverein")
		self.assertEqual(sidebar["module"], "ERPverein")
		self.assertEqual(sidebar["items"][0]["link_to"], "Startseite")
		self.assertEqual(sidebar["items"][0]["link_type"], "Workspace")
		links = {item["link_to"]: item for item in sidebar["items"]}
		self.assertEqual(links["Mitglied"]["label"], "Mitglieder")
		self.assertEqual(links["Mieter"]["label"], "Mieter")
		self.assertEqual(links["SEPA Mandat"]["label"], "SEPA-Mandate")
		self.assertEqual(links["Beitragsabrechnung"]["label"], "Beitragsabrechnungen")
		self.assertEqual(links["Mietabrechnung"]["label"], "Mietabrechnungen")
		self.assertEqual(links[REPORT_NAME]["link_type"], "Report")
