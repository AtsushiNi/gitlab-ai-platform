from __future__ import annotations

import logging

from gitlab_ai_platform.logging_ import context


def test_get_execution_id_defaults_to_none() -> None:
    assert context.get_execution_id() is None


def test_execution_id_scope_sets_and_restores() -> None:
    assert context.get_execution_id() is None
    with context.execution_id_scope("abc123") as execution_id:
        assert execution_id == "abc123"
        assert context.get_execution_id() == "abc123"
    assert context.get_execution_id() is None


def test_execution_id_scope_generates_id_when_omitted() -> None:
    with context.execution_id_scope() as execution_id:
        assert execution_id
        assert context.get_execution_id() == execution_id


def test_new_execution_id_returns_unique_values() -> None:
    assert context.new_execution_id() != context.new_execution_id()


def test_set_execution_id_updates_current_context() -> None:
    with context.execution_id_scope("placeholder"):
        context.set_execution_id("manual-id")
        assert context.get_execution_id() == "manual-id"
    assert context.get_execution_id() is None


def test_execution_id_filter_injects_current_execution_id() -> None:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "msg", (), None)
    filter_ = context.ExecutionIdFilter()

    with context.execution_id_scope("xyz789"):
        filter_.filter(record)

    assert record.execution_id == "xyz789"
