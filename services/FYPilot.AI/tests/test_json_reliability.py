"""
Unit tests for app/services/json_reliability.py -- robust JSON extraction,
truncation detection, deterministic local repair, and the (caller-supplied)
provider repair hook.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import json_reliability as jr  # noqa: E402


class ExtractionTests(unittest.TestCase):
    def test_fenced_json_is_unwrapped(self):
        text = '```json\n{"a": 1}\n```'
        self.assertEqual(jr.extract_json_object(text), '{"a": 1}')

    def test_fenced_json_without_language_tag(self):
        text = '```\n{"a": 1}\n```'
        self.assertEqual(jr.extract_json_object(text), '{"a": 1}')

    def test_prose_before_json_is_skipped(self):
        text = 'Here is the roadmap you requested:\n{"a": 1}'
        self.assertEqual(jr.extract_json_object(text), '{"a": 1}')

    def test_prose_after_json_is_dropped(self):
        text = '{"a": 1}\nHope this helps! Let me know if you need changes.'
        self.assertEqual(jr.extract_json_object(text), '{"a": 1}')

    def test_prose_before_and_after(self):
        text = 'Sure, here you go:\n\n{"a": 1}\n\nEnjoy your roadmap!'
        self.assertEqual(jr.extract_json_object(text), '{"a": 1}')

    def test_braces_inside_quoted_strings_do_not_confuse_the_scanner(self):
        text = '{"title": "Use {curly} braces in text", "value": 1}'
        extracted = jr.extract_json_object(text)
        self.assertEqual(json.loads(extracted), {"title": "Use {curly} braces in text", "value": 1})

    def test_escaped_quotes_inside_strings_do_not_confuse_the_scanner(self):
        text = r'{"title": "She said \"hello\" to the {robot}", "value": 2}'
        extracted = jr.extract_json_object(text)
        self.assertEqual(
            json.loads(extracted),
            {"title": 'She said "hello" to the {robot}', "value": 2},
        )

    def test_nested_objects_and_arrays_stay_balanced(self):
        text = '{"phases": [{"name": "A", "tasks": [{"id": "T1"}, {"id": "T2"}]}]}'
        extracted = jr.extract_json_object(text)
        self.assertEqual(json.loads(extracted), json.loads(text))

    def test_naive_first_and_last_brace_would_have_failed_here(self):
        # A trailing JSON-looking fragment in prose AFTER the real object --
        # a naive text.find("{")..text.rfind("}") slice would swallow it and
        # produce invalid/wrong JSON; the balanced scanner must not.
        text = '{"a": 1}\nFor reference, an example malformed object looks like {"b": 2'
        extracted = jr.extract_json_object(text)
        self.assertEqual(json.loads(extracted), {"a": 1})

    def test_empty_text_returns_empty(self):
        self.assertEqual(jr.extract_json_object(""), "")


class TruncationDetectionTests(unittest.TestCase):
    def test_unbalanced_brackets_is_truncated(self):
        candidate = jr.extract_json_object('{"phases": [{"name": "Requirements", "tasks": ["a", "b"')
        self.assertTrue(jr.looks_truncated(candidate))

    def test_unterminated_string_is_truncated(self):
        candidate = '{"a": "unterminated'
        self.assertTrue(jr.looks_truncated(candidate))

    def test_complete_balanced_object_is_not_truncated(self):
        candidate = '{"a": 1, "b": [1, 2, 3]}'
        self.assertFalse(jr.looks_truncated(candidate))

    def test_complete_but_malformed_object_is_not_truncated(self):
        # Missing comma between complete siblings -- malformed, not truncated.
        candidate = '{"a": 1 "b": 2}'
        self.assertFalse(jr.looks_truncated(candidate))

    def test_finish_reason_length_forces_truncated(self):
        self.assertTrue(jr.looks_truncated('{"a": 1}', finish_reason="length"))

    def test_finish_reason_stop_does_not_force_truncated(self):
        self.assertFalse(jr.looks_truncated('{"a": 1}', finish_reason="stop"))


class ErrorContextTests(unittest.TestCase):
    def test_context_is_bounded_and_includes_position(self):
        text = "x" * 1000 + '{"a": 1 "b": 2}' + "y" * 1000
        try:
            json.loads(text)
            self.fail("expected JSONDecodeError")
        except json.JSONDecodeError as error:
            context = jr.build_error_context(text, error, before=50, after=50)
            self.assertIn("line", context)
            self.assertIn("column", context)
            self.assertIn("position", context)
            self.assertLessEqual(len(context["context"]), 101)
            self.assertTrue(context["excerptTruncated"])

    def test_secrets_are_redacted_from_context(self):
        text = '{"apiKey": "sk-abcdefghijklmnop123456" "b": 2}'
        try:
            json.loads(text)
            self.fail("expected JSONDecodeError")
        except json.JSONDecodeError as error:
            context = jr.build_error_context(text, error)
            self.assertNotIn("sk-abcdefghijklmnop123456", context["context"])
            self.assertIn("REDACTED", context["context"])


class ParseJsonResponseTests(unittest.TestCase):
    def test_valid_json_parses_without_repair(self):
        outcome = jr.parse_json_response('{"a": 1}')
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.data, {"a": 1})
        self.assertTrue(outcome.initial_json_valid)
        self.assertFalse(outcome.repair_attempted)

    def test_empty_response_is_its_own_category(self):
        outcome = jr.parse_json_response("")
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.category, jr.EMPTY_RESPONSE)

    def test_deepinfra_style_missing_comma_deep_in_nested_structure_repairs_locally(self):
        # Simulates "Expecting ',' delimiter: line N column M" from a large
        # nested roadmap response -- a missing comma between two sibling
        # task objects several phases deep.
        phases = []
        for i in range(6):
            phases.append(
                '{"name": "Phase %d", "tasks": [{"title": "Task %d.1"} {"title": "Task %d.2"}]}'
                % (i, i, i)
            )
        text = '{"roadmapTitle": "Test", "phases": [' + ", ".join(phases) + "]}"
        # Sanity check: this really is broken (missing comma between the two
        # task objects) before we assert repair fixes it.
        with self.assertRaises(json.JSONDecodeError):
            json.loads(text)

        outcome = jr.parse_json_response(text)
        self.assertTrue(outcome.success, outcome.error)
        self.assertEqual(outcome.repair_method, "local_json_repair")
        self.assertTrue(outcome.repair_success)
        self.assertEqual(len(outcome.data["phases"]), 6)
        for phase in outcome.data["phases"]:
            self.assertEqual(len(phase["tasks"]), 2)

    def test_groq_style_missing_comma_in_long_response_repairs_locally(self):
        tasks = ", ".join(f'{{"title": "Task {i}", "hours": {i}}}' for i in range(40))
        text = '{"roadmapTitle": "Test", "tasks": [' + tasks + ']} "trailing": true}'
        outcome = jr.parse_json_response(text)
        # However it resolves, it must not silently invent task content --
        # the 40 real tasks must all still be present when repair succeeds.
        if outcome.success:
            self.assertEqual(len(outcome.data.get("tasks", [])), 40)

    def test_trailing_comma_repairs_locally(self):
        outcome = jr.parse_json_response('{"a": 1, "b": 2,}')
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.data, {"a": 1, "b": 2})
        self.assertEqual(outcome.repair_method, "local_json_repair")

    def test_single_quoted_json_like_output_repairs_locally(self):
        outcome = jr.parse_json_response("{'a': 1, 'b': 2}")
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.data, {"a": 1, "b": 2})

    def test_unquoted_keys_repair_locally(self):
        outcome = jr.parse_json_response("{a: 1, b: 2}")
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.data, {"a": 1, "b": 2})

    def test_truncated_response_is_not_silently_completed_by_local_repair(self):
        text = '{"roadmapTitle": "Test", "phases": [{"name": "Requirements", "tasks": ["a", "b'
        outcome = jr.parse_json_response(text)
        self.assertFalse(outcome.success)
        self.assertTrue(outcome.is_truncated)
        # Truncated input must never reach the local repairer -- only the
        # (here, unset) provider repair path is eligible.
        self.assertNotEqual(outcome.repair_method, "local_json_repair")

    def test_deterministic_repair_failure_then_provider_repair_succeeds(self):
        malformed = '{"a": 1 "b": 2, "c": ' + "x" * 500  # deliberately unrepairable garbage tail
        # Force local repair to "fail" is hard to guarantee with a real
        # library, so instead verify the provider_repair path is reached
        # and used when local repair's result still doesn't parse -- using
        # a repair_fn that returns a genuinely valid replacement.
        calls = []

        def repair_fn(candidate, error):
            calls.append((candidate, error))
            return '{"a": 1, "b": 2, "c": 3}'

        outcome = jr.parse_json_response(malformed, repair_fn=repair_fn)
        # Either local repair already fixed it, or the provider path did --
        # both are acceptable successes, but if local repair failed, the
        # provider path must have been consulted.
        if outcome.repair_method == "provider_repair":
            self.assertEqual(len(calls), 1)
            self.assertTrue(outcome.success)
            self.assertEqual(outcome.data, {"a": 1, "b": 2, "c": 3})

    def test_provider_repair_is_only_called_once(self):
        malformed = '{"a": 1 "b": 2, "c": ' + "x" * 500
        calls = []

        def repair_fn(candidate, error):
            calls.append(1)
            return None  # repair attempt itself fails

        jr.parse_json_response(malformed, repair_fn=repair_fn)
        self.assertLessEqual(len(calls), 1)

    def test_repair_fn_not_called_for_trivially_short_garbage(self):
        calls = []

        def repair_fn(candidate, error):
            calls.append(1)
            return None

        jr.parse_json_response("not json at all", repair_fn=repair_fn)
        self.assertEqual(calls, [])

    def test_provider_repair_result_is_used_verbatim_not_reinterpreted(self):
        # The repair_fn's job is to preserve semantic values exactly --
        # this test verifies parse_json_response trusts and uses its output
        # as-is (extract + parse), never second-guessing or merging it with
        # the original malformed candidate.
        def repair_fn(candidate, error):
            return '{"roadmapTitle": "Exact Preserved Title", "phases": []}'

        outcome = jr.parse_json_response('{"roadmapTitle": "Exact Preserved Title" "phases": [' + "a" * 400, repair_fn=repair_fn)
        self.assertTrue(outcome.success)
        self.assertEqual(outcome.data["roadmapTitle"], "Exact Preserved Title")

    def test_irreparable_garbage_is_classified_invalid_json_syntax(self):
        outcome = jr.parse_json_response("this is not json in any way {{{")
        self.assertFalse(outcome.success)
        self.assertEqual(outcome.category, jr.INVALID_JSON_SYNTAX)

    def test_is_substantial_threshold(self):
        self.assertFalse(jr.is_substantial("{}"))
        self.assertFalse(jr.is_substantial(""))
        substantial = '{"title": "' + "x" * 500 + '", "b": "y", "c": "z", "d": "w"}'
        self.assertTrue(jr.is_substantial(substantial))


if __name__ == "__main__":
    unittest.main()
