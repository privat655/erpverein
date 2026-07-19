import calendar
import hashlib
import json
from datetime import date, timedelta

import frappe
from frappe import _
from frappe.utils import getdate, nowdate


INTERVAL_WEEKLY = "Woechentlich"
INTERVAL_MONTHLY = "Monatlich"
INTERVAL_QUARTERLY = "Vierteljaehrlich"
INTERVAL_HALF_YEARLY = "Halbjaehrlich"
INTERVAL_YEARLY = "Jaehrlich"

ALLOWED_INTERVALS = {
    INTERVAL_WEEKLY,
    INTERVAL_MONTHLY,
    INTERVAL_QUARTERLY,
    INTERVAL_HALF_YEARLY,
    INTERVAL_YEARLY,
}
ANNUAL_INTERVAL_COUNTS = {
    INTERVAL_QUARTERLY: 4,
    INTERVAL_HALF_YEARLY: 2,
    INTERVAL_YEARLY: 1,
}
WEEKDAYS = {
    "Montag": 0,
    "Dienstag": 1,
    "Mittwoch": 2,
    "Donnerstag": 3,
    "Freitag": 4,
    "Samstag": 5,
    "Sonntag": 6,
}

STATUS_NOT_CONFIGURED = "Nicht konfiguriert"
STATUS_INCOMPLETE = "Unvollstaendig"
STATUS_DRAFT = "Entwurf"
STATUS_INACTIVE = "Inaktiv"
STATUS_PLANNED = "Geplant"
STATUS_DUE = "Einzug faellig"
STATUS_ENDED = "Beendet"

DERIVED_FIELDS = (
    "naechster_solltermin",
    "naechster_einzugstermin",
    "planungsstatus",
    "planung_berechnet_am",
    "einzugsplan_fingerprint",
)


def clamp_month_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    length = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * length) // 451
    month = (h + length - 7 * m + 114) // 31
    day = ((h + length - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def is_target_business_day(value) -> bool:
    value = getdate(value)
    if value.weekday() >= 5:
        return False
    easter = easter_sunday(value.year)
    holidays = {
        date(value.year, 1, 1),
        easter - timedelta(days=2),
        easter + timedelta(days=1),
        date(value.year, 5, 1),
        date(value.year, 12, 25),
        date(value.year, 12, 26),
    }
    return value not in holidays


def next_target_business_day(value) -> date:
    value = getdate(value)
    while not is_target_business_day(value):
        value += timedelta(days=1)
    return value


def schedule_fingerprint(config: dict) -> str:
    payload = json.dumps(config, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_and_set_collection_schedule(doc, *, as_of=None) -> None:
    as_of = getdate(as_of or nowdate())
    config, complete = get_schedule_config(doc)
    configured = _has_schedule_input(doc)

    if doc.status == "Aktiv" and not complete:
        frappe.throw(_("Ein aktives SEPA-Mandat benoetigt einen vollstaendigen Einzugsplan."))

    if not configured:
        _set_projection(doc, None, None, STATUS_NOT_CONFIGURED, as_of, None)
        return
    if not complete:
        _set_projection(doc, None, None, STATUS_INCOMPLETE, as_of, None)
        return

    fingerprint = schedule_fingerprint(config)
    if doc.status in {"Widerrufen", "Ersetzt"}:
        _set_projection(doc, None, None, STATUS_INACTIVE, as_of, fingerprint)
        return

    occurrence = next_collection_occurrence(config, as_of)
    if not occurrence:
        status = STATUS_ENDED if doc.status == "Aktiv" else STATUS_DRAFT
        _set_projection(doc, None, None, status, as_of, fingerprint)
        return

    nominal, effective = occurrence
    if doc.status != "Aktiv":
        status = STATUS_DRAFT
    elif effective == as_of:
        status = STATUS_DUE
    else:
        status = STATUS_PLANNED
    _set_projection(doc, nominal, effective, status, as_of, fingerprint)


def get_schedule_config(doc) -> tuple[dict | None, bool]:
    interval = _text(doc.get("einzugsintervall"))
    start = getdate(doc.einzugsplan_ab) if doc.get("einzugsplan_ab") else None
    end = getdate(doc.einzugsplan_bis) if doc.get("einzugsplan_bis") else None
    weekday = _text(doc.get("wochentag"))
    month_day = _optional_int(doc.get("monatstag"))
    regular_amount = _optional_amount(doc.get("regelmaessiger_einzugsbetrag"))
    annual_dates = _annual_dates(doc.get("einzugstermine") or [])

    if interval and interval not in ALLOWED_INTERVALS:
        frappe.throw(_("Ungueltiges Einzugsintervall: {0}").format(frappe.bold(interval)))
    if end and start and end < start:
        frappe.throw(_("Einzugsplan bis darf nicht vor Einzugsplan ab liegen."))

    if month_day is not None and not 1 <= month_day <= 31:
        frappe.throw(_("Monatstag muss zwischen 1 und 31 liegen."))
    if regular_amount is not None and regular_amount <= 0:
        frappe.throw(_("Einzugsbetrag muss groesser als null sein."))
    annual_date_keys = [(month, day) for month, day, amount in annual_dates]
    if len(annual_date_keys) != len(set(annual_date_keys)):
        frappe.throw(_("Einzugstermine duerfen nicht doppelt vorkommen."))

    if not interval:
        return None, False

    if interval == INTERVAL_WEEKLY:
        if month_day is not None or annual_dates:
            frappe.throw(_("Ein woechentlicher Einzugsplan darf keinen Monatstag oder Jahrestermine enthalten."))
        if weekday and weekday not in WEEKDAYS:
            frappe.throw(_("Ungueltiger Wochentag: {0}").format(frappe.bold(weekday)))
        complete = bool(start and weekday and regular_amount is not None)
    elif interval == INTERVAL_MONTHLY:
        if weekday or annual_dates:
            frappe.throw(_("Ein monatlicher Einzugsplan darf keinen Wochentag oder Jahrestermine enthalten."))
        complete = bool(start and month_day is not None and regular_amount is not None)
    else:
        if weekday or month_day is not None or regular_amount is not None:
            frappe.throw(_("Dieser Einzugsplan darf keinen Wochentag, Monatstag oder regelmaessigen Einzugsbetrag enthalten."))
        expected_count = ANNUAL_INTERVAL_COUNTS[interval]
        if len(annual_dates) > expected_count:
            frappe.throw(
                _("Einzugsintervall {0} benoetigt genau {1} Jahrestermine.").format(
                    frappe.bold(interval), expected_count
                )
            )
        validate_nominal_date_collisions(annual_dates, start, end)
        complete = bool(start and len(annual_dates) == expected_count)

    if not complete:
        return None, False

    config = {
        "interval": interval,
        "start": str(start),
        "end": str(end) if end else None,
        "weekday": weekday,
        "month_day": month_day,
        "regular_amount": regular_amount,
        "annual_dates": [
            {"month": month, "day": day, "amount": amount} for month, day, amount in annual_dates
        ],
        "currency": "EUR",
    }
    return config, True


def next_collection_occurrence(config: dict, as_of) -> tuple[date, date] | None:
    as_of = getdate(as_of)
    candidates = nominal_occurrences(config, as_of - timedelta(days=7), years_ahead=2)
    adjusted = [(nominal, next_target_business_day(nominal)) for nominal in candidates]
    eligible = [occurrence for occurrence in adjusted if occurrence[1] >= as_of]
    return min(eligible, key=lambda occurrence: (occurrence[1], occurrence[0])) if eligible else None


def validate_nominal_date_collisions(
    annual_dates: list[tuple[int, int, float]], start: date | None, end: date | None
) -> None:
    if len(annual_dates) < 2:
        return
    if not start:
        years = (2027, 2028)
    elif end and end.year - start.year <= 400:
        years = range(start.year, end.year + 1)
    else:
        leap_year = next(year for year in range(start.year, start.year + 8) if calendar.isleap(year))
        non_leap_year = next(year for year in range(start.year, start.year + 8) if not calendar.isleap(year))
        years = (leap_year, non_leap_year)

    for year in years:
        nominal = [clamp_month_day(year, month, day) for month, day, amount in annual_dates]
        nominal = [value for value in nominal if (not start or value >= start) and (not end or value <= end)]
        if len(nominal) != len(set(nominal)):
            frappe.throw(_("Jahrestermine duerfen nicht auf denselben Solltermin fallen."))


def nominal_occurrences(config: dict, from_date, *, years_ahead: int) -> list[date]:
    from_date = getdate(from_date)
    start = getdate(config["start"])
    end = getdate(config["end"]) if config.get("end") else None
    interval = config["interval"]
    results = []

    if interval == INTERVAL_WEEKLY:
        weekday = WEEKDAYS[config["weekday"]]
        first = start + timedelta(days=(weekday - start.weekday()) % 7)
        target = max(first, from_date)
        current = first + timedelta(days=max(0, (target - first).days // 7) * 7)
        while current < from_date:
            current += timedelta(days=7)
        limit = date(max(from_date.year, start.year) + years_ahead, 12, 31)
        while current <= limit and (not end or current <= end):
            results.append(current)
            current += timedelta(days=7)
        return results

    if interval == INTERVAL_MONTHLY:
        start_index = start.year * 12 + start.month - 1
        from_index = from_date.year * 12 + from_date.month - 1
        current_index = max(start_index, from_index - 1)
        end_index = max(start_index, from_index) + years_ahead * 12 + 11
        while current_index <= end_index:
            year, zero_month = divmod(current_index, 12)
            nominal = clamp_month_day(year, zero_month + 1, config["month_day"])
            if nominal >= start and nominal >= from_date and (not end or nominal <= end):
                results.append(nominal)
            if end and nominal > end:
                break
            current_index += 1
        return results

    first_year = max(start.year, from_date.year - 1)
    for year in range(first_year, first_year + years_ahead + 1):
        for template in config["annual_dates"]:
            nominal = clamp_month_day(year, template["month"], template["day"])
            if nominal >= start and nominal >= from_date and (not end or nominal <= end):
                results.append(nominal)
    return sorted(results)


def refresh_collection_schedule_projections() -> None:
    for name in frappe.get_all("SEPA Mandat", pluck="name", order_by="name asc"):
        try:
            doc = frappe.get_doc("SEPA Mandat", name)
            validate_and_set_collection_schedule(doc)
            values = {fieldname: doc.get(fieldname) for fieldname in DERIVED_FIELDS}
            current = frappe.db.get_value("SEPA Mandat", name, list(DERIVED_FIELDS), as_dict=True)
            if any((current.get(fieldname) or None) != (values[fieldname] or None) for fieldname in DERIVED_FIELDS):
                frappe.db.sql(
                    """
                    UPDATE `tabSEPA Mandat`
                    SET naechster_solltermin = %s,
                        naechster_einzugstermin = %s,
                        planungsstatus = %s,
                        planung_berechnet_am = %s,
                        einzugsplan_fingerprint = %s
                    WHERE name = %s AND modified = %s
                    """,
                    (
                        values["naechster_solltermin"],
                        values["naechster_einzugstermin"],
                        values["planungsstatus"],
                        values["planung_berechnet_am"],
                        values["einzugsplan_fingerprint"],
                        name,
                        doc.modified,
                    ),
                )
        except Exception:
            frappe.log_error(
                title=f"ERPverein SEPA-Terminprojektion fehlgeschlagen: {name}",
                message=frappe.get_traceback(),
            )


def _annual_dates(rows) -> list[tuple[int, int, float]]:
    values = []
    for row in rows:
        month = _optional_int(row.get("monat"))
        day = _optional_int(row.get("kalendertag"))
        amount = _optional_amount(row.get("einzugsbetrag"))
        if month is None and day is None and amount is None:
            continue
        if month is None or day is None:
            frappe.throw(_("Monat und Kalendertag sind fuer jeden Einzugstermin erforderlich."))
        if not 1 <= month <= 12:
            frappe.throw(_("Monat muss zwischen 1 und 12 liegen."))
        if not 1 <= day <= 31:
            frappe.throw(_("Kalendertag muss zwischen 1 und 31 liegen."))
        if amount is None or amount <= 0:
            frappe.throw(_("Einzugsbetrag muss fuer jeden Einzugstermin groesser als null sein."))
        values.append((month, day, amount))
    return sorted(values)


def _has_schedule_input(doc) -> bool:
    return bool(
        doc.get("einzugsintervall")
        or doc.get("einzugsplan_ab")
        or doc.get("einzugsplan_bis")
        or doc.get("wochentag")
        or doc.get("monatstag") not in {None, "", 0, "0"}
        or doc.get("regelmaessiger_einzugsbetrag") not in {None, "", 0, "0"}
        or doc.get("einzugstermine")
    )


def _set_projection(doc, nominal, effective, status: str, as_of: date, fingerprint: str | None) -> None:
    doc.naechster_solltermin = nominal
    doc.naechster_einzugstermin = effective
    doc.planungsstatus = status
    doc.planung_berechnet_am = as_of
    doc.einzugsplan_fingerprint = fingerprint


def _optional_int(value) -> int | None:
    if value in {None, "", 0, "0"}:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        frappe.throw(_("Terminwerte muessen ganze Zahlen sein."))


def _optional_amount(value) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        frappe.throw(_("Einzugsbetraege muessen gueltige Zahlen sein."))


def _text(value) -> str | None:
    value = str(value or "").strip()
    return value or None
