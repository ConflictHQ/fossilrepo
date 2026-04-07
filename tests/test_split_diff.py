"""Tests for the split-diff view helper (_compute_split_lines)."""

from fossil.views import _compute_split_lines


def _make_line(text, line_type, old_num="", new_num=""):
    """Build a diff-line dict matching the shape produced by checkin_detail."""
    if line_type in ("add", "del", "context") and text:
        prefix = text[0]
        code = text[1:]
    else:
        prefix = ""
        code = text
    return {
        "text": text,
        "type": line_type,
        "old_num": old_num,
        "new_num": new_num,
        "prefix": prefix,
        "code": code,
    }


class TestComputeSplitLines:
    """Unit tests for _compute_split_lines."""

    def test_context_lines_appear_on_both_sides(self):
        lines = [
            _make_line(" hello", "context", old_num=1, new_num=1),
            _make_line(" world", "context", old_num=2, new_num=2),
        ]
        left, right = _compute_split_lines(lines)
        assert len(left) == 2
        assert len(right) == 2
        assert left[0]["text"] == " hello"
        assert right[0]["text"] == " hello"
        assert left[1]["text"] == " world"
        assert right[1]["text"] == " world"

    def test_deletion_only_on_left(self):
        lines = [
            _make_line("-removed", "del", old_num=5),
        ]
        left, right = _compute_split_lines(lines)
        assert len(left) == 1
        assert len(right) == 1
        assert left[0]["type"] == "del"
        assert left[0]["text"] == "-removed"
        assert right[0]["type"] == "empty"
        assert right[0]["text"] == ""

    def test_addition_only_on_right(self):
        lines = [
            _make_line("+added", "add", new_num=10),
        ]
        left, right = _compute_split_lines(lines)
        assert len(left) == 1
        assert len(right) == 1
        assert left[0]["type"] == "empty"
        assert right[0]["type"] == "add"
        assert right[0]["text"] == "+added"

    def test_paired_del_add_block(self):
        """Adjacent del+add lines should be paired row-by-row."""
        lines = [
            _make_line("-old_a", "del", old_num=1),
            _make_line("-old_b", "del", old_num=2),
            _make_line("+new_a", "add", new_num=1),
            _make_line("+new_b", "add", new_num=2),
        ]
        left, right = _compute_split_lines(lines)
        assert len(left) == 2
        assert len(right) == 2
        assert left[0]["type"] == "del"
        assert right[0]["type"] == "add"
        assert left[1]["type"] == "del"
        assert right[1]["type"] == "add"

    def test_unequal_del_add_pads_with_empty(self):
        """When there are more dels than adds, right side gets empty placeholders."""
        lines = [
            _make_line("-old_a", "del", old_num=1),
            _make_line("-old_b", "del", old_num=2),
            _make_line("-old_c", "del", old_num=3),
            _make_line("+new_a", "add", new_num=1),
        ]
        left, right = _compute_split_lines(lines)
        assert len(left) == 3
        assert len(right) == 3
        assert left[0]["type"] == "del"
        assert right[0]["type"] == "add"
        assert left[1]["type"] == "del"
        assert right[1]["type"] == "empty"
        assert left[2]["type"] == "del"
        assert right[2]["type"] == "empty"

    def test_more_adds_than_dels_pads_left(self):
        """When there are more adds than dels, left side gets empty placeholders."""
        lines = [
            _make_line("-old", "del", old_num=1),
            _make_line("+new_a", "add", new_num=1),
            _make_line("+new_b", "add", new_num=2),
        ]
        left, right = _compute_split_lines(lines)
        assert len(left) == 2
        assert len(right) == 2
        assert left[0]["type"] == "del"
        assert right[0]["type"] == "add"
        assert left[1]["type"] == "empty"
        assert right[1]["type"] == "add"

    def test_hunk_and_header_lines_on_both_sides(self):
        lines = [
            _make_line("--- a/file.py", "header"),
            _make_line("+++ b/file.py", "header"),
            _make_line("@@ -1,3 +1,3 @@", "hunk"),
            _make_line(" ctx", "context", old_num=1, new_num=1),
        ]
        left, right = _compute_split_lines(lines)
        assert len(left) == 4
        assert len(right) == 4
        assert left[0]["type"] == "header"
        assert right[0]["type"] == "header"
        assert left[2]["type"] == "hunk"
        assert right[2]["type"] == "hunk"
        assert left[3]["type"] == "context"
        assert right[3]["type"] == "context"

    def test_mixed_sequence(self):
        """Full realistic sequence: header, hunk, context, del, add, context."""
        lines = [
            _make_line("--- a/f.py", "header"),
            _make_line("+++ b/f.py", "header"),
            _make_line("@@ -1,4 +1,4 @@", "hunk"),
            _make_line(" line1", "context", old_num=1, new_num=1),
            _make_line("-old2", "del", old_num=2),
            _make_line("+new2", "add", new_num=2),
            _make_line(" line3", "context", old_num=3, new_num=3),
        ]
        left, right = _compute_split_lines(lines)
        # header(2) + hunk(1) + context(1) + paired del/add(1) + context(1) = 6
        assert len(left) == 6
        assert len(right) == 6
        # Check paired del/add at index 4
        assert left[4]["type"] == "del"
        assert right[4]["type"] == "add"

    def test_empty_input(self):
        left, right = _compute_split_lines([])
        assert left == []
        assert right == []

    def test_orphan_add_without_preceding_del(self):
        """An add line not preceded by a del should still work."""
        lines = [
            _make_line(" ctx", "context", old_num=1, new_num=1),
            _make_line("+new", "add", new_num=2),
            _make_line(" ctx2", "context", old_num=2, new_num=3),
        ]
        left, right = _compute_split_lines(lines)
        assert len(left) == 3
        assert len(right) == 3
        assert left[1]["type"] == "empty"
        assert right[1]["type"] == "add"
