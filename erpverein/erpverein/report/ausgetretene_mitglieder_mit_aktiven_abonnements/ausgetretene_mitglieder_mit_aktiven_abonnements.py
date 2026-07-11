import frappe
from frappe import _
from frappe.utils import getdate, today


ACTIVE_SUBSCRIPTION_STATUSES = ("Trialing", "Active", "Grace Period", "Unpaid")
MEMBERSHIP_SOURCE_ROLES = ("Beitragszahler", "Uebernommenes Mitglied")


def execute(filters=None):
	frappe.has_permission("Mitglied", ptype="read", throw=True)
	frappe.has_permission("Subscription", ptype="read", throw=True)

	report_date = getdate((filters or {}).get("report_date") or today())
	subscriptions = frappe.db.get_list(
		"Subscription",
		filters={
			"erpverein_managed": 1,
			"erpverein_billing_kind": "Mitgliedsbeitrag",
			"status": ["in", ACTIVE_SUBSCRIPTION_STATUSES],
		},
		fields=[
			"name",
			"party",
			"status",
			"start_date",
			"end_date",
			"erpverein_generation_run_doctype",
			"erpverein_generation_run",
		],
		order_by="name asc",
		limit_page_length=0,
	)

	source_rows = []
	for subscription_row in subscriptions:
		subscription = frappe.get_doc("Subscription", subscription_row.name)
		subscription.check_permission("read")
		for source in subscription.erpverein_sources:
			if source.source_doctype != "Mitglied" or source.source_role not in MEMBERSHIP_SOURCE_ROLES:
				continue
			source_rows.append((subscription_row, source))

	member_names = sorted({source.source_name for _, source in source_rows})
	members = frappe.db.get_list(
		"Mitglied",
		filters={
			"name": ["in", member_names],
			"austrittsdatum": ["<=", report_date],
		},
		fields=["name", "mitglied_name", "eintrittsdatum", "austrittsdatum"],
		limit_page_length=0,
	)
	members_by_name = {member.name: member for member in members}

	data = []
	for subscription, source in source_rows:
		member = members_by_name.get(source.source_name)
		if not member:
			continue
		data.append(
			{
				"mitglied": member.name,
				"mitglied_name": member.mitglied_name,
				"source_role": source.source_role,
				"customer": subscription.party,
				"eintrittsdatum": member.eintrittsdatum,
				"austrittsdatum": member.austrittsdatum,
				"subscription": subscription.name,
				"subscription_status": subscription.status,
				"subscription_start_date": subscription.start_date,
				"subscription_end_date": subscription.end_date,
				"generation_run_doctype": subscription.erpverein_generation_run_doctype,
				"generation_run": subscription.erpverein_generation_run,
			}
		)

	message = _(
		"Hinweis: Eintritts- und Austrittsdaten stoppen die Abrechnung nicht automatisch. "
		"Pruefen und beenden Sie das Abonnement bei Bedarf manuell."
	)
	return get_columns(), data, message


def get_columns():
	return [
		{"fieldname": "mitglied", "label": _("Mitglied"), "fieldtype": "Link", "options": "Mitglied", "width": 150},
		{"fieldname": "mitglied_name", "label": _("Mitgliedsname"), "fieldtype": "Data", "width": 180},
		{"fieldname": "source_role", "label": _("Rolle"), "fieldtype": "Data", "width": 180},
		{"fieldname": "customer", "label": _("Kunde"), "fieldtype": "Link", "options": "Customer", "width": 150},
		{"fieldname": "eintrittsdatum", "label": _("Eintrittsdatum"), "fieldtype": "Date", "width": 110},
		{"fieldname": "austrittsdatum", "label": _("Austrittsdatum"), "fieldtype": "Date", "width": 110},
		{"fieldname": "subscription", "label": _("Abonnement"), "fieldtype": "Link", "options": "Subscription", "width": 160},
		{"fieldname": "subscription_status", "label": _("Status"), "fieldtype": "Data", "width": 110},
		{"fieldname": "subscription_start_date", "label": _("Abo-Beginn"), "fieldtype": "Date", "width": 110},
		{"fieldname": "subscription_end_date", "label": _("Abo-Ende"), "fieldtype": "Date", "width": 110},
		{"fieldname": "generation_run_doctype", "label": _("Lauf-Typ"), "fieldtype": "Data", "hidden": 1},
		{
			"fieldname": "generation_run",
			"label": _("Abrechnungslauf"),
			"fieldtype": "Dynamic Link",
			"options": "generation_run_doctype",
			"width": 170,
		},
	]
