from verein_erp.custom_fields import sync_custom_fields


def after_install() -> None:
    sync_custom_fields()
