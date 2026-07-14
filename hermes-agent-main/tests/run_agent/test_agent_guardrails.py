"""Unit tests for AIAgent pre/post-LLM-call guardrails.

Covers two static methods on AIAgent (inspired by PR #1321 — @alireza78a):
  - _sanitize_api_messages()    — Phase 1: orphaned tool pair repair
  - _deduplicate_tool_calls()   — Phase 2: identical call deduplication
"""

import types

from run_agent import AIAgent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_tc(name: str, arguments: str = "{}") -> types.SimpleNamespace:
    """Create a minimal tool_call SimpleNamespace mirroring the OpenAI SDK object."""
    tc = types.SimpleNamespace()
    tc.function = types.SimpleNamespace(name=name, arguments=arguments)
    return tc


def tool_result(call_id: str, content: str = "ok") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def assistant_dict_call(call_id: str, name: str = "terminal") -> dict:
    """Dict-style tool_call (as stored in message history)."""
    return {"id": call_id, "function": {"name": name, "arguments": "{}"}}


# ---------------------------------------------------------------------------
# Phase 1 — _sanitize_api_messages
# ---------------------------------------------------------------------------

class TestSanitizeApiMessages:

    def test_orphaned_result_removed(self):
        msgs = [
            {"role": "assistant", "tool_calls": [assistant_dict_call("c1")]},
            tool_result("c1"),
            tool_result("c_ORPHAN"),
        ]
        out = AIAgent._sanitize_api_messages(msgs)
        assert len(out) == 2
        assert all(m.get("tool_call_id") != "c_ORPHAN" for m in out)

    def test_orphaned_call_gets_stub_result(self):
        msgs = [
            {"role": "assistant", "tool_calls": [assistant_dict_call("c2")]},
        ]
        out = AIAgent._sanitize_api_messages(msgs)
        assert len(out) == 2
        stub = out[1]
        assert stub["role"] == "tool"
        assert stub["tool_call_id"] == "c2"
        assert stub["content"]

    def test_clean_messages_pass_through(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "tool_calls": [assistant_dict_call("c3")]},
            tool_result("c3"),
            {"role": "assistant", "content": "done"},
        ]
        out = AIAgent._sanitize_api_messages(msgs)
        assert out == msgs

    def test_mixed_orphaned_result_and_orphaned_call(self):
        msgs = [
            {"role": "assistant", "tool_calls": [
                assistant_dict_call("c4"),
                assistant_dict_call("c5"),
            ]},
            tool_result("c4"),
            tool_result("c_DANGLING"),
        ]
        out = AIAgent._sanitize_api_messages(msgs)
        ids = [m.get("tool_call_id") for m in out if m.get("role") == "tool"]
        assert "c_DANGLING" not in ids
        assert "c4" in ids
        assert "c5" in ids

    def test_empty_list_is_safe(self):
        assert AIAgent._sanitize_api_messages([]) == []

    def test_no_tool_messages(self):
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        out = AIAgent._sanitize_api_messages(msgs)
        assert out == msgs

    def test_sdk_object_tool_calls(self):
        tc_obj = types.SimpleNamespace(id="c6", function=types.SimpleNamespace(
            name="terminal", arguments="{}"
        ))
        msgs = [
            {"role": "assistant", "tool_calls": [tc_obj]},
        ]
        out = AIAgent._sanitize_api_messages(msgs)
        assert len(out) == 2
        assert out[1]["tool_call_id"] == "c6"


# ---------------------------------------------------------------------------
# Phase 2 — _deduplicate_tool_calls
# ---------------------------------------------------------------------------

class TestDeduplicateToolCalls:

    def test_duplicate_pair_deduplicated(self):
        tcs = [
            make_tc("web_search", '{"query":"foo"}'),
            make_tc("web_search", '{"query":"foo"}'),
        ]
        out = AIAgent._deduplicate_tool_calls(tcs)
        assert len(out) == 1

    def test_multiple_duplicates(self):
        tcs = [
            make_tc("web_search", '{"q":"a"}'),
            make_tc("web_search", '{"q":"a"}'),
            make_tc("terminal", '{"cmd":"ls"}'),
            make_tc("terminal", '{"cmd":"ls"}'),
            make_tc("terminal", '{"cmd":"pwd"}'),
        ]
        out = AIAgent._deduplicate_tool_calls(tcs)
        assert len(out) == 3

    def test_same_tool_different_args_kept(self):
        tcs = [
            make_tc("terminal", '{"cmd":"ls"}'),
            make_tc("terminal", '{"cmd":"pwd"}'),
        ]
        out = AIAgent._deduplicate_tool_calls(tcs)
        assert out is tcs

    def test_different_tools_same_args_kept(self):
        tcs = [
            make_tc("tool_a", '{"x":1}'),
            make_tc("tool_b", '{"x":1}'),
        ]
        out = AIAgent._deduplicate_tool_calls(tcs)
        assert out is tcs

    def test_clean_list_unchanged(self):
        tcs = [
            make_tc("web_search", '{"q":"x"}'),
            make_tc("terminal", '{"cmd":"ls"}'),
        ]
        out = AIAgent._deduplicate_tool_calls(tcs)
        assert out is tcs

    def test_empty_list_safe(self):
        assert AIAgent._deduplicate_tool_calls([]) == []

    def test_first_occurrence_kept(self):
        tc1 = make_tc("terminal", '{"cmd":"ls"}')
        tc2 = make_tc("terminal", '{"cmd":"ls"}')
        out = AIAgent._deduplicate_tool_calls([tc1, tc2])
        assert len(out) == 1
        assert out[0] is tc1

    def test_original_list_not_mutated(self):
        tcs = [
            make_tc("web_search", '{"q":"dup"}'),
            make_tc("web_search", '{"q":"dup"}'),
        ]
        original_len = len(tcs)
        AIAgent._deduplicate_tool_calls(tcs)
        assert len(tcs) == original_len


# ---------------------------------------------------------------------------
# _get_tool_call_id_static
# ---------------------------------------------------------------------------

class TestGetToolCallIdStatic:

    def test_dict_with_valid_id(self):
        assert AIAgent._get_tool_call_id_static({"id": "call_123"}) == "call_123"

    def test_dict_with_none_id(self):
        assert AIAgent._get_tool_call_id_static({"id": None}) == ""

    def test_dict_without_id_key(self):
        assert AIAgent._get_tool_call_id_static({"function": {}}) == ""

    def test_object_with_valid_id(self):
        tc = types.SimpleNamespace(id="call_456")
        assert AIAgent._get_tool_call_id_static(tc) == "call_456"

    def test_object_with_none_id(self):
        tc = types.SimpleNamespace(id=None)
        assert AIAgent._get_tool_call_id_static(tc) == ""

    def test_object_without_id_attr(self):
        tc = types.SimpleNamespace()
        assert AIAgent._get_tool_call_id_static(tc) == ""


# ---------------------------------------------------------------------------
# _get_tool_call_name_static
# ---------------------------------------------------------------------------

class TestGetToolCallNameStatic:

    def test_dict_with_valid_name(self):
        assert AIAgent._get_tool_call_name_static(
            {"id": "call_1", "function": {"name": "terminal", "arguments": "{}"}}
        ) == "terminal"

    def test_dict_with_missing_function(self):
        assert AIAgent._get_tool_call_name_static({"id": "call_1"}) == ""

    def test_dict_with_none_function(self):
        assert AIAgent._get_tool_call_name_static({"id": "call_1", "function": None}) == ""

    def test_dict_with_none_name(self):
        assert AIAgent._get_tool_call_name_static(
            {"function": {"name": None, "arguments": "{}"}}
        ) == ""

    def test_object_with_valid_name(self):
        tc = make_tc("read_file")
        assert AIAgent._get_tool_call_name_static(tc) == "read_file"

    def test_object_without_function_attr(self):
        tc = types.SimpleNamespace(id="call_1")
        assert AIAgent._get_tool_call_name_static(tc) == ""
