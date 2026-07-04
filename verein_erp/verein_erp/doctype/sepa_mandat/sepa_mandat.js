frappe.ui.form.on("SEPA Mandat", {
    setup(frm) {
        frm.set_query("bezugs_doctype", () => ({
            filters: {
                name: ["in", ["Mitglied"]],
            },
        }));
    },

    mandatskategorie(frm) {
        if (frm.doc.mandatskategorie === "Mitgliedsbeitrag" && frm.doc.bezugs_doctype !== "Mitglied") {
            frm.set_value("bezugs_doctype", "Mitglied");
            frm.set_value("bezugs_name", "");
        }
    },
});
