frappe.ui.form.on("Bank Transaction", {
    refresh(frm) {
        if (frm.doc.docstatus !== 1) {
            return;
        }
        erpverein_show_match_status(frm);
        const open = flt(frm.doc.unallocated_amount) > 0.005;
        if (frm.doc.erpverein_match_status === "Vorschlag" && open) {
            const group = __("ERPverein");
            frm.add_custom_button(__("Vorschlag bestätigen"), () => erpverein_confirm_transfer_proposal(frm), group);
            frm.add_custom_button(__("Vorschlag verwerfen"), () => erpverein_reject_transfer_proposal(frm), group);
        }
        if (frm.doc.erpverein_match_status === "Manuelle Pruefung" && open && flt(frm.doc.withdrawal) > 0) {
            frm.add_custom_button(__("Rücklastschrift zuordnen"), () => erpverein_confirm_return(frm), __("ERPverein"));
        }
        if (open && frm.doc.erpverein_match_status) {
            frm.add_custom_button(__("Arbeitsvorrat"), () => frappe.set_route("query-report", "ERPverein Arbeitsvorrat"));
        }
    },
});

function erpverein_show_match_status(frm) {
    const note = frappe.utils.escape_html(frm.doc.erpverein_match_note || "");
    const status = frm.doc.erpverein_match_status;
    if (status === "Vorschlag") {
        frm.dashboard.set_headline_alert(`${__("ERPverein schlägt eine Zuordnung vor.")} ${note}`, "orange");
    } else if (status === "Manuelle Pruefung") {
        frm.dashboard.set_headline_alert(`${__("Manuelle Prüfung.")} ${note}`, "red");
    } else if (status === "Abgeglichen" && note) {
        frm.dashboard.set_headline_alert(note, "green");
    } else if (status === "Offen" && note) {
        frm.dashboard.set_headline_alert(note, "blue");
    }
}

function erpverein_confirm_transfer_proposal(frm) {
    frappe.call({
        method: "erpverein.api.transfer_settlement.get_proposal",
        args: { bank_transaction: frm.doc.name },
        freeze: true,
        callback(r) {
            if (r.exc || !r.message) {
                return;
            }
            const proposal = r.message;
            if (!proposal.bookable) {
                frappe.msgprint({
                    title: __("Kein buchbarer Vorschlag"),
                    message: frappe.utils.escape_html(proposal.note),
                    indicator: "red",
                });
                return;
            }
            const rows = proposal.allocations
                .map(
                    (row) => `<tr>
                        <td>${frappe.utils.escape_html(row.customer)}</td>
                        <td>${frappe.utils.escape_html(row.invoice)}</td>
                        <td>${frappe.utils.escape_html(row.payment_term || "")}</td>
                        <td class="text-right">${format_currency(row.amount, frm.doc.currency)}</td>
                    </tr>`,
                )
                .join("");
            const rest = proposal.rest > 0.005
                ? `<p>${__("Rest am Bankumsatz: {0}. Er bleibt offen.", [format_currency(proposal.rest, frm.doc.currency)])}</p>`
                : "";
            const dialog = new frappe.ui.Dialog({
                title: __("Vorschlag bestätigen"),
                size: "large",
                fields: [{ fieldtype: "HTML", fieldname: "proposal" }],
                primary_action_label: __("Zahlung buchen"),
                primary_action() {
                    frappe.call({
                        method: "erpverein.api.transfer_settlement.confirm_proposal",
                        args: { bank_transaction: frm.doc.name, proposal_hash: proposal.proposal_hash },
                        freeze: true,
                        freeze_message: __("Zahlung wird gebucht..."),
                        callback(result) {
                            if (result.exc || !result.message) {
                                return;
                            }
                            dialog.hide();
                            frappe.show_alert({
                                message: __("Gebucht: {0}", [result.message.payments.join(", ")]),
                                indicator: "green",
                            });
                            frm.reload_doc();
                        },
                    });
                },
            });
            dialog.fields_dict.proposal.$wrapper.html(`
                <p>${frappe.utils.escape_html(proposal.note)}</p>
                <table class="table table-bordered table-condensed">
                    <thead><tr>
                        <th>${__("Kunde")}</th><th>${__("Rechnung")}</th>
                        <th>${__("Zahlungsbedingung")}</th><th>${__("Betrag")}</th>
                    </tr></thead>
                    <tbody>${rows}</tbody>
                </table>
                ${rest}
                <p>${__("ERPverein prüft den Vorschlag vor dem Buchen erneut.")}</p>
            `);
            dialog.show();
        },
    });
}

function erpverein_reject_transfer_proposal(frm) {
    frappe.prompt(
        [{ fieldtype: "Small Text", fieldname: "reason", label: __("Grund"), reqd: 1 }],
        (values) => {
            frappe.call({
                method: "erpverein.api.transfer_settlement.reject_proposal",
                args: { bank_transaction: frm.doc.name, reason: values.reason },
                freeze: true,
                callback(r) {
                    if (!r.exc) {
                        frm.reload_doc();
                    }
                },
            });
        },
        __("Vorschlag verwerfen"),
        __("Verwerfen"),
    );
}

function erpverein_confirm_return(frm) {
    frappe.prompt(
        [
            {
                fieldtype: "HTML",
                fieldname: "hint",
                options: `<p>${__(
                    "Wähle die bezahlte Einzugsposition, die die Bank zurückgegeben hat. ERPverein bucht die Gegenbuchung wie beim automatischen Abgleich: Hauptbetrag gleich Positionsbetrag, Gebühr gleich Rest. Die Belastung darf nicht kleiner sein als der Positionsbetrag.",
                )}</p>`,
            },
            {
                fieldtype: "Link",
                fieldname: "position",
                label: __("Einzugsposition"),
                options: "SEPA Einzugsposition",
                reqd: 1,
                get_query: () => ({ filters: { status: "Bezahlt" } }),
            },
            { fieldtype: "Small Text", fieldname: "note", label: __("Notiz (optional)") },
        ],
        (values) => {
            frappe.call({
                method: "erpverein.api.sepa_return.confirm_return",
                args: { bank_transaction: frm.doc.name, position: values.position, note: values.note },
                freeze: true,
                freeze_message: __("Gegenbuchung wird gebucht..."),
                callback(r) {
                    if (r.exc || !r.message) {
                        return;
                    }
                    frappe.show_alert({
                        message: __("Gebucht: {0}", [r.message.journal_entry]),
                        indicator: "green",
                    });
                    frm.reload_doc();
                },
            });
        },
        __("Rücklastschrift zuordnen"),
        __("Gegenbuchung buchen"),
    );
}
