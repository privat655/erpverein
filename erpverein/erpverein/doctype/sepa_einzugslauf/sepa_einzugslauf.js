frappe.ui.form.on("SEPA Einzugslauf", {
    refresh(frm) {
        erpverein_render_collection_positions(frm);
        erpverein_show_upload_window(frm);
        if (frm.is_new()) {
            return;
        }
        const status = frm.doc.status;
        if (["Entwurf", "Geprueft"].includes(status)) {
            frm.add_custom_button(__("Lauf prüfen"), () => erpverein_check_collection_run(frm));
        }
        if (status === "Geprueft") {
            frm.add_custom_button(__("pain.008-Datei erzeugen"), () => erpverein_export_collection_run(frm)).addClass(
                "btn-primary",
            );
        }
        if (status === "Entwurf" && frm.doc.error_count) {
            frm.add_custom_button(__("Fehlerhafte Positionen stornieren"), () =>
                erpverein_ask_reason(__("Fehlerhafte Positionen stornieren"), false, (values) =>
                    erpverein_collection_call(frm, "cancel_positions", { reason: values.reason, only_errors: 1 }),
                ),
            );
        }
        if (frm.doc.export_file && !["Entwurf", "Geprueft", "Storniert"].includes(status)) {
            frm.add_custom_button(__("Datei herunterladen"), () => window.open(frm.doc.export_file));
        }
        if (["Exportiert", "Bankreferenz erfasst"].includes(status) && frm.doc.export_file) {
            frm.add_custom_button(__("Bankreferenz erfassen"), () => erpverein_set_bank_reference(frm));
        }
        if (["Exportiert", "Bankreferenz erfasst", "Bankgutschrift erkannt", "Manuelle Pruefung"].includes(status)) {
            frm.add_custom_button(__("Abgleich starten"), () =>
                erpverein_collection_call(frm, "start_reconciliation", {}, __("Abgleich wird eingereiht...")).then((r) => {
                    if (r && !r.exc) {
                        frappe.show_alert({ message: __("Abgleich eingereiht. Lade das Formular gleich neu."), indicator: "blue" });
                    }
                }),
            );
        }
        if (!["Storniert", "Bankgutschrift erkannt", "Abgestimmt"].includes(status) && !frm.doc.settlement_bank_transaction) {
            frm.add_custom_button(__("Lauf stornieren"), () => erpverein_cancel_collection_run(frm));
        }
        erpverein_show_settlement_status(frm);
    },
});

function erpverein_show_settlement_status(frm) {
    const status = frm.doc.status;
    if (status === "Abgestimmt") {
        frm.dashboard.set_headline_alert(
            __("Abgestimmt am {0}. Jede Position ist bezahlt oder als erledigt markiert.", [
                frappe.datetime.str_to_user(frm.doc.settled_at),
            ]),
            "green",
        );
    } else if (status === "Bankgutschrift erkannt") {
        frm.dashboard.set_headline_alert(
            __("Die Bankgutschrift ist erkannt. Es fehlen noch Zahlungen. Klicke „Abgleich starten“, wenn das so bleibt."),
            "orange",
        );
    } else if (status === "Manuelle Pruefung") {
        frm.dashboard.set_headline_alert(
            `${__("Manuelle Prüfung.")} ${frappe.utils.escape_html(frm.doc.settlement_note || "")}`,
            "red",
        );
    }
}

function erpverein_collection_call(frm, method, args, freeze_message) {
    return frappe.call({
        method: `erpverein.api.sepa_collection.${method}`,
        args: { run: frm.doc.name, ...args },
        freeze: true,
        freeze_message: freeze_message || __("Bitte warten..."),
        callback(r) {
            if (!r.exc) {
                frm.reload_doc();
            }
        },
    });
}

function erpverein_check_collection_run(frm) {
    erpverein_collection_call(frm, "check_run", {}, __("Lauf wird geprüft...")).then((r) => {
        const result = (r && r.message) || {};
        if (result.errors) {
            frappe.msgprint(__("{0} Positionen haben Fehler. Der Grund steht in der Tabelle Positionen.", [result.errors]));
        } else if (result.status === "Geprueft") {
            frappe.show_alert({ message: __("Lauf geprüft. Du kannst die Datei erzeugen."), indicator: "green" });
        }
    });
}

function erpverein_export_collection_run(frm) {
    frappe.confirm(
        __("pain.008-Datei für {0} Lastschriften über {1} erzeugen? Danach ist der Lauf unveränderlich.", [
            frm.doc.position_count,
            format_currency(frm.doc.total_amount, frm.doc.currency),
        ]),
        () => {
            erpverein_collection_call(frm, "export_run", {}, __("Datei wird erzeugt...")).then((r) => {
                const result = (r && r.message) || {};
                if (result.exported === false) {
                    frappe.msgprint(__("Die erneute Prüfung hat Fehler gefunden. Der Lauf steht wieder auf Entwurf."));
                } else if (result.exported) {
                    frappe.msgprint(
                        __("Datei erzeugt: {0} Lastschriften, {1}. Lade die Datei herunter und lade sie bei der HVB hoch.", [
                            result.count,
                            format_currency(result.total, frm.doc.currency),
                        ]),
                    );
                }
            });
        },
    );
}

function erpverein_set_bank_reference(frm) {
    frappe.prompt(
        [
            {
                fieldtype: "Data",
                fieldname: "reference",
                label: __("Bankreferenz (Auftragsreferenz aus PayGate)"),
                reqd: 1,
                default: frm.doc.bank_order_reference,
            },
        ],
        (values) => erpverein_collection_call(frm, "set_bank_reference", { reference: values.reference }),
        __("Bankreferenz erfassen"),
        __("Speichern"),
    );
}

function erpverein_cancel_collection_run(frm) {
    const exported = ["Exportiert", "Bankreferenz erfasst", "Manuelle Pruefung"].includes(frm.doc.status);
    erpverein_ask_reason(__("Lauf stornieren"), exported, (values) =>
        erpverein_collection_call(frm, "cancel_run", {
            reason: values.reason,
            confirm_not_submitted: values.confirm_not_submitted ? 1 : 0,
        }),
    );
}

function erpverein_ask_reason(title, exported, callback) {
    const fields = [{ fieldtype: "Small Text", fieldname: "reason", label: __("Grund"), reqd: 1 }];
    if (exported) {
        fields.unshift({
            fieldtype: "HTML",
            fieldname: "warning",
            options: `<div class="alert alert-danger">${__(
                "Die Datei ist schon erzeugt. Storniere nur, wenn die Bank sie nicht ausführt: nicht hochgeladen, abgelehnt oder nicht freigegeben. Sonst droht ein doppelter Einzug.",
            )}</div>`,
        });
        fields.push({
            fieldtype: "Check",
            fieldname: "confirm_not_submitted",
            label: __("Ich habe in PayGate geprüft: Die Bank führt diese Datei nicht aus."),
            reqd: 1,
        });
    }
    frappe.prompt(fields, callback, title, __("Stornieren"));
}

function erpverein_render_collection_positions(frm) {
    const wrapper = frm.fields_dict.positions_html && frm.fields_dict.positions_html.$wrapper;
    if (!wrapper) {
        return;
    }
    const positions = (frm.doc.__onload || {}).positions || [];
    if (!positions.length) {
        wrapper.html(`<p class="text-muted">${__("Keine Positionen.")}</p>`);
        return;
    }
    const rows = positions
        .map(
            (position) => `<tr>
                <td><a href="/app/sepa-einzugsposition/${encodeURIComponent(position.name)}">${frappe.utils.escape_html(position.name)}</a></td>
                <td>${frappe.utils.escape_html(__(position.status))}</td>
                <td>${frappe.utils.escape_html(position.source_name || "")}</td>
                <td>${frappe.utils.escape_html(position.sales_invoice || "")}</td>
                <td>${frappe.datetime.str_to_user(position.due_date)}</td>
                <td class="text-right">${format_currency(position.amount, frm.doc.currency)}</td>
                <td>${frappe.utils.escape_html(position.mandate_reference_snapshot || "")}</td>
                <td>${frappe.utils.escape_html(position.debtor_iban_masked || "")}</td>
                <td>${
                    position.payment_entry
                        ? `<a href="/app/payment-entry/${encodeURIComponent(position.payment_entry)}">${frappe.utils.escape_html(position.payment_entry)}</a>`
                        : ""
                }</td>
                <td class="text-danger">${frappe.utils.escape_html(position.processing_error || "")}</td>
            </tr>`,
        )
        .join("");
    wrapper.html(`<table class="table table-bordered table-condensed">
        <thead><tr>
            <th>${__("Position")}</th><th>${__("Status")}</th><th>${__("Zahler")}</th><th>${__("Rechnung")}</th>
            <th>${__("Fälligkeit")}</th><th>${__("Betrag")}</th><th>${__("Mandatsreferenz")}</th><th>${__("IBAN")}</th>
            <th>${__("Zahlung")}</th><th>${__("Prüfergebnis")}</th>
        </tr></thead>
        <tbody>${rows}</tbody>
    </table>`);
}

function erpverein_show_upload_window(frm) {
    if (frm.is_new() || !frm.doc.upload_deadline || !["Entwurf", "Geprueft", "Exportiert"].includes(frm.doc.status)) {
        return;
    }
    const passed = (frm.doc.__onload || {}).upload_deadline_passed;
    const window_text = __("Upload bei der HVB frühestens am {0}, spätestens am {1} bis 07:00 Uhr.", [
        frappe.datetime.str_to_user(frm.doc.upload_earliest),
        frappe.datetime.str_to_user(frm.doc.upload_deadline),
    ]);
    if (passed && ["Entwurf", "Geprueft"].includes(frm.doc.status)) {
        frm.dashboard.set_headline_alert(
            `${window_text} ${__("Die Frist ist abgelaufen. Storniere den Lauf und bereite die Lastschriften neu vor.")}`,
            "red",
        );
    } else if (passed && frm.doc.status === "Exportiert") {
        frm.dashboard.set_headline_alert(
            `${window_text} ${__("Die Frist ist abgelaufen. Erfasse die Bankreferenz, wenn die Datei hochgeladen ist.")}`,
            "orange",
        );
    } else if (frm.doc.status !== "Bankreferenz erfasst") {
        frm.dashboard.set_headline_alert(window_text, "blue");
    }
}
