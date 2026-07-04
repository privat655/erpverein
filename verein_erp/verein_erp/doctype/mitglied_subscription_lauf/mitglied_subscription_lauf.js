frappe.ui.form.on("Mitglied Subscription Lauf", {
  refresh(frm) {
    if (frm.is_new()) {
      return;
    }

    frm.add_custom_button(__("Vorschau erzeugen"), () => {
      frappe.call({
        method: "verein_erp.api.subscription_generation.generate_preview",
        args: { run: frm.doc.name },
        freeze: true,
        freeze_message: __("Subscription-Vorschau wird erzeugt..."),
        callback(r) {
          if (!r.exc) {
            frm.reload_doc();
            const result = r.message || {};
            frappe.msgprint(__("Vorschau: {0} Zeilen, Fehler: {1}.", [result.total || 0, result.errors || 0]));
          }
        },
      });
    });

    if (["Vorschau erstellt", "Teilweise fehlgeschlagen"].includes(frm.doc.status)) {
      frm.add_custom_button(__("Subscriptions erstellen"), () => {
        frappe.confirm(
          __("Jetzt Subscriptions erstellen? ERPNext kann dabei sofort Sales Invoices fuer vergangene Perioden erzeugen."),
          () => {
            frappe.call({
              method: "verein_erp.api.subscription_generation.create_subscriptions",
              args: { run: frm.doc.name },
              freeze: true,
              freeze_message: __("Subscriptions werden erstellt..."),
              callback(r) {
                if (!r.exc) {
                  frm.reload_doc();
                  const result = r.message || {};
                  frappe.msgprint(
                    __("Erstellt: {0}. Uebersprungen: {1}. Fehler: {2}.", [
                      result.created || 0,
                      result.skipped || 0,
                      result.errors || 0,
                    ])
                  );
                }
              },
            });
          }
        );
      });
    }
  },
});
