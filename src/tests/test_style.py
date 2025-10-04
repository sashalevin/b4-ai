import argparse
import sys

import b4.style


def test_format_text_wraps_and_indents() -> None:
    text = "This is a simple paragraph that should wrap nicely when we apply formatting."
    formatted = b4.style.format_text(text, indent=2, width=40)
    lines = formatted.rstrip('\n').splitlines()
    assert all(len(line) <= 40 for line in lines)
    assert all(line.startswith('  ') for line in lines)


def test_format_text_handles_bullets() -> None:
    text = "- First item that is long enough to wrap around the width limit\n- Second item"
    formatted = b4.style.format_text(text, indent=2, width=50)
    lines = formatted.rstrip('\n').splitlines()
    assert lines[0].startswith('  - ')
    assert lines[1].startswith('    ')
    assert any(line.startswith('  - Second item') for line in lines)


def test_format_text_preserves_preformatted() -> None:
    text = "```\ncode block\n```\n\n    indented line\n"
    formatted = b4.style.format_text(text, indent=4, width=50)
    assert 'code block' in formatted
    assert '    indented line' in formatted


def test_style_main_writes_stdout(monkeypatch, capsys) -> None:
    args = argparse.Namespace(input='-', output='-', indent=2, max_lines=40, config={})

    monkeypatch.setattr(sys.stdin, 'read', lambda: "Short paragraph for formatting")

    prompts = {}

    monkeypatch.setattr(b4.style, 'resolve_agent', lambda cmdargs: 'mock-agent')

    def fake_call_agent(prompt: str, agent_cmd: str, timeout: int = 600):
        prompts['prompt'] = prompt
        return "  Short paragraph for formatting\n"

    monkeypatch.setattr(b4.style, 'call_agent', fake_call_agent)

    b4.style.main(args)
    out = capsys.readouterr().out
    assert out.startswith('  Short paragraph')
    assert 'Short paragraph for formatting' in out
    assert 'Wrap plain paragraphs' in prompts['prompt']
