"""Backward-compatible item defaults and adjustable ranking configuration."""
import re

WEIGHTS = dict(semantic=.35, keyword=.20, type=.20, section=.10, context=.10, ocr=.05)
ITEM_FIELDS = {"display_name", "description", "aliases", "positive_examples", "negative_examples",
               "expected_type", "expected_units", "preferred_sections", "search_mode", "negative_penalty"}
LIST_FIELDS = {"aliases", "positive_examples", "negative_examples", "expected_units", "preferred_sections"}

PROFILES = {
    "age": dict(aliases=["age", "aged", "mean age", "average age", "years old"],
        positive_examples=["Participant age at enrollment, reported mean in years", "The recruited adults were aged several decades"],
        expected_type="number_unit", expected_units=["years"],
        preferred_sections=["Participants", "Results", "Baseline characteristics", "Table"]),
    "frequency": dict(aliases=["stimulation frequency", "rTMS", "repetitive TMS", "Hz", "hertz"],
        positive_examples=["10 Hz repetitive transcranial magnetic stimulation", "stimulation was delivered at 20 Hz"],
        negative_examples=["EMG", "sampling", "sampled", "sampling rate", "frequency of therapy sessions"],
        expected_type="number_unit", expected_units=["Hz"], preferred_sections=["Methods", "Intervention", "Treatment", "Protocol"]),
    "intensity": dict(aliases=["intensity", "resting motor threshold", "RMT", "active motor threshold", "AMT", "maximum stimulator output"],
        negative_examples=["intensity of rehabilitation", "intensity of physical therapy"], expected_type="number_unit",
        expected_units=["% RMT", "% AMT", "% MSO"], preferred_sections=["Methods", "Intervention", "Protocol"]),
    "sample_size": dict(aliases=["participants", "subjects", "randomized", "randomised", "allocated", "sample size", "n ="],
        negative_examples=["treatment sessions"], expected_type="integer", preferred_sections=["Methods", "Results", "Participants"]),
    "statistic": dict(aliases=["mean", "SD", "standard deviation", "SE", "standard error", "CI", "confidence interval", "change"],
        expected_type="statistic", preferred_sections=["Results", "Table"]),
    "trial_id": dict(aliases=["trial registration", "registered", "NCT", "ChiCTR", "ACTRN", "CTRI", "ISRCTN"], expected_type="trial_id"),
}


def category(item):
    text = " ".join(str(item.get(k, "")) for k in ("name", "display_name", "export_heading")).replace("_", " ").lower()
    if re.search(r"\bage\b", text): return "age"
    if re.search(r"\b(sd|se|ci|mean|standard deviation|standard error)\b", text) and "age" not in text:
        return "statistic"
    if re.search(r"frequency|hertz|\bhz\b", text): return "frequency"
    if "intensity" in text or "motor threshold" in text: return "intensity"
    if re.search(r"sample size|number of (participants|subjects|patients)", text): return "sample_size"
    if re.search(r"trial.*(id|registration)|registration.*(id|number)", text): return "trial_id"
    return "general"


def item_spec(item):
    result = dict(item)
    profile = PROFILES.get(category(item), {})
    for key in LIST_FIELDS:
        result[key] = list(item[key]) if key in item else list(profile.get(key, []))
    result["aliases"] = list(dict.fromkeys(result["aliases"] + item.get("keywords", []) +
        ([item.get("name", "").replace("_", " ")] if profile else [])))
    result.setdefault("description", item.get("definition", ""))
    result.setdefault("expected_type", profile.get("expected_type", item.get("data_type", "text")))
    result.setdefault("negative_penalty", .35)
    result.setdefault("search_mode", "intelligent")
    result["category"] = category(item)
    return result


def query_text(item):
    return "\n".join([item.get(k, "") for k in ("name", "display_name", "description", "instructions")] +
                     item["aliases"] + item["positive_examples"] +
                     [f'{key}: {value}' for key,value in item.get("context", {}).items()])
