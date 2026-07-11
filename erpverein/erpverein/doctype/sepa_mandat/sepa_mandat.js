frappe.ui.form.on("SEPA Mandat", {
    refresh(frm) {
        if (!frm.is_new() && frm.doc.status === "Entwurf") {
            frm.add_custom_button(__("Als Ersatz aktivieren"), () => {
                frappe.call({
                    method: "erpverein.api.sepa_mandat.activate_replacement",
                    args: { mandate: frm.doc.name },
                    freeze: true,
                    freeze_message: __("Ersatzmandat wird aktiviert..."),
                    callback(r) {
                        if (!r.exc) {
                            frm.reload_doc();
                        }
                    },
                });
            });
        }
    },

    setup(frm) {
        frm.set_query("bezugs_doctype", () => ({
            filters: {
                name: ["in", ["Mitglied", "Mieter"]],
            },
        }));
    },

    mandatskategorie(frm) {
        if (frm.doc.mandatskategorie === "Mitgliedsbeitrag" && frm.doc.bezugs_doctype !== "Mitglied") {
            frm.set_value("bezugs_doctype", "Mitglied");
            frm.set_value("bezugs_name", "");
        }
        if (frm.doc.mandatskategorie === "Miete" && frm.doc.bezugs_doctype !== "Mieter") {
            frm.set_value("bezugs_doctype", "Mieter");
            frm.set_value("bezugs_name", "");
        }
    },
});
