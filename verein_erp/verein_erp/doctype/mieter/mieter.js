frappe.ui.form.on("Mieter", {
  refresh(frm) {
    if (!frm.is_new()) {
      frm.add_custom_button(__("Kunde erstellen/aktualisieren"), () => {
        frappe.call({
          method: "verein_erp.api.mieter_customer_sync.sync_customer",
          args: { mieter: frm.doc.name },
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

      frm.add_custom_button(__("Mietabrechnung vorbereiten"), () => {
        frappe.call({
          method: "verein_erp.api.rental_subscription_generation.create_run",
          args: { mieter: frm.doc.name },
          freeze: true,
          freeze_message: __("Mietabrechnung wird vorbereitet..."),
          callback(r) {
            if (!r.exc && r.message?.run) {
              frappe.set_route("Form", "Mietabrechnung", r.message.run);
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

    if (!frm.is_new() && frm.doc.sepa_mandat) {
      frm.add_custom_button(__("SEPA-Mandat öffnen"), () => {
        frappe.set_route("Form", "SEPA Mandat", frm.doc.sepa_mandat);
      });
    }
  },
});
