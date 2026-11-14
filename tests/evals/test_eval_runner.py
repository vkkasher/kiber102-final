import json
from pathlib import Path


def test_eval_cases_cover_positive_empty_ambiguity_and_ten_injections():
    root = Path(__file__).parent
    main = json.loads((root / "cases.json").read_text(encoding="utf-8"))
    holdout = json.loads((root / "holdout.json").read_text(encoding="utf-8"))
    assert len(main) >= 60 and len(holdout) == 10
    assert len([case for case in main if case.get("kind") == "injection"]) >= 10
    assert len([case for case in main if case.get("expected_ids")]) >= 20
    assert not {case["message"] for case in main} & {case["message"] for case in holdout}


def test_eval_runner_uses_real_backend_and_reports_missing_human_measurements(tmp_path):
    from tests.evals.run import run_evaluations
    report = run_evaluations(tmp_path / "evaluation.json")
    assert report["catalog_kind"] == "TEST_DATA isolated synthetic contracts"
    assert report["main"]["case_count"] >= 60 and report["holdout"]["status"] == "not_run"
    assert report["main"]["positive_case_count"] >= 20
    assert report["main"]["all_pass"] is True
    assert report["factual_claim_precision"] is None
    assert report["llm_quality"] == "not_measured"
