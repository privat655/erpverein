from erpverein.custom_fields import sync_custom_fields
from erpverein.setup_data import sync_setup_data


def before_tests() -> None:
    sync_custom_fields()
    sync_setup_data()
