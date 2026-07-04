from verein_erp.custom_fields import sync_custom_fields
from verein_erp.setup_data import sync_setup_data


def after_install() -> None:
    sync_custom_fields()
    sync_setup_data()
