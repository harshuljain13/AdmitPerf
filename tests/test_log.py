"""Log — must never be the reason a request failed."""

from __future__ import annotations

from admitperf.core.log import Log


def test_no_path_writes_nowhere(tmp_path) -> None:
    """Constructing a policy must not touch a disk, so a test leaves nothing behind."""
    log = Log(None)
    log.write({"a": 1})
    log.close()
    assert not list(tmp_path.iterdir())


def test_records_land_in_order(tmp_path) -> None:
    path = tmp_path / "d.jsonl"
    with_log = Log(path)
    for i in range(5):
        with_log.write({"i": i})
    with_log.close()
    assert [r["i"] for r in Log.read(path)] == [0, 1, 2, 3, 4]


def test_close_is_what_guarantees_the_tail(tmp_path) -> None:
    """The writer thread is a daemon, so without close() the last buffered records are
    lost when the process exits."""
    path = tmp_path / "d.jsonl"
    log = Log(path)
    log.write({"last": True})
    log.close()
    assert Log.read(path) == [{"last": True}]


def test_a_truncated_final_line_is_skipped(tmp_path) -> None:
    """What a gateway that was killed rather than closed leaves behind. One torn line
    must not make the whole log unreadable."""
    path = tmp_path / "d.jsonl"
    path.write_text('{"ok": 1}\n{"torn": ')
    assert Log.read(path) == [{"ok": 1}]


def test_an_unserialisable_value_does_not_raise(tmp_path) -> None:
    """A logging fault must not become a 500 on a request that was going to be
    admitted, so anything exotic is coerced rather than refused."""
    path = tmp_path / "d.jsonl"
    log = Log(path)
    log.write({"weird": object()})
    log.close()
    assert len(Log.read(path)) == 1


def test_writing_after_close_is_ignored(tmp_path) -> None:
    path = tmp_path / "d.jsonl"
    log = Log(path)
    log.close()
    log.write({"late": True})  # no exception
    assert Log.read(path) == []


def test_the_parent_directory_is_created(tmp_path) -> None:
    """`log="runs/today/decisions.jsonl"` should work on a fresh machine."""
    path = tmp_path / "runs" / "today" / "d.jsonl"
    log = Log(path)
    log.write({"a": 1})
    log.close()
    assert Log.read(path) == [{"a": 1}]
