#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
#
# b4 ai cover - Use AI to draft cover letters for patch series
#

import argparse
import os
import statistics
import sys
import textwrap
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import b4
from b4.ai import call_agent

logger = b4.logger


@dataclass
class CommitInfo:
    sha: str
    subject: str
    author: str
    date: str
    body: str


def _git_lines(args: Sequence[str]) -> List[str]:
    ecode, output = b4.git_run_command(None, list(args))
    if ecode != 0:
        raise RuntimeError('git command failed: %s' % ' '.join(args))
    return [line for line in output.splitlines() if line]


def _git_text(args: Sequence[str]) -> str:
    ecode, output = b4.git_run_command(None, list(args))
    if ecode != 0:
        raise RuntimeError('git command failed: %s' % ' '.join(args))
    return output.strip('\n')


def _get_upstream(series: str) -> Optional[str]:
    try:
        lines = _git_lines(['rev-parse', '--abbrev-ref', f'{series}@{{upstream}}'])
    except RuntimeError:
        return None
    if lines:
        return lines[0]
    return None


def _get_default_remote() -> Optional[str]:
    try:
        target = _git_text(['symbolic-ref', '--short', 'refs/remotes/origin/HEAD'])
    except RuntimeError:
        return None
    return target or None


def _is_commit(reference: str) -> bool:
    try:
        lines = _git_lines(['rev-parse', '--verify', f'{reference}^{{commit}}'])
    except RuntimeError:
        return False
    return bool(lines)


def determine_range(series: str, base: Optional[str]) -> Tuple[str, Optional[str]]:
    series = series.strip()
    if '..' in series or '...' in series or series.endswith('^!'):
        return series, None

    if base:
        return f'{base}..{series}', base

    if _is_commit(series):
        upstream = _get_upstream(series)
        if upstream:
            return f'{upstream}..{series}', upstream
        default_remote = _get_default_remote()
        if default_remote:
            return f'{default_remote}..{series}', default_remote
        return f'{series}^!', None

    return series, None


def collect_commits(range_expr: str, max_commits: int) -> List[CommitInfo]:
    gitargs = ['log', '--reverse', '--date=short', '--pretty=format:%H\x1f%an\x1f%ad\x1f%s\x1f%b\x1e']
    if max_commits > 0:
        gitargs.append(f'--max-count={max_commits}')
    gitargs.append(range_expr)

    ecode, output = b4.git_run_command(None, gitargs)
    if ecode != 0:
        raise RuntimeError('Unable to collect commits for %s' % range_expr)

    entries = [chunk for chunk in output.split('\x1e') if chunk.strip()]
    commits: List[CommitInfo] = []
    for entry in entries:
        fields = entry.split('\x1f')
        if len(fields) < 5:
            continue
        sha, author, date, subject, body = fields[:5]
        commits.append(CommitInfo(
            sha=sha.strip(),
            subject=subject.strip(),
            author=author.strip(),
            date=date.strip(),
            body=body.strip()
        ))

    return commits


def collect_diffstat(range_expr: str) -> str:
    ecode, output = b4.git_run_command(None, ['diff', '--stat', range_expr])
    if ecode != 0:
        return ''
    return output.strip()


def summarize_style(style_text: str) -> Dict[str, object]:
    lines = [line.rstrip() for line in style_text.splitlines()]
    non_empty = [line for line in lines if line.strip()]
    line_lengths = [len(line) for line in non_empty] or [0]

    bullet_char = '*'
    indent = 0
    bullet_lines = []
    for line in non_empty:
        stripped = line.lstrip()
        if stripped.startswith(('-', '*', '+')):
            bullet_lines.append(line)
            indent = len(line) - len(stripped)
            bullet_char = stripped[0]
            break

    blank_runs = []
    current = 0
    for line in lines:
        if line.strip():
            if current:
                blank_runs.append(current)
                current = 0
        else:
            current += 1
    if current:
        blank_runs.append(current)

    avg_blank = statistics.mean(blank_runs) if blank_runs else 1

    return {
        'line_count': len(lines),
        'avg_line_length': statistics.mean(line_lengths),
        'max_line_length': max(line_lengths),
        'bullet_char': bullet_char,
        'bullet_indent': indent,
        'avg_blank_lines': avg_blank,
    }


def describe_style(style: Dict[str, object]) -> str:
    return textwrap.dedent(
        f"""
        Maintain approximately {int(style['line_count'])} total lines.
        Average line length is {int(style['avg_line_length'])} characters (max {int(style['max_line_length'])}).
        Bullets should use '{style['bullet_char']}' with {int(style['bullet_indent'])} leading spaces.
        Leave about {style['avg_blank_lines']:.1f} blank line(s) between sections.
        Wrap text to roughly {int(style['max_line_length'])} characters.
        """
    ).strip()


def build_prompt(series: str,
                 range_expr: str,
                 commits: List[CommitInfo],
                 diffstat: str,
                 style_notes: str,
                 style_reference: Optional[str]) -> str:
    commit_blocks = []
    for idx, commit in enumerate(commits, start=1):
        summary = commit.body.strip()
        if summary:
            summary = summary.split('\n\n', 1)[0].strip()
        commit_blocks.append(
            textwrap.dedent(
                f"""
                Commit {idx}: {commit.subject}
                  Author: {commit.author}
                  Date: {commit.date}
                  Summary: {summary or 'No additional summary provided.'}
                """
            ).strip()
        )

    diff_summary = diffstat or 'Diffstat unavailable.'

    prompt = textwrap.dedent(
        f"""
        You are assisting a Linux kernel maintainer by drafting a first-pass cover letter
        for a patch series. Carefully review every commit and associated change, using any
        internal analysis agents or tools available to you to ensure accuracy.

        Series identifier: {series}
        Commit range: {range_expr}
        Total commits: {len(commits)}

        Diffstat summary:
        {diff_summary}

        Style guidance:
        {style_notes}
        """
    ).strip()

    prompt += '\n\nCommit details:\n' + '\n'.join(commit_blocks)

    if style_reference:
        prompt += '\n\nReference cover letter style (for tone and structure, do not copy text verbatim):\n'
        prompt += style_reference.strip()

    prompt += textwrap.dedent(
        """

        Instructions:
        - Produce the cover letter body only; no git headers.
        - Begin with a maintainer-editable headline summarizing the series.
        - Provide grouped highlights, calling out notable fixes, reverts, or regressions addressed.
        - Reference source commits directly with lore.kernel.org or git web URLs when listing highlights.
        - Include an introductory paragraph and a bullet-style summary of notable changes.
        - Follow commit order when describing highlights.
        - Mention testing results only if explicitly provided in summaries.
        - Return plain text with no Markdown fences or additional commentary.
        """
    )

    return prompt


def normalize_cover(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith('```'):
        lines = stripped.splitlines()
        while lines and lines[0].startswith('```'):
            lines.pop(0)
        while lines and lines[-1].startswith('```'):
            lines.pop()
        stripped = '\n'.join(lines).strip()
    return stripped + '\n'


def resolve_agent(cmdargs: argparse.Namespace) -> Optional[str]:
    config = b4.get_main_config()
    agent_cmd = None
    if getattr(cmdargs, 'config', None):
        for key in ('COVER_AGENT', 'AGENT'):
            if key in cmdargs.config:
                agent_cmd = cmdargs.config[key]
                break
    if not agent_cmd:
        agent_cmd = config.get('cover-agent', config.get('agent', None))
    return agent_cmd


def main(cmdargs: argparse.Namespace) -> None:
    agent_cmd = resolve_agent(cmdargs)
    if not agent_cmd:
        logger.critical('No AI agent configured. Set cover-agent in config or use -c AGENT=/path/to/agent.sh')
        sys.exit(1)

    try:
        range_expr, _ = determine_range(cmdargs.series, cmdargs.base)
    except RuntimeError as exc:
        logger.critical('Could not resolve series: %s', exc)
        sys.exit(1)

    logger.info('Using commit range: %s', range_expr)

    try:
        commits = collect_commits(range_expr, cmdargs.max_commits)
    except RuntimeError as exc:
        logger.critical(str(exc))
        sys.exit(1)

    if not commits:
        logger.critical('No commits found for range %s', range_expr)
        sys.exit(1)

    diffstat = collect_diffstat(range_expr)

    style_text = None
    style_notes = 'Default kernel cover letter style: wrap at ~72 characters, use concise paragraphs, indent bullet lists with two spaces and hyphen prefixes.'
    if cmdargs.style_file:
        try:
            with open(os.path.expanduser(cmdargs.style_file), 'r', encoding='utf-8') as fh:
                style_text = fh.read()
        except OSError as exc:
            logger.warning('Could not read style file %s: %s', cmdargs.style_file, exc)
        else:
            style_info = summarize_style(style_text)
            style_notes = describe_style(style_info)

    prompt = build_prompt(cmdargs.series, range_expr, commits, diffstat, style_notes, style_text)

    response = call_agent(prompt, agent_cmd)
    if not response:
        logger.critical('No response from agent')
        sys.exit(1)

    cover_letter = normalize_cover(response)

    if cmdargs.output:
        output_path = os.path.expanduser(cmdargs.output)
        outdir = os.path.dirname(output_path)
        if outdir and not os.path.exists(outdir):
            os.makedirs(outdir, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as fh:
            fh.write(cover_letter)
        logger.info('Cover letter written to %s', output_path)
    else:
        print(cover_letter)
