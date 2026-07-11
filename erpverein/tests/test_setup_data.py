from unittest.mock import patch

from frappe.tests import UnitTestCase

from erpverein.setup_data import sync_customer_masters


class TestSetupData(UnitTestCase):
	@patch("erpverein.setup_data.frappe.get_doc")
	@patch("erpverein.setup_data.frappe.db.exists", return_value=False)
	def test_customer_masters_wait_for_erpnext_tree_roots(self, _exists, get_doc):
		currency = get_doc.return_value

		sync_customer_masters()

		get_doc.assert_called_once_with(
			{"doctype": "Currency", "currency_name": "EUR", "enabled": 1}
		)
		currency.insert.assert_called_once_with(ignore_permissions=True)
