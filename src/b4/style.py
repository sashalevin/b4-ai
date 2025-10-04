#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
#
# b4 ai style - Apply consistent formatting to generated text without altering content
#

import argparse
import os
import sys
import textwrap
from typing import Iterable, List, Optional

import b4
from b4.ai import call_agent

logger = b4.logger


def _is_bullet_line(line: str) -> bool:
    stripped = line.lstrip()
    if not stripped:
        return False
    if stripped.startswith(('-', '*', '+')) and len(stripped.split(maxsplit=1)) > 1:
        return True
    head = stripped.split(maxsplit=1)[0]
    return head.rstrip('.').isdigit()


def _is_preformatted(lines: Iterable[str]) -> bool:
    for line in lines:
        stripped = line.rstrip('\n')
        if not stripped.strip():
            continue
        if stripped.startswith('```'):
            return True
        if stripped.startswith('    ') or stripped.startswith('\t'):
            return True
    return False


def _format_paragraph(lines: List[str], indent: int, width: int) -> str:
    contents = ' '.join(line.strip() for line in lines if line.strip())
    if not contents:
        return ''

    lstripped = lines[0].lstrip()
    bullet_prefix = ''
    remainder = contents

    if lstripped.startswith(('-', '*', '+')) and len(lstripped.split(maxsplit=1)) > 1:
        bullet_prefix = lstripped.split(maxsplit=1)[0]
        remainder = contents[len(bullet_prefix):].lstrip()
    elif lstripped[: lstripped.find(' ') if ' ' in lstripped else len(lstripped)].rstrip('.').isdigit():
        chunk = lstripped.split(maxsplit=1)[0]
        if chunk.endswith('.'):
            bullet_prefix = chunk
            remainder = contents[len(bullet_prefix):].lstrip()

    base_indent = ' ' * indent

    if bullet_prefix:
        initial_indent = f"{base_indent}{bullet_prefix} "
        subsequent_indent = f"{base_indent}{' ' * (len(bullet_prefix) + 1)}"
    else:
        initial_indent = base_indent
        subsequent_indent = base_indent

    wrapper = textwrap.TextWrapper(
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
        drop_whitespace=True,
        initial_indent=initial_indent,
        subsequent_indent=subsequent_indent,
    )

    text = remainder if bullet_prefix else contents
    return wrapper.fill(text)


def format_text(text: str, indent: int, width: int) -> str:
    paragraphs: List[str] = []
    block: List[str] = []
    lines = text.splitlines()

    def flush_block() -> None:
        if not block:
            return
        if _is_preformatted(block):
            paragraphs.append('\n'.join(block).rstrip())
        else:
            paragraphs.append(_format_paragraph(block, indent, width))
        block.clear()

    for line in lines:
        if not line.strip():
            flush_block()
            paragraphs.append('')
        else:
            if _is_bullet_line(line):
                flush_block()
                block.append(line)
                flush_block()
            else:
                block.append(line)
    flush_block()

    # Collapse multiple consecutive blanks to single blank lines
    normalized: List[str] = []
    blank_pending = False
    for para in paragraphs:
        if not para:
            if not blank_pending and normalized:
                normalized.append('')
            blank_pending = True
        else:
            normalized.append(para)
            blank_pending = False

    return '\n'.join(normalized).rstrip() + '\n'


def resolve_agent(cmdargs: argparse.Namespace) -> Optional[str]:
    config = b4.get_main_config()
    agent_cmd = None
    if getattr(cmdargs, 'config', None):
        for key in ('STYLE_AGENT', 'AGENT'):
            if key in cmdargs.config:
                agent_cmd = cmdargs.config[key]
                break
    if not agent_cmd:
        agent_cmd = config.get('style-agent', config.get('agent', None))
    return agent_cmd


def build_prompt(raw_text: str, indent: int, width: int, example: str) -> str:
    indent_desc = 'no additional indent' if indent == 0 else f'{indent} leading space(s)'
    header_hint = ''
    if any(line.strip().endswith(':') for line in raw_text.splitlines() if line.strip()):
        header_hint = textwrap.dedent(
            """
            Additional rule: if a line ends with a colon, treat it as a section header.
            Any immediately following list items must be indented two spaces further than the header.
            Example:
            section:
              - child item
              - nested:
                  - grandchild item
            The words themselves must remain unchanged; only spacing adjusts.
            """
        ).strip()

    guidance = textwrap.dedent(
        f"""
        You are a meticulous text formatter. Reformat the provided text to the specified style
        without changing any of its words, punctuation, capitalization, ordering, or structure.

        Formatting requirements:
        - Wrap plain paragraphs so that no line exceeds {width} characters.
        - Apply {indent_desc} at the start of each wrapped paragraph line.
        - Preserve blank lines and paragraph boundaries exactly as in the input.
        - Keep existing bullet markers, numbering, and quote markers.
        - Treat code/preformatted blocks as read-only: lines that start with four spaces, a tab,
          or appear inside triple backticks must be emitted exactly as received.
        - Do not add commentary or Markdown fences; output plain text only.
        - The text must contain exactly the same words as the input, in the same order.

        {header_hint}

        Reference formatting example (for guidance only):
        ---
        {example.strip()}
        ---

        Reformat the following text accordingly and return only the reformatted text:
        ```
        {raw_text.rstrip()}
        ```
        """
    ).strip()
    return guidance


def normalize_output(output: str) -> str:
    stripped = output.strip('\n')
    if stripped.startswith('```'):
        lines = stripped.splitlines()
        while lines and lines[0].startswith('```'):
            lines.pop(0)
        while lines and lines[-1].startswith('```'):
            lines.pop()
        stripped = '\n'.join(lines).strip('\n')
    return stripped + '\n'


def main(cmdargs: argparse.Namespace) -> None:
    width = cmdargs.max_lines
    indent = cmdargs.indent

    if cmdargs.input and cmdargs.input != '-':
        with open(os.path.expanduser(cmdargs.input), 'r', encoding='utf-8') as fh:
            raw = fh.read()
    else:
        raw = sys.stdin.read()

    agent_cmd = resolve_agent(cmdargs)
    if not agent_cmd:
        logger.critical('No AI agent configured. Set style-agent in config or use -c STYLE_AGENT=/path/to/agent.sh')
        sys.exit(1)

    example = format_text(raw, indent, width)
    prompt = build_prompt(raw, indent, width, example)

    response = call_agent(prompt, agent_cmd)
    if not response:
        logger.critical('No response from agent')
        sys.exit(1)

    formatted = normalize_output(response)

    if cmdargs.output and cmdargs.output != '-':
        outpath = os.path.expanduser(cmdargs.output)
        outdir = os.path.dirname(outpath)
        if outdir and not os.path.exists(outdir):
            os.makedirs(outdir, exist_ok=True)
        with open(outpath, 'w', encoding='utf-8') as fh:
            fh.write(formatted)
    else:
        sys.stdout.write(formatted)


def setup_parser(sp):
    sp.add_argument('input', nargs='?', default='-', help='Input file (defaults to stdin)')
    sp.add_argument('-o', '--output', default='-', help='Output file (defaults to stdout)')
    sp.add_argument('--indent', type=int, default=0, help='Number of spaces to indent paragraphs')
    sp.add_argument('--max-lines', dest='max_lines', type=int, default=72,
                    help='Maximum characters per line when wrapping text')
