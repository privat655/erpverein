import hashlib
import json
from decimal import Decimal

import frappe
from frappe.core.doctype.file.file import has_permission as file_has_permission
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, add_to_date, cint, now_datetime

from erpverein.custom_fields import sync_custom_fields
from erpverein.services.pain008_writer import validate_pain008
from erpverein.services.payment_schedule_service import get_schedule_editor
from erpverein.services.sepa_collection_export_service import export_collection_run
from erpverein.services.sepa_collection_schedule_service import next_target_business_day
from erpverein.services.sepa_collection_service import (
    POSITION_DOCTYPE,
    RUN_DOCTYPE,
    build_collection_preview,
    cancel_collection_position,
    cancel_collection_run,
    check_collection_run,
    create_collection_runs,
    create_position,
    set_collection_bank_reference,
)
from erpverein.setup_data import sync_setup_data
from erpverein.tests.collection_fixtures import NEXT_YEAR, TEST_COMPANY, ensure_export_profile
from erpverein.tests.test_customer_sync_service import make_erpverein_manager_user
from erpverein.tests.test_payment_schedule_service import (
    make_eur_customer,
    make_managed_subscription,
    make_plan,
    make_price_list,
    make_user_with_roles,
)
from erpverein.tests.test_sepa_mandat_doctype import (
    make_german_iban,
    make_mieter,
    make_mitglied,
    make_rent_sepa_mandat,
    make_sepa_mandat,
)


HALF_YEARLY = ((3, 31, 175), (9, 30, 175))
MARCH_CUTOFF = f"{NEXT_YEAR}-03-31"
SEPTEMBER_CUTOFF = f"{NEXT_YEAR}-09-30"


class TestSEPACollectionService(IntegrationTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        sync_custom_fields()
        sync_setup_data()
        make_price_list()
        cls.profile = ensure_export_profile()

    def test_preview_writes_nothing_and_names_a_reason_for_each_conflict(self):
        ready = make_member_invoice(make_member_with_mandate("Collection Ready"))
        pending = make_member_invoice(make_member_with_mandate("Collection Pending"), notified=False)
        leaving_member = make_member_with_mandate("Collection Leaving")
        leaving = make_member_invoice(leaving_member)
        frappe.db.set_value("Mitglied", leaving_member.name, "austrittsdatum", f"{NEXT_YEAR}-02-28")
        invoices = [ready.name, pending.name, leaving.name]
        before = snapshot(invoices)

        preview = build_collection_preview(TEST_COMPANY, MARCH_CUTOFF)

        self.assertEqual(snapshot(invoices), before)
        rows = {row["invoice"]: row for row in rows_for(preview, *invoices)}
        self.assertEqual(len(rows_for(preview, ready.name)), 1)
        self.assertEqual(rows[ready.name]["status"], "Bereit")
        self.assertEqual(rows[ready.name]["collection_date"], str(next_target_business_day(MARCH_CUTOFF)))
        self.assertEqual(rows[ready.name]["amount"], 175.0)
        self.assertTrue(rows[ready.name]["iban_masked"].startswith("**** "))
        self.assertEqual(rows[pending.name]["status"], "Konflikt")
        self.assertIn("Vorabankuendigung", rows[pending.name]["reason"])
        self.assertEqual(rows[leaving.name]["status"], "Konflikt")
        self.assertIn("Austritt", rows[leaving.name]["reason"])
        self.assertTrue(all(row["reason"] for row in preview["rows"] if row["status"] != "Bereit"))
        mandate_iban = frappe.db.get_value("SEPA Mandat", ready.erpverein_sepa_mandate, "iban")
        self.assertNotIn(mandate_iban, json.dumps(preview))

    def test_generation_twice_creates_no_second_position(self):
        invoice = make_member_invoice(make_member_with_mandate("Collection Twice"))
        preview = build_collection_preview(TEST_COMPANY, SEPTEMBER_CUTOFF)
        rows = rows_for(preview, invoice.name)
        selected = json.dumps([row["schedule_row"] for row in rows])

        result = create_collection_runs(TEST_COMPANY, SEPTEMBER_CUTOFF, preview["preview_hash"], selected_rows=selected)
        with self.assertRaises(frappe.ValidationError):
            create_collection_runs(TEST_COMPANY, SEPTEMBER_CUTOFF, preview["preview_hash"], selected_rows=selected)
        run = frappe.get_doc(RUN_DOCTYPE, result["runs"][0])
        duplicate = create_position(run, rows[0], self.profile)
        again = build_collection_preview(TEST_COMPANY, SEPTEMBER_CUTOFF)

        self.assertEqual(result["created"], 2)
        self.assertEqual(len(result["runs"]), 2)
        self.assertEqual(duplicate, "skipped")
        self.assertEqual(len(positions_for(invoice.name)), 2)
        self.assertEqual({row["status"] for row in rows_for(again, invoice.name)}, {"Vorhanden"})
        self.assertTrue(all(row["position"] for row in rows_for(again, invoice.name)))

    def test_export_writes_valid_private_file_and_freezes_the_run(self):
        first = make_member_invoice(make_member_with_mandate("Collection Export A"))
        second = make_member_invoice(make_member_with_mandate("Collection Export B"))
        result = generate(MARCH_CUTOFF, first.name, second.name)
        run_name = result["runs"][0]

        checked = check_collection_run(run_name)
        exported = export_collection_run(run_name)
        run = frappe.get_doc(RUN_DOCTYPE, run_name)
        file_doc = frappe.get_doc("File", {"file_url": run.export_file})
        content = file_content(file_doc)
        summary = validate_pain008(content)
        positions = positions_for(first.name) + positions_for(second.name)

        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(checked["status"], "Geprueft")
        self.assertTrue(exported["exported"])
        self.assertEqual(run.status, "Exportiert")
        self.assertEqual(run.pmtinf_id, run.name)
        self.assertTrue(run.msg_id.startswith(run.name))
        self.assertEqual(run.export_file_sha256, hashlib.sha256(content).hexdigest())
        self.assertEqual(run.exported_by, "Administrator")
        self.assertEqual(run.export_format, "pain.008.001.08")
        self.assertEqual(
            run.export_profile_version, cint(frappe.db.get_value("SEPA Exportprofil", run.export_profile, "profile_version"))
        )
        self.assertEqual((run.position_count, float(run.total_amount)), (2, 350.0))
        self.assertTrue(cint(file_doc.is_private))
        self.assertEqual((file_doc.attached_to_doctype, file_doc.attached_to_name), (RUN_DOCTYPE, run.name))
        self.assertEqual(summary["count"], 2)
        self.assertEqual(summary["total"], Decimal("350.00"))
        self.assertTrue(summary["batch_booking"])
        self.assertEqual(summary["collection_date"], str(run.collection_date))
        self.assertEqual(sorted(summary["end_to_end_ids"]), sorted(position.name for position in positions))
        self.assertEqual({position.status for position in positions}, {"Exportiert"})

        run.notes = "Hochgeladen"
        run.save()
        run.collection_date = add_days(run.collection_date, 1)
        with self.assertRaises(frappe.ValidationError):
            run.save()
        editor = get_schedule_editor(first.name)
        self.assertEqual([row["locked"] for row in editor["rows"]], [True, False])
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc("Sales Invoice", first.name).cancel()

    def test_single_booking_run_exports_batch_booking_false(self):
        invoice = make_member_invoice(make_member_with_mandate("Collection Single"))
        result = generate(MARCH_CUTOFF, invoice.name, batch_booking=0)
        check_collection_run(result["runs"][0])
        export_collection_run(result["runs"][0])
        run = frappe.get_doc(RUN_DOCTYPE, result["runs"][0])

        self.assertFalse(cint(run.batch_booking))
        self.assertFalse(validate_pain008(file_content(frappe.get_doc("File", {"file_url": run.export_file})))["batch_booking"])

    def test_member_and_tenant_of_one_customer_get_separate_positions(self):
        customer = make_eur_customer("Collection Shared")
        member = make_member_with_mandate("Collection Shared", customer=customer)
        membership = make_member_invoice(member)
        tenant = make_tenant_with_mandate(customer)
        rent = make_rent_invoice(tenant)

        result = generate(MARCH_CUTOFF, membership.name, rent.name)
        membership_positions = positions_for(membership.name)
        rent_positions = positions_for(rent.name)

        self.assertEqual(rent.erpverein_prenotification_status, "Nicht erforderlich")
        self.assertEqual(result["created"], 2)
        self.assertEqual((len(membership_positions), len(rent_positions)), (1, 1))
        self.assertEqual(membership_positions[0].customer, rent_positions[0].customer)
        self.assertEqual((membership_positions[0].source_doctype, rent_positions[0].source_doctype), ("Mitglied", "Mieter"))
        self.assertEqual((membership_positions[0].billing_kind, rent_positions[0].billing_kind), ("Mitgliedsbeitrag", "Miete"))
        self.assertNotEqual(membership_positions[0].sepa_mandate, rent_positions[0].sepa_mandate)
        self.assertEqual((float(membership_positions[0].amount), float(rent_positions[0].amount)), (175.0, 500.0))

    def test_user_without_accounts_role_cannot_prepare_see_or_export(self):
        invoice = make_member_invoice(make_member_with_mandate("Collection Rights"))
        run_name = generate(MARCH_CUTOFF, invoice.name)["runs"][0]
        check_collection_run(run_name)
        export_collection_run(run_name)
        file_doc = frappe.get_doc("File", {"file_url": frappe.db.get_value(RUN_DOCTYPE, run_name, "export_file")})
        system_manager = make_user_with_roles("System Manager", "Sales User", "Sales Master Manager")
        sales_user = make_user_with_roles("Sales User", "Sales Master Manager")

        for user in (system_manager.name, sales_user.name):
            try:
                frappe.set_user(user)
                with self.subTest(user=user):
                    with self.assertRaises(frappe.PermissionError):
                        build_collection_preview(TEST_COMPANY, MARCH_CUTOFF)
                    with self.assertRaises(frappe.PermissionError):
                        set_collection_bank_reference(run_name, "PG-0001")
                    self.assertFalse(frappe.has_permission(RUN_DOCTYPE, "read", doc=run_name))
                    self.assertFalse(frappe.has_permission(POSITION_DOCTYPE, "read", doc=positions_for(invoice.name)[0].name))
                    self.assertEqual(visible_runs(run_name), [])
                    self.assertFalse(file_has_permission(file_doc, "read", user=user))
            finally:
                frappe.set_user("Administrator")

    def test_treasurer_roles_prepare_check_and_export(self):
        invoice = make_member_invoice(make_member_with_mandate("Collection Treasurer"))
        user = make_erpverein_manager_user()
        try:
            frappe.set_user(user.name)
            run_name = generate(MARCH_CUTOFF, invoice.name)["runs"][0]
            check_collection_run(run_name)
            result = export_collection_run(run_name)
            set_collection_bank_reference(run_name, "PG-2027-0001")
        finally:
            frappe.set_user("Administrator")
        run = frappe.get_doc(RUN_DOCTYPE, run_name)

        self.assertTrue(result["exported"])
        self.assertEqual(run.exported_by, user.name)
        self.assertEqual(run.status, "Bankreferenz erfasst")
        self.assertEqual(run.bank_order_reference, "PG-2027-0001")

    def test_due_dates_before_go_live_are_never_collected(self):
        invoice = make_member_invoice(make_member_with_mandate("Collection Go Live"))
        ensure_export_profile(go_live_date=f"{NEXT_YEAR}-06-01")
        try:
            preview = build_collection_preview(TEST_COMPANY, SEPTEMBER_CUTOFF)
        finally:
            ensure_export_profile(go_live_date=f"{NEXT_YEAR}-01-01")

        self.assertEqual([row["due_date"] for row in rows_for(preview, invoice.name)], [SEPTEMBER_CUTOFF])
        early = build_collection_preview(TEST_COMPANY, f"{NEXT_YEAR - 1}-12-31")
        self.assertEqual(rows_for(early, invoice.name), [])

    def test_changed_mandate_and_partial_payment_block_the_export(self):
        changed = make_member_invoice(make_member_with_mandate("Collection Changed"))
        paid = make_member_invoice(make_member_with_mandate("Collection Paid"))
        run_name = generate(MARCH_CUTOFF, changed.name, paid.name)["runs"][0]
        self.assertEqual(check_collection_run(run_name)["status"], "Geprueft")
        frappe.db.set_value("SEPA Mandat", changed.erpverein_sepa_mandate, "iban", make_german_iban())
        paid_row = next(row for row in paid.payment_schedule if str(row.due_date) == MARCH_CUTOFF)
        frappe.db.set_value("Payment Schedule", paid_row.name, {"paid_amount": 75, "outstanding": 100})

        result = export_collection_run(run_name)
        run = frappe.get_doc(RUN_DOCTYPE, run_name)
        reasons = {
            position.sales_invoice: position.processing_error
            for position in frappe.get_all(
                POSITION_DOCTYPE, filters={"collection_run": run_name}, fields=["sales_invoice", "processing_error"]
            )
        }

        self.assertFalse(result["exported"])
        self.assertEqual(run.status, "Entwurf")
        self.assertEqual(run.error_count, 2)
        self.assertFalse(run.export_file)
        self.assertIn("SEPA-Mandat", reasons[changed.name])
        self.assertIn("offene Betrag", reasons[paid.name])

    def test_cancelled_position_frees_the_row_for_a_new_run(self):
        invoice = make_member_invoice(make_member_with_mandate("Collection Cancel"))
        first_run = generate(MARCH_CUTOFF, invoice.name)["runs"][0]
        position = positions_for(invoice.name)[0]

        cancel_collection_position(position.name, "Betrag mit dem Mitglied klaeren")
        second_run = generate(MARCH_CUTOFF, invoice.name)["runs"][0]
        positions = positions_for(invoice.name)

        self.assertEqual(frappe.db.get_value(RUN_DOCTYPE, first_run, "status"), "Storniert")
        self.assertNotEqual(first_run, second_run)
        self.assertEqual([(row.status, bool(row.active_key)) for row in positions], [("Storniert", False), ("Entwurf", True)])

    def test_exported_run_needs_confirmation_before_cancellation(self):
        invoice = make_member_invoice(make_member_with_mandate("Collection Exported Cancel"))
        run_name = generate(MARCH_CUTOFF, invoice.name)["runs"][0]
        check_collection_run(run_name)
        export_collection_run(run_name)

        with self.assertRaises(frappe.ValidationError):
            cancel_collection_run(run_name, "Upload abgelehnt")
        cancel_collection_run(run_name, "Upload abgelehnt", confirm_not_submitted=1)
        run = frappe.get_doc(RUN_DOCTYPE, run_name)

        self.assertEqual(run.status, "Storniert")
        self.assertTrue(run.export_file)
        self.assertEqual({position.status for position in positions_for(invoice.name)}, {"Storniert"})
        self.assertFalse(any(row["locked"] for row in get_schedule_editor(invoice.name)["rows"]))

    def test_runs_and_positions_are_created_only_by_erpverein(self):
        with self.assertRaises(frappe.ValidationError):
            frappe.get_doc(
                {
                    "doctype": RUN_DOCTYPE,
                    "company": TEST_COMPANY,
                    "export_profile": self.profile.name,
                    "collection_date": f"{NEXT_YEAR}-03-31",
                }
            ).insert()
        invoice = make_member_invoice(make_member_with_mandate("Collection Purpose"))
        generate(MARCH_CUTOFF, invoice.name)
        position = frappe.get_doc(POSITION_DOCTYPE, positions_for(invoice.name)[0].name)

        position.purpose = "Beitrag Müller 2027"
        position.save()
        self.assertEqual(position.purpose, "Beitrag Mueller 2027")
        position.amount = 1
        with self.assertRaises(frappe.ValidationError):
            position.save()
        with self.assertRaises(frappe.ValidationError):
            frappe.delete_doc(POSITION_DOCTYPE, position.name)


def make_member_with_mandate(label: str, dates=HALF_YEARLY, customer=None):
    customer = customer or make_eur_customer(label)
    mitglied = make_mitglied(customer=customer.name, jahresbeitrag=350)
    interval = {1: "Jaehrlich", 2: "Halbjaehrlich", 4: "Vierteljaehrlich"}[len(dates)]
    make_sepa_mandat(
        mitglied,
        einzugsintervall=interval,
        einzugstermine=[{"monat": month, "kalendertag": day, "einzugsbetrag": amount} for month, day, amount in dates],
    ).insert(ignore_permissions=True)
    mitglied.reload()
    mitglied.abrechnungsart = "Lastschrift"
    mitglied.save(ignore_permissions=True)
    return mitglied


def make_member_invoice(mitglied, *, notified: bool = True):
    subscription = make_managed_subscription(
        mitglied.customer,
        "Mitglied",
        mitglied.name,
        "Beitragszahler",
        make_plan(350, "Year").name,
        f"{NEXT_YEAR}-01-01",
        "Mitgliedsbeitrag",
    )
    invoice = subscription.create_invoice()
    invoice.submit()
    if notified:
        frappe.db.set_value(
            "Sales Invoice",
            invoice.name,
            {
                "erpverein_prenotification_status": "Versendet",
                "erpverein_prenotification_sent_at": add_to_date(now_datetime(), seconds=1),
            },
            update_modified=False,
        )
    invoice.reload()
    return invoice


def make_tenant_with_mandate(customer):
    mieter = make_mieter(customer=customer.name)
    make_rent_sepa_mandat(
        mieter,
        einzugsintervall="Monatlich",
        monatstag=1,
        regelmaessiger_einzugsbetrag=500,
        einzugstermine=[],
    ).insert(ignore_permissions=True)
    mieter.reload()
    mieter.abrechnungsart = "Lastschrift"
    mieter.save(ignore_permissions=True)
    return mieter


def make_rent_invoice(mieter):
    subscription = make_managed_subscription(
        mieter.customer, "Mieter", mieter.name, "Mieter", make_plan(500, "Month").name, f"{NEXT_YEAR}-03-01", "Miete"
    )
    invoice = subscription.create_invoice()
    invoice.submit()
    invoice.reload()
    return invoice


def generate(cutoff: str, *invoices: str, batch_booking=None) -> dict:
    preview = build_collection_preview(TEST_COMPANY, cutoff, batch_booking)
    selected = [row["schedule_row"] for row in rows_for(preview, *invoices) if row["status"] == "Bereit"]
    return create_collection_runs(
        TEST_COMPANY, cutoff, preview["preview_hash"], batch_booking, selected_rows=json.dumps(selected)
    )


def rows_for(preview: dict, *invoices: str) -> list[dict]:
    return [row for row in preview["rows"] if row["invoice"] in invoices]


def positions_for(invoice: str) -> list:
    return frappe.get_all(
        POSITION_DOCTYPE,
        filters={"sales_invoice": invoice},
        fields=[
            "name",
            "status",
            "collection_run",
            "amount",
            "active_key",
            "billing_kind",
            "sepa_mandate",
            "source_doctype",
            "customer",
        ],
        order_by="name asc",
    )


def visible_runs(run_name: str) -> list:
    try:
        return frappe.get_list(RUN_DOCTYPE, filters={"name": run_name})
    except frappe.PermissionError:
        return []


def snapshot(invoices: list[str]) -> tuple:
    return (
        frappe.db.count(RUN_DOCTYPE),
        frappe.db.count(POSITION_DOCTYPE),
        [str(frappe.db.get_value("Sales Invoice", name, "modified")) for name in invoices],
    )


def file_content(file_doc) -> bytes:
    content = file_doc.get_content()
    return content.encode("utf-8") if isinstance(content, str) else content
