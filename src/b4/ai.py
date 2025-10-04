#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Common helpers for AI-enabled commands
#

import os
import shlex
import shutil
import subprocess
import tempfile
from typing import Optional, List

import b4

logger = b4.logger


DEFAULT_AGENT_TIMEOUT = 600


def sanitize_msgid(raw: Optional[str]) -> str:
    """Return a msgid without surrounding whitespace or angle brackets."""

    if not raw:
        return ''

    msgid = raw.strip()
    if msgid.startswith('<'):
        msgid = msgid[1:]
    if msgid.endswith('>'):
        msgid = msgid[:-1]
    return msgid.strip()


def _resolve_agent_command(agent_cmd: str) -> Optional[List[str]]:
    agent_cmd = agent_cmd.strip()
    if not agent_cmd:
        return None

    try:
        cmd_parts = shlex.split(agent_cmd)
    except ValueError as exc:
        logger.error('Invalid agent command: %s', exc)
        return None

    if not cmd_parts:
        return None

    executable = os.path.expanduser(cmd_parts[0])
    if os.path.sep in executable:
        if not os.path.exists(executable):
            logger.error('Agent command not found: %s', executable)
            return None
        if not os.access(executable, os.X_OK):
            logger.error('Agent command is not executable: %s', executable)
            return None
        cmd_parts[0] = executable
    else:
        resolved = shutil.which(cmd_parts[0])
        if not resolved:
            logger.error('Agent command not found in PATH: %s', cmd_parts[0])
            return None
        cmd_parts[0] = resolved

    return cmd_parts


def call_agent(prompt: str, agent_cmd: str, timeout: int = DEFAULT_AGENT_TIMEOUT) -> Optional[str]:
    """Call the configured agent command with the provided prompt."""

    cmd_parts = _resolve_agent_command(agent_cmd)
    if not cmd_parts:
        return None

    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as tmp:
            tmp.write(prompt)
            tmp_path = tmp.name

        cmd_with_prompt = cmd_parts + [tmp_path]
        logger.info('Calling agent: %s', ' '.join(shlex.quote(part) for part in cmd_with_prompt))

        result = subprocess.run(
            cmd_with_prompt,
            capture_output=True,
            text=True,
            timeout=timeout
        )

        if result.returncode != 0:
            logger.error('Agent returned error code %d', result.returncode)
            if result.stderr:
                logger.error('Agent stderr: %s', result.stderr.strip())
            if result.stdout:
                logger.debug('Agent stdout: %s', result.stdout.strip())
            return None

        return result.stdout

    except subprocess.TimeoutExpired:
        logger.error('Agent command timed out after %d seconds', timeout)
        return None
    except Exception as exc:
        logger.error('Error calling agent: %s', exc)
        return None
    finally:
        if 'tmp_path' in locals():
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.debug('Failed to remove temporary file %s', tmp_path)
