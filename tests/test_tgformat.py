from aifred.tgformat import to_html


def test_headings_bold_italic_code_and_bullets():
    out = to_html("### **Prossimo** impegno\n- **Roma** vince\n* *Oggi* 20:45 e `x`\n1. uno")
    assert out == "<b>Prossimo impegno</b>\n• <b>Roma</b> vince\n• <i>Oggi</i> 20:45 e <code>x</code>\n1. uno"


def test_html_is_escaped_never_passed_through():
    out = to_html("<script>alert(1)</script> & <b>x</b>\n```\n<i>raw</i>\n```")
    assert "<script>" not in out and "&lt;script&gt;" in out and "&amp;" in out
    assert "<pre>&lt;i&gt;raw&lt;/i&gt;</pre>" in out


def test_links_only_http_and_quotes_escaped():
    out = to_html('[ok](https://a.example/x?a=1&b="2") [bad](javascript:alert(1)) see https://b.example/p.')
    assert '<a href="https://a.example/x?a=1&amp;b=&quot;2&quot;">ok</a>' in out
    assert "<a href=\"javascript" not in out and "[bad](javascript:alert(1))" in out
    assert '<a href="https://b.example/p">https://b.example/p</a>.' in out


def test_rule_quote_and_table():
    out = to_html("a\n---\n> citazione\n\n| a | bb |\n|---|---|\n| 1 | 2 |")
    assert "──────────" in out and "<blockquote>citazione</blockquote>" in out
    assert "<pre>a  bb\n1  2</pre>" in out


def test_unterminated_code_block_is_closed():
    assert to_html("```\nx < y") == "<pre>x &lt; y</pre>"
