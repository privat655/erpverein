frappe.ui.form.on("SEPA Mandat", {
    refresh(frm) {
        set_schedule_visibility(frm);
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

    einzugsintervall(frm) {
        const interval = frm.doc.einzugsintervall;
        if (interval !== "Woechentlich") {
            frm.set_value("wochentag", "");
        }
        if (interval !== "Monatlich") {
            frm.set_value("monatstag", null);
        }
        if (!["Vierteljaehrlich", "Halbjaehrlich", "Jaehrlich"].includes(interval)) {
            frm.clear_table("einzugstermine");
            frm.refresh_field("einzugstermine");
        }
        set_schedule_visibility(frm);
    },
});

function set_schedule_visibility(frm) {
    const interval = frm.doc.einzugsintervall;
    frm.toggle_display("wochentag", interval === "Woechentlich");
    frm.toggle_display("monatstag", interval === "Monatlich");
    frm.toggle_display("einzugstermine", ["Vierteljaehrlich", "Halbjaehrlich", "Jaehrlich"].includes(interval));
}
