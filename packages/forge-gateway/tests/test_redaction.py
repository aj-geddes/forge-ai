r"""Unit tests for :mod:`forge_gateway.redaction`.

Focus: :func:`redaction.redact_text`, the only redaction path that operates on
already-serialized text (the git-promotion diff + PR body). Because that text is
produced by ``yaml.dump`` and then string-diffed, PyYAML may FOLD a long plain
scalar across lines AT WHITESPACE -- inserting ``\n`` + indentation into the
middle of a value. A resolved secret that contains whitespace would then evade a
naive contiguous substring match and leak in cleartext across the wrapped lines.
:func:`redact_text` must therefore match a secret's internal whitespace as
``\s+`` while behaving EXACTLY like a contiguous match for whitespace-free
secrets (no lowered floor, no new over-redaction of ordinary spaced text).
"""

from __future__ import annotations

from forge_gateway import redaction

_REDACTED = "***REDACTED***"


class TestRedactTextWhitespaceFolding:
    """The residual: a space-containing secret folded across lines by
    ``yaml.dump`` must still be redacted."""

    def test_secret_folded_across_whitespace_is_redacted(self) -> None:
        r"""RED before / GREEN after. The secret ``"sk live CANARY topsecret"``
        appears in the text with its internal space replaced by ``\n`` +
        indentation (exactly how PyYAML folds a long plain scalar). A contiguous
        ``str.replace`` misses it; the whitespace-tolerant regex catches it."""
        secret = "sk live CANARY topsecret"
        # PyYAML folded the space between "CANARY" and "topsecret".
        text = "    system_prompt: the key is sk live CANARY\n      topsecret keep it safe"

        out = redaction.redact_text(text, frozenset({secret}))

        assert _REDACTED in out
        # No fragment of the secret survives, even across the fold.
        assert "CANARY" not in out
        assert "topsecret" not in out
        # Legitimate surrounding content is preserved.
        assert "keep it safe" in out

    def test_secret_folded_at_multiple_whitespace_runs_is_redacted(self) -> None:
        """A secret with several internal spaces, each of which PyYAML may fold
        independently, is redacted whole."""
        secret = "alpha bravo charlie delta echo foxtrot"
        text = "note: alpha bravo\n      charlie delta echo\n      foxtrot done"

        out = redaction.redact_text(text, frozenset({secret}))

        assert _REDACTED in out
        assert "charlie" not in out
        assert "foxtrot" not in out
        assert "done" in out


class TestRedactTextRegression:
    """A whitespace-free secret must behave EXACTLY as before (contiguous
    match), and ordinary spaced prose must not be over-redacted."""

    def test_whitespace_free_secret_still_redacted_contiguously(self) -> None:
        secret = "sk-ant-SECRET-CANARY-9999"
        text = f"model api_key was {secret} in the dump"

        out = redaction.redact_text(text, frozenset({secret}))

        assert secret not in out
        assert out == f"model api_key was {_REDACTED} in the dump"

    def test_whitespace_free_secret_all_occurrences_redacted(self) -> None:
        secret = "topsecretvalue123"
        text = f"{secret} and again {secret}"

        out = redaction.redact_text(text, frozenset({secret}))

        assert secret not in out
        assert out == f"{_REDACTED} and again {_REDACTED}"

    def test_ordinary_spaced_text_is_not_over_redacted(self) -> None:
        r"""A known secret that does NOT appear must leave the text untouched --
        the ``\s+`` tolerance must not turn unrelated prose into a match."""
        text = "the quick brown fox jumps over the lazy dog"

        out = redaction.redact_text(text, frozenset({"sk live CANARY topsecret"}))

        assert out == text
        assert _REDACTED not in out

    def test_no_known_values_returns_unchanged(self) -> None:
        text = "sk live CANARY topsecret appears here"
        assert redaction.redact_text(text, None) == text
        assert redaction.redact_text(text, frozenset()) == text
