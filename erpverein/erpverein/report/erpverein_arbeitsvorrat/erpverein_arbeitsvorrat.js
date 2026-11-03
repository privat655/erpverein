frappe.query_reports["ERPverein Arbeitsvorrat"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Unternehmen"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "area",
			label: __("Bereich"),
			fieldtype: "Select",
			options: ["", "Bankumsatz", "Einzugslauf", "Einzugsposition", "Bankimport", "Zahlungsplan", "Vorabankündigung"].join("\n"),
		},
		{
			fieldname: "days_ahead",
			label: __("Vorabankündigung: Tage voraus"),
			fieldtype: "Int",
			default: 45,
		},
	],
	onload(report) {
		report.page.add_inner_button(__("Banking-Bereich öffnen"), () => window.open("/banking"));
	},
};
