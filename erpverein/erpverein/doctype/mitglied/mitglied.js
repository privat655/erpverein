frappe.ui.form.on("Mitglied", {
  refresh(frm) {
    if (!frm.is_new()) {
      frm.add_custom_button(__("Kunde, Adresse und Kontakt synchronisieren"), () => {
        frappe.call({
          method: "erpverein.api.mitglied_customer_sync.sync_customer",
          args: { mitglied: frm.doc.name },
          freeze: true,
          freeze_message: __("Kunde wird aktualisiert..."),
          callback(r) {
            if (!r.exc) {
              frm.reload_doc();
              frappe.show_alert({
                message: r.message?.created
                  ? __("Kunde wurde erstellt und aktualisiert.")
                  : __("Kunde wurde aktualisiert."),
                indicator: "green",
              });
            }
          },
        });
      });

      frm.add_custom_button(__("Beitragsabrechnung vorbereiten"), () => {
        frappe.call({
          method: "erpverein.api.subscription_generation.create_run",
          args: { mitglied: frm.doc.name },
          freeze: true,
          freeze_message: __("Beitragsabrechnung wird vorbereitet..."),
          callback(r) {
            if (!r.exc && r.message?.run) {
              frappe.set_route("Form", "Beitragsabrechnung", r.message.run);
            }
          },
        });
      });
    }

    if (!frm.is_new() && frm.doc.customer) {
      frm.add_custom_button(__("Kunde öffnen"), () => {
        frappe.set_route("Form", "Customer", frm.doc.customer);
      });
    }
  },
});
