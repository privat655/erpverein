frappe.query_reports["Ausgetretene Mitglieder mit aktiven Abonnements"] = {
	filters: [
		{
			fieldname: "report_date",
			label: __("Stichtag"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
	],
};
