"""Associate labels with straight vector dimensions having two arrowheads."""
from collections import defaultdict

import pymupdf as fitz


def check_parallel_spacing(items):
    """Measure the sheet gap between overlapping horizontal or vertical dimension lines."""
    bands = defaultdict(list)
    linked = 0
    for item in items:
        line = item.get("dimension_line")
        if not line:
            continue
        (x1, y1), (x2, y2) = line["endpoints_pt"]
        vertical = abs(x2-x1) < abs(y2-y1)
        along, across = ((y1, y2), (x1, x2)) if vertical else ((x1, x2), (y1, y2))
        bands[vertical].append((sum(across)/2, min(along), max(along), linked, item))
        linked += 1
    findings = []
    for lines in bands.values():
        lines.sort()
        for i, (coordinate, start, end, _, item) in enumerate(lines):
            for j in range(i-1, -1, -1):
                prior, low, high, _, other = lines[j]
                gap_pt = coordinate-prior
                if gap_pt >= 7*72/25.4:
                    break
                # Rounded CAD endpoints: ignore coincident axes and touching projections.
                if gap_pt <= .3 or min(end, high)-max(start, low) <= .3:
                    continue
                findings.append({"status": "review", "gap_mm": round(gap_pt*25.4/72, 3),
                                 "labels": [other["label"], item["label"]],
                                 "lines_pt": [other["dimension_line"]["endpoints_pt"],
                                              item["dimension_line"]["endpoints_pt"]]})
    return {"standard": "ГОСТ 2.307-2011, п. 5.11", "minimum_mm": 7,
            "linked_lines": linked, "unlinked_labels": len(items)-linked,
            "scope": "Параллельные горизонтальные и вертикальные размерные линии с перекрывающимися проекциями",
            "recommendation": "Сверить обе линии на чертеже. Если привязка подтверждена, разнести их минимум на 7 мм на листе.",
            "findings": findings}


def arrowed_segments(page):
    # Key endpoints at PDF-point resolution to tolerate CAD export rounding.
    wings = defaultdict(set)
    segments = set()
    matrix = page.rotation_matrix
    for path in page.get_cdrawings():
        for item in path["items"]:
            if item[0] != "l":
                continue
            a, b = (fitz.Point(p)*matrix for p in item[1:3])
            dx, dy = b.x-a.x, b.y-a.y
            vertical = abs(dy)>abs(dx)
            along, across = (dy,dx) if vertical else (dx,dy)
            if abs(across)<.3 and abs(along)>=14:
                start,end = sorted(((round(a.x,1),round(a.y,1)),(round(b.x,1),round(b.y,1))))
                if vertical and start[1]>end[1]:
                    start,end = end,start
                segments.add((vertical,start,end))
            elif 2<=abs(along)<=23 and .06<=abs(across/along)<=.5:
                for pt,sign in ((a,1),(b,-1)):
                    key = (vertical,round(pt.x),round(pt.y),sign*along>0)
                    wings[key].add(sign*across>0)
    return [(v,a,b) for v,a,b in sorted(segments)
            if all(len(wings.get((v,round(x),round(y),forward),()))==2
                   for (x,y),forward in ((a,True),(b,False)))]


def link_dimensions(page, items):
    if not any(i["method"]=="pdf" for i in items):
        return
    bands = defaultdict(list)
    for vertical,a,b in arrowed_segments(page):
        coordinate = a[0] if vertical else a[1]
        bands[(vertical,int(coordinate//8))].append((vertical,a,b))
    owners = defaultdict(list)
    for item in items:
        if item["method"]!="pdf":
            continue
        box = fitz.Rect(item["box_pt"])
        cx,cy = (box.tl+box.br)*.5
        choices = []
        nearby = {(vertical,int((edge+delta)//8)) for vertical,edges in
                  ((True,(box.x0,box.x1)),(False,(box.y0,box.y1)))
                  for edge in edges for delta in (-4,0,4)}
        for vertical,a,b in (segment for band in nearby for segment in bands.get(band,())):
            if vertical:
                middle,extent = cy,box.height
                start,end = a[1],b[1]
                gap = min(abs(a[0]-box.x0),abs(a[0]-box.x1))
            else:
                middle,extent = cx,box.width
                start,end = a[0],b[0]
                gap = min(abs(a[1]-box.y0),abs(a[1]-box.y1))
            offset = abs(middle-(start+end)/2)
            if start<middle<end and gap<=4 and offset<=max(extent*.6,(end-start)*.06):
                choices.append((gap+offset*.1,a,b))
        choices.sort()
        if choices and (len(choices)==1 or choices[1][0]-choices[0][0]>1):
            _,a,b = choices[0]
            owners[(a,b)].append(item)
    for (a,b), candidates in owners.items():
        if len(candidates)==1:
            candidates[0]["dimension_line"] = {"endpoints_pt":[list(a),list(b)],
                                               "method":"paired_vector_arrowheads"}
