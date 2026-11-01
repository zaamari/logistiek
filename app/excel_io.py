"""Excel inlezen (bestellingen), sjabloon maken en planning exporteren."""
from __future__ import annotations

import datetime as dt
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

HEADERS = ["Klant", "Adres", "Kratten", "Splitsen", "Venster van", "Venster tot"]

# Kolomnaam (kleine letters, bevat) -> veld. Volgorde telt: eerste match wint.
COLUMN_KEYS = [
    ("venster van", "window_start"), ("venster tot", "window_end"),
    ("klant", "name"), ("naam", "name"),
    ("postcode", "postcode"), ("plaats", "city"),
    ("adres", "address"), ("straat", "address"),
    ("krat", "crates"), ("aantal", "crates"),
    ("split", "split"),
    ("van", "window_start"), ("tot", "window_end"),
]

HEADER_FILL = PatternFill("solid", fgColor="1F2937")
HEADER_FONT = Font(bold=True, color="FFFFFF")


def _time_text(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, dt.datetime):
        value = value.time()
    if isinstance(value, dt.time):
        return value.strftime("%H:%M")
    if isinstance(value, float) and 0 <= value < 1:
        minutes = round(value * 24 * 60)
        return f"{minutes // 60:02d}:{minutes % 60:02d}"
    return str(value).strip()


def read_orders(data: bytes) -> list[dict]:
    ws = load_workbook(BytesIO(data), data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    header_row = next((i for i, row in enumerate(rows[:20])
                       if any(isinstance(c, str) and "adres" in c.lower() for c in row)), None)
    if header_row is None:
        raise ValueError("Geen kolom 'Adres' gevonden in de eerste 20 rijen.")

    columns: dict[str, int] = {}
    for col, title in enumerate(rows[header_row]):
        if not isinstance(title, str):
            continue
        name = title.strip().lower()
        for key, field in COLUMN_KEYS:
            if key in name and field not in columns:
                columns[field] = col
                break

    orders = []
    for row in rows[header_row + 1:]:
        def cell(field):
            col = columns.get(field)
            return row[col] if col is not None and col < len(row) else None

        address = str(cell("address") or "").strip()
        extra = " ".join(str(cell(f)).strip() for f in ("postcode", "city") if cell(f))
        if extra and extra.lower() not in address.lower():
            address = f"{address}, {extra}" if address else extra
        if not address:
            continue
        crates = cell("crates")
        orders.append({
            "name": str(cell("name") or "").strip(),
            "address": address,
            "crates": int(crates) if isinstance(crates, (int, float)) else (str(crates or "1").strip() or "1"),
            "split": str(cell("split") or "?").strip().lower(),
            "window_start": _time_text(cell("window_start")),
            "window_end": _time_text(cell("window_end")),
        })
    return orders


def _style_header(ws, widths: list[int]) -> None:
    for col, width in enumerate(widths, 1):
        c = ws.cell(row=1, column=col)
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        ws.column_dimensions[c.column_letter].width = width
    ws.freeze_panes = "A2"


def template() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Bestellingen"
    ws.append(HEADERS)
    ws.append(["Bakkerij De Vries", "Damrak 1, Amsterdam", 4, "?", "", ""])
    ws.append(["Café Centraal", "Oudegracht 100, Utrecht", 2, "nee", "09:00", "11:00"])
    _style_header(ws, [28, 42, 10, 11, 13, 13])
    dv = DataValidation(type="list", formula1='"ja,nee,?"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add("D2:D500")
    return _save(wb)


def _hhmm(minutes) -> str:
    return "" if minutes is None else f"{minutes // 60:02d}:{minutes % 60:02d}"


def export_plan(result: dict) -> bytes:
    wb = Workbook()
    summary = wb.active
    summary.title = "Overzicht"
    summary.append(["", "Zonder splitsen", "Met splitsen"])
    a, b = result["scenarios"]["nosplit"], result["scenarios"]["split"]
    for label, key, fmt in [
        ("Vertrek startlocatie", "departure", _hhmm), ("Eerste klant", "first_arrival", _hhmm),
        ("Klaar in loods", "finish", _hhmm), ("Ritten", "reloads", lambda r: r + 1),
        ("Keer naar loods", "reloads", str), ("Werktijd (min)", "work_time", str),
        ("Rijtijd (min)", "drive_time", str),
    ]:
        summary.append([label, fmt(a[key]), fmt(b[key])])
    summary.append([])
    summary.append(["Advies", result["compare"]["message"]])
    _style_header(summary, [22, 20, 20])

    for title, key in (("Zonder splitsen", "nosplit"), ("Met splitsen", "split")):
        ws = wb.create_sheet(title)
        ws.append(["Rit", "Vertrek", "Van", "Aankomst", "Klaar", "Klant", "Adres", "Kratten", "Venster", "Opmerking"])
        for trip in result["scenarios"][key]["trips"]:
            origin = "Start" if trip["from"] == "start" else "Loods"
            for i, s in enumerate(trip["stops"]):
                window = f"{_hhmm(s['window'][0])}-{_hhmm(s['window'][1])}" if s["window"] else ""
                notes = []
                if s["suggested"]:
                    notes.append("venster voorstel")
                if s["partial"]:
                    notes.append(f"deel van {s['of']} kratten")
                if s["late"]:
                    notes.append("TE LAAT")
                if s["wait"]:
                    notes.append(f"{s['wait']} min wachten")
                ws.append([trip["nr"], _hhmm(trip["depart"]) if i == 0 else "", origin if i == 0 else "",
                           _hhmm(s["arrive"]), _hhmm(s["leave"]), s["name"], s["address"], s["crates"],
                           window, ", ".join(notes)])
            ws.append([trip["nr"], "", "", _hhmm(trip["arrive_loods"]), "", "→ Loods", "", trip["load"], "", ""])
            for c in ws[ws.max_row]:
                c.font = Font(italic=True, color="6B7280")
        _style_header(ws, [6, 9, 8, 10, 8, 26, 40, 9, 13, 30])
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(vertical="top")
    return _save(wb)


def _save(wb: Workbook) -> bytes:
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
