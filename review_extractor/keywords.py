"""Offline, editable search vocabulary. Suggestions are search aids, not evidence."""

from functools import lru_cache
from importlib.resources import files
import json
import re


# Related phrases may describe different groups or measurements. Reviewers still
# decide whether a match supports an item; these never populate final values.
CONCEPTS = [
    (("citation", "reference"), ("doi", "authors", "published", "journal")),
    (("link", "doi", "url"), ("doi", "https://doi.org", "www.")),
    (("age",), ("age", "mean age", "average age", "median age", "years old")),
    (("country", "setting", "site"), ("country", "hospital", "center", "centre", "recruited", "single-center", "multicenter")),
    (("study design", "trial design"), ("study design", "randomized", "randomised", "controlled trial", "crossover", "parallel", "blinded")),
    (("sci", "ais", "spinal cord injury"), ("spinal cord injury", "SCI", "AIS", "ASIA", "incomplete", "complete injury")),
    (("injury phase", "chronicity"), ("acute", "subacute", "sub-acute", "chronic")),
    (("time since injury", "injury duration"), ("time since injury", "months after injury", "years after injury", "injury duration")),
    (("injury level", "neurological level"), ("neurological level", "injury level", "cervical", "thoracic", "lumbar")),
    (("etiology", "aetiology", "population"), ("etiology", "aetiology", "traumatic", "nontraumatic", "inclusion criteria", "eligibility")),
    (("gms", "gait", "walking", "lems", "wisci", "10mwt"), ("gait", "walking speed", "LEMS", "WISCI", "10MWT", "6MWT", "lower extremity motor score")),
    (("baseline",), ("baseline", "pretreatment", "pre-treatment", "initial assessment")),
    (("completed", "withdrawn", "attrition"), ("completed", "withdrawn", "withdrawal", "lost to follow-up", "attrition", "flow diagram")),
    (("adverse", "dropout", "safety"), ("adverse events", "side effects", "dropout", "withdrawal", "tolerability", "serious adverse event")),
    (("modality", "protocol", "rtms", "itbs"), ("rTMS", "iTBS", "theta burst", "transcranial magnetic stimulation", "protocol")),
    (("stimulator", "device"), ("stimulator", "device", "Magstim", "MagVenture")),
    (("hz", "frequency", "burst"), ("Hz", "hertz", "frequency", "burst", "intermittent theta burst")),
    (("intensity", "motor threshold"), ("intensity", "RMT", "AMT", "resting motor threshold", "active motor threshold")),
    (("pulses",), ("pulses", "pulse count", "stimuli")),
    (("sessions",), ("sessions", "session", "treatment visits")),
    (("sessions per week", "sessions week"), ("sessions per week", "sessions/week", "weekly", "days per week")),
    (("treatment duration", "intervention duration"), ("treatment duration", "intervention period", "weeks", "days")),
    (("coil",), ("coil", "figure-of-8", "figure of eight", "double-cone", "circular coil")),
    (("target location", "stimulation site"), ("motor cortex", "M1", "hotspot", "hot spot", "neuronavigation", "stimulation site")),
    (("comparator", "control group", "control comparator"), ("control group", "comparator", "sham", "placebo", "usual care")),
    (("concurrent therapy", "rehabilitation", "cointervention"), ("concurrent therapy", "rehabilitation", "gait training", "physiotherapy", "physical therapy", "co-intervention")),
    (("confounding", "adjustment"), ("confounding", "covariates", "adjusted", "adjustment", "matched", "co-interventions")),
    (("primary outcome",), ("primary outcome", "primary endpoint", "post-treatment", "posttreatment", "immediately after")),
    (("follow up", "followup"), ("follow-up", "follow up", "followup", "retention", "long-term")),
    (("sample size", "participants", "enrollment"), ("sample size", "participants", "enrolled", "recruited", "randomized", "randomised")),
    (("sex", "gender"), ("sex", "gender", "male", "female", "men", "women")),
]


def normalized(text):
    return " ".join(re.findall(r"\w+", text.casefold()))


@lru_cache(maxsize=1)
def reference_vocabulary():
    items = json.loads(files("review_extractor").joinpath("resources/reference_items.json").read_text(encoding="utf-8"))
    return {normalized(item["name"]): item["keywords"] for item in items}


def suggest_keywords(item):
    """Use known heading/definition concepts, falling back to heading phrases.

    Never use template example rows, PDF content, or saved extracted values.
    Word boundaries prevent e.g. 'age' from matching 'dosage'.
    """
    values = [item.get(key, "") for key in ("name", "export_heading", "definition")]
    if any(not isinstance(value, str) for value in values):
        raise ValueError("Item name, export heading and definition must be text")
    headings = values[:2]
    candidates = []
    for heading in headings:
        candidates.extend(reference_vocabulary().get(normalized(heading), []))
    contexts = [f" {normalized(value)} " for value in values]
    for aliases, terms in CONCEPTS:
        if any(f" {alias} " in context for alias in aliases for context in contexts):
            candidates.extend(terms)
    if not candidates:
        for heading in headings:
            candidates.extend(part.strip() for part in re.split(r"[/;|\n]", heading))
    result, seen = [], set()
    for term in candidates:
        term = term.strip()
        key = term.casefold()
        if term and key not in seen:
            result.append(term)
            seen.add(key)
    return result
