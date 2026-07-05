import json
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.patches.v0_1 import p0007_sync_startseite_workspace


class TestStartseiteWorkspace(IntegrationTestCase):
    def test_workspace_json_defines_startseite_for_app(self):
        workspace_path = (
            Path(__file__).resolve().parents[1]
            / "verein_erp"
            / "workspace"
            / "startseite"
            / "startseite.json"
        )
        workspace = json.loads(workspace_path.read_text())

        self.assertEqual(workspace["name"], "Startseite")
        self.assertEqual(workspace["title"], "Startseite")
        self.assertEqual(workspace["label"], "Startseite")
        self.assertEqual(workspace["app"], "verein_erp")
        self.assertEqual(workspace["module"], "verein_erp")
        links = {link["link_to"]: link["label"] for link in workspace["links"] if link.get("link_to")}
        self.assertEqual(links["Mitglied"], "Mitglieder")
        self.assertEqual(links["SEPA Mandat"], "SEPA-Mandate")
        self.assertEqual(links["Beitragsabrechnung"], "Beitragsabrechnungen")

    def test_workspace_sidebar_json_defines_startseite_header(self):
        sidebar_path = Path(__file__).resolve().parents[1] / "workspace_sidebar" / "startseite.json"
        sidebar = json.loads(sidebar_path.read_text())

        self.assertEqual(sidebar["name"], "Startseite")
        self.assertEqual(sidebar["title"], "Startseite")
        self.assertEqual(sidebar["app"], "verein_erp")
        self.assertEqual(sidebar["module"], "verein_erp")
        self.assertEqual(sidebar["items"][0]["link_to"], "Startseite")
        self.assertEqual(sidebar["items"][0]["link_type"], "Workspace")
        links = {item["link_to"]: item["label"] for item in sidebar["items"]}
        self.assertEqual(links["Mitglied"], "Mitglieder")
        self.assertEqual(links["SEPA Mandat"], "SEPA-Mandate")
        self.assertEqual(links["Beitragsabrechnung"], "Beitragsabrechnungen")

    def test_patch_syncs_startseite_and_removes_legacy_workspace(self):
        frappe.delete_doc_if_exists("Workspace", "verein_erp", force=True)
        frappe.get_doc(
            {
                "doctype": "Workspace",
                "label": "verein_erp",
                "title": "verein_erp",
                "module": "verein_erp",
                "app": "verein_erp",
                "public": 1,
                "content": "[]",
                "type": "Workspace",
            }
        ).insert(ignore_permissions=True)

        p0007_sync_startseite_workspace.execute()
        p0007_sync_startseite_workspace.execute()

        workspace = frappe.get_doc("Workspace", "Startseite")
        self.assertEqual(workspace.title, "Startseite")
        self.assertEqual(workspace.app, "verein_erp")
        sidebar = frappe.get_doc("Workspace Sidebar", "Startseite")
        self.assertEqual(sidebar.title, "Startseite")
        self.assertEqual(sidebar.app, "verein_erp")
        self.assertFalse(frappe.db.exists("Workspace", "verein_erp"))
