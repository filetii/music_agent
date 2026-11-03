import pytest

from emporio.agent import Reply, ToolTrace
from evals import results
from evals.report import benchmark_report, pass_hat_k, run_report, wilson_interval
from evals.run import check, regrade, transcript
from evals.scenarios import SUITES, Scenario


def test_wilson_interval():
    assert wilson_interval(100, 100) == pytest.approx((0.963, 1.0), abs=0.001)  # 100/100 still leaves ~4% of doubt
    assert wilson_interval(50, 100) == pytest.approx((0.404, 0.596), abs=0.001)
    assert wilson_interval(0, 5)[0] == 0.0


def test_pass_hat_k_rewards_consistency():
    counts = [(5, 5), (4, 5)]  # one scenario always passes, one fails once
    assert pass_hat_k(counts, 1) == pytest.approx(0.9)
    assert pass_hat_k(counts, 5) == pytest.approx(0.5)


def test_scenario_ids_are_unique_across_suites():
    ids = [s.id for scenarios in SUITES.values() for s in scenarios]
    assert len(ids) == len(set(ids))


def test_check_reports_each_kind_of_failure():
    scenario = Scenario(
        "s", "c", ["oi"], expect_tools=["get_product_details|search_products", "quote_purchase"],
        forbid_tools=["get_order_status"], must_match=[r"599,90"], must_not_match=[r"Paris"],
    )
    replies = [Reply("Paris", [ToolTrace("search_products", {}, {}), ToolTrace("get_order_status", {}, {})])]
    assert check(scenario, replies) == [
        "tool not called: quote_purchase",
        "forbidden tool called: get_order_status",
        "missing /599,90/",
        "forbidden /Paris/",
    ]
    assert check(scenario, [Reply("R$ 599,90", [ToolTrace("get_product_details", {}, {}),
                                                ToolTrace("quote_purchase", {}, {})])]) == []
    thinking_aloud = Reply("Okay, the user wants the price. O C40 custa R$ 599,90.",
                           [ToolTrace("get_product_details", {}, {}), ToolTrace("quote_purchase", {}, {})])
    assert check(scenario, [thinking_aloud]) == ["reasoning leaked into a reply"]


def test_results_round_trip_into_reports(tmp_path):
    conn = results.connect(tmp_path / "results.db")
    run_id = results.start_run(conn, "ollama:test", "core", 2, "abc123")
    for repetition in (1, 2):
        results.save_result(conn, run_id, "off_topic", repetition, [], [], 1.0, 100, 0, "ok")
        failures = ["missing /x/"] if repetition == 2 else []
        results.save_result(conn, run_id, "empty_category", repetition, failures, [], 3.0, 300, 1, "t")
    assert results.latest_runs(conn) == []  # unfinished runs are not reported
    results.finish_run(conn, run_id)

    [run] = results.latest_runs(conn)
    report = run_report(run, results.run_results(conn, run_id))
    assert "**Pass rate: 3/4 (75%)**" in report
    assert "pass^2 (passes all 2 repetitions): 50%" in report
    assert "| empty_category | category listed by the store but without products | 1/2 | 3.0s | `missing /x/` (1) |" in report
    assert "### empty_category — repetition 2, FAIL" in report  # the failing transcript is the one shown
    assert "| [`ollama:test`](core-ollama_test.md) | 3/4 (75%) |" in benchmark_report(conn)


def test_regrade_reapplies_current_checks_to_stored_replies(tmp_path):
    scenario = next(s for s in SUITES["hard"] if s.id == "change_of_mind_followup")
    replies = [
        Reply("O C40 custa R$ 599,90; no PIX, R$ 569,91.", [ToolTrace("get_product_details", {}, {"status": "ok"})]),
        Reply("O KA-15S custa R$ 199,90.\n\nQuer ver outras opções?"),
        Reply("No PIX, o KA-15S fica R$ 189,91."),
    ]
    stored = transcript(scenario, replies) + "\nFailures: `forbidden /569,91/`\n"  # graded by an older check
    conn = results.connect(tmp_path / "results.db")
    run_id = results.start_run(conn, "ollama:test", "hard", 1, None)
    results.save_result(conn, run_id, scenario.id, 1, ["forbidden /569,91/"], ["get_product_details"], 1.0, 1, 0, stored)

    assert regrade(conn) == 1
    [row] = results.run_results(conn, run_id)
    assert row["passed"] == 1 and row["failures"] == "[]"
    assert "Failures" not in row["transcript"] and row["transcript"].endswith("R$ 189,91.\n")
    assert regrade(conn) == 0  # idempotent
