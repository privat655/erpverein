frappe.ui.form.on("SEPA Exportprofil", {
    setup(frm) {
        frm.set_query("bank_account", () => ({
            filters: { is_company_account: 1, company: frm.doc.company || undefined },
        }));
        frm.set_query("default_fee_account", () => ({
            filters: { company: frm.doc.company || undefined, is_group: 0 },
        }));
    },

    refresh(frm) {
        const warnings = (frm.doc.__onload || {}).accounts_settings_warnings || [];
        if (warnings.length) {
            frm.dashboard.set_headline_alert(
                `<div>${__("Accounts Settings weichen von der Empfehlung ab:")}</div><ul>${warnings
                    .map((warning) => `<li>${frappe.utils.escape_html(warning)}</li>`)
                    .join("")}</ul>`,
                "orange",
            );
        }
    },
});
