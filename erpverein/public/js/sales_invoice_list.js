frappe.listview_settings["Sales Invoice"] = frappe.listview_settings["Sales Invoice"] || {};

(() => {
    const settings = frappe.listview_settings["Sales Invoice"];
    const previous_onload = settings.onload;

    settings.onload = function (listview) {
        if (previous_onload) {
            previous_onload.call(this, listview);
        }
        listview.page.add_menu_item(__("ERPverein: Zahlungsplan nachtragen"), () => preview_backfill(listview));
    };

    function preview_backfill(listview) {
        frappe.call({
            method: "erpverein.api.payment_schedule.preview_backfill",
            freeze: true,
            freeze_message: __("Vorschau wird erstellt..."),
            callback(r) {
                if (r.exc || !r.message) {
                    return;
                }
                const preview = r.message;
                const allowed = preview.invoices.filter((invoice) => invoice.allowed);
                const skipped = preview.invoices.filter((invoice) => !invoice.allowed);
                if (!preview.invoices.length) {
                    frappe.msgprint(__("Es gibt keine gebuchte ERPverein-Rechnung ohne Zahlungsplan."));
                    return;
                }
                const rows = preview.invoices
                    .map(
                        (invoice) => `<tr>
                            <td>${frappe.utils.escape_html(invoice.invoice)}</td>
                            <td>${invoice.allowed ? frappe.utils.escape_html(invoice.origin) : __("übersprungen")}</td>
                            <td>${frappe.utils.escape_html(invoice.allowed ? invoice.note || "" : invoice.reason || "")}</td>
                        </tr>`,
                    )
                    .join("");
                const message = `
                    <p>${__("Neuer Zahlungsplan für {0} Rechnungen, {1} übersprungen.", [allowed.length, skipped.length])}</p>
                    <table class="table table-bordered table-condensed">
                        <thead><tr><th>${__("Rechnung")}</th><th>${__("Herkunft")}</th><th>${__("Hinweis")}</th></tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                    <p>${__("Zahlungspläne jetzt nachtragen?")}</p>
                `;
                frappe.confirm(message, () => {
                    frappe.call({
                        method: "erpverein.api.payment_schedule.apply_backfill",
                        args: { preview_hash: preview.preview_hash },
                        freeze: true,
                        freeze_message: __("Zahlungspläne werden nachgetragen..."),
                        callback(res) {
                            if (res.exc || !res.message) {
                                return;
                            }
                            const result = res.message;
                            frappe.msgprint(
                                __("Nachgetragen: {0}, übersprungen: {1}, fehlgeschlagen: {2}.", [
                                    result.applied,
                                    result.skipped,
                                    result.failed,
                                ]),
                            );
                            listview.refresh();
                        },
                    });
                });
            },
        });
    }
})();
