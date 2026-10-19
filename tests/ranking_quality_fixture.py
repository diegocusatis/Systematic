"""Original synthetic CC0 evidence; labels never enter the retrieval pipeline."""

AGE_DISTRACTORS = [
    "The response to therapy was affected by age and lesion severity.",
    "Age may influence recovery and should be discussed in future work.",
    "Prospective participants were eligible if aged 18–70 years.",
    "The median age was 51 years with an interquartile range of 14 years.",
    "Signals were averaged over sixteen sweeps for further analysis.",
    "The root mean square amplitude was computed for each recording.",
    "Participants were assessed on average 6 years after injury.",
]

CASES = [
    dict(name="Mean age", good=["Recruited adults were aged 43.7 ± 9.4 years.",
                                "In the comparison cohort, average age was 49.2 years."], bad=AGE_DISTRACTORS),
    dict(name="Mean age", good=["The enrolled patients had a mean age of 38.6 years.",
                                "Participants averaged 46 years at enrollment."], bad=AGE_DISTRACTORS),
    dict(name="Mean age", good=["Women and men aged 62.4 � 7.9 years were recruited.",
                                "The average age of the recruited cohort was 59 years."], bad=AGE_DISTRACTORS),
    dict(name="Stimulation frequency", good=["Repetitive TMS was administered over M1 at 10 Hz.",
                                             "Magnetic pulses were delivered at 15 hertz."],
         bad=["Surface EMG was sampled at 2000 Hz.", "A 50 Hz bandpass filter was applied.", "The frequency of therapy sessions was three per week."]),
    dict(name="Stimulation intensity", good=["Stimulation intensity was 90% of resting motor threshold.",
                                             "The magnetic pulses used 75% AMT."],
         bad=["The intensity of rehabilitation increased.", "Exercise intensity was 80% of maximum heart rate."]),
    dict(name="Sample size", good=["Twenty-four participants were randomized.", "The replication cohort comprised n = 36 patients."],
         bad=["Participants completed 24 treatment sessions.", "The machine recorded n = 600 measurements."]),
    dict(name="LEMS SD of change", good=["The change in LEMS was 4.6 ± 2.7.",
                                         "The change in LEMS had SD = 3.1 in the intervention group at 4 weeks."],
         bad=["Baseline LEMS was 32.1 ± 4.2.", "The change in LEMS had SE = 0.5.", "The change in 10MWT had SD = 0.9."]),
]


def evaluate(search, make_page, make_item, semantic=None):
    details = []
    for case in CASES:
        # Relevance labels deliberately remain outside item definitions and query.
        source = make_page("Methods\n" + "\n\n".join(case["bad"] + case["good"]))
        rows = search([source], make_item(case["name"]), semantic=semantic)["matches"]
        ranks = [next((n for n,r in enumerate(rows,1) if good in r["quote"]), None) for good in case["good"]]
        first = min((r for r in ranks if r is not None), default=0)
        details.append(dict(name=case["name"], ranks=ranks, reciprocal_rank=1/first if first else 0,
                            recall_at_5=sum(r is not None and r <= 5 for r in ranks)/len(ranks),
                            precision_at_1=float(first == 1),
                            duplicate_findings_at_5=sum(max(0,sum(good in r["quote"] for r in rows[:5])-1) for good in case["good"])))
    return dict(cases=details, queries=len(details), relevant_findings=sum(len(c["good"]) for c in CASES),
                mrr=sum(d["reciprocal_rank"] for d in details)/len(details),
                recall_at_5=sum(d["recall_at_5"] for d in details)/len(details),
                precision_at_1=sum(d["precision_at_1"] for d in details)/len(details),
                duplicate_findings_at_5=sum(d["duplicate_findings_at_5"] for d in details))
