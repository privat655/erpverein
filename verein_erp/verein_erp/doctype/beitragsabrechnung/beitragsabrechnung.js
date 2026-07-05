frappe.ui.form.on("Beitragsabrechnung", {
  refresh(frm) {
    frm.add_custom_button(__("Vorschau anzeigen"), () => {
      frm.save().then(() => {
        frappe.call({
          method: "verein_erp.api.subscription_generation.generate_preview",
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
      frm.add_custom_button(__("Beiträge abrechnen"), () => {
        frappe.confirm(
          __("Beitragsabrechnung starten? Dabei koennen sofort Rechnungen fuer vergangene Zeitraeume erstellt werden."),
          () => {
            frm.save().then(() => {
              frappe.call({
                method: "verein_erp.api.subscription_generation.create_subscriptions",
                args: { run: frm.doc.name },
                freeze: true,
                freeze_message: __("Beitraege werden abgerechnet..."),
                callback(r) {
                  if (!r.exc) {
                    frm.reload_doc();
                    const result = r.message || {};
                    frappe.msgprint(
                      __("Abgerechnet: {0}. Uebersprungen: {1}. Fehler: {2}.", [
                        result.created || 0,
                        result.skipped || 0,
                        result.errors || 0,
                      ])
                    );
                  }
                },
              });
            });
          }
        );
      });
    }
  },
});
