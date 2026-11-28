"""Bounded template inspection and formula-safe tabular file output."""
import csv
import hashlib
from io import BytesIO, StringIO
import re
import zipfile

from commerce_hub.export_mapping import MappingError


def headers(raw, filename):
    if len(raw)>2*1024*1024:
        raise MappingError('Template limit is 2 MB.')
    if filename.lower().endswith('.csv'):
        try:
            names = next(csv.reader(StringIO(raw.decode('utf-8-sig'))))
        except (ValueError, UnicodeError,StopIteration,csv.Error):
            raise MappingError('Use a UTF-8 CSV with a header row.') from None
    elif filename.lower().endswith('.xlsx'):
        try:
            with zipfile.ZipFile(BytesIO(raw)) as package:
                if len(package.infolist())>1000 or sum(f.file_size for f in package.infolist())>20*1024*1024:
                    raise ValueError
            from openpyxl import load_workbook
            book = load_workbook(BytesIO(raw),read_only=True,data_only=False,keep_links=False)
            try:
                names = [cell.value for cell in next(book.active.iter_rows(max_row=1))]
            finally:
                book.close()
        except Exception:
            raise MappingError('Use a valid, unencrypted XLSX template with a header row.') from None
    else:
        raise MappingError('Upload CSV or XLSX.')
    if not names or len(names)>500 or any(not isinstance(n,str) or not n.strip() or len(n)>200 or n.startswith('=') for n in names) or len(set(names))!=len(names):
        raise MappingError('Template headers must be nonempty, unique text values.')
    return [{'name': n, 'required': False} for n in names], hashlib.sha256(raw).hexdigest()


def dangerous(value):
    if not isinstance(value,str):
        return False
    text = value.lstrip()
    return bool(text and text[0] in '=+-@' and not re.fullmatch(r'-?\d+(\.\d+)?',text)) or '\x00' in value


def csv_file(columns, rows, *, lineterminator='\r\n'):
    if any(dangerous(c) for c in columns):
        raise MappingError('Unsafe spreadsheet header.')
    result=StringIO(newline='')
    writer=csv.writer(result,lineterminator=lineterminator)
    writer.writerow(columns)
    for row in rows:
        values=[row.get(c,'') for c in columns]
        if any(dangerous(v) for v in values):
            raise MappingError('Unsafe spreadsheet formula text; review the source or mapping.')
        writer.writerow(values)
    return result.getvalue().encode('utf-8-sig')


def xlsx_file(sheets):
    from openpyxl import Workbook
    book=Workbook()
    book.remove(book.active)
    for title, columns, rows in sheets:
        sheet=book.create_sheet(title)
        for values in [columns, *([row.get(c,'') for c in columns] for row in rows)]:
            if any(isinstance(v,str) and len(v)>32767 for v in values):
                raise MappingError('Excel cell exceeds 32,767 characters; source remains preserved.')
            sheet.append(values)
            for cell in sheet[sheet.max_row]:
                if isinstance(cell.value,str):
                    cell.data_type='s'  # Never interpret extracted text as a formula.
        sheet.freeze_panes='A2'
        sheet.auto_filter.ref=sheet.dimensions
    output=BytesIO()
    book.save(output)
    return output.getvalue()
