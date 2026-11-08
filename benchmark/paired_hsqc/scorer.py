"""Evaluator-only external adjudication, freeze and scoring. Never imported by inference."""
from __future__ import annotations

import math
import random
from collections import Counter, defaultdict

from .harness import (CONFIG, digest, external, fields, inputs, load_bundle, read,
                      require, sha, snapshot, text, validate_lock, write)


def validate_keys(data, keys):
    fields(keys, ("schema", "evidence", "cases"))
    require(keys["schema"] == 1, "unsupported key schema")
    require(isinstance(keys["cases"], list), "invalid key cases")
    by_case = {}
    all_ids = {data["executor"]._db.candidate_by_inchikey(k).id
               for k in data["executor"]._db.known_inchikeys()}
    partitions, query_sources = {}, set()
    reference_sources = set()
    for row in keys["cases"]:
        fields(row, ("case_id", "truth_status", "acceptable_candidate_ids", "independence_group",
                     "partition", "query_source_id", "reference_source_ids", "condition_mismatch",
                     "provenance_review"))
        case_id = text(row["case_id"])
        require(case_id not in by_case, "duplicate key case")
        by_case[case_id] = row
        require(row["truth_status"] in ("present", "absent", "ambiguous"), "invalid truth status")
        accepted = row["acceptable_candidate_ids"]
        require(isinstance(accepted, list) and all(isinstance(a, str) for a in accepted)
                and len(set(accepted)) == len(accepted), "invalid acceptable set")
        require(set(accepted) <= all_ids, "accepted identity outside frozen library")
        count = len(accepted)
        require((row["truth_status"] == "present" and count == 1)
                or (row["truth_status"] == "absent" and count == 0)
                or (row["truth_status"] == "ambiguous" and count >= 2), "truth/set mismatch")
        group = text(row["independence_group"])
        partition = row["partition"]
        require(partition in ("dev", "test"), "invalid key partition")
        require(group not in partitions or partitions[group] == partition, "group split overlap")
        partitions[group] = partition
        source = text(row["query_source_id"])
        require(source not in query_sources, "duplicate query acquisition")
        query_sources.add(source)
        refs = row["reference_source_ids"]
        require(isinstance(refs, list) and len(refs) == len(set(refs)), "invalid reference sources")
        for ref in refs:
            reference_sources.add(text(ref))
        require(type(row["condition_mismatch"]) is bool, "invalid condition stratum")
        fields(row["provenance_review"], ("rights_cleared", "independently_acquired", "identity_verified"))
        require(all(type(v) is bool for v in row["provenance_review"].values()), "invalid review flags")
    require(not query_sources & reference_sources, "query/reference acquisition overlap")
    require(set(by_case) == {c["case_id"] for c in data["queries"]}, "key/query coverage mismatch")
    for case in data["queries"]:
        require(by_case[case["case_id"]]["partition"] == case["partition"], "partition mismatch")
    evidence = keys["evidence"]
    fields(evidence, ("classification", "reviewer", "adjudication_id", "screening_log_sha256",
                      "external_timestamp_receipt", "records"))
    require(digest(evidence) == data["manifest"]["review_artifact_sha256"], "evidence digest mismatch")
    require(evidence["classification"] == data["manifest"]["classification"], "evidence class mismatch")
    for key in ("reviewer", "adjudication_id", "external_timestamp_receipt"):
        text(evidence[key])
    h = evidence["screening_log_sha256"]
    require(isinstance(h, str) and len(h) == 64 and all(c in "0123456789abcdef" for c in h),
            "invalid screening digest")
    require(isinstance(evidence["records"], list), "invalid evidence records")
    if evidence["classification"] == "independent_measured":
        records = {}
        for record in evidence["records"]:
            fields(record, ("source_id", "role", "acquisition_id", "raw_sha256", "rights_verified",
                            "independently_acquired", "external_adjudication", "metadata"))
            sid = text(record["source_id"])
            require(sid not in records, "duplicate evidence source")
            require(record["role"] in ("query", "reference"), "invalid evidence role")
            text(record["acquisition_id"])
            text(record["external_adjudication"])
            raw = record["raw_sha256"]
            require(isinstance(raw, str) and len(raw) == 64
                    and all(c in "0123456789abcdef" for c in raw), "invalid raw digest")
            require(record["rights_verified"] is True and record["independently_acquired"] is True,
                    "uncleared acquisition or rights")
            fields(record["metadata"], ("solvent", "temperature", "pH", "field", "referencing",
                                        "processing", "peak_picking", "assignment_provenance"))
            for value in record["metadata"].values():
                text(value)
            records[sid] = record
        require(set(records) == query_sources | reference_sources, "evidence coverage mismatch")
        require(all(records[s]["role"] == "query" for s in query_sources)
                and all(records[s]["role"] == "reference" for s in reference_sources), "source role mismatch")
        # Different accession strings cannot hide re-exported acquisitions or raw files.
        q_acq = {records[s]["acquisition_id"] for s in query_sources}
        r_acq = {records[s]["acquisition_id"] for s in reference_sources}
        q_raw = {records[s]["raw_sha256"] for s in query_sources}
        r_raw = {records[s]["raw_sha256"] for s in reference_sources}
        require(not q_acq & r_acq and not q_raw & r_raw, "reused query/reference acquisition")
        require(len(q_acq) == len(query_sources) and len(q_raw) == len(query_sources),
                "reused query acquisition")
        # Every loaded reference must point to an evidence source, not merely a free-text citation.
        loaded_sources = {r.source for refs in data["executor"]._references.values() for r in refs}
        require(loaded_sources == reference_sources, "library reference source coverage mismatch")
        group_by_candidate = {}
        for row in by_case.values():
            require(all(v is True for v in row["provenance_review"].values()), "uncleared case")
            expected_sources = {r.source for cid in data["retrieval"][row["case_id"]]
                                for r in data["executor"]._references.get(cid, [])}
            require(set(row["reference_source_ids"]) == expected_sources, "case reference source mismatch")
            for cid in row["acceptable_candidate_ids"]:
                previous = group_by_candidate.setdefault(cid, row["independence_group"])
                require(previous == row["independence_group"], "compound occurs in multiple groups")
        # Same source acquisition/raw identity must not cross dev/test, including references.
        source_splits = {}
        for row in by_case.values():
            for sid in [row["query_source_id"], *row["reference_source_ids"]]:
                for identity in (records[sid]["acquisition_id"], records[sid]["raw_sha256"]):
                    require(identity not in source_splits or source_splits[identity] == row["partition"],
                            "source split overlap")
                    source_splits[identity] = row["partition"]
    return by_case


def freeze(bundle, keys_path, output):
    external(keys_path)
    data, keys = load_bundle(bundle), read(keys_path)
    validate_keys(data, keys)
    if data["manifest"]["classification"] == "independent_measured":
        external(output)
    lock = {"schema": 1, "status": "review_lock_pending",
            "classification": data["manifest"]["classification"], "snapshot": snapshot(data),
            "key_sha256": sha(keys_path)}
    lock["lock_id"] = digest(lock)
    write(output, lock)
    return lock


def rate(values):
    n, x = len(values), sum(values)
    if not n:
        return {"numerator": 0, "denominator": 0, "value": None, "wilson95": None}
    z = 1.959963984540054
    p, d = x / n, 1 + z * z / n
    center = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return {"numerator": x, "denominator": n, "value": p,
            "wilson95": [max(0., center - half), min(1., center + half)]}


def valid_prediction(prediction, case, retrieval):
    fields(prediction, ("ranking", "disposition", "unavailable_references", "hits", "query_peak_count"))
    require(prediction["query_peak_count"] == len(case["peaks"]), "query count mismatch")
    hits = prediction["hits"]
    require(isinstance(hits, list), "invalid hits")
    seen = set()
    for hit in hits:
        fields(hit, ("candidate_id", "reference_id", "distance", "matched_query_count",
                     "distinct_matched", "reference_peak_count"))
        cid, rid = hit["candidate_id"], hit["reference_id"]
        require(cid in retrieval and rid in retrieval[cid], "foreign candidate/reference")
        require(rid not in seen, "duplicate hit")
        seen.add(rid)
        require(type(hit["distance"]) in (int, float) and math.isfinite(hit["distance"])
                and 0 <= hit["distance"] <= 4, "invalid distance")
        for k in ("matched_query_count", "distinct_matched", "reference_peak_count"):
            require(type(hit[k]) is int and 0 <= hit[k] <= 256, "invalid hit count")
        require(0 <= hit["distinct_matched"] <= hit["matched_query_count"] <= len(case["peaks"])
                and hit["reference_peak_count"] >= 1, "inconsistent counts")
    require(seen == {r for refs in retrieval.values() for r in refs}, "incomplete reference output")
    require(hits == sorted(hits, key=lambda h: (h["distance"], h["candidate_id"], h["reference_id"])),
            "unsorted hits")
    require(prediction["ranking"] == list(dict.fromkeys(h["candidate_id"] for h in hits)),
            "invalid unique candidate ranking")
    missing = sorted(cid for cid, refs in retrieval.items() if not refs)
    require(prediction["unavailable_references"] == missing, "missing-reference mismatch")
    disposition = "no_hits"
    if hits:
        top = hits[0]
        runner = next((h for h in hits if h["candidate_id"] != top["candidate_id"]), None)
        if missing or top["distinct_matched"] < 3 or round(top["matched_query_count"] / len(case["peaks"]), 6) < .5:
            disposition = "insufficient_evidence"
        elif runner and round(runner["distance"] - top["distance"], 6) < .25:
            disposition = "ambiguous"
        else:
            disposition = "ranked"
    require(prediction["disposition"] == disposition, "inconsistent disposition")


def cluster_interval(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[row["group"]].append(int(row["A"]) - int(row["B"]))
    if not groups:
        return {"groups": 0, "ci95": None, "difference": None, "degenerate": True}
    clusters = [groups[k] for k in sorted(groups)]
    rng = random.Random(CONFIG["bootstrap_seed"])
    values = []
    for _ in range(CONFIG["bootstrap_replicates"]):
        sample = [clusters[rng.randrange(len(clusters))] for _ in clusters]
        values.append(sum(map(sum, sample)) / sum(map(len, sample)))
    values.sort()
    n = len(values)
    # Deterministic nearest-rank percentile CI; ratio estimand, not mean cluster effect.
    return {"groups": len(clusters), "ci95": [values[math.ceil(.025*n)-1], values[math.ceil(.975*n)-1]],
            "difference": sum(sum(v) for v in clusters) / sum(map(len, clusters)),
            "degenerate": len(clusters) < 2, "seed": CONFIG["bootstrap_seed"],
            "replicates": CONFIG["bootstrap_replicates"]}


def score(bundle, lock_path, keys_path, records_path, output):
    external(keys_path)
    data, lock, keys, run = load_bundle(bundle), read(lock_path), read(keys_path), read(records_path)
    validate_lock(data, lock)
    require(sha(keys_path) == lock["key_sha256"], "key digest mismatch")
    truth = validate_keys(data, keys)
    if lock["classification"] == "independent_measured":
        for path in (lock_path, records_path, output):
            external(path)
    require(isinstance(run, dict) and run.get("schema") == 1
            and run.get("lock_id") == lock["lock_id"] and run.get("config") == CONFIG
            and run.get("classification") == lock["classification"], "foreign/malformed run")
    require(isinstance(run.get("records"), list), "invalid run records")
    cases = {c["case_id"]: c for c in data["queries"]}
    slots = {}
    for record in run["records"]:
        require(isinstance(record, dict), "invalid record")
        cid, arm = record.get("case_id"), record.get("arm")
        require(cid in cases and arm in ("A", "B"), "foreign case/arm")
        slot = cid, arm
        # Duplicate planned slots are failures, not extra observations or best-of retries.
        if slot in slots:
            slots[slot] = {"status": "duplicate", "prediction": None, "elapsed_seconds": None}
            continue
        status = record.get("status")
        prediction = record.get("prediction")
        try:
            fields(record, ("case_id", "arm", "status", "prediction", "error_type",
                            "elapsed_seconds", "input_sha256"))
            require(record["input_sha256"] == digest(inputs(cases[cid])), "input hash mismatch")
            elapsed = record["elapsed_seconds"]
            require(type(elapsed) in (int, float) and math.isfinite(elapsed) and elapsed >= 0,
                    "invalid runtime")
            require(status in ("ok", "exception", "timeout"), "invalid execution status")
            require(record["error_type"] is None or isinstance(record["error_type"], str), "invalid error")
            if status == "ok":
                require(elapsed <= CONFIG["timeout_seconds"], "late success")
                valid_prediction(prediction, cases[cid], data["retrieval"][cid])
            else:
                require(prediction is None, "failure has prediction")
        except (ValueError, TypeError, KeyError):
            status, prediction = "malformed", None
        slots[slot] = {"status": status, "prediction": prediction,
                       "elapsed_seconds": record.get("elapsed_seconds") if status != "malformed" else None}
    outcomes, paired = [], []
    metrics = {a: defaultdict(list) for a in ("A", "B")}
    statuses = {a: Counter() for a in ("A", "B")}
    matched = []
    for cid, case in cases.items():
        if case["partition"] != "test":
            continue
        key = truth[cid]
        present = key["truth_status"] == "present"
        accepted = set(key["acceptable_candidate_ids"])
        retrieved = data["retrieval"][cid]
        row = {"case_id": cid, "truth_status": key["truth_status"],
               "group": key["independence_group"], "condition_mismatch": key["condition_mismatch"],
               "candidate_count": len(retrieved), "reference_count": sum(map(len, retrieved.values())),
               "query_peak_count": len(case["peaks"]),
               "present_reference_missing": present and not any(retrieved.get(c) for c in accepted),
               "arms": {}}
        for arm in ("A", "B"):
            slot = slots.get((cid, arm), {"status": "missing", "prediction": None, "elapsed_seconds": None})
            statuses[arm][slot["status"]] += 1
            pred = slot["prediction"] if slot["status"] == "ok" else None
            ranking = pred["ranking"] if pred else []
            supported = pred is not None and pred["disposition"] == "ranked"
            correct = present and supported and bool(ranking) and ranking[0] in accepted
            wrong = supported and (not present or ranking[0] not in accepted)
            abstain = pred is not None and not supported
            m = metrics[arm]
            m["supported_coverage"].append(supported)
            m["supported_wrong"].append(wrong)
            m["failure"].append(slot["status"] != "ok")
            m["abstention"].append(abstain)
            if supported:
                m["wrong_among_supported"].append(wrong)
            if present:
                m["retrieval_recall"].append(bool(accepted & set(retrieved)))
                m["reference_retrieval_recall"].append(any(retrieved.get(c) for c in accepted))
                m["raw_top1"].append(bool(ranking) and ranking[0] in accepted)
                m["raw_topk"].append(bool(accepted & set(ranking[:CONFIG["top_k"]])))
                m["supported_correct"].append(correct)
            if key["truth_status"] == "absent":
                m["absent_abstention"].append(abstain)
            if key["truth_status"] == "ambiguous":
                m["ambiguous_flag"].append(pred is not None and pred["disposition"] == "ambiguous")
                m["ambiguous_abstention"].append(abstain)
            row["arms"][arm] = {**slot, "supported_correct": bool(correct),
                                 "supported_wrong": bool(wrong)}
        outcomes.append(row)
        if present:
            pair = {"group": key["independence_group"],
                    **{a: row["arms"][a]["supported_correct"] for a in ("A", "B")}}
            paired.append(pair)
            if all(row["arms"][a]["status"] == "ok" for a in ("A", "B")):
                matched.append(pair)
    names = ("retrieval_recall", "reference_retrieval_recall", "raw_top1", "raw_topk",
             "supported_correct", "supported_coverage", "supported_wrong", "wrong_among_supported",
             "failure", "abstention", "absent_abstention", "ambiguous_flag", "ambiguous_abstention")
    discordant = Counter((r["A"], r["B"]) for r in paired)
    b, c = discordant[(True, False)], discordant[(False, True)]
    independent = len({r["group"] for r in paired}) == len(paired)
    p = None
    if paired and independent:
        n = b + c
        p = min(1., 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2**n) if n else 1.
    result = {"schema": 1, "lock_id": lock["lock_id"], "review_status": "review_lock_pending",
              "classification": lock["classification"], "scientific_validation": False,
              "planned_test_cases": len(outcomes), "planned_slots_per_arm": len(outcomes),
              "rates": {a: {n: rate(metrics[a][n]) for n in names} for a in ("A", "B")},
              "statuses": statuses, "paired": {"present_denominator": len(paired),
                  "A_only": b, "B_only": c, "cluster_bootstrap": cluster_interval(paired),
                  "matched_success_denominator": len(matched),
                  "matched_success_sensitivity": cluster_interval(matched), "exact_mcnemar_p": p},
              "wilson_warning": "Descriptive query-level intervals; repeated groups violate independence.",
              "per_case": outcomes}
    write(output, result)
    return result
