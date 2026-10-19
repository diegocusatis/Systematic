"""Original CC0 synthetic papers for a frozen, paper-held-out feedback audit.

Evaluation relevance labels are used only after retrieval. Training quotations
belong to separate synthetic documents and contain different wording/numbers.
The fixture deliberately includes strong baseline cases and broad custom fields;
it is an audit corpus, not a claim about clinical extraction accuracy.
"""
from copy import deepcopy
import hashlib
import re


CASES = [
    dict(key="age", name="Mean age", train=[
        "The mean age of recruited patients was 37.2 years.",
        "The enrolled cohort had an average age of 58.1 years."], good=[
        "At entry, participants averaged 46 years of age.",
        "Recruited adults were\naged 64.8 ± 11.3 years."], bad=[
        "Age affected recovery and was considered a potential confounder.",
        "Age-related cortical changes may explain these observations.",
        "Eligible participants were aged 21–75 years.",
        "The median age was 53 years with an interquartile range of 12 years.",
        "The mean time since injury was 7 years.",
        "Signals were averaged over forty sweeps before analysis.",
        "The root mean square amplitude was computed for each recording."]),
    dict(key="frequency", name="Stimulation frequency", context={"group": "intervention"}, train=[
        "The active intervention delivered magnetic stimulation at 12 Hz.",
        "In the intervention group, repetitive TMS was applied at 17 hertz."], good=[
        "Active group magnetic pulses were delivered at 8 hertz.",
        "The intervention group received stimulation at a frequency of 23 Hz."], bad=[
        "Control group stimulation was delivered at 2 Hz.",
        "The active group surface EMG was sampled at 2400 Hz.",
        "A 60 Hz bandpass filter removed electrical noise.",
        "The frequency of therapy sessions was twice weekly.",
        "Intervention visits occurred at a frequency of three per week.",
        "The stimulation frequency was discussed in previous work.",
        "A high-frequency recording system was used during assessment."]),
    dict(key="intensity", name="Stimulation intensity", context={"group": "intervention", "timepoint": "4 weeks"}, train=[
        "At 4 weeks, active group stimulation intensity was 82% RMT.",
        "The intervention group at 4 weeks received pulses at 72% of resting motor threshold."], good=[
        "At 4 weeks, magnetic pulses in the active group used 88% AMT.",
        "At 4 weeks, the intervention group received stimulation at 94% of resting motor threshold."], bad=[
        "Control group stimulation intensity at 4 weeks was 65% RMT.",
        "At 8 weeks, active group stimulation intensity was 92% RMT.",
        "The intensity of rehabilitation increased throughout treatment.",
        "Exercise intensity was 85% of maximum heart rate.",
        "Participants rated pain intensity on a scale of zero to ten.",
        "Stimulation intensity may affect cortical excitability.",
        "The recorded signal intensity was normalized to baseline."]),
    dict(key="sample", name="Sample size", context={"group": "intervention"}, train=[
        "The intervention group included n = 31 patients.",
        "Forty-two participants were allocated to the active group."], good=[
        "Thirty-seven participants were randomized to the intervention group.",
        "The active replication group comprised n = 29 patients."], bad=[
        "Control group included n = 26 participants.",
        "Participants completed 28 treatment sessions.",
        "The recording device stored n = 730 measurements.",
        "Sample size calculations suggested that a larger trial was desirable.",
        "The number of participants could influence statistical power.",
        "Each patient completed 18 repetitions during testing.",
        "Twelve hospitals were invited to participate in the survey."]),
    dict(key="sd_change", name="LEMS SD of change", context={"group": "intervention", "timepoint": "4 weeks"}, train=[
        "Active group at 4 weeks: change in LEMS mean (SD) was 5.2 (2.4).",
        "The change in LEMS had SD = 3.3 in the intervention group at 4 weeks."], good=[
        "At 4 weeks, the active group change in LEMS mean (SD) was 6.8 (3.7).",
        "The intervention group at 4 weeks had a change in LEMS with standard deviation = 4.1."], bad=[
        "Control group at 4 weeks: change in LEMS mean (SD) was 2.8 (1.6).",
        "Active group at 8 weeks: change in LEMS mean (SD) was 7.4 (3.2).",
        "Active group at 4 weeks: change in 10MWT mean (SD) was 1.7 (0.8).",
        "Active group at baseline: LEMS mean (SD) was 29.4 (5.3).",
        "Active group at 4 weeks: change in LEMS had SE = 0.6.",
        "Active group at 4 weeks: change in LEMS was 6.8 ± 3.7.",
        "Change in LEMS was discussed as a clinically meaningful endpoint."]),
    dict(key="delivery", name="Treatment delivery", train=[
        "Therapy was delivered one-to-one in a clinic under direct supervision.",
        "Participants practised independently at home with weekly telephone support."], good=[
        "A therapist led small classes at the rehabilitation centre.",
        "Patients completed the exercises in their own homes without an instructor present."], bad=[
        "Treatment delivery was feasible and well tolerated.",
        "The treatment was delivered at 16 Hz for twenty minutes.",
        "Drug delivery occurred through a subcutaneous catheter.",
        "Treatment delivery equipment was inspected annually.",
        "Treatment delivery costs were lower than anticipated.",
        "The treatment delivery schedule was published in advance.",
        "Treatment delivery was discussed with the hospital administrator."]),
    dict(key="concealment", name="Allocation concealment", train=[
        "Sequentially numbered opaque sealed envelopes concealed the next assignment.",
        "A central telephone service released the treatment code only after enrollment."], good=[
        "Recruiters opened tamper-proof opaque envelopes after obtaining consent.",
        "An independent web service revealed each assignment once registration was complete."], bad=[
        "Allocation concealment was considered during protocol development.",
        "The allocation ratio was two to one.",
        "Outcome assessors were blinded to group allocation.",
        "Participants were asked not to disclose their allocation.",
        "A computer generated the random sequence in blocks of six.",
        "The allocation concealment checklist was filed in the study archive.",
        "Previous reports of allocation concealment were inconsistent."]),
    dict(key="retention", name="Retention support", train=[
        "Staff telephoned participants before each appointment and arranged transport.",
        "Travel reimbursement and reminder messages encouraged continued attendance."], good=[
        "Taxi vouchers covered journeys to follow-up visits.",
        "A research assistant sent appointment prompts by text message."], bad=[
        "Retention support was considered during planning.",
        "Memory retention was assessed with a delayed recall task.",
        "Data retention followed the institutional archiving policy.",
        "The support surface reduced pressure at the heel.",
        "Staff retention remained stable during the study.",
        "Social support scores were higher in the intervention arm.",
        "The retention support budget was approved by the sponsor."]),
]


def fingerprint(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def feedback_context(case, *, target="heldout", revision=1):
    """No held-out quotation, finding label or expected numeric answer enters this."""
    examples = []
    for index, text in enumerate(case["train"], 1):
        identity = f'{case["key"]}-training-{index}'
        examples.append(dict(example_id=identity, feedback_id=identity + "-decision",
            paper_id=identity, item_id="item-" + case["key"],
            document_fingerprints=[fingerprint(text)], quote=text, decision="accepted"))
    return dict(config=dict(enabled=True, version=1, epoch=0, revision=revision),
                target_paper_id=target, target_document_fingerprints=[fingerprint(target)], examples=examples)


def make_item(case, *, fast=False):
    return dict(id="item-" + case["key"], name=case["name"], keywords=[], rules=[],
                context=deepcopy(case.get("context", {})), search_mode="fast" if fast else "intelligent")


def source_text(case):
    # Relevant passages deliberately follow distractors so ties cannot create a
    # spurious success. These labels never reach the item or feedback context.
    return "Methods\n" + "\n\n".join(case["bad"] + case["good"])


def _normalized(text):
    return re.sub(r"\s+", " ", text).strip().casefold()


def _contains(row, text):
    return _normalized(text) in _normalized(row["quote"])


def _measure(case, rows):
    ranks = [next((n for n, row in enumerate(rows, 1) if _contains(row, good)), None) for good in case["good"]]
    first = min((rank for rank in ranks if rank is not None), default=None)
    wrong = [dict(quote=row["quote"], value=row["proposed_value"])
             for row in rows if row.get("proposed_value") is not None and any(_contains(row, bad) for bad in case["bad"])]
    return dict(ranks=ranks, reciprocal_rank=1 / first if first else 0.,
                recall_at_5=sum(rank is not None and rank <= 5 for rank in ranks) / len(ranks),
                duplicate_findings_at_5=sum(max(0, sum(_contains(row, good) for row in rows[:5]) - 1) for good in case["good"]),
                incorrect_value_proposals=len(wrong), wrong_proposals=wrong,
                top_five=[dict(quote=row["quote"], score=row["rank_score"],
                              baseline=row.get("learning", {}).get("baseline_score", row["rank_score"]),
                              feedback=row.get("learning", {}).get("feedback_score", 0.),
                              status=row.get("learning", {}).get("status"),
                              influences=row.get("learning", {}).get("influences", []),
                              proposed_value=row.get("proposed_value")) for row in rows[:5]])


def evaluate_feedback(search, page, semantic=None, *, cases=CASES, fast=False):
    details = []
    for case in cases:
        source = page(source_text(case))
        source["paper_id"] = "heldout"
        item = make_item(case, fast=fast)
        baseline = search([source], item, semantic=semantic)
        enabled = search([source], item, semantic=semantic, feedback=feedback_context(case))
        details.append(dict(key=case["key"], name=case["name"],
                            baseline=_measure(case, baseline["matches"]),
                            enabled=_measure(case, enabled["matches"]),
                            baseline_warnings=baseline.get("warnings", []),
                            enabled_warnings=enabled.get("warnings", [])))
    summary = {}
    for mode in ("baseline", "enabled"):
        summary[mode] = dict(mrr=sum(d[mode]["reciprocal_rank"] for d in details) / len(details),
                             recall_at_5=sum(d[mode]["recall_at_5"] for d in details) / len(details),
                             duplicate_findings_at_5=sum(d[mode]["duplicate_findings_at_5"] for d in details),
                             incorrect_value_proposals=sum(d[mode]["incorrect_value_proposals"] for d in details))
    transitions = []
    for d in details:
        for index, (before, after) in enumerate(zip(d["baseline"]["ranks"], d["enabled"]["ranks"])):
            a, b = before or float("inf"), after or float("inf")
            transitions.append(dict(case=d["key"], finding=index + 1, before=before, after=after,
                                    change="improved" if b < a else "regressed" if b > a else "unchanged"))
    return dict(protocol="Two independent accepted training documents; separate held-out document; frozen baseline; no held-out feedback.",
                queries=len(details), relevant_findings=sum(len(c["good"]) for c in cases),
                summary=summary, cases=details, rank_transitions=transitions,
                improvements=sum(t["change"] == "improved" for t in transitions),
                regressions=sum(t["change"] == "regressed" for t in transitions))
