"""Conservative value proposals, separate from retrieval and approval.

Nearby context aids ranking, but never supplies a statistical label across an
unrelated sentence. A bare ± does not identify SD versus SE or a CI.
"""
import re
from .chunking import normalize
from .lexical import contains
from .relevance import age_kind, evidence_relevance

NUMBER = r"[-−]?\d+(?:,\d{3})*(?:\.\d+)?"
OUTCOMES = {"LEMS": ["LEMS", "lower extremity motor score"],
    "10MWT": ["10MWT", "10-meter walk test", "10 metre walk test", "10-m walk test"],
    "6MWT": ["6MWT", "6-minute walk test"], "WISCI-II": ["WISCI-II", "WISCI II"]}
GROUPS = {"intervention": ["active rTMS", "active group", "intervention group", "treatment group"],
          "control": ["sham", "control group", "placebo"]}


def contexts(text):
    outcomes = [key for key, aliases in OUTCOMES.items() if any(contains(text, a) for a in aliases)]
    groups = [key for key, aliases in GROUPS.items() if any(contains(text, a) for a in aliases)]
    times = re.findall(r"\b\d+\s*(?:weeks?|months?|days?|hours?)\b|\bbaseline\b|\bpost[- ]treatment\b|\bfollow[- ]up\b", text, re.I)
    return dict(outcome=outcomes, group=groups, timepoint=list(dict.fromkeys(t.lower() for t in times)))


def requirements(item):
    title = item.get("name", "").replace("_", " ") + " " + item.get("display_name", "")
    found = contexts(title)
    explicit = item.get("context", {})
    result = {k: v[0] for k, v in found.items() if len(v) == 1}
    for key in ("outcome", "group", "timepoint"):
        value = explicit.get(key, explicit.get("time_point") if key == "timepoint" else None)
        if value: result[key] = str(value)
    return result


def context_matches(key, wanted, text, observed):
    mapping = OUTCOMES if key == "outcome" else GROUPS if key == "group" else {}
    canonical = next((name for name, aliases in mapping.items() if wanted.lower() == name.lower() or any(wanted.lower() == a.lower() for a in aliases)), wanted)
    return canonical in observed or contains(text, wanted)


def extract_value(item, candidate):
    text = normalize(candidate.get("semantic_context") or candidate["quote"])
    cat = item["category"]
    result = dict(value=None, unit=None, group=None, outcome=None, timepoint=None, statistic=None,
        source_text=candidate["quote"], page=candidate["page_number"], chunk_id=candidate.get("chunk_id"),
        ambiguous=False, issues=[])
    values = []
    if cat == "age":
        kind = age_kind(text)
        result["issues"].append("Verify participant age statistic and arm; no automatic age value proposal")
        result["statistic"] = "mean" if kind == "mean_age" else "median" if kind == "median_age" else None
    elif cat == "frequency":
        frequencies = list(re.finditer(rf"({NUMBER})\s*[-–]?\s*(k?Hz|hertz)\b", text, re.I))
        values = [f'{m.group(1)} {"kHz" if m.group(2).lower()=="khz" else "Hz"}' for m in frequencies]
        if len(frequencies)==1: result["unit"] = "kHz" if frequencies[0].group(2).lower()=="khz" else "Hz"
        if re.search(r"sampling|sampled|\bEMG\b", text, re.I):
            result["issues"].append("Measurement frequency may be a confounder")
    elif cat == "intensity":
        pattern = rf"({NUMBER})\s*%\s*(?:of\s+)?(?:the\s+)?(RMT|AMT|MSO|resting motor threshold|active motor threshold|motor threshold|maximum stimulator output)"
        for m in re.finditer(pattern, text, re.I):
            unit = {"resting motor threshold":"RMT", "active motor threshold":"AMT", "maximum stimulator output":"MSO"}.get(m.group(2).lower(), m.group(2))
            values.append(f'{m.group(1)}% {unit}')
            result["unit"] = f'% {unit}'
    elif cat == "trial_id" or item["expected_type"] == "trial_id":
        values = re.findall(r"\b(?:NCT\d{8}|ChiCTR[-\w]+|ACTRN\d+|CTRI/[\d/]+|ISRCTN\d+)\b", text, re.I)
    elif cat == "sample_size":
        values = re.findall(r"\bn\s*=\s*(\d+)\b", text, re.I)
        values += re.findall(r"\b(\d+)\s+(?:participants|patients|subjects)\s+(?:were\s+)?(?:randomi[sz]ed|allocated|enrolled|included|recruited)\b", text, re.I)
        names = {"one":1,"two":2,"three":3,"four":4,"five":5,"six":6,"seven":7,"eight":8,"nine":9,"ten":10,"eleven":11,"twelve":12,"thirteen":13,"fourteen":14,"fifteen":15,"sixteen":16,"seventeen":17,"eighteen":18,"nineteen":19,"twenty":20,"thirty":30,"forty":40,"fifty":50,"sixty":60,"seventy":70,"eighty":80,"ninety":90}
        for m in re.finditer(r"\b([a-z]+(?:[- ][a-z]+)?)\s+(?:participants|subjects|patients)\s+(?:were\s+)?(?:randomi[sz]ed|allocated|enrolled|recruited)\b", text, re.I):
            parts = re.split(r"[- ]", m.group(1).lower())
            if all(p in names for p in parts): values.append(str(sum(names[p] for p in parts)))
    elif cat == "statistic" or item["expected_type"] == "statistic":
        observed = contexts(text)
        required = requirements(item)
        for key, entries in observed.items():
            result[key] = entries[0] if len(entries) == 1 else None
            if len(entries) > 1: result["issues"].append(f"Multiple {key} labels")
        for key, wanted in required.items():
            if not context_matches(key, wanted, text, observed[key]):
                result["issues"].append(f"Requested {key} is not established: {wanted}")
            elif result[key] is None and not observed[key]: result[key] = wanted
        heading = (item.get("name", "") + " " + item.get("display_name", "")).replace("_", " ")
        change = bool(re.search(r"\bchange\b", heading, re.I))
        stat = "SD" if re.search(r"\bSD\b|standard deviation", heading, re.I) else "SE" if re.search(r"\bSE\b|standard error", heading, re.I) else "CI" if re.search(r"\bCI\b|confidence interval", heading, re.I) else "mean"
        result["statistic"] = stat + ("_change" if change else "")
        if change and not re.search(r"\bchange\b|change scores?", text, re.I):
            result["issues"].append("Change-score statistic is not established")
        if change and contains(text, "baseline"):
            result["issues"].append("Baseline and change context need separate review")
        if stat in {"SD", "SE"}:
            if re.search(r"\bSD\b|standard deviation", text, re.I) and re.search(r"\bSE\b|standard error", text, re.I):
                result["issues"].append("SD and SE labels coexist; verify the association")
            label = r"(?:SD|standard deviation)" if stat == "SD" else r"(?:SE|standard error)"
            values = re.findall(rf"\b{label}\s*(?:of (?:the )?change\s*)?[=:]\s*({NUMBER})", text, re.I)
            if re.search(rf"\b{label}\b", text, re.I):
                values += re.findall(rf"{NUMBER}\s*±\s*({NUMBER})", text)
                values += re.findall(rf"{NUMBER}\s*\(\s*({NUMBER})\s*\)", text)
            if not values: result["issues"].append(f"No explicitly labeled {stat}; ± alone is ambiguous")
        elif stat == "mean":
            if re.search(r"\bmean\b|average", text, re.I):
                values = re.findall(rf"({NUMBER})\s*(?:±|\(\s*{NUMBER}\s*\))", text)
                if not values: values = re.findall(rf"\bmean\s*(?:[=:]|was)\s*({NUMBER})", text, re.I)
        else:
            values = [f'{m[0]} to {m[1]}' for m in re.findall(rf"\bCI\s*[=:]?\s*[\[(]?\s*({NUMBER})\s*(?:to|,|–)\s*({NUMBER})", text, re.I)]
        # A statistical proposal is incomplete without explicit arm/outcome/timing.
        for key in ("group", "outcome", "timepoint"):
            if not result[key]: result["issues"].append(f"Missing {key} context")
        if candidate.get("table_header_inferred"):
            result["issues"].append("Table header relationships are heuristic; confirm them")
    elif item["expected_type"] == "number_unit" and item["expected_units"]:
        units = "|".join(re.escape(u) for u in item["expected_units"])
        values = [m.group() for m in re.finditer(rf"{NUMBER}\s*(?:{units})(?!\w)", text, re.I)]
        if len(values)==1:
            result["unit"] = next((u for u in item["expected_units"] if values[0].lower().endswith(u.lower())),None)
    # Context restrictions apply to all specialized profiles, not just statistics.
    observed = contexts(text)
    for key, wanted in requirements(item).items():
        if not context_matches(key, wanted, text, observed[key]) or len(observed[key]) > 1:
            issue = f"Requested {key} is not uniquely established: {wanted}"
            if issue not in result["issues"]: result["issues"].append(issue)
    if evidence_relevance(item, text)["kind"] == "different_statistic":
        result["issues"].append("Reported statistic conflicts with the requested statistic")
    # Preserve ambiguity even when two arms happen to report the same value.
    if len(values) > 1: result["issues"].append("Multiple values in this passage")
    result["ambiguous"] = bool(result["issues"])
    if len(values) == 1 and not result["ambiguous"]: result["value"] = values[0]
    result["detected_values"] = values
    return result


def validate_value(item, extracted):
    issues = list(extracted["issues"])
    value = extracted["value"]
    if value is not None and item.get("allowed_values") and str(value) not in [str(v) for v in item["allowed_values"]]:
        issues.append("Value is outside the item's allowed values")
    if value is not None and item["expected_type"] == "integer" and not str(value).isdigit():
        issues.append("Expected an integer")
    if value is not None and item["expected_type"] == "number_unit" and item["expected_units"]:
        if (extracted.get("unit") or "").lower() not in [u.lower() for u in item["expected_units"]]:
            issues.append("Reported unit differs from the expected units; verify conversion")
    return dict(valid=value is not None and not issues, ambiguous=bool(issues), issues=issues)


def type_score(item, text):
    cat = item["category"]
    if cat == "age":
        if re.search(r"\bmedian\b", item.get("name", ""), re.I):
            return {"median_age": 1., "age_summary": .65, "mean_age": .25}.get(age_kind(text), 0.)
        return {"mean_age": 1., "age_summary": .85, "median_age": .25,
                "eligibility_age": .1, "age_range": .15}.get(age_kind(text), 0.)
    if cat == "frequency": return float(bool(re.search(rf"{NUMBER}\s*[-–]?\s*(?:k?Hz|hertz)\b", text, re.I)))
    if cat == "intensity": return float(bool(re.search(r"\d\s*%\s*(?:of\s+)?(?:the\s+)?(?:RMT|AMT|MSO|(?:resting |active )?motor threshold|maximum stimulator output)", text, re.I)))
    if cat == "sample_size": return float(bool(re.search(r"\bn\s*=\s*\d|\b(?:participants|subjects|patients)\s+(?:were\s+)?(?:randomi[sz]ed|allocated|enrolled|recruited)", text, re.I)))
    if cat == "statistic":
        score = float(bool(re.search(r"\d.*(?:±|\bSD\b|\bSE\b|\bCI\b)|\b(?:SD|SE|CI)\b.*\d", text, re.I)))
        if "change" in item.get("name", "").lower() and not contains(text, "change"): score *= .15
        return score
    if cat == "trial_id": return float(bool(re.search(r"NCT\d{8}|ChiCTR|ACTRN|CTRI/|ISRCTN", text, re.I)))
    if item["expected_type"] == "number_unit":
        return float(any(re.search(rf"{NUMBER}\s*{re.escape(u)}(?!\w)", text, re.I) for u in item["expected_units"]))
    return 0.
