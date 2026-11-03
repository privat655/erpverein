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
        } else if (frm.doc.status === "Rueckbelastet") {
            erpverein_show_return(frm);
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

function erpverein_show_return(frm) {
    if (frm.doc.return_decision) {
        frm.dashboard.set_headline_alert(
            `${__("Zurückbelastet.")} ${frappe.utils.escape_html(frm.doc.return_decision_note || "")}`,
            "blue",
        );
        return;
    }
    frm.dashboard.set_headline_alert(
        __(
            "Zurückbelastet. Entscheide: erneut einziehen oder auf Überweisung umstellen. Bis dahin bleibt die Zahlungsplanzeile gesperrt.",
        ),
        "orange",
    );
    frm.add_custom_button(
        __("Erneut einziehen"),
        () => erpverein_decide_return(frm, "Erneut einziehen"),
        __("Rücklastschrift"),
    );
    frm.add_custom_button(
        __("Auf Überweisung umstellen"),
        () => erpverein_decide_return(frm, "Ueberweisung"),
        __("Rücklastschrift"),
    );
}

function erpverein_decide_return(frm, decision) {
    const recollect = decision === "Erneut einziehen";
    const hint = recollect
        ? __(
              "Wähle eine neue Fälligkeit. ERPverein zieht die Zeile nicht von selbst ein: Sende die Rechnung danach erneut per E-Mail (neue Vorabankündigung, bei Mieten nicht nötig). Der nächste Einzugslauf nach der Frist nimmt die Zeile auf. Prüfe vorher den Rückgabegrund, zum Beispiel ein aufgelöstes Konto.",
          )
        : __(
              "Die Zeile bekommt die Zahlungsweise Überweisung. ERPverein bucht eine passende Überweisung dann automatisch. Sende die Rechnung erneut, damit der Zahler den offenen Betrag kennt.",
          );
    frappe.prompt(
        [
            { fieldtype: "HTML", fieldname: "hint", options: `<p>${hint}</p>` },
            {
                fieldtype: "Date",
                fieldname: "due_date",
                label: recollect ? __("Neue Fälligkeit") : __("Neue Fälligkeit (optional)"),
                reqd: recollect ? 1 : 0,
            },
            { fieldtype: "Small Text", fieldname: "note", label: __("Notiz (optional)") },
        ],
        (values) => {
            frappe.call({
                method: "erpverein.api.sepa_collection.decide_return",
                args: { position: frm.doc.name, decision, due_date: values.due_date, note: values.note },
                freeze: true,
                callback(r) {
                    if (r.exc || !r.message) {
                        return;
                    }
                    if (r.message.prenotification_status === "Erneut senden") {
                        frappe.msgprint(
                            __("Sende die Rechnung {0} jetzt erneut per E-Mail. Sie ist die neue Vorabankündigung.", [
                                r.message.invoice,
                            ]),
                        );
                    }
                    frm.reload_doc();
                },
            });
        },
        recollect ? __("Erneut einziehen") : __("Auf Überweisung umstellen"),
        __("Entscheiden"),
    );
}
