from frappe.custom.doctype.custom_field.custom_field import create_custom_fields, delete_custom_fields

from erpverein.services.bank_statement_parser import CATEGORIES as BANK_CATEGORY_OPTIONS


CUSTOMER_MITGLIED_FIELDNAME = "erpverein_mitglied"
CUSTOMER_MIETER_FIELDNAME = "erpverein_mieter"
CUSTOMER_SYNC_STATE_FIELDNAME = "erpverein_sync_state"
BANK_ACCOUNT_MANAGED_FIELDNAME = "erpverein_managed"
BANK_ACCOUNT_SYNC_STATE_FIELDNAME = "erpverein_sync_state"
SUBSCRIPTION_MANAGED_FIELDNAME = "erpverein_managed"
SUBSCRIPTION_BILLING_KIND_FIELDNAME = "erpverein_billing_kind"
SUBSCRIPTION_GENERATION_KEY_FIELDNAME = "erpverein_generation_key"
SUBSCRIPTION_GENERATION_PAYLOAD_FIELDNAME = "erpverein_generation_payload"
SUBSCRIPTION_RUN_DOCTYPE_FIELDNAME = "erpverein_generation_run_doctype"
SUBSCRIPTION_RUN_FIELDNAME = "erpverein_generation_run"
SUBSCRIPTION_SOURCES_FIELDNAME = "erpverein_sources"
SALES_INVOICE_SECTION_FIELDNAME = "erpverein_payment_schedule_section"
SALES_INVOICE_SCHEDULE_ORIGIN_FIELDNAME = "erpverein_schedule_origin"
SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME = "erpverein_prenotification_status"
SALES_INVOICE_PRENOTIFICATION_SENT_AT_FIELDNAME = "erpverein_prenotification_sent_at"
SALES_INVOICE_SCHEDULE_NOTE_FIELDNAME = "erpverein_schedule_note"
SALES_INVOICE_COLUMN_FIELDNAME = "erpverein_payment_schedule_column"
SALES_INVOICE_BILLING_KIND_FIELDNAME = "erpverein_billing_kind"
SALES_INVOICE_PAYER_DOCTYPE_FIELDNAME = "erpverein_payer_doctype"
SALES_INVOICE_PAYER_FIELDNAME = "erpverein_payer"
SALES_INVOICE_SEPA_MANDATE_FIELDNAME = "erpverein_sepa_mandate"
SALES_INVOICE_SCHEDULE_FINGERPRINT_FIELDNAME = "erpverein_schedule_fingerprint"
PAYMENT_SCHEDULE_CHANGED_AT_FIELDNAME = "erpverein_changed_at"
BANK_ACCOUNT_IMPORT_ALIAS_FIELDNAME = "erpverein_bank_import_alias"
BANK_TRANSACTION_SECTION_FIELDNAME = "erpverein_bank_section"
BANK_TRANSACTION_CATEGORY_FIELDNAME = "erpverein_bank_category"
BANK_TRANSACTION_MATCH_STATUS_FIELDNAME = "erpverein_match_status"
BANK_TRANSACTION_VALUE_DATE_FIELDNAME = "erpverein_value_date"
BANK_TRANSACTION_END_TO_END_FIELDNAME = "erpverein_sepa_end_to_end_reference"
BANK_TRANSACTION_MANDATE_FIELDNAME = "erpverein_sepa_mandate_reference"
BANK_TRANSACTION_CREDITOR_FIELDNAME = "erpverein_sepa_creditor_identifier"
BANK_TRANSACTION_COLUMN_FIELDNAME = "erpverein_bank_column"
BANK_TRANSACTION_CUSTOMER_REFERENCE_FIELDNAME = "erpverein_customer_reference"
BANK_TRANSACTION_PRIMANOTA_FIELDNAME = "erpverein_primanota"
BANK_TRANSACTION_TEXT_KEY_FIELDNAME = "erpverein_text_key"
BANK_TRANSACTION_IMPORT_BATCH_FIELDNAME = "erpverein_import_batch"
BANK_TRANSACTION_SOURCE_ROW_FIELDNAME = "erpverein_source_row_number"
BANK_TRANSACTION_FINGERPRINT_FIELDNAME = "erpverein_source_fingerprint"
BANK_TRANSACTION_MATCH_NOTE_FIELDNAME = "erpverein_match_note"
BANK_MATCH_STATUS_OPTIONS = ("Offen", "Abgeglichen", "Vorschlag", "Manuelle Pruefung")
PAYMENT_ENTRY_COLLECTION_ITEM_FIELDNAME = "erpverein_sepa_collection_item"
PAYMENT_ENTRY_BANK_TRANSACTION_FIELDNAME = "erpverein_bank_transaction"


def get_custom_fields() -> dict:
    return {
        "Customer": [
            {
                "fieldname": CUSTOMER_MITGLIED_FIELDNAME,
                "label": "ERPverein Mitglied",
                "fieldtype": "Link",
                "options": "Mitglied",
                "insert_after": "customer_name",
                "description": "1:1 verknuepfter Mitglied-Datensatz aus ERPverein.",
                "no_copy": 1,
                "in_standard_filter": 1,
                "search_index": 1,
                "unique": 1,
            },
            {
                "fieldname": CUSTOMER_SYNC_STATE_FIELDNAME,
                "label": "ERPverein Sync-Status",
                "fieldtype": "Long Text",
                "insert_after": CUSTOMER_MITGLIED_FIELDNAME,
                "description": "Interner ERPverein-Snapshot fuer kontrollierte Synchronisation.",
                "hidden": 1,
                "no_copy": 1,
                "read_only": 1,
            },
            {
                "fieldname": CUSTOMER_MIETER_FIELDNAME,
                "label": "ERPverein Mieter",
                "fieldtype": "Link",
                "options": "Mieter",
                "insert_after": CUSTOMER_SYNC_STATE_FIELDNAME,
                "description": "1:1 verknuepfter Mieter-Datensatz aus ERPverein.",
                "no_copy": 1,
                "in_standard_filter": 1,
                "search_index": 1,
                "unique": 1,
            },
        ],
        "Bank Account": [
            {
                "fieldname": BANK_ACCOUNT_MANAGED_FIELDNAME,
                "label": "ERPverein verwaltet",
                "fieldtype": "Check",
                "insert_after": "party",
                "description": "Markiert ausschliesslich durch ERPverein angelegte Bankkonten.",
                "no_copy": 1,
                "read_only": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": BANK_ACCOUNT_SYNC_STATE_FIELDNAME,
                "label": "ERPverein Sync-Status",
                "fieldtype": "Long Text",
                "insert_after": BANK_ACCOUNT_MANAGED_FIELDNAME,
                "description": "Interner Snapshot fuer kontrollierte Bankkonto-Synchronisation.",
                "hidden": 1,
                "no_copy": 1,
                "read_only": 1,
            },
            {
                "fieldname": BANK_ACCOUNT_IMPORT_ALIAS_FIELDNAME,
                "label": "ERPverein Bankimport-Alias",
                "fieldtype": "Data",
                "insert_after": "company",
                "description": "Kontoalias aus dem Manifest der Banking-Webtop, zum Beispiel verein-main.",
                "depends_on": "is_company_account",
                "no_copy": 1,
                "unique": 1,
            },
        ],
        "Subscription": [
            {
                "fieldname": SUBSCRIPTION_MANAGED_FIELDNAME,
                "label": "ERPverein verwaltet",
                "fieldtype": "Check",
                "insert_after": "party",
                "no_copy": 1,
                "read_only": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": SUBSCRIPTION_BILLING_KIND_FIELDNAME,
                "label": "ERPverein Abrechnungsart",
                "fieldtype": "Select",
                "options": "\nMitgliedsbeitrag\nMiete",
                "insert_after": SUBSCRIPTION_MANAGED_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": SUBSCRIPTION_GENERATION_KEY_FIELDNAME,
                "label": "ERPverein Generierungsschluessel",
                "fieldtype": "Data",
                "insert_after": SUBSCRIPTION_BILLING_KIND_FIELDNAME,
                "hidden": 1,
                "no_copy": 1,
                "read_only": 1,
                "unique": 1,
            },
            {
                "fieldname": SUBSCRIPTION_GENERATION_PAYLOAD_FIELDNAME,
                "label": "ERPverein Generierungsdaten",
                "fieldtype": "Long Text",
                "insert_after": SUBSCRIPTION_GENERATION_KEY_FIELDNAME,
                "hidden": 1,
                "no_copy": 1,
                "read_only": 1,
            },
            {
                "fieldname": SUBSCRIPTION_RUN_DOCTYPE_FIELDNAME,
                "label": "ERPverein Lauf-Typ",
                "fieldtype": "Link",
                "options": "DocType",
                "insert_after": SUBSCRIPTION_GENERATION_PAYLOAD_FIELDNAME,
                "hidden": 1,
                "no_copy": 1,
                "read_only": 1,
            },
            {
                "fieldname": SUBSCRIPTION_RUN_FIELDNAME,
                "label": "ERPverein Abrechnungslauf",
                "fieldtype": "Dynamic Link",
                "options": SUBSCRIPTION_RUN_DOCTYPE_FIELDNAME,
                "insert_after": SUBSCRIPTION_RUN_DOCTYPE_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
            },
            {
                "fieldname": SUBSCRIPTION_SOURCES_FIELDNAME,
                "label": "ERPverein Quellen",
                "fieldtype": "Table",
                "options": "ERPverein Subscription Source",
                "insert_after": SUBSCRIPTION_RUN_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
            },
        ],
        "Sales Invoice": [
            {
                "fieldname": SALES_INVOICE_SECTION_FIELDNAME,
                "label": "ERPverein",
                "fieldtype": "Section Break",
                "insert_after": "payment_schedule",
            },
            {
                "fieldname": SALES_INVOICE_SCHEDULE_ORIGIN_FIELDNAME,
                "label": "Herkunft des Zahlungsplans",
                "fieldtype": "Select",
                "options": "\nMandat\nStandard\nBearbeitet",
                "insert_after": SALES_INVOICE_SECTION_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
                "print_hide": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME,
                "label": "Vorabankuendigung",
                "fieldtype": "Select",
                "options": "\nNicht erforderlich\nAusstehend\nVersendet\nErneut senden\nManuell bestaetigt",
                "insert_after": SALES_INVOICE_SCHEDULE_ORIGIN_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
                "print_hide": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": SALES_INVOICE_PRENOTIFICATION_SENT_AT_FIELDNAME,
                "label": "Vorabankuendigung versendet am",
                "fieldtype": "Datetime",
                "insert_after": SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
                "print_hide": 1,
            },
            {
                "fieldname": SALES_INVOICE_SCHEDULE_NOTE_FIELDNAME,
                "label": "Hinweis zum Zahlungsplan",
                "fieldtype": "Small Text",
                "insert_after": SALES_INVOICE_PRENOTIFICATION_SENT_AT_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
                "print_hide": 1,
            },
            {
                "fieldname": SALES_INVOICE_COLUMN_FIELDNAME,
                "fieldtype": "Column Break",
                "insert_after": SALES_INVOICE_SCHEDULE_NOTE_FIELDNAME,
            },
            {
                "fieldname": SALES_INVOICE_BILLING_KIND_FIELDNAME,
                "label": "ERPverein Abrechnungsart",
                "fieldtype": "Select",
                "options": "\nMitgliedsbeitrag\nMiete",
                "insert_after": SALES_INVOICE_COLUMN_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
                "print_hide": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": SALES_INVOICE_PAYER_DOCTYPE_FIELDNAME,
                "label": "Zahlertyp",
                "fieldtype": "Link",
                "options": "DocType",
                "insert_after": SALES_INVOICE_BILLING_KIND_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
                "print_hide": 1,
            },
            {
                "fieldname": SALES_INVOICE_PAYER_FIELDNAME,
                "label": "Zahler",
                "fieldtype": "Dynamic Link",
                "options": SALES_INVOICE_PAYER_DOCTYPE_FIELDNAME,
                "insert_after": SALES_INVOICE_PAYER_DOCTYPE_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
                "print_hide": 1,
                "search_index": 1,
            },
            {
                "fieldname": SALES_INVOICE_SEPA_MANDATE_FIELDNAME,
                "label": "SEPA-Mandat",
                "fieldtype": "Link",
                "options": "SEPA Mandat",
                "insert_after": SALES_INVOICE_PAYER_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
                "print_hide": 1,
            },
            {
                "fieldname": SALES_INVOICE_SCHEDULE_FINGERPRINT_FIELDNAME,
                "label": "Zahlungsplan-Fingerprint",
                "fieldtype": "Data",
                "insert_after": SALES_INVOICE_SEPA_MANDATE_FIELDNAME,
                "hidden": 1,
                "no_copy": 1,
                "read_only": 1,
                "print_hide": 1,
            },
        ],
        "Payment Schedule": [
            {
                "fieldname": PAYMENT_SCHEDULE_CHANGED_AT_FIELDNAME,
                "label": "ERPverein geaendert am",
                "fieldtype": "Datetime",
                "insert_after": "mode_of_payment",
                "hidden": 1,
                "no_copy": 1,
                "read_only": 1,
                "print_hide": 1,
            },
        ],
        "Bank Transaction": [
            {
                "fieldname": BANK_TRANSACTION_SECTION_FIELDNAME,
                "label": "ERPverein",
                "fieldtype": "Section Break",
                "insert_after": "excluded_fee",
            },
            {
                "fieldname": BANK_TRANSACTION_CATEGORY_FIELDNAME,
                "label": "ERPverein Kategorie",
                "fieldtype": "Select",
                "options": "\n" + "\n".join(BANK_CATEGORY_OPTIONS),
                "insert_after": BANK_TRANSACTION_SECTION_FIELDNAME,
                "allow_on_submit": 1,
                "no_copy": 1,
                "read_only": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": BANK_TRANSACTION_MATCH_STATUS_FIELDNAME,
                "label": "ERPverein Abgleich",
                "fieldtype": "Select",
                "options": "\n" + "\n".join(BANK_MATCH_STATUS_OPTIONS),
                "insert_after": BANK_TRANSACTION_CATEGORY_FIELDNAME,
                "allow_on_submit": 1,
                "no_copy": 1,
                "read_only": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": BANK_TRANSACTION_MATCH_NOTE_FIELDNAME,
                "label": "ERPverein Hinweis zum Abgleich",
                "fieldtype": "Small Text",
                "insert_after": BANK_TRANSACTION_MATCH_STATUS_FIELDNAME,
                "allow_on_submit": 1,
                "no_copy": 1,
                "read_only": 1,
            },
            {
                "fieldname": BANK_TRANSACTION_VALUE_DATE_FIELDNAME,
                "label": "Wertstellung",
                "fieldtype": "Date",
                "insert_after": BANK_TRANSACTION_MATCH_NOTE_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
            },
            {
                "fieldname": BANK_TRANSACTION_END_TO_END_FIELDNAME,
                "label": "SEPA-End-to-End-Referenz",
                "fieldtype": "Data",
                "insert_after": BANK_TRANSACTION_VALUE_DATE_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
                "search_index": 1,
            },
            {
                "fieldname": BANK_TRANSACTION_MANDATE_FIELDNAME,
                "label": "SEPA-Mandatsreferenz",
                "fieldtype": "Data",
                "insert_after": BANK_TRANSACTION_END_TO_END_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
                "search_index": 1,
            },
            {
                "fieldname": BANK_TRANSACTION_CREDITOR_FIELDNAME,
                "label": "SEPA-Glaeubiger-ID",
                "fieldtype": "Data",
                "insert_after": BANK_TRANSACTION_MANDATE_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
            },
            {
                "fieldname": BANK_TRANSACTION_COLUMN_FIELDNAME,
                "fieldtype": "Column Break",
                "insert_after": BANK_TRANSACTION_CREDITOR_FIELDNAME,
            },
            {
                "fieldname": BANK_TRANSACTION_CUSTOMER_REFERENCE_FIELDNAME,
                "label": "Kundenreferenz",
                "fieldtype": "Data",
                "insert_after": BANK_TRANSACTION_COLUMN_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
            },
            {
                "fieldname": BANK_TRANSACTION_PRIMANOTA_FIELDNAME,
                "label": "Primanota",
                "fieldtype": "Data",
                "insert_after": BANK_TRANSACTION_CUSTOMER_REFERENCE_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
            },
            {
                "fieldname": BANK_TRANSACTION_TEXT_KEY_FIELDNAME,
                "label": "Textschluessel / GVC",
                "fieldtype": "Data",
                "insert_after": BANK_TRANSACTION_PRIMANOTA_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
            },
            {
                "fieldname": BANK_TRANSACTION_IMPORT_BATCH_FIELDNAME,
                "label": "ERPverein Bankimport",
                "fieldtype": "Link",
                "options": "ERPverein Bankimport",
                "insert_after": BANK_TRANSACTION_TEXT_KEY_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
                "search_index": 1,
                "in_standard_filter": 1,
            },
            {
                "fieldname": BANK_TRANSACTION_SOURCE_ROW_FIELDNAME,
                "label": "Zeile in der Originaldatei",
                "fieldtype": "Int",
                "insert_after": BANK_TRANSACTION_IMPORT_BATCH_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
            },
            {
                "fieldname": BANK_TRANSACTION_FINGERPRINT_FIELDNAME,
                "label": "ERPverein Fingerprint",
                "fieldtype": "Data",
                "insert_after": BANK_TRANSACTION_SOURCE_ROW_FIELDNAME,
                "hidden": 1,
                "no_copy": 1,
                "read_only": 1,
                "unique": 1,
            },
        ],
        "Payment Entry": [
            {
                "fieldname": PAYMENT_ENTRY_COLLECTION_ITEM_FIELDNAME,
                "label": "SEPA-Einzugsposition",
                "fieldtype": "Link",
                "options": "SEPA Einzugsposition",
                "insert_after": "clearance_date",
                "description": "Einzugsposition, aus der ERPverein diese Zahlung erzeugt hat.",
                "no_copy": 1,
                "read_only": 1,
                "unique": 1,
            },
            {
                "fieldname": PAYMENT_ENTRY_BANK_TRANSACTION_FIELDNAME,
                "label": "ERPverein Bankumsatz",
                "fieldtype": "Link",
                "options": "Bank Transaction",
                "insert_after": PAYMENT_ENTRY_COLLECTION_ITEM_FIELDNAME,
                "no_copy": 1,
                "read_only": 1,
            },
        ],
    }


def sync_custom_fields() -> None:
    create_custom_fields(get_custom_fields(), update=True)


def remove_custom_fields() -> None:
    delete_custom_fields(
        {
            "Customer": [CUSTOMER_MITGLIED_FIELDNAME, CUSTOMER_SYNC_STATE_FIELDNAME, CUSTOMER_MIETER_FIELDNAME],
            "Bank Account": [
                BANK_ACCOUNT_MANAGED_FIELDNAME,
                BANK_ACCOUNT_SYNC_STATE_FIELDNAME,
                BANK_ACCOUNT_IMPORT_ALIAS_FIELDNAME,
            ],
            "Subscription": [
                SUBSCRIPTION_MANAGED_FIELDNAME,
                SUBSCRIPTION_BILLING_KIND_FIELDNAME,
                SUBSCRIPTION_GENERATION_KEY_FIELDNAME,
                SUBSCRIPTION_GENERATION_PAYLOAD_FIELDNAME,
                SUBSCRIPTION_RUN_DOCTYPE_FIELDNAME,
                SUBSCRIPTION_RUN_FIELDNAME,
                SUBSCRIPTION_SOURCES_FIELDNAME,
            ],
            "Sales Invoice": [
                SALES_INVOICE_SECTION_FIELDNAME,
                SALES_INVOICE_SCHEDULE_ORIGIN_FIELDNAME,
                SALES_INVOICE_PRENOTIFICATION_STATUS_FIELDNAME,
                SALES_INVOICE_PRENOTIFICATION_SENT_AT_FIELDNAME,
                SALES_INVOICE_SCHEDULE_NOTE_FIELDNAME,
                SALES_INVOICE_COLUMN_FIELDNAME,
                SALES_INVOICE_BILLING_KIND_FIELDNAME,
                SALES_INVOICE_PAYER_DOCTYPE_FIELDNAME,
                SALES_INVOICE_PAYER_FIELDNAME,
                SALES_INVOICE_SEPA_MANDATE_FIELDNAME,
                SALES_INVOICE_SCHEDULE_FINGERPRINT_FIELDNAME,
            ],
            "Payment Schedule": [PAYMENT_SCHEDULE_CHANGED_AT_FIELDNAME],
            "Bank Transaction": [
                BANK_TRANSACTION_SECTION_FIELDNAME,
                BANK_TRANSACTION_CATEGORY_FIELDNAME,
                BANK_TRANSACTION_MATCH_STATUS_FIELDNAME,
                BANK_TRANSACTION_MATCH_NOTE_FIELDNAME,
                BANK_TRANSACTION_VALUE_DATE_FIELDNAME,
                BANK_TRANSACTION_END_TO_END_FIELDNAME,
                BANK_TRANSACTION_MANDATE_FIELDNAME,
                BANK_TRANSACTION_CREDITOR_FIELDNAME,
                BANK_TRANSACTION_COLUMN_FIELDNAME,
                BANK_TRANSACTION_CUSTOMER_REFERENCE_FIELDNAME,
                BANK_TRANSACTION_PRIMANOTA_FIELDNAME,
                BANK_TRANSACTION_TEXT_KEY_FIELDNAME,
                BANK_TRANSACTION_IMPORT_BATCH_FIELDNAME,
                BANK_TRANSACTION_SOURCE_ROW_FIELDNAME,
                BANK_TRANSACTION_FINGERPRINT_FIELDNAME,
            ],
            "Payment Entry": [PAYMENT_ENTRY_COLLECTION_ITEM_FIELDNAME, PAYMENT_ENTRY_BANK_TRANSACTION_FIELDNAME],
        }
    )
