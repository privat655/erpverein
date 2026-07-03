from verein_erp.custom_fields import remove_custom_fields


def before_uninstall() -> None:
    remove_custom_fields()
