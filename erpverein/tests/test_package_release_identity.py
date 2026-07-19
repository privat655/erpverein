import importlib
import pathlib
import re

from frappe.tests import UnitTestCase


ROOT = pathlib.Path(__file__).resolve().parents[2]
EXPECTED_APP = "erpverein"
EXPECTED_VERSION = "0.1.10"


class TestPackageReleaseIdentity(UnitTestCase):
	def test_package_and_project_identity(self):
		project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
		name = re.search(r'^name = "([^"]+)"$', project, re.MULTILINE)
		version = re.search(r'^version = "([^"]+)"$', project, re.MULTILINE)

		self.assertIsNotNone(name)
		self.assertIsNotNone(version)
		self.assertEqual(name.group(1), EXPECTED_APP)
		self.assertEqual(version.group(1), EXPECTED_VERSION)
		self.assertEqual(importlib.import_module(EXPECTED_APP).__version__, EXPECTED_VERSION)

	def test_release_workflow_identity(self):
		workflow = (ROOT / ".github" / "workflows" / "build-image.yml").read_text(encoding="utf-8")

		self.assertIn('"erpverein-v*.*.*-*.*.*"', workflow)
		self.assertNotIn("branches:", workflow)
		self.assertIn("/${{ github.repository_owner }}/erpverein", workflow)
		self.assertIn("TAG_APP_VERSION", workflow)
		self.assertNotIn("\n  release:", workflow)

	def test_distribution_manifest_uses_package_identity(self):
		manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8")

		self.assertIn("recursive-include erpverein ", manifest)

	def test_preproduction_baseline_has_no_patches(self):
		self.assertFalse((ROOT / "erpverein" / "patches.txt").exists())
		self.assertEqual(list((ROOT / "erpverein" / "patches").glob("**/*.py")), [])

	def test_collection_schedule_is_not_coupled_to_invoice_generation(self):
		for filename in ("subscription_generation_service.py", "rental_subscription_generation_service.py"):
			service = (ROOT / "erpverein" / "services" / filename).read_text(encoding="utf-8")
			self.assertNotIn("einzugsintervall", service)
			self.assertNotIn("sepa_collection_schedule_service", service)
