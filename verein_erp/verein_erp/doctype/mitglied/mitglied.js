frappe.ui.form.on("Mitglied", {
  refresh(frm) {
    if (!frm.is_new() && frm.doc.customer) {
      frm.add_custom_button(__("Customer öffnen"), () => {
        frappe.set_route("Form", "Customer", frm.doc.customer);
      });
    }
  },
});
