"""Regression tests for issue #1023: CSV/TSV multiline quoted field parsing."""

from __future__ import annotations

import csv
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agentos.tools.builtin.filesystem import (
    _read_delimited_rows,
    _safe_csv_field_size_limit,
    _scoped_csv_field_size_limit,
    read_spreadsheet,
)
from agentos.tools.types import ToolError

# ── Multiline quoted field ──────────────────────────────────────────────


def test_csv_multiline_quoted_field_stays_single_row(tmp_path: Path) -> None:
    """A quoted field with embedded newlines must be parsed as one cell."""
    csv_content = 'name,description\n"Alice","Line one\nLine two\nLine three"\n"Bob","Simple"\n'
    csv_file = tmp_path / "multi.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    [(_, rows, _)] = _read_delimited_rows(csv_file, ",")

    assert rows[1] == ["name", "description"]
    assert rows[2] == ["Alice", "Line one\nLine two\nLine three"]
    assert rows[3] == ["Bob", "Simple"]
    assert len(rows) == 3


# ── Delimiter inside quoted field ───────────────────────────────────────


def test_csv_delimiter_inside_quoted_field(tmp_path: Path) -> None:
    """A comma inside a quoted field must not be treated as a column split."""
    csv_content = 'key,value\n"item","one, two, three"\n"other","plain"\n'
    csv_file = tmp_path / "delim.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    [(_, rows, _)] = _read_delimited_rows(csv_file, ",")

    assert rows[1] == ["key", "value"]
    assert rows[2] == ["item", "one, two, three"]
    assert rows[3] == ["other", "plain"]
    assert len(rows) == 3


# ── Both: multiline + delimiter in the same field ───────────────────────


def test_csv_multiline_and_delimiter_in_same_field(tmp_path: Path) -> None:
    """A field containing both embedded newlines and the delimiter character."""
    csv_content = 'id,data\n"1","first, value\nsecond, value"\n"2","ok"\n'
    csv_file = tmp_path / "combo.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    [(_, rows, _)] = _read_delimited_rows(csv_file, ",")

    assert rows[1] == ["id", "data"]
    assert rows[2] == ["1", "first, value\nsecond, value"]
    assert rows[3] == ["2", "ok"]
    assert len(rows) == 3


# ── TSV variant ─────────────────────────────────────────────────────────


def test_tsv_multiline_quoted_field(tmp_path: Path) -> None:
    """Same bug applied to TSV files with tab delimiter."""
    tsv_content = 'name\tnotes\n"Alice"\t"Line1\nLine2"\n"Bob"\t"ok"\n'
    tsv_file = tmp_path / "multi.tsv"
    tsv_file.write_text(tsv_content, encoding="utf-8")

    [(_, rows, _)] = _read_delimited_rows(tsv_file, "\t")

    assert rows[1] == ["name", "notes"]
    assert rows[2] == ["Alice", "Line1\nLine2"]
    assert rows[3] == ["Bob", "ok"]
    assert len(rows) == 3


# ── Unicode line separators ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "sep,label",
    [
        ("\v", "vertical-tab"),
        ("\f", "form-feed"),
        ("\x1c", "file-separator"),
        ("\x1d", "group-separator"),
        ("\x1e", "record-separator"),
        ("\x85", "next-line"),
        ("\u2028", "line-separator"),
        ("\u2029", "paragraph-separator"),
    ],
)
def test_unicode_line_separator_inside_field_not_treated_as_row_break(
    tmp_path: Path, sep: str, label: str
) -> None:
    """splitlines() splits on these; csv.reader(StringIO) must not."""
    csv_content = f'a,b\n"x{sep}y","z"\n'
    csv_file = tmp_path / f"unicode_{label}.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    [(_, rows, _)] = _read_delimited_rows(csv_file, ",")

    assert len(rows) == 2, f"Expected 2 rows for {label!r}, got {len(rows)}"
    assert rows[1] == ["a", "b"]
    assert rows[2] == [f"x{sep}y", "z"]


# ── Issue #1580: Fields exceeding 128KB default limit ─────────────────


def test_csv_large_field_exceeding_default_limit_succeeds(tmp_path: Path) -> None:
    """A field exceeding Python's default 131,072-char limit must parse safely."""
    payload = "A" * 150_000
    csv_file = tmp_path / "large_field.csv"
    csv_file.write_text(f'id,payload\n1,"{payload}"\n', encoding="utf-8")

    [(_, rows, total_rows)] = _read_delimited_rows(csv_file, ",")

    assert total_rows == 2
    assert rows[1] == ["id", "payload"]
    assert rows[2] == ["1", payload]


def test_tsv_large_field_exceeding_default_limit_succeeds(tmp_path: Path) -> None:
    """TSV files with large fields must also parse without error."""
    payload = "B" * 200_000
    tsv_file = tmp_path / "large_field.tsv"
    tsv_file.write_text(f"id\tdata\n100\t{payload}\n", encoding="utf-8")

    [(_, rows, total_rows)] = _read_delimited_rows(tsv_file, "\t")

    assert total_rows == 2
    assert rows[1] == ["id", "data"]
    assert rows[2] == ["100", payload]


@pytest.mark.asyncio
async def test_read_spreadsheet_large_field_end_to_end(tmp_path: Path) -> None:
    """read_spreadsheet end-to-end handles fields exceeding 128KB."""
    payload = "JSON_BLOB_" + ("X" * 140_000)
    csv_file = tmp_path / "large_e2e.csv"
    csv_file.write_text(f'id,blob\n1,"{payload}"\n', encoding="utf-8")

    result = await read_spreadsheet(str(csv_file))

    assert "large_e2e.csv" in result
    assert "JSON_BLOB_" in result


def test_csv_field_size_limit_restored_after_success(tmp_path: Path) -> None:
    """The process-wide field_size_limit must be restored after parsing."""
    baseline = csv.field_size_limit()
    csv_file = tmp_path / "large.csv"
    csv_file.write_text("col\n" + "Z" * 160_000, encoding="utf-8")

    _read_delimited_rows(csv_file, ",")

    assert csv.field_size_limit() == baseline


def test_csv_field_size_limit_restored_after_custom_baseline(tmp_path: Path) -> None:
    """Restores to any pre-existing custom limit, not just hardcoded 131,072."""
    custom_baseline = 65_536
    old = csv.field_size_limit(custom_baseline)
    try:
        csv_file = tmp_path / "custom_base.csv"
        csv_file.write_text("col\n" + "M" * 150_000, encoding="utf-8")

        _read_delimited_rows(csv_file, ",")

        assert csv.field_size_limit() == custom_baseline
    finally:
        csv.field_size_limit(old)


def test_csv_field_size_limit_restored_on_parse_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restores field_size_limit even when csv parsing encounters an error."""
    baseline = csv.field_size_limit()
    csv_file = tmp_path / "large_corrupt.csv"
    csv_file.write_text("a,b\n1," + "K" * 150_000, encoding="utf-8")

    def failing_reader(*args: object, **kwargs: object) -> list[list[str]]:
        raise csv.Error("simulated parse failure")

    monkeypatch.setattr(csv, "reader", failing_reader)

    with pytest.raises(ToolError, match="Cannot parse delimited spreadsheet"):
        _read_delimited_rows(csv_file, ",")

    assert csv.field_size_limit() == baseline


def test_csv_malformed_input_raises_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """csv.Error must be caught and raised as a ToolError instead of crashing."""
    csv_file = tmp_path / "corrupt.csv"
    csv_file.write_text("header1,header2\nval1,val2\n", encoding="utf-8")

    def failing_reader(*args: object, **kwargs: object) -> list[list[str]]:
        raise csv.Error("corrupt CSV syntax")

    monkeypatch.setattr(csv, "reader", failing_reader)

    with pytest.raises(ToolError) as exc_info:
        _read_delimited_rows(csv_file, ",")

    assert "Cannot parse delimited spreadsheet" in str(exc_info.value)
    assert "corrupt.csv" in str(exc_info.value)


def test_small_csv_does_not_mutate_field_size_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files within the existing limit should not invoke field_size_limit with a new value."""
    csv_file = tmp_path / "small.csv"
    csv_file.write_text("a,b\n1,2\n", encoding="utf-8")

    mutated: list[int] = []
    original_fn = csv.field_size_limit

    def spy_limit(new_limit: int | None = None) -> int:
        if new_limit is not None:
            mutated.append(new_limit)
            return original_fn(new_limit)
        return original_fn()

    monkeypatch.setattr(csv, "field_size_limit", spy_limit)

    _read_delimited_rows(csv_file, ",")

    assert len(mutated) == 0


def test_csv_concurrent_large_field_reads(tmp_path: Path) -> None:
    """Concurrent reads across threads must not race on global field_size_limit."""
    baseline = csv.field_size_limit()
    files: list[tuple[Path, str, str]] = []

    for i in range(8):
        size = 140_000 + i * 10_000
        content = f"id,data\n{i}," + ("X" * size)
        p = tmp_path / f"concurrent_{i}.csv"
        p.write_text(content, encoding="utf-8")
        files.append((p, ",", "X" * size))

    def read_one(args: tuple[Path, str, str]) -> bool:
        path, sep, expected = args
        [(_, rows, _)] = _read_delimited_rows(path, sep)
        return rows[2][1] == expected

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read_one, files))

    assert all(results)
    assert csv.field_size_limit() == baseline


def test_csv_large_reads_execute_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Large CSV reads must not serialize each other during csv.reader parsing."""
    barrier = threading.Barrier(2, timeout=5.0)
    orig_reader = csv.reader

    def concurrent_reader(*args: object, **kwargs: object) -> object:
        barrier.wait()
        return orig_reader(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(csv, "reader", concurrent_reader)

    file1 = tmp_path / "large1.csv"
    file1.write_text("id,val\n1," + "A" * 140_000, encoding="utf-8")
    file2 = tmp_path / "large2.csv"
    file2.write_text("id,val\n2," + "B" * 150_000, encoding="utf-8")

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(_read_delimited_rows, file1, ",")
        f2 = pool.submit(_read_delimited_rows, file2, ",")
        res1 = f1.result()
        res2 = f2.result()

    assert len(res1[0][1]) == 2
    assert len(res2[0][1]) == 2


def test_scoped_csv_field_size_limit_high_water_mark() -> None:
    """Verify high-water mark tracking and nested restoration."""
    baseline = csv.field_size_limit()

    with _scoped_csv_field_size_limit(200_000):
        assert csv.field_size_limit() >= 200_000
        with _scoped_csv_field_size_limit(350_000):
            assert csv.field_size_limit() >= 350_000
        # After inner exit, should adjust to outer demand
        assert csv.field_size_limit() >= 200_000

    # After outer exit, baseline restored
    assert csv.field_size_limit() == baseline


def test_scoped_csv_field_size_limit_overflow_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify _safe_csv_field_size_limit falls back when OverflowError is encountered."""
    calls: list[int] = []

    def mock_limit(val: int | None = None) -> int:
        if val is not None:
            calls.append(val)
            if val > 2_147_483_647:
                raise OverflowError("Python int too large to convert to C long")
            return val
        return 131_072

    monkeypatch.setattr(csv, "field_size_limit", mock_limit)

    applied = _safe_csv_field_size_limit(5_000_000_000)
    assert applied == 2_147_483_647
    assert 2_147_483_647 in calls


@pytest.mark.asyncio
async def test_read_spreadsheet_corrupt_csv_raises_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """read_spreadsheet wraps csv.Error into an actionable ToolError."""
    csv_file = tmp_path / "corrupt_e2e.csv"
    csv_file.write_text("id,val\n1,bad\n", encoding="utf-8")

    def failing_reader(*args: object, **kwargs: object) -> list[list[str]]:
        raise csv.Error("unexpected quote character in line 2")

    monkeypatch.setattr(csv, "reader", failing_reader)

    with pytest.raises(ToolError, match="Cannot parse delimited spreadsheet"):
        await read_spreadsheet(str(csv_file))
