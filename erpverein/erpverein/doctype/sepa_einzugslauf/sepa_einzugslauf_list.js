frappe.listview_settings["SEPA Einzugslauf"] = {
    add_fields: ["status", "collection_date", "total_amount", "currency"],

    get_indicator(doc) {
        const colors = {
            Entwurf: "orange",
            Geprueft: "blue",
            Exportiert: "purple",
            "Bankreferenz erfasst": "cyan",
            "Bankgutschrift erkannt": "yellow",
            Abgestimmt: "green",
            "Teilweise zurueckbelastet": "orange",
            Abgeschlossen: "darkgrey",
            "Manuelle Pruefung": "red",
            Storniert: "gray",
        };
        return [__(doc.status), colors[doc.status] || "gray", `status,=,${doc.status}`];
    },

    primary_action() {
        erpverein_prepare_collection();
    },

    onload(listview) {
        listview.page.add_inner_button(__("Fällige Lastschriften vorbereiten"), () => erpverein_prepare_collection(listview));
    },
};

function erpverein_prepare_collection(listview) {
    const dialog = new frappe.ui.Dialog({
        title: __("Fällige Lastschriften vorbereiten"),
        fields: [
            {
                fieldtype: "Link",
                fieldname: "company",
                label: __("Unternehmen"),
                options: "Company",
                reqd: 1,
                default: frappe.defaults.get_user_default("Company"),
            },
            {
                fieldtype: "Date",
                fieldname: "cutoff_date",
                label: __("Stichtag"),
                reqd: 1,
                default: frappe.datetime.add_months(frappe.datetime.month_start(), 1),
                description: __("ERPverein sucht Lastschriftzeilen mit Fälligkeit bis zu diesem Tag."),
            },
            {
                fieldtype: "Check",
                fieldname: "single_booking",
                label: __("Einzelbuchung (BtchBookg = false)"),
                description: __("Nur im Einzelfall. Standard ist die Sammelbuchung."),
            },
        ],
        primary_action_label: __("Vorschau anzeigen"),
        primary_action(values) {
            frappe.call({
                method: "erpverein.api.sepa_collection.get_preview",
                args: {
                    company: values.company,
                    cutoff_date: values.cutoff_date,
                    batch_booking: values.single_booking ? 0 : null,
                },
                freeze: true,
                freeze_message: __("Vorschau wird erstellt..."),
                callback(r) {
                    if (r.exc || !r.message) {
                        return;
                    }
                    dialog.hide();
                    erpverein_show_collection_preview(listview, r.message);
                },
            });
        },
    });
    dialog.show();
}

function erpverein_show_collection_preview(listview, preview) {
    const ready = preview.rows.filter((row) => row.status === "Bereit");
    const others = preview.rows.filter((row) => row.status !== "Bereit");
    const dialog = new frappe.ui.Dialog({
        title: __("Fällige Lastschriften bis {0}", [frappe.datetime.str_to_user(preview.cutoff_date)]),
        size: "extra-large",
        fields: [
            { fieldtype: "HTML", fieldname: "summary" },
            {
                fieldtype: "Table",
                fieldname: "rows",
                label: __("Einzugsbereite Zeilen"),
                cannot_add_rows: true,
                cannot_delete_rows: true,
                in_place_edit: true,
                data: ready.map((row) => ({
                    selected: 1,
                    schedule_row: row.schedule_row,
                    invoice: row.invoice,
                    customer_name: row.customer_name || row.customer,
                    due_date: row.due_date,
                    collection_date: row.collection_date,
                    amount: row.amount,
                    iban: row.iban_masked,
                })),
                fields: [
                    { fieldtype: "Check", fieldname: "selected", label: __("Einziehen"), in_list_view: 1, columns: 1 },
                    { fieldtype: "Data", fieldname: "schedule_row", hidden: 1 },
                    { fieldtype: "Data", fieldname: "invoice", label: __("Rechnung"), in_list_view: 1, read_only: 1, columns: 2 },
                    { fieldtype: "Data", fieldname: "customer_name", label: __("Kunde"), in_list_view: 1, read_only: 1, columns: 2 },
                    { fieldtype: "Date", fieldname: "due_date", label: __("Fälligkeit"), in_list_view: 1, read_only: 1, columns: 1 },
                    { fieldtype: "Date", fieldname: "collection_date", label: __("Einzug"), in_list_view: 1, read_only: 1, columns: 1 },
                    { fieldtype: "Currency", fieldname: "amount", label: __("Betrag"), in_list_view: 1, read_only: 1, columns: 2 },
                    { fieldtype: "Data", fieldname: "iban", label: __("IBAN"), in_list_view: 1, read_only: 1, columns: 1 },
                ],
            },
            { fieldtype: "HTML", fieldname: "others" },
        ],
        primary_action_label: ready.length ? __("Einzugsläufe erzeugen") : __("Schließen"),
        primary_action(values) {
            if (!ready.length) {
                dialog.hide();
                return;
            }
            const selected = (values.rows || []).filter((row) => row.selected).map((row) => row.schedule_row);
            if (!selected.length) {
                frappe.msgprint(__("Bitte mindestens eine Zeile wählen."));
                return;
            }
            frappe.call({
                method: "erpverein.api.sepa_collection.create_runs",
                args: {
                    company: preview.company,
                    cutoff_date: preview.cutoff_date,
                    preview_hash: preview.preview_hash,
                    batch_booking: preview.batch_booking,
                    selected_rows: selected.length === ready.length ? null : JSON.stringify(selected),
                },
                freeze: true,
                freeze_message: __("Einzugsläufe werden erzeugt..."),
                callback(r) {
                    if (r.exc || !r.message) {
                        return;
                    }
                    dialog.hide();
                    const result = r.message;
                    frappe.msgprint(
                        __("Einzugsläufe: {0}. Positionen erzeugt: {1}, übersprungen: {2}, fehlgeschlagen: {3}.", [
                            result.runs.join(", ") || "-",
                            result.created,
                            result.skipped,
                            result.failed,
                        ]),
                    );
                    if (listview) {
                        listview.refresh();
                    }
                    if (result.runs.length === 1) {
                        frappe.set_route("Form", "SEPA Einzugslauf", result.runs[0]);
                    }
                },
            });
        },
    });

    const runs = preview.runs
        .map(
            (run) => `<tr>
                <td>${frappe.datetime.str_to_user(run.collection_date)}</td>
                <td class="text-right">${run.count}</td>
                <td class="text-right">${format_currency(run.total, "EUR")}</td>
                <td>${frappe.datetime.str_to_user(run.upload_earliest)}</td>
                <td>${frappe.datetime.str_to_user(run.upload_deadline)} ${__("07:00 Uhr")}</td>
            </tr>`,
        )
        .join("");
    dialog.fields_dict.summary.$wrapper.html(`
        <p>${__("Bereit: {0}. Konflikte: {1}. Schon in einem Lauf: {2}.", [
            preview.ready_count,
            preview.conflict_count,
            preview.existing_count,
        ])}</p>
        <p>${preview.batch_booking ? __("Sammelbuchung") : __("Einzelbuchung")}. ${__("Go-live-Stichtag")}: ${frappe.datetime.str_to_user(
            preview.go_live_date,
        )}. ${__("Frühestes Einzugsdatum")}: ${frappe.datetime.str_to_user(preview.earliest_collection_date)}.</p>
        ${
            runs
                ? `<table class="table table-bordered table-condensed">
                    <thead><tr>
                        <th>${__("Einzugsdatum")}</th><th>${__("Positionen")}</th><th>${__("Summe")}</th>
                        <th>${__("Upload frühestens")}</th><th>${__("Upload spätestens")}</th>
                    </tr></thead><tbody>${runs}</tbody>
                </table>`
                : `<p>${__("Es gibt keine einzugsbereite Lastschriftzeile.")}</p>`
        }
    `);
    if (!ready.length) {
        dialog.fields_dict.rows.$wrapper.hide();
    }
    dialog.fields_dict.others.$wrapper.html(
        others.length
            ? `<p><b>${__("Nicht einzugsbereit")}</b></p>
            <table class="table table-bordered table-condensed">
                <thead><tr>
                    <th>${__("Rechnung")}</th><th>${__("Kunde")}</th><th>${__("Fälligkeit")}</th>
                    <th>${__("Betrag")}</th><th>${__("Grund")}</th>
                </tr></thead>
                <tbody>${others
                    .map(
                        (row) => `<tr>
                            <td>${frappe.utils.escape_html(row.invoice)}</td>
                            <td>${frappe.utils.escape_html(row.customer_name || row.customer || "")}</td>
                            <td>${frappe.datetime.str_to_user(row.due_date)}</td>
                            <td class="text-right">${format_currency(row.amount, "EUR")}</td>
                            <td>${frappe.utils.escape_html(row.reason || "")}</td>
                        </tr>`,
                    )
                    .join("")}</tbody>
            </table>`
            : "",
    );
    dialog.show();
}
