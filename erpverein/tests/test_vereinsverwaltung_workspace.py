import json
from pathlib import Path

from frappe.tests import UnitTestCase


REPORT_NAME = "Ausgetretene Mitglieder mit aktiven Abonnements"
WORK_QUEUE_REPORT = "ERPverein Arbeitsvorrat"


class TestVereinsverwaltungWorkspace(UnitTestCase):
	def test_workspace_json_defines_vereinsverwaltung_for_app(self):
		workspace_path = (
			Path(__file__).resolve().parents[1]
			/ "erpverein"
			/ "workspace"
			/ "vereinsverwaltung"
			/ "vereinsverwaltung.json"
		)
		workspace = json.loads(workspace_path.read_text(encoding="utf-8"))

		self.assertEqual(workspace["name"], "Vereinsverwaltung")
		self.assertEqual(workspace["title"], "Vereinsverwaltung")
		self.assertEqual(workspace["label"], "Vereinsverwaltung")
		self.assertEqual(workspace["app"], "erpverein")
		self.assertEqual(workspace["module"], "ERPverein")
		links = {link["link_to"]: link for link in workspace["links"] if link.get("link_to")}
		self.assertEqual(links["Mitglied"]["label"], "Mitglieder")
		self.assertEqual(links["Mieter"]["label"], "Mieter")
		self.assertEqual(links["SEPA Mandat"]["label"], "SEPA-Mandate")
		self.assertEqual(links["SEPA Exportprofil"]["label"], "SEPA-Exportprofile")
		self.assertEqual(links["SEPA Einzugslauf"]["label"], "SEPA-Einzugsläufe")
		self.assertEqual(links["SEPA Einzugsposition"]["label"], "SEPA-Einzugspositionen")
		self.assertEqual(links["ERPverein Bankimport"]["label"], "Bankimporte")
		self.assertEqual(links["Bank Transaction"]["label"], "Bankumsätze")
		self.assertEqual(links["Beitragsabrechnung"]["label"], "Beitragsabrechnungen")
		self.assertEqual(links["Mietabrechnung"]["label"], "Mietabrechnungen")
		self.assertEqual(links[REPORT_NAME]["link_type"], "Report")
		self.assertEqual(links[REPORT_NAME]["is_query_report"], 1)
		self.assertEqual(links[WORK_QUEUE_REPORT]["label"], "Arbeitsvorrat")
		self.assertEqual(links[WORK_QUEUE_REPORT]["link_type"], "Report")
		self.assertEqual(links[WORK_QUEUE_REPORT]["is_query_report"], 1)
		card = next(link for link in workspace["links"] if link["type"] == "Card Break")
		self.assertEqual(card["link_count"], len([link for link in workspace["links"] if link["type"] == "Link"]))

	def test_workspace_sidebar_json_defines_vereinsverwaltung_for_app(self):
		sidebar_path = Path(__file__).resolve().parents[1] / "workspace_sidebar" / "vereinsverwaltung.json"
		sidebar = json.loads(sidebar_path.read_text(encoding="utf-8"))

		self.assertEqual(sidebar["name"], "Vereinsverwaltung")
		self.assertEqual(sidebar["title"], "Vereinsverwaltung")
		self.assertEqual(sidebar["app"], "erpverein")
		self.assertEqual(sidebar["module"], "ERPverein")
		self.assertEqual(sidebar["items"][0]["label"], "Vereinsverwaltung")
		self.assertEqual(sidebar["items"][0]["link_to"], "Vereinsverwaltung")
		self.assertEqual(sidebar["items"][0]["link_type"], "Workspace")
		links = {item["link_to"]: item for item in sidebar["items"]}
		self.assertEqual(links["Mitglied"]["label"], "Mitglieder")
		self.assertEqual(links["Mieter"]["label"], "Mieter")
		self.assertEqual(links["SEPA Mandat"]["label"], "SEPA-Mandate")
		self.assertEqual(links["SEPA Exportprofil"]["label"], "SEPA-Exportprofile")
		self.assertEqual(links["SEPA Einzugslauf"]["label"], "SEPA-Einzugsläufe")
		self.assertEqual(links["SEPA Einzugsposition"]["label"], "SEPA-Einzugspositionen")
		self.assertEqual(links["ERPverein Bankimport"]["label"], "Bankimporte")
		self.assertEqual(links["Bank Transaction"]["label"], "Bankumsätze")
		self.assertEqual(links["Beitragsabrechnung"]["label"], "Beitragsabrechnungen")
		self.assertEqual(links["Mietabrechnung"]["label"], "Mietabrechnungen")
		self.assertEqual(links[REPORT_NAME]["link_type"], "Report")
		self.assertEqual(links[WORK_QUEUE_REPORT]["label"], "Arbeitsvorrat")
		self.assertEqual(links[WORK_QUEUE_REPORT]["link_type"], "Report")
