frappe.ui.form.on("Mietabrechnung", {
  refresh(frm) {
    frm.add_custom_button(__("Vorschau anzeigen"), () => {
      save_if_needed(frm).then(() => {
        frappe.call({
          method: "verein_erp.api.rental_subscription_generation.generate_preview",
          args: { run: frm.doc.name },
          freeze: true,
          freeze_message: __("Vorschau wird aktualisiert..."),
          callback(r) {
            if (!r.exc) {
              frm.reload_doc();
              const result = r.message || {};
              frappe.msgprint(__("Vorschau aktualisiert: {0} Zeilen, {1} Fehler.", [result.total || 0, result.errors || 0]));
            }
          },
        });
      });
    });

    if (["Vorschau erstellt", "Teilweise fehlgeschlagen"].includes(frm.doc.status)) {
      frm.add_custom_button(__("Mieten abrechnen"), () => {
        frappe.confirm(
          __("Mietabrechnung starten? Dabei koennen sofort Rechnungen fuer vergangene Zeitraeume erstellt werden."),
          () => {
            save_if_needed(frm).then(() => {
              frappe.call({
                method: "verein_erp.api.rental_subscription_generation.create_subscriptions",
                args: { run: frm.doc.name },
                freeze: true,
                freeze_message: __("Mietabrechnung wird gestartet..."),
                callback(r) {
                  if (!r.exc) {
                    frm.reload_doc();
                    frappe.msgprint(__("Mietabrechnung wurde im Hintergrund gestartet. Bitte aktualisieren Sie den Datensatz, um das Ergebnis zu sehen."));
                  }
                },
              });
            });
          }
        );
      });
    }

    if (frm.doc.status === "In Bearbeitung") {
      frm.add_custom_button(__("Status aktualisieren"), () => frm.reload_doc());
    }
  },
});

function save_if_needed(frm) {
  if (frm.is_new() || frm.is_dirty()) {
    return frm.save();
  }

  return Promise.resolve();
}
