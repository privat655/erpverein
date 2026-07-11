import json
from pathlib import Path

from frappe.tests import UnitTestCase


class TestDoctypeModules(UnitTestCase):
	def test_each_doctype_json_has_python_module_and_erpverein_module(self):
		doctype_root = Path(__file__).resolve().parents[1] / "erpverein" / "doctype"
		missing_modules = []
		doctypes = {}

		for json_path in sorted(doctype_root.glob("*/*.json")):
			definition = json.loads(json_path.read_text())
			doctypes[definition["name"]] = definition
			module_path = json_path.with_suffix(".py")
			if not module_path.exists():
				missing_modules.append(str(module_path.relative_to(doctype_root.parents[1])))

		self.assertEqual(missing_modules, [])
		self.assertTrue(doctypes)
		self.assertEqual({definition["module"] for definition in doctypes.values()}, {"ERPverein"})
		self.assertIn("ERPverein Subscription Source", doctypes)
		self.assertEqual(doctypes["ERPverein Subscription Source"]["istable"], 1)
