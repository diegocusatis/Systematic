"""Best-effort ruled-table layout, linked to canonical page word offsets.

First row/column header associations are explicitly heuristic. Unruled tables
remain accessible through prose search; this adapter does not invent geometry.
"""


def extract_tables(source, result, origin, detected=None):
    tables = []
    for index, table in enumerate(source.find_tables() if detected is None else detected):
        cells = []
        for r, row in enumerate(table.rows):
            for c, box in enumerate(row.cells):
                if box is None: continue
                x0, top, x1, bottom = box
                selected = [w for w in result["words"] if
                    x0 <= (w["x0"]+w["x1"])/2+origin[0] < x1 and
                    top <= (w["top"]+w["bottom"])/2+origin[1] < bottom]
                if not selected: continue
                start, end = min(w["start"] for w in selected), max(w["end"] for w in selected)
                cells.append(dict(row=r,column=c,start=start,end=end,text=" ".join(w["text"] for w in selected),
                    boxes=[dict(x0=x0-origin[0],top=top-origin[1],x1=x1-origin[0],bottom=bottom-origin[1])]))
        lookup = {(cell["row"],cell["column"]):cell["text"] for cell in cells}
        for cell in cells:
            cell.update(row_header=lookup.get((cell["row"],0)),column_header=lookup.get((0,cell["column"])))
        above = [w for w in result["words"] if table.bbox[1]-45 <= w["top"]+origin[1] < table.bbox[1]]
        title = " ".join(w["text"] for w in above)
        tables.append(dict(id=f'p{result["page_number"]}-table{index+1}',title=title or f"Table {index+1}",cells=cells))
    return tables
