from pathlib import Path
import sys
import textwrap
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from migration_checker.collector import collect_facts


class CollectorFactTests(unittest.TestCase):
    def collect(self, source: str):
        return collect_facts(textwrap.dedent(source))

    def test_records_import_aliases(self):
        facts = self.collect(
            """
            import requests as rq
            from flask import request as incoming
            """
        )

        imports = {(item["name"], item["alias"]) for item in facts.imports}
        self.assertIn(("requests", "rq"), imports)
        self.assertIn(("flask.request", "incoming"), imports)

    def test_records_decorator_calls(self):
        facts = self.collect(
            """
            import click

            @click.command()
            @click.option("--count", default=1)
            def main(count):
                return count
            """
        )

        decorator_calls = {
            call["qualified_name"]
            for call in facts.calls
            if call["in_decorator"]
        }
        self.assertIn("click.command", decorator_calls)
        self.assertIn("click.option", decorator_calls)

    def test_records_async_with_bindings_and_awaited_methods(self):
        facts = self.collect(
            """
            import aiohttp

            async def fetch():
                async with aiohttp.ClientSession() as session:
                    async with session.get("https://example.com") as response:
                        payload = await response.text()
                        return payload
            """
        )

        response_binding = next(binding for binding in facts.bindings if binding["variable_name"] == "response")
        self.assertEqual(response_binding["origin_kind"], "call")
        self.assertEqual(response_binding["context_usage"], "async_with")

        text_call = next(call for call in facts.calls if call.get("method_name") == "text")
        self.assertTrue(text_call["awaited"])

    def test_records_qualified_access_on_imported_object(self):
        facts = self.collect(
            """
            from flask import request

            def handler():
                return request.form
            """
        )

        access = next(item for item in facts.accesses if item["attribute_name"] == "form")
        self.assertEqual(access["base_name"], "request")
        self.assertEqual(access["qualified_name"], "flask.request.form")


if __name__ == "__main__":
    unittest.main()
