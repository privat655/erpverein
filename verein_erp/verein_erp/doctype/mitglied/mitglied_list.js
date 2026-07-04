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
  },
};
