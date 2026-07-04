from pathlib import Path

from frappe.tests import UnitTestCase


class TestDoctypeModules(UnitTestCase):
    def test_each_doctype_json_has_python_module(self):
        doctype_root = Path(__file__).resolve().parents[1] / "verein_erp" / "doctype"
        missing_modules = []

        for json_path in sorted(doctype_root.glob("*/*.json")):
            module_path = json_path.with_suffix(".py")
            if not module_path.exists():
                missing_modules.append(str(module_path.relative_to(doctype_root.parents[1])))

        self.assertEqual(missing_modules, [])
