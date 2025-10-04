import argparse

import b4.cover


def test_summarize_style_extracts_knobs() -> None:
    sample = (
        "[PATCH 0/3] Example series\n\n"
        "Intro paragraph goes here.\n\n"
        "  - Change one\n"
        "  - Change two\n"
    )
    info = b4.cover.summarize_style(sample)
    desc = b4.cover.describe_style(info)

    assert info['bullet_char'] == '-'
    assert int(info['bullet_indent']) == 2
    assert 'Bullets should use' in desc


def test_cover_main_writes_output(tmp_path, gitdir, monkeypatch) -> None:
    output_path = tmp_path / 'cover.txt'

    def fake_resolve_agent(cmdargs: argparse.Namespace) -> str:
        return 'mock-agent'

    prompts = {}

    def fake_call_agent(prompt: str, agent_cmd: str) -> str:
        prompts['prompt'] = prompt
        return "[PATCH 0/2] Example\n\n  - summary item\n"

    monkeypatch.setattr(b4.cover, 'resolve_agent', fake_resolve_agent)
    monkeypatch.setattr(b4.cover, 'call_agent', fake_call_agent)

    args = argparse.Namespace(
        series='HEAD~1..HEAD',
        base=None,
        style_file=None,
        output=str(output_path),
        max_commits=5,
        config={},
    )

    b4.cover.main(args)

    assert output_path.exists()
    content = output_path.read_text(encoding='utf-8')
    assert 'summary item' in content
    assert 'Commit 1' in prompts['prompt']
    assert 'Diffstat summary' in prompts['prompt']
