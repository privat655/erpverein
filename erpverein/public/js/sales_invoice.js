frappe.ui.form.on("Sales Invoice", {
    refresh(frm) {
        if (frm.doc.docstatus !== 1 || !frm.doc.subscription) {
            return;
        }
        const group = __("ERPverein");
        frm.add_custom_button(__("Zahlungsplan bearbeiten"), () => erpverein_edit_payment_schedule(frm), group);
        frm.add_custom_button(__("Zahlungsplan aus Mandat neu erzeugen"), () => erpverein_regenerate_payment_schedule(frm), group);
        if (["Ausstehend", "Erneut senden", "Manuell bestaetigt"].includes(frm.doc.erpverein_prenotification_status)) {
            frm.add_custom_button(__("Vorabankündigung manuell bestätigen"), () => erpverein_confirm_prenotification(frm), group);
        }
    },
});

function erpverein_edit_payment_schedule(frm) {
    frappe.call({
        method: "erpverein.api.payment_schedule.get_editor",
        args: { invoice: frm.doc.name },
        callback(r) {
            if (r.exc || !r.message) {
                return;
            }
            const editor = r.message;
            const dialog = new frappe.ui.Dialog({
                title: __("Zahlungsplan bearbeiten"),
                size: "large",
                fields: [
                    { fieldtype: "HTML", fieldname: "info" },
                    {
                        fieldtype: "Table",
                        fieldname: "rows",
                        label: __("Zahlungsplan"),
                        in_place_edit: true,
                        data: editor.rows.map((row) => ({
                            schedule_row: row.name,
                            due_date: row.due_date,
                            payment_amount: row.payment_amount,
                            mode_of_payment: row.mode_of_payment,
                            locked: row.locked ? 1 : 0,
                        })),
                        fields: [
                            { fieldtype: "Data", fieldname: "schedule_row", hidden: 1 },
                            { fieldtype: "Date", fieldname: "due_date", label: __("Fälligkeit"), in_list_view: 1, reqd: 1 },
                            { fieldtype: "Currency", fieldname: "payment_amount", label: __("Betrag"), in_list_view: 1, reqd: 1 },
                            {
                                fieldtype: "Select",
                                fieldname: "mode_of_payment",
                                label: __("Zahlungsweise"),
                                options: editor.modes.join("\n"),
                                in_list_view: 1,
                                reqd: 1,
                            },
                            { fieldtype: "Check", fieldname: "locked", label: __("Gesperrt"), in_list_view: 1, read_only: 1 },
                        ],
                    },
                ],
                primary_action_label: __("Speichern"),
                primary_action(values) {
                    const rows = (values.rows || []).map((row) => ({
                        name: row.schedule_row || null,
                        due_date: row.due_date,
                        payment_amount: row.payment_amount,
                        mode_of_payment: row.mode_of_payment,
                    }));
                    frappe.call({
                        method: "erpverein.api.payment_schedule.update_schedule",
                        args: { invoice: frm.doc.name, rows: JSON.stringify(rows), modified: editor.modified },
                        freeze: true,
                        freeze_message: __("Zahlungsplan wird gespeichert..."),
                        callback(res) {
                            if (!res.exc) {
                                dialog.hide();
                                frm.reload_doc();
                            }
                        },
                    });
                },
            });
            const locked = editor.rows.filter((row) => row.locked);
            dialog.fields_dict.info.$wrapper.html(`
                <p>${__("Rechnungsbetrag")}: <b>${format_currency(editor.total, editor.currency)}</b></p>
                <p>${__("Die Summe der Zeilen muss dem Rechnungsbetrag entsprechen. Gesperrte Zeilen bleiben unverändert.")}</p>
                ${
                    locked.length
                        ? `<ul>${locked
                              .map(
                                  (row) =>
                                      `<li>${frappe.datetime.str_to_user(row.due_date)}: ${frappe.utils.escape_html(row.lock_reason || "")}</li>`,
                              )
                              .join("")}</ul>`
                        : ""
                }
            `);
            dialog.show();
        },
    });
}

function erpverein_regenerate_payment_schedule(frm) {
    frappe.call({
        method: "erpverein.api.payment_schedule.preview_regeneration",
        args: { invoice: frm.doc.name },
        callback(r) {
            if (r.exc || !r.message) {
                return;
            }
            const preview = r.message;
            if (!preview.allowed) {
                frappe.msgprint(frappe.utils.escape_html(preview.reason || ""));
                return;
            }
            const message = `
                <p>${__("Bisheriger Zahlungsplan")}</p>${erpverein_schedule_table(preview.current_rows, frm.doc.currency)}
                <p>${__("Neuer Zahlungsplan")} (${frappe.utils.escape_html(preview.origin)})</p>${erpverein_schedule_table(preview.rows, frm.doc.currency)}
                ${preview.note ? `<p>${frappe.utils.escape_html(preview.note)}</p>` : ""}
                <p>${__("Neuen Zahlungsplan übernehmen?")}</p>
            `;
            frappe.confirm(message, () => {
                frappe.call({
                    method: "erpverein.api.payment_schedule.apply_regeneration",
                    args: { invoice: frm.doc.name, preview_hash: preview.preview_hash },
                    freeze: true,
                    freeze_message: __("Zahlungsplan wird neu erzeugt..."),
                    callback(res) {
                        if (!res.exc) {
                            frm.reload_doc();
                        }
                    },
                });
            });
        },
    });
}

function erpverein_confirm_prenotification(frm) {
    frappe.confirm(__("Hat der Zahler die Rechnung mit dem Zahlungsplan als Vorabankündigung erhalten?"), () => {
        frappe.call({
            method: "erpverein.api.payment_schedule.confirm_prenotification",
            args: { invoice: frm.doc.name },
            callback(r) {
                if (!r.exc) {
                    frm.reload_doc();
                }
            },
        });
    });
}

function erpverein_schedule_table(rows, currency) {
    const body = (rows || [])
        .map(
            (row) => `<tr>
                <td>${frappe.datetime.str_to_user(row.due_date)}</td>
                <td class="text-right">${format_currency(row.payment_amount, currency)}</td>
                <td>${frappe.utils.escape_html(row.mode_of_payment || "")}</td>
            </tr>`,
        )
        .join("");
    return `<table class="table table-bordered table-condensed">
        <thead><tr><th>${__("Fälligkeit")}</th><th>${__("Betrag")}</th><th>${__("Zahlungsweise")}</th></tr></thead>
        <tbody>${body}</tbody>
    </table>`;
}
