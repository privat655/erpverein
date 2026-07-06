frappe.ui.form.on("SEPA Mandat", {
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
