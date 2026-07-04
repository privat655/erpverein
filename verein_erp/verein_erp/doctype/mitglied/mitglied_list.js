frappe.listview_settings["Mitglied"] = {
  onload(listview) {
    listview.page.add_inner_button(__("Customers fuer unverknuepfte Mitglieder erstellen"), () => {
      frappe.call({
        method: "verein_erp.api.mitglied_customer_sync.sync_all_unlinked",
        freeze: true,
        freeze_message: __("Customers werden synchronisiert..."),
        callback(r) {
          if (!r.exc) {
            const result = r.message || {};
            listview.refresh();
            frappe.msgprint(
              __(
                "Verarbeitet: {0}. Erstellt: {1}. Synchronisiert: {2}. Fehler: {3}.",
                [result.total || 0, result.created || 0, result.synced || 0, (result.errors || []).length]
              )
            );
          }
        },
      });
    });

    listview.page.add_inner_button(__("Subscription Lauf erstellen"), () => {
      const checked = listview.get_checked_items ? listview.get_checked_items() : [];
      const mitglieder = (checked || []).map((row) => row.name).filter(Boolean);
      const createRun = () => {
        frappe.call({
          method: "verein_erp.api.subscription_generation.create_run",
          args: { mitglieder_json: JSON.stringify(mitglieder) },
          freeze: true,
          freeze_message: __("Subscription Lauf wird erstellt..."),
          callback(r) {
            if (!r.exc && r.message?.run) {
              frappe.set_route("Form", "Mitglied Subscription Lauf", r.message.run);
            }
          },
        });
      };

      if (mitglieder.length) {
        createRun();
      } else {
        frappe.confirm(__("Keine Mitglieder ausgewaehlt. Lauf fuer alle abrechnungspflichtigen Mitglieder erstellen?"), createRun);
      }
    });
  },
};
