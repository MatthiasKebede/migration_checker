import json
import os
import io
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
PYMIGBENCH_ROOT = REPO_ROOT.parent / "PyMigBench"
if PYMIGBENCH_ROOT.exists() and str(PYMIGBENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(PYMIGBENCH_ROOT))

from evaluation.scripts.evaluate import (
    ExpectedDiagnostic,
    LEFTOVER_GROUPS,
    SnapshotCache,
    analyze_content,
    build_by_code_rows,
    build_track_summary_rows,
    discover_feasible_pair_configs,
    derive_leftover_oracle,
    evaluate_clean_post,
    evaluate_fault_injection,
    get_by_code_csv_path,
    get_track_summary_csv_path,
    get_effective_fault_types,
    get_github_token,
    inject_fault,
    load_evaluator_env,
    main,
    normalize_output_json_path,
    print_aggregate_summary,
    sanitize_component,
    score_expected,
    write_by_code_csv,
    write_track_summary_csv,
)
from migration_checker.rules import load_rules
from pymigbench.line_range import LineRange


class FakeMigrationFile:
    def __init__(self, path: str):
        self.path = path


class FakeMigration:
    def __init__(self, repo: str, commit: str, source: str, target: str, identifier: str):
        self.repo = repo
        self.commit = commit
        self.source = source
        self.target = target
        self._identifier = identifier
        self.files = []

    def id(self) -> str:
        return self._identifier


class EvaluateHelpersTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_rules(REPO_ROOT / "rules/http_clients/requests_to_aiohttp.yml")
        cls.clean_post_content = (REPO_ROOT / "fixtures/requests/sample_aiohttp.py").read_text(encoding="utf-8")
        cls.pre_content = (REPO_ROOT / "fixtures/requests/sample_requests.py").read_text(encoding="utf-8")

    def test_snapshot_cache_uses_local_metadata_and_snapshot_for_pre_content(self):
        migration = FakeMigration("owner/repo", "postsha", "requests", "aiohttp", "requests__aiohttp__owner_repo__postsha")
        migration_file = FakeMigrationFile("pkg/example.py")

        with tempfile.TemporaryDirectory() as temp_dir:
            cache = SnapshotCache(Path(temp_dir), token=None, refresh=False)
            migration_dir = cache.root / sanitize_component(migration.id())
            snapshot_path = migration_dir / "pre" / migration_file.path
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text("import requests\n", encoding="utf-8")
            (migration_dir / "metadata.json").write_text(
                json.dumps({"parent_sha": "presha"}),
                encoding="utf-8",
            )

            content, reason = cache.get_snapshot(migration, migration_file, "pre")

        self.assertIsNone(reason)
        self.assertEqual(content, "import requests\n")
        self.assertEqual(cache.stats["metadata_hits"], 1)
        self.assertEqual(cache.stats["snapshot_hits"], 1)

    def test_default_fault_injectors_are_parseable_and_detectable(self):
        cases = [
            ("source-import", self.clean_post_content),
            ("source-call-swap", self.clean_post_content),
            (
                "mixed-usage",
                "import aiohttp\nresponse = aiohttp.ClientSession.get('https://example.com')\nprint(response)\n",
            ),
        ]

        for fault_type, content in cases:
            with self.subTest(fault_type=fault_type):
                injected_content, expected, error = inject_fault(content, self.rules, fault_type)
                self.assertIsNone(error)
                self.assertTrue(expected)
                diagnostics, analysis_error = analyze_content(injected_content, self.rules)
                self.assertIsNone(analysis_error)
                score = score_expected(diagnostics, expected)
                self.assertGreaterEqual(score["tp"], 1)
                self.assertEqual(score["fn"], 0)

    def test_pair_specific_fault_injectors_are_parseable_and_detectable(self):
        cases = [
            ("aiohttp-missing-context", self.clean_post_content, "missing_context_manager"),
            ("aiohttp-missing-await-method", self.clean_post_content, "missing_await"),
            ("aiohttp-renamed-attribute", self.clean_post_content, "renamed_attribute_access"),
            ("aiohttp-forbidden-attribute", self.clean_post_content, "forbidden_attribute_access"),
        ]

        for fault_type, content, expected_code in cases:
            with self.subTest(fault_type=fault_type):
                injected_content, expected, error = inject_fault(content, self.rules, fault_type)
                self.assertIsNone(error)
                self.assertTrue(expected)
                self.assertIn(expected_code, {item.code for item in expected})
                diagnostics, analysis_error = analyze_content(injected_content, self.rules)
                self.assertIsNone(analysis_error)
                score = score_expected(diagnostics, expected)
                self.assertGreaterEqual(score["tp"], 1)
                self.assertEqual(score["fn"], 0)

    def test_get_effective_fault_types_includes_pair_specific_by_default(self):
        fault_types, skip_reason = get_effective_fault_types(self.rules, ("source-import",), "default")
        self.assertIsNone(skip_reason)
        self.assertIn("source-import", fault_types)
        self.assertIn("aiohttp-missing-context", fault_types)

    def test_get_effective_fault_types_reports_unsupported_for_only_mode(self):
        quart_rules = load_rules(REPO_ROOT / "rules/web_framework/flask_to_fastapi.yml")
        fault_types, skip_reason = get_effective_fault_types(quart_rules, ("source-import",), "only")
        self.assertEqual(fault_types, [])
        self.assertEqual(skip_reason, "pair-faults-unsupported")

    def test_clean_post_track_reports_zero_false_positives_for_clean_fixture(self):
        migration = FakeMigration("owner/repo", "postsha", "requests", "aiohttp", "requests__aiohttp__owner_repo__postsha")
        migration_file = FakeMigrationFile("pkg/example.py")
        migration.files = [migration_file]

        with tempfile.TemporaryDirectory() as temp_dir:
            cache = SnapshotCache(Path(temp_dir), token=None, refresh=False)
            snapshot_path = cache.root / sanitize_component(migration.id()) / "post" / migration_file.path
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(self.clean_post_content, encoding="utf-8")

            report = evaluate_clean_post([migration], self.rules, cache, "test-version")

        self.assertEqual(report["summary"]["files_evaluated"], 1)
        self.assertEqual(report["summary"]["false_positive_diagnostics"], 0)
        self.assertEqual(report["summary"]["clean_files"], 1)

    def test_fault_injection_scoring_excludes_matching_baseline_diagnostics(self):
        diagnostic, _ = analyze_content("import requests\n", self.rules, enabled_groups=LEFTOVER_GROUPS)
        expected = [
            ExpectedDiagnostic(
                "source-import",
                "leftover_source_import",
                LineRange.from_bounds(1, 1),
            )
        ]

        score = score_expected(diagnostic, expected, baseline=diagnostic)

        self.assertEqual(score["tp"], 0)
        self.assertEqual(score["fp"], 0)
        self.assertEqual(score["fn"], 1)

    def test_fault_injection_scoring_tracks_duplicate_missing_expectations(self):
        diagnostics = [analyze_content("import requests\n", self.rules, enabled_groups=LEFTOVER_GROUPS)[0][0]]
        expected = [
            ExpectedDiagnostic("source-import", "leftover_source_import", LineRange.from_bounds(1, 1)),
            ExpectedDiagnostic("source-import", "leftover_source_import", LineRange.from_bounds(1, 1)),
        ]

        score = score_expected(diagnostics, expected)

        self.assertEqual(score["tp"], 1)
        self.assertEqual(score["fn"], 1)
        self.assertEqual(len(score["missing"]), 1)

    def test_pre_leftover_oracle_matches_fixture(self):
        expected = derive_leftover_oracle(self.pre_content, self.rules)
        diagnostics, analysis_error = analyze_content(self.pre_content, self.rules, enabled_groups=LEFTOVER_GROUPS)

        self.assertIsNone(analysis_error)
        score = score_expected(diagnostics, expected)
        self.assertEqual(score["tp"], len(expected))
        self.assertEqual(score["fp"], 0)
        self.assertEqual(score["fn"], 0)

    def test_pre_leftover_oracle_includes_source_access_rules(self):
        quart_rules = load_rules(REPO_ROOT / "rules/web_framework/flask_to_quart.yml")
        content = "from flask import request\n\ndef handler():\n    return request.form\n"

        expected = derive_leftover_oracle(content, quart_rules)
        codes_and_lines = {(item.code, str(item.line_range)) for item in expected}

        self.assertIn(("leftover_source_import", "1"), codes_and_lines)
        self.assertIn(("leftover_source_call", "4"), codes_and_lines)

    def test_github_token_reads_environment_only(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "token-value"}, clear=False):
            self.assertEqual(get_github_token(), "token-value")

        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(get_github_token())

    def test_load_evaluator_env_uses_dotenv_files_without_override(self):
        with patch("evaluation.scripts.evaluate.load_dotenv") as mock_load_dotenv:
            load_evaluator_env()

        self.assertEqual(mock_load_dotenv.call_count, 2)
        expected_calls = [
            ((REPO_ROOT / "evaluation" / "scripts" / ".env",), {"override": False}),
            ((REPO_ROOT / ".env",), {"override": False}),
        ]
        self.assertEqual(mock_load_dotenv.call_args_list[0].args, expected_calls[0][0])
        self.assertEqual(mock_load_dotenv.call_args_list[0].kwargs, expected_calls[0][1])
        self.assertEqual(mock_load_dotenv.call_args_list[1].args, expected_calls[1][0])
        self.assertEqual(mock_load_dotenv.call_args_list[1].kwargs, expected_calls[1][1])

    def test_environment_token_takes_precedence_over_dotenv_loading(self):
        def fake_load_dotenv(*_args, **_kwargs):
            os.environ.setdefault("GITHUB_TOKEN", "dotenv-token")
            return True

        with patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}, clear=True):
            with patch("evaluation.scripts.evaluate.load_dotenv", side_effect=fake_load_dotenv):
                load_evaluator_env()
                self.assertEqual(get_github_token(), "env-token")

    def test_normalize_output_json_path_uses_results_dir_for_bare_name(self):
        output_path = normalize_output_json_path(Path("testing"))
        self.assertEqual(output_path, REPO_ROOT / "evaluation" / "data" / "results" / "testing.json")

    def test_normalize_output_json_path_uses_results_dir_for_filename_json(self):
        output_path = normalize_output_json_path(Path("testing.json"))
        self.assertEqual(output_path, REPO_ROOT / "evaluation" / "data" / "results" / "testing.json")

    def test_write_track_specific_csvs_emit_separate_row_shapes(self):
        report = {
            "benchmark_version": "2.2.5",
            "evaluated_pairs": [
                {"source": "requests", "target": "aiohttp", "migration_count": 3},
            ],
            "summary": {},
            "pair_reports": [
                {
                    "rule_pair": {"source": "requests", "target": "aiohttp"},
                    "migration_count": 3,
                    "tracks": [
                        {
                            "benchmark_version": "2.2.5",
                            "rule_pair": {"source": "requests", "target": "aiohttp"},
                            "track": "clean-post",
                            "summary": {
                                "files_evaluated": 3,
                                "clean_files": 2,
                                "files_with_diagnostics": 1,
                                "false_positive_diagnostics": 1,
                                "clean_file_rate": 0.67,
                            },
                            "skips": {"post-missing": 1},
                        },
                        {
                            "benchmark_version": "2.2.5",
                            "rule_pair": {"source": "requests", "target": "aiohttp"},
                            "track": "fault-inject",
                            "summary": {
                                "variants_evaluated": 4,
                                "true_positives": 3,
                                "false_positives": 1,
                                "false_negatives": 0,
                                "precision": 0.75,
                                "recall": 1.0,
                            },
                            "summary_by_code": {
                                "missing_await": {
                                    "true_positives": 2,
                                    "false_positives": 0,
                                    "false_negatives": 0,
                                    "precision": 1.0,
                                    "recall": 1.0,
                                }
                            },
                            "skips": {},
                        },
                    ],
                },
            ],
        }

        clean_rows = build_track_summary_rows(report, "clean-post")
        fault_rows = build_track_summary_rows(report, "fault-inject")
        self.assertEqual(len(clean_rows), 1)
        self.assertEqual(len(fault_rows), 1)
        self.assertIn("false_positive_diagnostics", clean_rows[0])
        self.assertNotIn("clean_files", fault_rows[0])
        self.assertIn("variants_evaluated", fault_rows[0])
        self.assertNotIn("files_evaluated", fault_rows[0])

        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "testing.json"
            clean_csv_path = get_track_summary_csv_path(json_path, "clean-post")
            detection_csv_path = get_track_summary_csv_path(json_path, "fault-inject")
            write_track_summary_csv(clean_csv_path, report, "clean-post")
            write_track_summary_csv(detection_csv_path, report, "fault-inject")
            clean_csv_text = clean_csv_path.read_text(encoding="utf-8")
            detection_csv_text = detection_csv_path.read_text(encoding="utf-8")

        self.assertIn("track", clean_csv_text)
        self.assertIn("clean-post", clean_csv_text)
        self.assertIn("false_positive_diagnostics", clean_csv_text)
        self.assertNotIn("fault-inject", clean_csv_text)

        self.assertIn("track", detection_csv_text)
        self.assertIn("fault-inject", detection_csv_text)
        self.assertIn("precision", detection_csv_text)
        self.assertNotIn("clean-post", detection_csv_text)
        self.assertNotIn("files_evaluated", detection_csv_text)

        by_code_rows = build_by_code_rows(report)
        self.assertEqual(len(by_code_rows), 1)
        self.assertEqual(by_code_rows[0]["code"], "missing_await")

    def test_discover_feasible_pair_configs_finds_direct_benchmark_backed_pairs(self):
        configs = discover_feasible_pair_configs(PYMIGBENCH_ROOT, limit=1)
        pair_set = {(config["pair"]["source"], config["pair"]["target"]) for config in configs}

        expected_pairs = {
            ("requests", "aiohttp"),
            ("argparse", "click"),
            ("argparse", "docopt"),
            ("argparse", "configargparse"),
            ("flask", "quart"),
            ("flask", "fastapi"),
            ("flask", "bottle"),
        }
        self.assertEqual(pair_set, expected_pairs)
        self.assertTrue(all(len(config["migrations"]) == 1 for config in configs))

    def test_discover_feasible_pair_configs_honors_pair_filter(self):
        configs = discover_feasible_pair_configs(
            PYMIGBENCH_ROOT,
            source="flask",
            target="quart",
            limit=2,
        )

        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["pair"], {"source": "flask", "target": "quart"})
        self.assertEqual(len(configs[0]["migrations"]), 2)

    def test_get_track_summary_csv_path_uses_clean_post_sidecar_name(self):
        json_path = REPO_ROOT / "evaluation" / "data" / "results" / "testing.json"
        csv_path = get_track_summary_csv_path(json_path, "clean-post")
        self.assertEqual(
            csv_path,
            REPO_ROOT / "evaluation" / "data" / "results" / "testing_clean_post_summary.csv",
        )

    def test_get_track_summary_csv_path_uses_fault_inject_sidecar_name(self):
        json_path = REPO_ROOT / "evaluation" / "data" / "results" / "testing.json"
        csv_path = get_track_summary_csv_path(json_path, "fault-inject")
        self.assertEqual(
            csv_path,
            REPO_ROOT / "evaluation" / "data" / "results" / "testing_fault_inject_summary.csv",
        )

    def test_get_by_code_csv_path_uses_parallel_sidecar_name(self):
        json_path = REPO_ROOT / "evaluation" / "data" / "results" / "testing.json"
        csv_path = get_by_code_csv_path(json_path)
        self.assertEqual(
            csv_path,
            REPO_ROOT / "evaluation" / "data" / "results" / "testing_by_code.csv",
        )

    def test_write_by_code_csv_emits_one_row_per_pair_track_code(self):
        report = {
            "benchmark_version": "2.2.5",
            "pair_reports": [
                {
                    "rule_pair": {"source": "requests", "target": "aiohttp"},
                    "migration_count": 3,
                    "tracks": [
                        {
                            "benchmark_version": "2.2.5",
                            "track": "fault-inject",
                            "summary": {},
                            "summary_by_code": {
                                "missing_await": {
                                    "true_positives": 2,
                                    "false_positives": 1,
                                    "false_negatives": 0,
                                    "precision": 0.67,
                                    "recall": 1.0,
                                }
                            },
                            "skips": {},
                        }
                    ],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "testing_fault_inject_by_code.csv"
            write_by_code_csv(csv_path, report, track="fault-inject")
            csv_text = csv_path.read_text(encoding="utf-8")

        self.assertIn("code", csv_text)
        self.assertIn("missing_await", csv_text)

    def test_evaluate_fault_injection_includes_per_code_summary(self):
        migration = FakeMigration("owner/repo", "postsha", "requests", "aiohttp", "requests__aiohttp__owner_repo__postsha")
        migration_file = FakeMigrationFile("pkg/example.py")
        migration.files = [migration_file]

        with tempfile.TemporaryDirectory() as temp_dir:
            cache = SnapshotCache(Path(temp_dir), token=None, refresh=False)
            snapshot_path = cache.root / sanitize_component(migration.id()) / "post" / migration_file.path
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot_path.write_text(self.clean_post_content, encoding="utf-8")

            report = evaluate_fault_injection(
                [migration],
                self.rules,
                cache,
                "test-version",
                ["source-import"],
                "default",
            )

        self.assertIn("leftover_source_import", report["summary_by_code"])
        self.assertIn("missing_context_manager", report["summary_by_code"])

    def test_print_aggregate_summary_includes_average_metrics(self):
        report = {
            "summary": {
                "clean-post": {
                    "files_evaluated": 3,
                    "clean_files": 2,
                    "files_with_diagnostics": 1,
                    "false_positive_diagnostics": 1,
                    "clean_file_rate": 0.67,
                },
                "fault-inject": {
                    "variants_evaluated": 4,
                    "true_positives": 3,
                    "false_positives": 1,
                    "false_negatives": 1,
                    "precision": 0.75,
                    "recall": 0.75,
                },
            },
            "pair_reports": [
                {
                    "rule_pair": {"source": "requests", "target": "aiohttp"},
                    "tracks": [
                        {"track": "clean-post", "summary": {"clean_file_rate": 1.0}},
                        {"track": "fault-inject", "summary": {"precision": 0.5, "recall": 1.0}},
                    ],
                },
                {
                    "rule_pair": {"source": "flask", "target": "quart"},
                    "tracks": [
                        {"track": "clean-post", "summary": {"clean_file_rate": 0.33}},
                        {"track": "fault-inject", "summary": {"precision": 1.0, "recall": 0.5}},
                    ],
                },
            ],
        }

        output = io.StringIO()
        with redirect_stdout(output):
            print_aggregate_summary(report)
        text = output.getvalue()

        self.assertIn("Aggregate Track Metrics:", text)
        self.assertIn("average_clean_file_rate_across_pairs", text)
        self.assertIn("average_precision_across_pairs", text)
        self.assertIn("aggregate_precision", text)

    def test_main_writes_by_code_csv_and_prints_aggregate_metrics(self):
        fake_pair_reports = [
            {
                "rule_pair": {"source": "requests", "target": "aiohttp"},
                "migration_count": 1,
                "tracks": [
                    {
                        "benchmark_version": "test-version",
                        "rule_pair": {"source": "requests", "target": "aiohttp"},
                        "track": "fault-inject",
                        "summary": {
                            "variants_evaluated": 1,
                            "true_positives": 1,
                            "false_positives": 0,
                            "false_negatives": 0,
                            "precision": 1.0,
                            "recall": 1.0,
                        },
                        "summary_by_code": {
                            "missing_await": {
                                "true_positives": 1,
                                "false_positives": 0,
                                "false_negatives": 0,
                                "precision": 1.0,
                                "recall": 1.0,
                            }
                        },
                        "per_file": [],
                        "skips": {},
                        "cache_stats": {},
                    }
                ],
            }
        ]

        with tempfile.TemporaryDirectory() as temp_dir:
            pymigbench_path = Path(temp_dir) / "PyMigBench"
            pymigbench_path.mkdir()
            (pymigbench_path / "version").write_text("test-version", encoding="utf-8")
            output_stem = Path(temp_dir) / "report"

            with patch("evaluation.scripts.evaluate.discover_feasible_pair_configs", return_value=[{"pair": {"source": "requests", "target": "aiohttp"}, "rules": self.rules, "migrations": [object()]}]):
                with patch("evaluation.scripts.evaluate.run_selected_tracks", return_value=fake_pair_reports[0]["tracks"]):
                    output = io.StringIO()
                    with patch.object(sys, "argv", ["evaluate.py", str(pymigbench_path), "--output-json", str(output_stem)]):
                        with redirect_stdout(output):
                            main()

            text = output.getvalue()
            self.assertIn("Aggregate Track Metrics:", text)
            self.assertTrue(output_stem.with_suffix(".json").exists())
            self.assertTrue(output_stem.with_name("report_fault_inject_summary.csv").exists())
            self.assertTrue(output_stem.with_name("report_by_code.csv").exists())
            self.assertFalse(output_stem.with_name("report_clean_post_summary.csv").exists())


if __name__ == "__main__":
    unittest.main()
