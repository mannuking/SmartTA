"""Tests for export utility markdown conversion helpers."""

from Smart_Teaching_Assistance.export_manager.utils import md_to_reportlab


def test_md_to_reportlab_keeps_inline_code_safe_when_html_like_text_present():
    """Inline code with angle brackets should remain escaped inside font tags."""
    src = (
        "2. **Define the LoRA Config:** Set target modules "
        "(e.g., `~q<i>proj`, `~</i>proj`)."
    )

    out = md_to_reportlab(src)

    assert "<b>Define the LoRA Config:</b>" in out
    assert '<font face="Courier" size="9">~q&lt;i&gt;proj</font>' in out
    assert '<font face="Courier" size="9">~&lt;/i&gt;proj</font>' in out
    assert "<font" in out and "</font>" in out


def test_md_to_reportlab_does_not_apply_italic_inside_inline_code():
    """Underscores inside code should not be interpreted as markdown italics."""
    src = "Use `q_i_proj` and keep _this_ italic."

    out = md_to_reportlab(src)

    assert '<font face="Courier" size="9">q_i_proj</font>' in out
    assert "<i>this</i>" in out
    assert "<i>q" not in out
