from verein_erp.custom_fields import sync_custom_fields


def before_tests() -> None:
    sync_custom_fields()
