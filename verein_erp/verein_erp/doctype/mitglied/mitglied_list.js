frappe.listview_settings["Mitglied"] = {
  onload(listview) {
    listview.page.add_inner_button(__("Fehlende Kunden erstellen"), () => {
      frappe.call({
        method: "verein_erp.api.mitglied_customer_sync.sync_all_unlinked",
        freeze: true,
        freeze_message: __("Kunden werden erstellt und aktualisiert..."),
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

    listview.page.add_inner_button(__("Beitragsabrechnung vorbereiten"), () => {
      const checked = listview.get_checked_items ? listview.get_checked_items() : [];
      const mitglieder = (checked || []).map((row) => row.name).filter(Boolean);
      const createRun = () => {
        frappe.call({
          method: "verein_erp.api.subscription_generation.create_run",
          args: { mitglieder_json: JSON.stringify(mitglieder) },
          freeze: true,
          freeze_message: __("Beitragsabrechnung wird vorbereitet..."),
          callback(r) {
            if (!r.exc && r.message?.run) {
              frappe.set_route("Form", "Beitragsabrechnung", r.message.run);
            }
          },
        });
      };

      if (mitglieder.length) {
        createRun();
      } else {
        frappe.confirm(__("Keine Mitglieder ausgewaehlt. Beitragsabrechnung fuer alle abrechnungspflichtigen Mitglieder vorbereiten?"), createRun);
      }
    });
  },
};
