frappe.ui.form.on("ERPverein Bankimport", {
    refresh(frm) {
        if (frm.is_new()) {
            return;
        }
        erpverein_show_bank_import_intro(frm);
        frm.add_custom_button(__("Bankumsätze anzeigen"), () =>
            frappe.set_route("List", "Bank Transaction", { erpverein_import_batch: frm.doc.name }),
        );
        frm.add_custom_button(__("Banking-Bereich öffnen"), () => window.open("/banking"));
        if (["Entwurf", "Teilfehler", "Fehler"].includes(frm.doc.status)) {
            frm.add_custom_button(__("Erneut verarbeiten"), () => erpverein_reprocess_bank_import(frm)).addClass(
                "btn-primary",
            );
        }
    },
});

function erpverein_show_bank_import_intro(frm) {
    if (frm.doc.status === "Fehler") {
        frm.set_intro(
            __("Der Import ist fehlgeschlagen. Behebe den Grund unter Fehler und klicke dann „Erneut verarbeiten“."),
            "red",
        );
    } else if (frm.doc.status === "Teilfehler") {
        frm.set_intro(
            __("{0} Zeilen haben Fehler. Die übrigen Bankumsätze sind importiert. Prüfe die Tabelle Fehlerhafte Zeilen.", [
                frm.doc.error_count,
            ]),
            "orange",
        );
    } else if (frm.doc.status === "Importiert") {
        frm.set_intro(
            __("{0} neue Bankumsätze, {1} Duplikate, {2} vor dem Stichtag.", [
                frm.doc.new_transaction_count,
                frm.doc.duplicate_count,
                frm.doc.before_go_live_count,
            ]),
            "green",
        );
    }
}

function erpverein_reprocess_bank_import(frm) {
    frappe.confirm(
        __("Die Originaldatei erneut verarbeiten? Vorhandene Bankumsätze bleiben unverändert und entstehen nicht doppelt."),
        () => {
            frappe.call({
                method: "erpverein.api.bank_import.reprocess",
                args: { bank_import: frm.doc.name },
                freeze: true,
                freeze_message: __("Bankumsätze werden importiert..."),
                callback(r) {
                    if (r.exc) {
                        return;
                    }
                    const result = r.message || {};
                    frappe.msgprint(
                        __("Status: {0}. Neu: {1}, Duplikate: {2}, Fehler: {3}.", [
                            result.status,
                            result.new,
                            result.duplicates,
                            result.errors,
                        ]),
                    );
                    frm.reload_doc();
                },
            });
        },
    );
}
