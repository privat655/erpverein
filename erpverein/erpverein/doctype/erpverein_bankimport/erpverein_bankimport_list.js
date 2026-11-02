frappe.listview_settings["ERPverein Bankimport"] = {
    get_indicator(doc) {
        const colors = { Entwurf: "gray", Importiert: "green", Teilfehler: "orange", Fehler: "red" };
        return [__(doc.status), colors[doc.status] || "gray", `status,=,${doc.status}`];
    },
};
