frappe.ui.form.on("SEPA Einzugsposition", {
    refresh(frm) {
        if (frm.is_new() || !["Entwurf", "Geprueft"].includes(frm.doc.status)) {
            return;
        }
        frm.add_custom_button(__("Position stornieren"), () => {
            frappe.prompt(
                [{ fieldtype: "Small Text", fieldname: "reason", label: __("Grund"), reqd: 1 }],
                (values) => {
                    frappe.call({
                        method: "erpverein.api.sepa_collection.cancel_position",
                        args: { position: frm.doc.name, reason: values.reason },
                        freeze: true,
                        callback(r) {
                            if (!r.exc) {
                                frm.reload_doc();
                            }
                        },
                    });
                },
                __("Position stornieren"),
                __("Stornieren"),
            );
        });
    },
});
