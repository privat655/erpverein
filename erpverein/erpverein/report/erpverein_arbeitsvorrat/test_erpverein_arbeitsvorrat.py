import json
from pathlib import Path
from unittest.mock import patch

from frappe.tests import UnitTestCase

from erpverein.erpverein.report.erpverein_arbeitsvorrat import erpverein_arbeitsvorrat as report


class TestERPvereinArbeitsvorrat(UnitTestCase):
	def test_standard_report_metadata_and_filters(self):
		report_path = Path(__file__).with_name("erpverein_arbeitsvorrat.json")
		definition = json.loads(report_path.read_text())
		script = report_path.with_suffix(".js").read_text()

		self.assertEqual(definition["name"], "ERPverein Arbeitsvorrat")
		self.assertEqual(definition["report_type"], "Script Report")
		self.assertEqual(definition["is_standard"], "Yes")
		self.assertEqual(definition["module"], "ERPverein")
		self.assertEqual(definition["ref_doctype"], "Bank Transaction")
		self.assertEqual(definition["roles"], [{"role": "Accounts User"}, {"role": "Accounts Manager"}])
		for fieldname in ("company", "area", "days_ahead"):
			self.assertIn(f'fieldname: "{fieldname}"', script)
		self.assertIn("/banking", script)

	def test_execute_delegates_to_the_work_queue_service(self):
		with patch.object(report, "get_work_queue", return_value=([], [], "0 offene Faelle.")) as service:
			self.assertEqual(report.execute({"area": "Bankumsatz"}), ([], [], "0 offene Faelle."))

		service.assert_called_once_with({"area": "Bankumsatz"})
