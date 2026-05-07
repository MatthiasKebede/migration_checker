from pathlib import Path
import sys
import tempfile
import textwrap
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from migration_checker.main import analyze_source_code
from migration_checker.rules import load_rules


FULL_SEVERITY_BLOCK = """
diagnostics:
  severity:
    leftover_source_import: error
    leftover_source_call: error
    renamed_keyword_argument: error
    forbidden_keyword_argument: error
    missing_required_keyword: error
    positional_argument_misuse: error
    missing_await: error
    missing_context_manager: error
    mixed_source_target_assignment: warning
    renamed_attribute_access: error
    forbidden_attribute_access: error
    duplicate_migration_usage: warning
"""


class AnalysisBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.aiohttp_rules = load_rules(REPO_ROOT / "rules/http_clients/requests_to_aiohttp.yml")
        cls.quart_rules = load_rules(REPO_ROOT / "rules/web_framework/flask_to_quart.yml")

    def load_inline_rule(self, call_rule_block: str):
        call_rule_text = textwrap.indent(textwrap.dedent(call_rule_block).strip(), "  ")
        rule_text = textwrap.dedent(
            """
            pair:
              source: source_lib
              target: target_lib
            libraries:
              source_roots:
                - source_lib
              target_roots:
                - target_lib
            """
        ).strip()
        rule_text = "\n".join(
            [
                rule_text,
                textwrap.dedent(FULL_SEVERITY_BLOCK).strip(),
                "rules:",
                "  - id: source-import-root",
                "    kind: import",
                "    source:",
                "      module: source_lib",
                "    target:",
                "      module: target_lib",
                call_rule_text,
                "",
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            rule_path = Path(temp_dir) / "inline_rule.yml"
            rule_path.write_text(rule_text, encoding="utf-8")
            return load_rules(rule_path)

    def test_reports_missing_required_keyword(self):
        rules = self.load_inline_rule(
            """
            - id: target-run
              kind: call
              source:
                symbol: source_lib.run
              target:
                symbol: target_lib.run
              contract:
                renamed_keywords: {}
                forbidden_keywords: []
                required_keywords:
                  - mode
                require_keywords: false
              usage:
                await_required: false
                context_manager: none
              return:
                tag: result
                renamed_attributes: {}
                forbidden_attributes: []
                await_required_methods: []
            """
        )

        diagnostics = analyze_source_code("import target_lib\ntarget_lib.run()\n", rules)
        codes = {diagnostic["code"] for diagnostic in diagnostics}
        self.assertIn("missing_required_keyword", codes)

    def test_reports_positional_argument_misuse(self):
        rules = self.load_inline_rule(
            """
            - id: target-run
              kind: call
              source:
                symbol: source_lib.run
              target:
                symbol: target_lib.run
              contract:
                renamed_keywords: {}
                forbidden_keywords: []
                required_keywords: []
                require_keywords: true
              usage:
                await_required: false
                context_manager: none
              return:
                tag: result
                renamed_attributes: {}
                forbidden_attributes: []
                await_required_methods: []
            """
        )

        diagnostics = analyze_source_code("import target_lib\ntarget_lib.run('fast')\n", rules)
        codes = {diagnostic["code"] for diagnostic in diagnostics}
        self.assertIn("positional_argument_misuse", codes)

    def test_reports_missing_await_for_aiohttp_response_method(self):
        source = textwrap.dedent(
            """
            import aiohttp

            async def fetch():
                async with aiohttp.ClientSession() as session:
                    async with session.get("https://example.com") as response:
                        return response.text()
            """
        )

        diagnostics = analyze_source_code(source, self.aiohttp_rules)
        codes = {diagnostic["code"] for diagnostic in diagnostics}
        self.assertIn("missing_await", codes)

    def test_reports_renamed_attribute_access_for_aiohttp_status(self):
        source = textwrap.dedent(
            """
            import aiohttp

            async def fetch():
                async with aiohttp.ClientSession() as session:
                    async with session.get("https://example.com") as response:
                        return response.status_code
            """
        )

        diagnostics = analyze_source_code(source, self.aiohttp_rules)
        codes = {diagnostic["code"] for diagnostic in diagnostics}
        self.assertIn("renamed_attribute_access", codes)

    def test_reports_leftover_source_access_for_flask_request_form(self):
        source = textwrap.dedent(
            """
            from flask import request

            def handler():
                return request.form
            """
        )

        diagnostics = analyze_source_code(source, self.quart_rules, enabled_groups=["leftover"])
        codes = {diagnostic["code"] for diagnostic in diagnostics}
        self.assertIn("leftover_source_call", codes)

    def test_reports_duplicate_migration_usage(self):
        source = textwrap.dedent(
            """
            import flask
            from quart import Quart

            def create_app():
                app = flask.Flask(__name__)
                app = Quart(__name__)
                return app
            """
        )

        diagnostics = analyze_source_code(source, self.quart_rules)
        codes = {diagnostic["code"] for diagnostic in diagnostics}
        self.assertIn("duplicate_migration_usage", codes)


if __name__ == "__main__":
    unittest.main()
