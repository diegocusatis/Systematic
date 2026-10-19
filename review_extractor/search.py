"""Replaceable literal/regex search with exact source spans and bounded regex work."""
import regex

REGEX_TIMEOUT_SECONDS = 0.05


def validate_rules(rules):
    if not isinstance(rules, list):
        raise ValueError("Rules must be a list")
    for rule in rules:
        if not isinstance(rule, dict) or not isinstance(rule.get("kind", "literal"), str) or rule.get("kind", "literal") not in {"literal", "regex"}:
            raise ValueError("Each rule must be a literal or regex rule")
        if "label" in rule and not isinstance(rule["label"], str):
            raise ValueError("Rule labels must be text")
        pattern = rule.get("pattern", "")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError("Rule patterns cannot be empty")
        if len(pattern) > 1000:
            raise ValueError("Rule patterns are limited to 1000 characters")
        if rule.get("kind") == "regex":
            try:
                compiled = regex.compile(pattern, regex.IGNORECASE)
            except regex.error as exc:
                raise ValueError(f"Invalid regex: {exc}") from exc
            group = rule.get("capture_group")
            if group not in (None, ""):
                if isinstance(group, bool) or not isinstance(group, (int, str)):
                    raise ValueError("Capture group must be a number or group name")
                if isinstance(group, str) and group.isdigit():
                    group = int(group)
                if not ((isinstance(group, int) and 0 <= group <= compiled.groups) or group in compiled.groupindex):
                    raise ValueError("Capture group does not exist in the regex")


def boxes_for_span(page, start, end):
    """Merge neighboring word boxes by line while retaining exact source offsets."""
    result = []
    for word in page.get("words", []):
        if word.get("end", 0) <= start or word.get("start", 0) >= end:
            continue
        box = {key: float(word[key]) for key in ("x0", "top", "x1", "bottom")}
        if result and abs(result[-1]["top"] - box["top"]) < 3 and result[-1]["x0"] <= box["x0"] <= result[-1]["x1"] + 15:
            result[-1]["x1"] = max(result[-1]["x1"], box["x1"])
            result[-1]["bottom"] = max(result[-1]["bottom"], box["bottom"])
        else:
            result.append(box)
    return result


def passage_span(text, start, end):
    """Use a complete nearby sentence/line as evidence, preserving exact wording."""
    left_limit, right_limit = max(0, start - 300), min(len(text), end + 300)
    left = left_limit
    for match in regex.finditer(r"(?:[.!?](?=\s|$)|\n)", text[left_limit:start]):
        left = left_limit + match.end()
    after = regex.search(r"(?:[.!?](?=\s|$)|\n)", text[end:right_limit])
    right = end + after.end() if after else right_limit
    while left < start and text[left].isspace():
        left += 1
    while right > end and text[right - 1].isspace():
        right -= 1
    return left, right


def search_pages(pages, keywords=None, rules=None):
    rules = list(rules or []) + [{"kind": "literal", "pattern": value, "label": value} for value in (keywords or []) if value.strip()]
    validate_rules(rules)
    found, errors = {}, []
    for rule in rules:
        pattern = rule["pattern"]
        if rule.get("kind", "literal") == "literal":
            pattern = r"\s+".join(regex.escape(part) for part in pattern.split())
        compiled = regex.compile(pattern, regex.IGNORECASE)
        label = rule.get("label") or rule["pattern"]
        for page in pages:
            if page.get("status") != "ok":
                continue
            source = page.get("text", "")
            # A finditer deadline keeps running between yields, including time
            # spent constructing quotations/boxes or descheduled by the OS.
            # Escaped literal terms cannot introduce arbitrary regex operators;
            # only custom regex rules need the defensive matching deadline.
            options = {"timeout": REGEX_TIMEOUT_SECONDS} if rule.get("kind") == "regex" else {}
            group = rule.get("capture_group") if rule.get("kind") == "regex" else None
            if isinstance(group, str) and group.isdigit():
                group = int(group)
            hits = []
            try:
                for match in compiled.finditer(source, **options):
                    if match.start() != match.end():
                        hits.append((match.start(), match.end(),
                                     match.group(group) if group not in (None, "") else None))
            except TimeoutError:
                # Retain completed matches, but explicitly flag this rule/page
                # as incomplete. Other rules and pages continue normally.
                errors.append({"page_number": page["page_number"], "rule": label,
                               "code": "regex_timeout", "rule_kind": "regex",
                               "error": "Custom pattern search timed out. Simplify the pattern and scan again, or inspect this page manually."})
            for match_start, match_end, value in hits:
                start, end = passage_span(source, match_start, match_end)
                key = (page["page_number"], start, end)
                if key in found:
                    existing = found[key]
                    if label not in existing["matched_rules"]:
                        existing["matched_rules"].append(label)
                    if value is not None and existing["proposed_value"] is None and not existing.get("multiple_proposed_values"):
                        existing["proposed_value"] = value
                    elif value is not None and existing["proposed_value"] != value:
                        # Several distinct values must never silently select the first.
                        existing["proposed_value"] = None
                        existing["multiple_proposed_values"] = True
                    continue
                found[key] = {
                    "page_number": page["page_number"], "start": start, "end": end,
                    "quote": source[start:end], "context": source[max(0, start - 150):min(len(source), end + 150)],
                    "boxes": boxes_for_span(page, start, end), "matched_rules": [label],
                    "proposed_value": value, "ocr": bool(page.get("ocr", False)),
                }
    return {"matches": sorted(found.values(), key=lambda row: (row["page_number"], row["start"], row["end"])), "errors": errors}
