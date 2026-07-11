import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from frappe.tests import UnitTestCase

from erpverein.erpverein.report.ausgetretene_mitglieder_mit_aktiven_abonnements import (
	ausgetretene_mitglieder_mit_aktiven_abonnements as report,
)


class TestAusgetreteneMitgliederMitAktivenAbonnements(UnitTestCase):
	def test_standard_report_metadata_and_default_date(self):
		report_path = Path(__file__).with_name("ausgetretene_mitglieder_mit_aktiven_abonnements.json")
		definition = json.loads(report_path.read_text())
		script = report_path.with_suffix(".js").read_text()

		self.assertEqual(definition["name"], "Ausgetretene Mitglieder mit aktiven Abonnements")
		self.assertEqual(definition["report_type"], "Script Report")
		self.assertEqual(definition["is_standard"], "Yes")
		self.assertEqual(definition["module"], "ERPverein")
		self.assertEqual(definition["ref_doctype"], "Mitglied")
		self.assertEqual(definition["roles"], [{"role": "System Manager"}])
		self.assertIn("frappe.datetime.get_today()", script)

	def setUp(self):
		super().setUp()
		self.subscription = SimpleNamespace(
			name="SUB-0001",
			party="CUST-0001",
			status="Active",
			start_date="2026-01-01",
			end_date=None,
			erpverein_generation_run_doctype="Beitragsabrechnung",
			erpverein_generation_run="ABR-0001",
		)
		self.sources = [
			SimpleNamespace(source_doctype="Mitglied", source_name="MIT-PAST", source_role="Beitragszahler"),
			SimpleNamespace(
				source_doctype="Mitglied",
				source_name="MIT-COVERED",
				source_role="Uebernommenes Mitglied",
			),
			SimpleNamespace(source_doctype="Mieter", source_name="MIE-0001", source_role="Mieter"),
			SimpleNamespace(source_doctype="Mitglied", source_name="MIT-OTHER", source_role="Andere Rolle"),
		]

	@patch.object(report.frappe, "get_doc")
	@patch.object(report.frappe.db, "get_list")
	@patch.object(report.frappe, "has_permission")
	def test_filters_by_date_status_kind_and_source(self, has_permission, get_list, get_doc):
		get_list.side_effect = [
			[self.subscription],
			[
				SimpleNamespace(
					name="MIT-PAST",
					mitglied_name="Past Member",
					eintrittsdatum="2020-01-01",
					austrittsdatum="2026-06-30",
				),
				SimpleNamespace(
					name="MIT-COVERED",
					mitglied_name="Covered Member",
					eintrittsdatum="2021-01-01",
					austrittsdatum="2026-07-01",
				),
			],
		]
		subscription_doc = SimpleNamespace(erpverein_sources=self.sources, check_permission=Mock())
		get_doc.return_value = subscription_doc

		columns, data, message = report.execute({"report_date": "2026-07-01"})

		has_permission.assert_has_calls(
			[
				call("Mitglied", ptype="read", throw=True),
				call("Subscription", ptype="read", throw=True),
			]
		)
		subscription_call = get_list.call_args_list[0]
		self.assertEqual(subscription_call.args[0], "Subscription")
		self.assertEqual(subscription_call.kwargs["filters"]["erpverein_managed"], 1)
		self.assertEqual(subscription_call.kwargs["filters"]["erpverein_billing_kind"], "Mitgliedsbeitrag")
		self.assertEqual(
			subscription_call.kwargs["filters"]["status"],
			["in", ("Trialing", "Active", "Grace Period", "Unpaid")],
		)
		member_call = get_list.call_args_list[1]
		self.assertEqual(member_call.args[0], "Mitglied")
		self.assertEqual(member_call.kwargs["filters"]["austrittsdatum"], ["<=", report.getdate("2026-07-01")])
		self.assertEqual(member_call.kwargs["filters"]["name"], ["in", ["MIT-COVERED", "MIT-PAST"]])
		self.assertEqual([row["mitglied"] for row in data], ["MIT-PAST", "MIT-COVERED"])
		self.assertEqual([row["source_role"] for row in data], ["Beitragszahler", "Uebernommenes Mitglied"])
		self.assertEqual(data[0]["customer"], "CUST-0001")
		self.assertEqual(data[0]["generation_run"], "ABR-0001")
		subscription_doc.check_permission.assert_called_once_with("read")
		self.assertTrue(any(column["fieldname"] == "subscription" for column in columns))
		self.assertIn("stoppen die Abrechnung nicht automatisch", message)

	@patch.object(report, "today", return_value="2026-07-11")
	@patch.object(report.frappe, "get_doc")
	@patch.object(report.frappe.db, "get_list")
	@patch.object(report.frappe, "has_permission")
	def test_defaults_report_date_to_today(self, _has_permission, get_list, _get_doc, today):
		get_list.side_effect = [[], []]

		report.execute()

		today.assert_called_once_with()
		self.assertEqual(
			get_list.call_args_list[1].kwargs["filters"]["austrittsdatum"],
			["<=", report.getdate("2026-07-11")],
		)

	@patch.object(report.frappe, "has_permission", side_effect=report.frappe.PermissionError)
	def test_mitglied_permission_failure_stops_report(self, has_permission):
		with self.assertRaises(report.frappe.PermissionError):
			report.execute({"report_date": "2026-07-01"})

		has_permission.assert_called_once_with("Mitglied", ptype="read", throw=True)

	@patch.object(report.frappe, "has_permission", side_effect=[True, report.frappe.PermissionError])
	def test_subscription_permission_failure_stops_report(self, has_permission):
		with self.assertRaises(report.frappe.PermissionError):
			report.execute({"report_date": "2026-07-01"})

		has_permission.assert_has_calls(
			[
				call("Mitglied", ptype="read", throw=True),
				call("Subscription", ptype="read", throw=True),
			]
		)
