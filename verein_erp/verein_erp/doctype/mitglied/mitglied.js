frappe.ui.form.on("Mitglied", {
  refresh(frm) {
    if (!frm.is_new()) {
      frm.add_custom_button(__("Customer erstellen/synchronisieren"), () => {
        frappe.call({
          method: "verein_erp.api.mitglied_customer_sync.sync_customer",
          args: { mitglied: frm.doc.name },
          freeze: true,
          freeze_message: __("Customer wird synchronisiert..."),
          callback(r) {
            if (!r.exc) {
              frm.reload_doc();
              frappe.show_alert({
                message: r.message?.created
                  ? __("Customer wurde erstellt und synchronisiert.")
                  : __("Customer wurde synchronisiert."),
                indicator: "green",
              });
            }
          },
        });
      });

      frm.add_custom_button(__("Subscription Lauf erstellen"), () => {
        frappe.call({
          method: "verein_erp.api.subscription_generation.create_run",
          args: { mitglied: frm.doc.name },
          freeze: true,
          freeze_message: __("Subscription Lauf wird erstellt..."),
          callback(r) {
            if (!r.exc && r.message?.run) {
              frappe.set_route("Form", "Mitglied Subscription Lauf", r.message.run);
            }
          },
        });
      });
    }

    if (!frm.is_new() && frm.doc.customer) {
      frm.add_custom_button(__("Customer öffnen"), () => {
        frappe.set_route("Form", "Customer", frm.doc.customer);
      });
    }
  },
});
