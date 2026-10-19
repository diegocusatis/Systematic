"""Conservative two-column reading order from native word geometry.

Only a sustained central gutter with long prose lines on both sides qualifies.
Spanning headings split vertical bands. Short tables and rotated pages retain
their native order; complex layouts remain a reviewer responsibility.
"""


def column_order(words, width, rotation=0):
    if rotation or len(words) < 40:
        return words, {"mode": "native", "version": "columns-1"}
    lines = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if not lines or abs(lines[-1][0]["top"] - word["top"]) > 3:
            lines.append([])
        lines[-1].append(word)
    best = None
    origin = min(w["x0"] for w in words)
    for x in range(int(origin + width*.32), int(origin + width*.62), 2):
        support, crossing = 0, 0
        for line in lines:
            left = [w for w in line if w["x1"] <= x-3]
            right = [w for w in line if w["x0"] >= x+3]
            crosses = any(w["x0"] < x+3 and w["x1"] > x-3 for w in line)
            crossing += crosses
            if left and right and not crosses and max(w["x1"] for w in left)-min(w["x0"] for w in left) > 90 and max(w["x1"] for w in right)-min(w["x0"] for w in right) > 90:
                support += 1
        if support >= 6 and crossing <= max(2, len(lines)*.2):
            score = (support, -crossing, -abs(x-width/2))
            if best is None or score > best[0]: best = (score, x)
    if best is None: return words, {"mode": "native", "version": "columns-1"}
    gutter = best[1]
    result, band, block = [], [], 0

    def emit(group):
        nonlocal block
        tokens = sorted((w for line in group for w in line), key=lambda w: (w["top"],w["x0"]))
        if not tokens: return
        # Recluster after separating columns. Baselines may be staggered and
        # superscript/symbol glyphs must stay within their own column's line.
        local_lines = []
        for word in tokens:
            if not local_lines or abs(local_lines[-1][0]["top"]-word["top"]) > 3:
                local_lines.append([])
            local_lines[-1].append(word)
        for line in local_lines:
            result.extend(dict(w, layout_block=block) for w in sorted(line, key=lambda w: w["x0"]))
        block += 1

    def flush():
        for side in (0, 1):
            emit([[w for w in line if (w["x0"] >= gutter) == bool(side)] for line in band])
        band.clear()

    for line in lines:
        if any(w["x0"] < gutter+3 and w["x1"] > gutter-3 for w in line):
            flush()
            emit([line])
        else: band.append(line)
    flush()
    return result, {"mode": "two_columns", "version": "columns-1", "gutter_x": gutter,
                    "supporting_lines": best[0][0], "blocks": block}
