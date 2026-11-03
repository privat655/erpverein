frappe.ui.form.on("SEPA Einzugsposition", {
    refresh(frm) {
        if (frm.is_new()) {
            return;
        }
        if (frm.doc.status === "Manuelle Pruefung") {
            frm.dashboard.set_headline_alert(
                `${__("Manuelle Prüfung.")} ${frappe.utils.escape_html(frm.doc.processing_error || "")}`,
                "red",
            );
            frm.add_custom_button(__("Als erledigt markieren"), () => erpverein_mark_position_done(frm)).addClass(
                "btn-primary",
            );
        } else if (frm.doc.status === "Erledigt") {
            frm.dashboard.set_headline_alert(frappe.utils.escape_html(frm.doc.resolution_note || ""), "green");
        }
        if (!["Entwurf", "Geprueft"].includes(frm.doc.status)) {
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

function erpverein_mark_position_done(frm) {
    frappe.prompt(
        [
            {
                fieldtype: "HTML",
                fieldname: "hint",
                options: `<p>${__(
                    "Markiere die Position erst, wenn du den Fall im Banking-Bereich geklärt hast. ERPverein bucht für diese Position danach nichts mehr. Die Zahlungsplanzeile bleibt für neue Einzüge gesperrt.",
                )}</p>`,
            },
            { fieldtype: "Small Text", fieldname: "reason", label: __("Was hast du getan?"), reqd: 1 },
        ],
        (values) => {
            frappe.call({
                method: "erpverein.api.sepa_collection.mark_done",
                args: { position: frm.doc.name, reason: values.reason },
                freeze: true,
                callback(r) {
                    if (!r.exc) {
                        frm.reload_doc();
                    }
                },
            });
        },
        __("Als erledigt markieren"),
        __("Erledigt"),
    );
}
