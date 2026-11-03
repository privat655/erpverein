import json
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase, UnitTestCase

from erpverein import hooks
from erpverein.services.app_permission_service import APP_DOCTYPES, has_app_permission
from erpverein.tests.test_payment_schedule_service import make_user_with_roles

APP_ROOT = Path(__file__).resolve().parents[1]


class TestAppsScreenHook(UnitTestCase):
    def test_hook_puts_erpverein_on_the_apps_screen(self):
        # Frappe's apps screen and app switcher list only apps that declare `add_to_apps_screen`.
        (entry,) = hooks.add_to_apps_screen
        workspace_path = APP_ROOT / "erpverein" / "workspace" / "vereinsverwaltung" / "vereinsverwaltung.json"
        workspace = json.loads(workspace_path.read_text(encoding="utf-8"))

        self.assertEqual(entry["name"], "erpverein")
        self.assertEqual(entry["title"], "ERPverein")
        # The legacy desktop-icon seeding reads `route` and `logo` by key, so both must be present.
        self.assertEqual(entry["route"], f"/desk/{workspace['name'].lower()}")
        self.assertEqual(workspace["public"], 1)
        self.assertTrue(entry["logo"].startswith("/assets/erpverein/"))
        self.assertTrue((APP_ROOT / "public" / entry["logo"].removeprefix("/assets/erpverein/")).is_file())
        self.assertIs(frappe.get_attr(entry["has_permission"]), has_app_permission)
        # A third `app_logo_url` would make the navbar fall back from ERPNext's logo to Frappe's.
        self.assertFalse(hasattr(hooks, "app_logo_url"))

    def test_permission_check_covers_every_app_doctype(self):
        doctypes = set()
        for json_path in (APP_ROOT / "erpverein" / "doctype").glob("*/*.json"):
            definition = json.loads(json_path.read_text(encoding="utf-8"))
            if definition.get("doctype") == "DocType" and not definition.get("istable"):
                doctypes.add(definition["name"])

        self.assertEqual(set(APP_DOCTYPES), doctypes)


class TestAppPermission(IntegrationTestCase):
    def test_app_is_offered_to_users_who_can_read_an_app_doctype(self):
        cases = (
            (("System Manager",), True),
            (("Accounts User",), True),
            (("Sales User", "Sales Master Manager"), False),
        )
        for roles, expected in cases:
            user = make_user_with_roles(*roles)
            try:
                frappe.set_user(user.name)
                allowed = has_app_permission()
            finally:
                frappe.set_user("Administrator")

            with self.subTest(roles=roles):
                self.assertEqual(allowed, expected)

        self.assertTrue(has_app_permission())
