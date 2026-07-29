"""The helper that stops source-scanning tests failing on their own comments.

Five rounds of debugging in one week traced to the same thing: a comment that
NAMES the string it forbids is indistinguishable from the code doing it. Two
earlier fixes used regex and each missed a case. This one uses tokenize.

Its contract is narrow and worth pinning, because a scanning helper that
quietly mangles offsets would break every `"foo(a, b)" in block` assertion
built on it, and would do so in the direction of false PASSES.
"""

from tests.source_scan import code_only


def test_comments_are_removed():
    src = 'x = 1  # never render "Engine live"\n'
    assert "Engine live" not in code_only(src)
    assert "x = 1" in code_only(src)


def test_docstrings_are_removed():
    src = 'def f():\n    """never render Engine live"""\n    return 1\n'
    out = code_only(src)
    assert "Engine live" not in out
    assert "return 1" in out


def test_a_string_that_is_a_VALUE_survives():
    """The whole point is distinguishing prose from what the code renders."""
    src = 'msg = "Engine live"\n'
    assert "Engine live" in code_only(src)


def test_a_hash_inside_a_string_is_not_treated_as_a_comment():
    """The case the first regex attempt got wrong."""
    src = 'url = "https://x/#frag"  # a real comment\n'
    out = code_only(src)
    assert "#frag" in out, "that # is data, not a comment"
    assert "a real comment" not in out


def test_a_string_after_an_assignment_is_never_a_docstring():
    src = 'def f():\n    """doc"""\n    a = "kept"\n    return a\n'
    out = code_only(src)
    assert "doc" not in out
    assert "kept" in out


def test_every_offset_is_preserved():
    """Ordering and substring assertions are built on this. A helper that
    re-joined tokens would break `"foo(a, b)" in block` silently."""
    src = ('def f():\n'
           '    """doc"""\n'
           '    # comment mentioning raised\n'
           '    _rec(what, cap, timed_out=True)\n'
           '    raise\n')
    out = code_only(src)
    assert len(out) == len(src), "blanked in place, not deleted"
    assert out.count("\n") == src.count("\n")
    assert "_rec(what, cap, timed_out=True)" in out, "substrings stay contiguous"
    assert out.index("_rec(") < out.index("raise")
    assert "mentioning raised" not in out


def test_unparseable_source_returns_unchanged_rather_than_empty():
    """A syntax error is the caller's problem to report. Returning "" would
    make every `not in` assertion pass — a silent false green."""
    broken = "def f(:\n  pass\n"
    assert code_only(broken) == broken


def test_it_is_idempotent():
    src = 'x = 1  # c\ndef f():\n    """d"""\n    return 2\n'
    once = code_only(src)
    assert code_only(once) == once


def test_dict_keys_are_not_mistaken_for_docstrings():
    """The bug this helper shipped with, caught within the hour.

    NEWLINE ends a logical line; NL is the newline INSIDE brackets. Treating
    NL as a statement boundary made every key in a multi-line dict literal
    look like a docstring, and they were blanked — which is worse than the
    problem the helper exists to fix, because an assertion that a key is
    ABSENT would then pass for entirely the wrong reason.
    """
    src = ('d = {\n'
           '    "phase": what,\n'
           '    "cap_s": cap,\n'
           '}\n')
    out = code_only(src)
    assert '"phase"' in out and '"cap_s"' in out


def test_a_string_inside_a_call_is_a_value():
    src = 'f(\n    "kept",\n)\n'
    assert '"kept"' in code_only(src)


def test_a_docstring_after_a_multiline_signature_is_still_removed():
    """Depth returns to zero before the body, so this must still work."""
    src = ('def f(\n    a,\n    b,\n):\n    """doc"""\n    return a\n')
    out = code_only(src)
    assert "doc" not in out
    assert "return a" in out


def test_a_module_docstring_is_removed():
    src = '"""module doc"""\nx = 1\n'
    out = code_only(src)
    assert "module doc" not in out and "x = 1" in out
