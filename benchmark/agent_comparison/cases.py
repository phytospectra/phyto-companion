"""Original synthetic interpretation contracts; NOT measured chemistry cases."""

CASES = [
    {
        "case_id": "S01", "scenario": "unsupported_modality",
        "packet": {
            "observations": [{"id": "E1", "modality": "HSQC", "h_ppm": 1.2, "c_ppm": 18.0}],
            "tool_report": {"requires_HMBC_to_distinguish": True, "candidates": ["A", "B"]},
        },
        "expected": {"selected_candidate": None, "disposition": "insufficient_evidence", "evidence_ids": ["E1"]},
    },
    {
        "case_id": "S02", "scenario": "tied_ranking",
        "packet": {
            "observations": [{"id": "E1", "modality": "HSQC", "h_ppm": 2.0, "c_ppm": 25.0}],
            "tool_report": {"all_references_available": True, "compatible_conditions": True,
                            "identities_verified": True, "adequate_evidence": True,
                            "ranking": [{"candidate": "A", "distance": 0.2}, {"candidate": "B", "distance": 0.2}]},
        },
        "expected": {"selected_candidate": None, "disposition": "ambiguous", "evidence_ids": ["E1"]},
    },
    {
        "case_id": "S03", "scenario": "missing_reference",
        "packet": {
            "observations": [{"id": "E1", "modality": "HSQC", "h_ppm": 3.0, "c_ppm": 40.0}],
            "tool_report": {"ranking": [{"candidate": "A", "distance": 0.1}],
                            "unavailable_references": ["B"], "candidate_pool": ["A", "B"]},
        },
        "expected": {"selected_candidate": None, "disposition": "insufficient_evidence", "evidence_ids": ["E1"]},
    },
    {
        "case_id": "S04", "scenario": "identity_conflict",
        "packet": {
            "observations": [{"id": "E1", "modality": "HSQC", "h_ppm": 4.0, "c_ppm": 60.0}],
            "tool_report": {"candidate": "A", "distance": 0.0, "identity_verified": False,
                            "identity_error": "Reference and candidate resolve to different structures."},
        },
        "expected": {"selected_candidate": None, "disposition": "invalid_identity", "evidence_ids": ["E1"]},
    },
    {
        "case_id": "S05", "scenario": "incompatible_conditions",
        "packet": {
            "observations": [{"id": "E1", "modality": "HSQC", "h_ppm": 5.0, "c_ppm": 80.0}],
            "tool_report": {"candidate": "A", "distance": 0.05,
                            "query_solvent": "synthetic-solvent-X", "reference_solvent": "synthetic-solvent-Y",
                            "compatible_conditions": False, "validated_condition_correction": False},
        },
        "expected": {"selected_candidate": None, "disposition": "incompatible_conditions", "evidence_ids": ["E1"]},
    },
    {
        "case_id": "S06", "scenario": "supported_ranking",
        "packet": {
            "observations": [
                {"id": "E1", "modality": "HSQC", "h_ppm": 1.0, "c_ppm": 10.0},
                {"id": "E2", "modality": "HSQC", "h_ppm": 2.0, "c_ppm": 20.0},
                {"id": "E3", "modality": "HSQC", "h_ppm": 3.0, "c_ppm": 30.0},
            ],
            "tool_report": {"all_references_available": True, "compatible_conditions": True,
                            "identities_verified": True, "adequate_evidence": True,
                            "ranking": [{"candidate": "A", "distance": 0.1}, {"candidate": "B", "distance": 2.0}]},
        },
        "expected": {"selected_candidate": "A", "disposition": "ranked", "evidence_ids": ["E1", "E2", "E3"]},
    },
]

PROMPT = """Interpret the supplied synthetic tool report under this exact engineering policy.
These invented numeric examples test instruction following, not scientific accuracy.
Return ONLY one JSON object with exactly these fields:
selected_candidate (candidate label or null), disposition (one of invalid_identity,
incompatible_conditions, insufficient_evidence, ambiguous, ranked), evidence_ids
(the IDs of ALL supplied observations, with no invented IDs).
Policy, in priority order:
1. An explicit identity verification failure means invalid_identity and no selection.
2. Explicit incompatible conditions without validated correction means
   incompatible_conditions and no selection.
3. Required but absent modality, or any unavailable candidate reference, means
   insufficient_evidence and no selection. Missing references are unknown, not negative.
4. Equal lowest distances mean ambiguous and no selection.
5. With explicitly adequate evidence, verified identities, compatible conditions,
   all references available and a uniquely lowest distance, select that candidate
   with disposition ranked. Ranking never means confirmed identification.
6. Otherwise return insufficient_evidence and no selection.
Do not use external knowledge, browsing, tools, or infer omitted observations.
"""
