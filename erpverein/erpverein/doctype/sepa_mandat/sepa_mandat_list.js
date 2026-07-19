frappe.listview_settings["SEPA Mandat"] = {
  add_fields: ["planungsstatus", "naechster_einzugstermin", "einzugsintervall"],
  get_indicator(doc) {
    const indicators = {
      "Nicht konfiguriert": [__("Nicht konfiguriert"), "orange", "planungsstatus,=,Nicht konfiguriert"],
      Unvollstaendig: [__("Unvollstaendig"), "orange", "planungsstatus,=,Unvollstaendig"],
      Entwurf: [__("Entwurf"), "gray", "planungsstatus,=,Entwurf"],
      Inaktiv: [__("Inaktiv"), "gray", "planungsstatus,=,Inaktiv"],
      Geplant: [__("Geplant"), "green", "planungsstatus,=,Geplant"],
      "Einzug faellig": [__("Einzug faellig"), "red", "planungsstatus,=,Einzug faellig"],
      Beendet: [__("Beendet"), "gray", "planungsstatus,=,Beendet"],
    };
    return indicators[doc.planungsstatus];
  },
};
