"""Keep original XLSX parts, rich text and links; transplant authored ID columns."""
import copy
import json
import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as E

OUT = Path(__file__).resolve().parents[2] / 'outputs/chart-mapping-20261015'
data = json.loads((OUT / 'bm25-matches.json').read_text(encoding='utf-8'))
path = OUT / 'ChunithmChart_with_ID.xlsx'
S = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
N = {'s': S}
E.register_namespace('', S)


def workbook_sheets(z):
    rels = {r.attrib['Id']: r.attrib['Target'].lstrip('/') for r in E.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
    result = {}
    for sheet in E.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet', N):
        part = rels[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
        result[sheet.attrib['name']] = part if part.startswith('xl/') else 'xl/' + part
    return result


with zipfile.ZipFile(data['source_workbook']) as original, zipfile.ZipFile(path) as authored:
    base = E.fromstring(original.read('xl/styles.xml'))
    newer = E.fromstring(authored.read('xl/styles.xml'))
    offsets = {}
    for tag in ['fonts', 'fills', 'borders', 'cellXfs']:
        offsets[tag] = len(base.find('s:' + tag, N))
    formats = base.find('s:numFmts', N)
    if formats is None:
        formats = E.Element(f'{{{S}}}numFmts', {'count': '0'})
        base.insert(0, formats)
    number_ids = {}
    next_id = max([163] + [int(x.attrib['numFmtId']) for x in formats]) + 1
    added_formats = newer.find('s:numFmts', N)
    for item in list(added_formats) if added_formats is not None else []:
        clone = copy.deepcopy(item)
        number_ids[int(clone.attrib['numFmtId'])] = next_id
        clone.attrib['numFmtId'] = str(next_id)
        next_id += 1
        formats.append(clone)
    formats.attrib['count'] = str(len(formats))
    for tag in ['fonts', 'fills', 'borders', 'cellXfs']:
        parent = base.find('s:' + tag, N)
        for item in newer.find('s:' + tag, N):
            clone = copy.deepcopy(item)
            if tag == 'cellXfs':
                for attr, group in [('fontId', 'fonts'), ('fillId', 'fills'), ('borderId', 'borders')]:
                    clone.attrib[attr] = str(int(clone.attrib.get(attr, 0)) + offsets[group])
                clone.attrib['xfId'] = '0'
                value = int(clone.attrib.get('numFmtId', 0))
                clone.attrib['numFmtId'] = str(number_ids.get(value, value))
            parent.append(clone)
        parent.attrib['count'] = str(len(parent))
    parts = {'xl/styles.xml': E.tostring(base, encoding='utf-8', xml_declaration=True)}
    strings = []
    if 'xl/sharedStrings.xml' in authored.namelist():
        strings = [''.join(x.itertext()) for x in E.fromstring(authored.read('xl/sharedStrings.xml'))]
    originals = workbook_sheets(original)
    authors = workbook_sheets(authored)
    changed = {loc['sheet'] for row in data['rows'] for loc in row['locations']}
    for name in changed:
        source = E.fromstring(original.read(originals[name]))
        added = E.fromstring(authored.read(authors[name]))
        rows = source.find('s:sheetData', N)
        by_row = {int(r.attrib['r']): r for r in rows}
        for row in added.findall('s:sheetData/s:row', N):
            cells = [c for c in row if re.sub(r'\d', '', c.attrib.get('r', '')) in ['L', 'M', 'N', 'O', 'P', 'Q', 'R'] and (c.find('s:v', N) is not None or c.find('s:is', N) is not None)]
            if not cells:
                continue
            target = by_row.get(int(row.attrib['r']))
            if target is None:
                target = E.SubElement(rows, f'{{{S}}}row', {'r': row.attrib['r']})
            for cell in cells:
                clone = copy.deepcopy(cell)
                for existing in list(target):
                    if existing.attrib.get('r') == clone.attrib['r']:
                        assert existing.find('s:v', N) is None and existing.find('s:is', N) is None
                        target.remove(existing)
                if clone.attrib.get('t') == 's':
                    value = strings[int(clone.find('s:v', N).text)]
                    clone.remove(clone.find('s:v', N))
                    clone.attrib['t'] = 'inlineStr'
                    E.SubElement(E.SubElement(clone, f'{{{S}}}is'), f'{{{S}}}t').text = value
                clone.attrib['s'] = str(int(clone.attrib.get('s', 0)) + offsets['cellXfs'])
                target.append(clone)
        columns = source.find('s:cols', N)
        if columns is None:
            columns = E.Element(f'{{{S}}}cols')
            source.insert(list(source).index(rows), columns)
        for column in added.findall('s:cols/s:col', N):
            if int(column.attrib['min']) >= 12 and int(column.attrib['max']) <= 18:
                columns.append(copy.deepcopy(column))
        dimension = source.find('s:dimension', N)
        if dimension is not None:
            dimension.attrib['ref'] = 'A1:R' + str(max(int(r.attrib['r']) for r in rows))
        # Every original cell, including rich text/colors/formulas, must survive.
        prior = E.fromstring(original.read(originals[name]))
        after_cells = {c.attrib['r']: c for c in source.findall('s:sheetData/s:row/s:c', N)}
        for cell in prior.findall('s:sheetData/s:row/s:c', N):
            if re.sub(r'\d', '', cell.attrib['r']) in ['L', 'M', 'N', 'O', 'P', 'Q', 'R']:
                assert cell.find('s:v', N) is None and cell.find('s:is', N) is None
                continue
            assert E.tostring(cell) == E.tostring(after_cells[cell.attrib['r']]), (name, cell.attrib['r'])
        parts[originals[name]] = E.tostring(source, encoding='utf-8', xml_declaration=True)
    temporary = path.with_suffix('.preserved.xlsx')
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_DEFLATED) as output:
        for item in original.infolist():
            output.writestr(item, parts.get(item.filename, original.read(item.filename)))
os.replace(temporary, path)
print('Preserved every original cell and original XLSX parts; added ID columns L:R.')
