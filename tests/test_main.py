import json
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class VerifierJsonOutputTests(unittest.TestCase):
    def run_checker_raw(self, *args, check=True):
        command = [sys.executable, "-m", "migration_checker", *args]
        return subprocess.run(
            command,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=check,
        )

    def run_checker(self, *args):
        result = self.run_checker_raw(*args, "--output-json")
        return json.loads(result.stdout)

    def test_json_output_contains_code_and_severity_with_pair_selection(self):
        payload = self.run_checker(
            "fixtures/requests/sample_requests.py",
            "--source",
            "requests",
            "--target",
            "aiohttp",
        )
        self.assertTrue(payload)
        diagnostics = next(iter(payload.values()))
        self.assertGreaterEqual(len(diagnostics), 2)

        for diagnostic in diagnostics:
            self.assertIn("line", diagnostic)
            self.assertIn("message", diagnostic)
            self.assertIn("code", diagnostic)
            self.assertIn("severity", diagnostic)

    def test_rule_file_override_still_works(self):
        payload = self.run_checker(
            "fixtures/requests/sample_requests.py",
            "--rule-file",
            "rules/http_clients/requests_to_aiohttp.yml",
        )
        self.assertTrue(payload)

    def test_requests_httpx_invalid_fixture_is_flagged(self):
        payload = self.run_checker(
            "fixtures/requests/invalid_httpx.py",
            "--source",
            "requests",
            "--target",
            "httpx",
        )
        diagnostics = next(iter(payload.values()))
        self.assertEqual(diagnostics[0]["code"], "forbidden_attribute_access")

    def test_pandas_dask_invalid_fixture_is_flagged(self):
        payload = self.run_checker(
            "fixtures/pandas/invalid_dask.py",
            "--source",
            "pandas",
            "--target",
            "dask.dataframe",
        )
        diagnostics = next(iter(payload.values()))
        self.assertEqual(diagnostics[0]["code"], "forbidden_attribute_access")

    def test_matplotlib_bokeh_invalid_fixture_is_flagged(self):
        payload = self.run_checker(
            "fixtures/matplotlib/invalid_bokeh.py",
            "--source",
            "matplotlib.pyplot",
            "--target",
            "bokeh.plotting",
        )
        diagnostics = next(iter(payload.values()))
        self.assertEqual(diagnostics[0]["code"], "forbidden_attribute_access")

    def test_argparse_click_invalid_fixture_is_flagged(self):
        payload = self.run_checker(
            "fixtures/argparse/invalid_click.py",
            "--source",
            "argparse",
            "--target",
            "click",
        )
        diagnostics = next(iter(payload.values()))
        self.assertEqual(diagnostics[0]["code"], "forbidden_keyword_argument")

    def test_argparse_click_leftover_parse_args_fixture_is_flagged(self):
        payload = self.run_checker(
            "fixtures/argparse/invalid_click_parse_args.py",
            "--source",
            "argparse",
            "--target",
            "click",
        )
        codes = [diagnostic["code"] for diagnostic in next(iter(payload.values()))]
        self.assertIn("leftover_source_call", codes)

    def test_flask_quart_invalid_fixture_is_flagged(self):
        payload = self.run_checker(
            "fixtures/flask/invalid_quart.py",
            "--source",
            "flask",
            "--target",
            "quart",
        )
        diagnostics = next(iter(payload.values()))
        self.assertEqual(diagnostics[0]["code"], "leftover_source_import")

    def test_requests_aiohttp_status_fixture_reports_renamed_attribute_access(self):
        payload = self.run_checker(
            "fixtures/requests/invalid_aiohttp_status.py",
            "--source",
            "requests",
            "--target",
            "aiohttp",
        )
        codes = [diagnostic["code"] for diagnostic in next(iter(payload.values()))]
        self.assertIn("renamed_attribute_access", codes)

    def test_requests_aiohttp_missing_await_fixture_is_flagged(self):
        payload = self.run_checker(
            "fixtures/requests/invalid_aiohttp_missing_await.py",
            "--source",
            "requests",
            "--target",
            "aiohttp",
        )
        codes = [diagnostic["code"] for diagnostic in next(iter(payload.values()))]
        self.assertIn("missing_await", codes)

    def test_flask_quart_template_fixture_is_flagged(self):
        payload = self.run_checker(
            "fixtures/flask/invalid_quart_template.py",
            "--source",
            "flask",
            "--target",
            "quart",
        )
        codes = [diagnostic["code"] for diagnostic in next(iter(payload.values()))]
        self.assertIn("missing_await", codes)

    def test_flask_quart_request_json_fixture_is_flagged(self):
        payload = self.run_checker(
            "fixtures/flask/invalid_quart_request_json.py",
            "--source",
            "flask",
            "--target",
            "quart",
        )
        codes = [diagnostic["code"] for diagnostic in next(iter(payload.values()))]
        self.assertIn("missing_await", codes)

    def test_complex_base_fixtures_are_clean(self):
        cases = [
            ("fixtures/requests/complex_aiohttp.py", "requests", "aiohttp"),
            ("fixtures/pandas/complex_polars.py", "pandas", "polars"),
            ("fixtures/matplotlib/complex_plotly.py", "matplotlib.pyplot", "plotly.express"),
            ("fixtures/argparse/complex_click.py", "argparse", "click"),
            ("fixtures/flask/complex_quart.py", "flask", "quart"),
        ]

        for fixture_path, source, target in cases:
            with self.subTest(fixture=fixture_path):
                payload = self.run_checker(
                    fixture_path,
                    "--source",
                    source,
                    "--target",
                    target,
                )
                self.assertEqual(payload, {})

    def test_missing_input_path_exits_nonzero(self):
        result = self.run_checker_raw(
            "fixtures/does_not_exist.py",
            "--source",
            "requests",
            "--target",
            "aiohttp",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("File or directory not found", result.stdout)

    def test_invalid_rule_selection_exits_nonzero(self):
        result = self.run_checker_raw(
            "fixtures/requests/sample_httpx.py",
            "--source",
            "requests",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Both --source and --target are required together", result.stdout)


if __name__ == "__main__":
    unittest.main()
