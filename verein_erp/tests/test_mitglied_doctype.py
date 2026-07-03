import frappe
from frappe.tests import IntegrationTestCase

from verein_erp.custom_fields import sync_custom_fields


class TestMitgliedDoctype(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()

    def test_mitglied_generates_id_and_syncs_to_customer(self):
        customer = make_customer("Mitglied Sync")
        mitglied = frappe.get_doc(
            {
                "doctype": "Mitglied",
                "vorname": "  Erika ",
                "nachname": " Musterfrau ",
                "eintrittsdatum": "2026-01-01",
                "email": " ERIKA@example.org ",
                "abrechnungsart": "Rechnung",
                "customer": customer.name,
            }
        ).insert(ignore_permissions=True)

        self.assertTrue(mitglied.mitglied_id.startswith("MIT-"))
        self.assertEqual(mitglied.name, mitglied.mitglied_id)
        self.assertEqual(mitglied.mitglied_name, "Erika Musterfrau")
        self.assertEqual(mitglied.email, "erika@example.org")
        self.assertEqual(frappe.db.get_value("Customer", customer.name, "mitglied"), mitglied.name)

    def test_customer_link_syncs_back_to_mitglied(self):
        customer = make_customer("Customer Backlink")
        mitglied = frappe.get_doc(
            {
                "doctype": "Mitglied",
                "vorname": "Max",
                "nachname": "Mustermann",
                "eintrittsdatum": "2026-01-01",
                "abrechnungsart": "Rechnung",
            }
        ).insert(ignore_permissions=True)

        customer.mitglied = mitglied.name
        customer.save(ignore_permissions=True)

        self.assertEqual(frappe.db.get_value("Mitglied", mitglied.name, "customer"), customer.name)

    def test_lastschrift_requires_mandate_data(self):
        mitglied = frappe.get_doc(
            {
                "doctype": "Mitglied",
                "vorname": "Laura",
                "nachname": "Lastschrift",
                "eintrittsdatum": "2026-01-01",
                "abrechnungsart": "Lastschrift",
            }
        )

        self.assertRaises(frappe.ValidationError, mitglied.insert, ignore_permissions=True)

    def test_mandat_id_must_be_unique(self):
        mandate_id = f"MANDAT-{frappe.generate_hash(length=8)}"
        frappe.get_doc(
            {
                "doctype": "Mitglied",
                "vorname": "Mara",
                "nachname": "Mandat",
                "eintrittsdatum": "2026-01-01",
                "abrechnungsart": "Lastschrift",
                "mandat_id": mandate_id,
                "mandatsdatum": "2026-01-01",
            }
        ).insert(ignore_permissions=True)

        duplicate = frappe.get_doc(
            {
                "doctype": "Mitglied",
                "vorname": "Duplikat",
                "nachname": "Mandat",
                "eintrittsdatum": "2026-01-01",
                "abrechnungsart": "Lastschrift",
                "mandat_id": mandate_id,
                "mandatsdatum": "2026-01-01",
            }
        )

        self.assertRaises(frappe.ValidationError, duplicate.insert, ignore_permissions=True)

    def test_austrittsdatum_must_not_precede_eintrittsdatum(self):
        mitglied = frappe.get_doc(
            {
                "doctype": "Mitglied",
                "vorname": "Datum",
                "nachname": "Test",
                "eintrittsdatum": "2026-02-01",
                "austrittsdatum": "2026-01-01",
                "abrechnungsart": "Rechnung",
            }
        )

        self.assertRaises(frappe.ValidationError, mitglied.insert, ignore_permissions=True)


def make_customer(label: str):
    suffix = frappe.generate_hash(length=8)
    customer = frappe.get_doc(
        {
            "doctype": "Customer",
            "customer_name": f"{label} {suffix}",
            "customer_type": "Individual",
        }
    )

    customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
    if customer_group and customer.meta.has_field("customer_group"):
        customer.customer_group = customer_group

    territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
    if territory and customer.meta.has_field("territory"):
        customer.territory = territory

    return customer.insert(ignore_permissions=True)
