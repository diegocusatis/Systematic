"""Field-answerability signals, independent of retrieval source and decisions.

These classify evidence, not values. An unlabeled age summary can be useful
evidence while remaining unsuitable for a mean or SD proposal.
"""
import re

NUM = r"\d+(?:\.\d+)?"


def age_kind(text):
    age = bool(re.search(r"\bage[ds]?\b|\byears? old\b", text, re.I))
    participant_average = re.search(rf"\b(?:participants?|patients?|subjects?|adults?|group|cohort)\b[^.;]{{0,60}}\baveraged?\s+{NUM}\s+years", text, re.I)
    if not age and not participant_average:
        return "unrelated"
    if re.search(r"\bmedian\b", text, re.I): return "median_age"
    if re.search(r"\b(?:eligib\w*|inclusion|prospective|criteria|at least|at most|between)\b", text, re.I) and re.search(NUM, text):
        return "eligibility_age"
    if re.search(rf"\baged?\s+{NUM}\s*[-–�]\s*{NUM}\s+years", text, re.I):
        # A damaged separator is not decoded as ±. Decimal pairs in a recruited
        # cohort are summary-like, but still never assigned a statistic.
        if "�" not in text or not re.search(r"\brecruited\b|\benrolled\b", text, re.I):
            return "age_range"
    if participant_average or re.search(rf"\b(?:mean|average)\s+age\b[^.;]{{0,35}}{NUM}|\bage\s*(?:was|of|[=:])?\s*(?:on average|averaged)\s*{NUM}", text, re.I):
        return "mean_age"
    if re.search(rf"\baged?\b[^.;]{{0,30}}{NUM}(?:\s*(?:±|�|\()\s*{NUM}\)?)?\s*years\b|{NUM}\s*years? old\b", text, re.I):
        return "age_summary"
    return "age_discussion"


def evidence_relevance(item, text):
    cat = item["category"]
    kind, penalty, reason = "unspecified", 0., "No specialized evidence classification"
    if cat == "age":
        kind = age_kind(text)
        wanted_median = bool(re.search(r"\bmedian\b", item.get("name", ""), re.I))
        penalty = {"mean_age": .75 if wanted_median else 0., "age_summary": 0.,
                   "median_age": 0. if wanted_median else .85,
                   "eligibility_age": 1., "age_range": .8, "age_discussion": 1., "unrelated": 1.}[kind]
        reason = {"mean_age": "Explicit participant mean age", "age_summary": "Reported age; mean and dispersion labels require verification",
                  "median_age": "Median is a distinct statistic", "eligibility_age": "Eligibility does not describe the observed cohort",
                  "age_range": "Range does not establish a mean", "age_discussion": "Age mentioned without a participant summary",
                  "unrelated": "No participant age summary"}[kind]
    elif cat == "frequency":
        measurement = re.search(r"\bsampl\w*\b|\bEMG\b|\bfilter\w*\b", text, re.I)
        if measurement: kind, penalty, reason = "measurement_frequency", 1., "Recording frequency can differ from treatment frequency"
    elif cat == "sample_size":
        if re.search(r"\b(?:sessions|trials|measurements|cycles)\b", text, re.I) and not re.search(r"\b(?:randomi[sz]ed|enrolled|recruited|allocated)\b", text, re.I):
            kind, penalty, reason = "procedure_count", 1., "Procedure counts do not establish participant count"
    elif cat == "statistic":
        heading = item.get("name", "") + " " + item.get("display_name", "")
        requested = "SD" if re.search(r"\bSD\b|standard deviation", heading, re.I) else "SE" if re.search(r"\bSE\b|standard error", heading, re.I) else "mean"
        conflict = (requested == "SD" and re.search(r"\bSE\b|standard error|confidence interval", text, re.I)) or (requested == "SE" and re.search(r"\bSD\b|standard deviation", text, re.I)) or (requested == "mean" and re.search(r"\bmedian\b", text, re.I))
        if conflict: kind, penalty, reason = "different_statistic", 1., "Reported statistic differs from the requested field"
    return dict(kind=kind, penalty=penalty, reason=reason)
