"""
Reading an uploaded order file into (headers, rows), whatever its
format (ADR 0038). Everything happens here on our own server: no file
is ever sent to an outside extraction or OCR service.

- CSV: any delimiter, UTF-8 or Windows encoding.
- Excel (.xlsx): the first sheet; the first non-empty row is the header.
- PDF with text: tables become rows. A PDF with no table is read as one
  order form of "Label: value" lines. Scanned PDFs (images, no text)
  need OCR, which isn't built yet: they're refused with a clear message.
"""

import csv
import io
import re
from datetime import date, datetime, time

MAX_ROWS = 2000
LABEL_LINE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9 /#&().'-]{1,60}?)\s*[:\-–]\s+(.+?)\s*$")


class ExtractError(Exception):
    pass


def read_file(content: bytes, name: str) -> tuple[list[str], list[dict]]:
    lower = name.lower()
    if lower.endswith(".csv") or lower.endswith(".txt"):
        headers, rows = _csv(content)
    elif lower.endswith(".xlsx") or lower.endswith(".xlsm"):
        headers, rows = _excel(content)
    elif lower.endswith(".pdf"):
        headers, rows = _pdf(content)
    elif lower.endswith(".xls"):
        raise ExtractError("Old .xls files can't be read. Open it in Excel and save as .xlsx or .csv.")
    else:
        raise ExtractError("Upload a CSV, Excel (.xlsx) or PDF file.")
    rows = [r for r in rows if any(str(v).strip() for v in r.values())]
    if not headers or not rows:
        raise ExtractError("No rows found in that file.")
    if len(rows) > MAX_ROWS:
        raise ExtractError(f"That file has {len(rows)} rows; split it into files of {MAX_ROWS} or fewer.")
    return headers, rows


def _clean_headers(cells) -> list[str]:
    headers, seen = [], {}
    for index, cell in enumerate(cells):
        name = re.sub(r"\s+", " ", str(cell or "").strip()) or f"Column {index + 1}"
        if name in seen:
            seen[name] += 1
            name = f"{name} ({seen[name]})"
        else:
            seen[name] = 1
        headers.append(name)
    return headers


def _text(value) -> str:
    """Excel cells as text the normalizer understands (no time zone: file times are Texas time)."""
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M") if (value.hour or value.minute) else value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _csv(content: bytes):
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    lines = [line for line in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in line)]
    if not lines:
        return [], []
    headers = _clean_headers(lines[0])
    return headers, [dict(zip(headers, (c.strip() for c in line))) for line in lines[1:]]


def _excel(content: bytes):
    from openpyxl import load_workbook

    try:
        sheet = load_workbook(io.BytesIO(content), read_only=True, data_only=True).worksheets[0]
    except Exception:  # noqa: BLE001 - any unreadable workbook
        raise ExtractError("That Excel file couldn't be read.") from None
    lines = [[_text(c) for c in row] for row in sheet.iter_rows(values_only=True)]
    lines = [line for line in lines if any(line)]
    if not lines:
        return [], []
    headers = _clean_headers(lines[0])
    return headers, [dict(zip(headers, line)) for line in lines[1:]]


def _pdf(content: bytes):
    import pdfplumber

    try:
        pdf = pdfplumber.open(io.BytesIO(content))
    except Exception:  # noqa: BLE001 - any unreadable PDF
        raise ExtractError("That PDF couldn't be read.") from None
    with pdf:
        headers, rows, text = None, [], []
        for page in pdf.pages:
            text.append(page.extract_text() or "")
            for table in page.extract_tables():
                table = [[_text(c) for c in line] for line in table if any(_text(c) for c in line)]
                if len(table) < 2 and not (headers and table):
                    continue
                if headers is None:
                    headers = _clean_headers(table[0])
                    table = table[1:]
                elif _clean_headers(table[0]) == headers:
                    table = table[1:]  # the header repeated on a new page
                rows += [dict(zip(headers, line)) for line in table]
    if headers:
        return headers, rows
    pairs = {}
    for line in "\n".join(text).splitlines():
        match = LABEL_LINE.match(line)
        if match and match.group(1).strip() not in pairs:
            pairs[match.group(1).strip()] = match.group(2).strip()
    if not pairs:
        if not "".join(text).strip():
            raise ExtractError(
                "That PDF is a scanned image, with no text to read. Scanned PDFs aren't supported yet: "
                "ask the sender for the CSV/Excel or a text PDF, or enter the trips by hand."
            )
        raise ExtractError("No table or 'Label: value' lines found in that PDF.")
    return list(pairs), [pairs]
