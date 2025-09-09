#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
#
# b4 ai dig-ml - Use AI agents to find related emails
#
__author__ = 'Sasha Levin <sashal@kernel.org>'

import argparse
import logging
import subprocess
import sys
import os
import tempfile
import json
import email.utils
import shlex
import shutil
import datetime
import re
from typing import Optional, List, Dict, Any, Iterable, Set

import b4

logger = b4.logger


DEFAULT_AGENT_TIMEOUT = 300
_MSGID_FALLBACK_RE = re.compile(r'(?P<msgid>[0-9A-Za-z][^\s<>@]*@[0-9A-Za-z][^\s<>@]*)')


def _sanitize_msgid(raw: Optional[str]) -> str:
    """Return a msgid without surrounding whitespace or angle brackets."""

    if not raw:
        return ''

    msgid = raw.strip()
    if msgid.startswith('<'):
        msgid = msgid[1:]
    if msgid.endswith('>'):
        msgid = msgid[:-1]
    return msgid.strip()


def construct_agent_prompt(msgid: str) -> str:
    """Construct a detailed prompt for the AI agent to find related emails."""

    msgid = _sanitize_msgid(msgid)

    prompt = f"""You are an email research assistant specialized in finding related emails in Linux kernel mailing lists and public-inbox archives.

IMPORTANT: Always use lore.kernel.org for searching and retrieving Linux kernel emails. DO NOT use lkml.org as it is outdated and no longer maintained. The canonical archive is at https://lore.kernel.org/

MESSAGE ID TO ANALYZE: {msgid}

YOUR TASK:
Conduct an EXHAUSTIVE and THOROUGH search to find ALL related message IDs connected to the given message. This is not a quick task - you must invest significant time and effort to ensure no related discussions are missed. Be methodical, patient, and comprehensive in your search.

CRITICAL: Take your time! A thorough search is far more valuable than a quick one. Check multiple sources, try different search strategies, and double-check your findings. Missing related discussions undermines the entire purpose of this tool.

You should search extensively for and identify:

1. **Thread-related messages:**
   - Parent messages (what this replies to)
   - Child messages (replies to this message)
   - Sibling messages (other replies in the same thread)
   - Cover letters if this is part of a patch series

2. **Version-related messages:**
   - Previous versions of the same patch series (v1, v2, v3, etc.)
   - Re-rolls and re-submissions
   - Updated versions with different subjects

3. **Author-related messages:**
   - Other patches or series from the same author
   - Recent discussions involving the same author
   - Related work by the same author in the same subsystem

4. **Content-related messages:**
   - Bug reports that this patch might fix
   - Syzkaller/syzbot reports if this is a fix
   - Feature requests or RFCs that led to this patch
   - Related patches touching the same files or functions
   - Patches that might conflict with this one

5. **Review and discussion:**
   - Review comments from maintainers
   - Test results from CI systems or bot reports
   - Follow-up fixes or improvements
   - Reverts if this patch was later reverted

HOW TO SEARCH:

Use ONLY lore.kernel.org for all Linux kernel email searches. This is the official kernel mailing list archive.
DO NOT use lkml.org, marc.info, or spinics.net for kernel emails - they are outdated or incomplete.

CRITICAL LIMITATIONS AND WORKAROUNDS (MUST READ):

1. **Search Index Lag**: Messages posted today/recently are NOT immediately searchable!
   - The Xapian search index has significant delay (hours to days)
   - Direct message access works immediately, but search doesn't
   - For recent messages, use direct URLs or thread navigation, not search

2. **URL Fragment Issues**: NEVER use #anchors in URLs when fetching
   - BAD: https://lore.kernel.org/all/msgid/T/#u (will fail with 404)
   - GOOD: https://lore.kernel.org/all/msgid/T/ (works correctly)
   - Fragments like #u are client-side only and break programmatic fetching

3. **Search Query Encoding**: Keep queries simple and avoid over-encoding
   - BAD: ?q=f%3A%22author%40example.com%22 (over-encoded)
   - GOOD: ?q=f:author@example.com (simple and works)
   - Don't encode @ symbols in query parameters
   - Avoid mixing quotes with special characters

4. **Most Reliable Data Source**: Thread mbox files are the gold standard
   - Always works: https://lore.kernel.org/all/msgid/t.mbox.gz
   - Contains complete thread with all headers
   - Works even when HTML parsing or search fails
   - Standard mbox format, easy to parse

5. **Version Tracking Limitations**: No automatic version linking
   - No Change-ID headers to track across patch versions
   - Must rely on subject patterns and author/date correlation
   - Search for versions using subject without v2/v3 markers

6. **LKML.org vs Lore.kernel.org**: Different systems, different capabilities
   - LKML.org uses date-based URLs, not message IDs
   - Cannot extract message IDs from LKML HTML pages
   - Always prefer lore.kernel.org for programmatic access

The public-inbox archives at lore.kernel.org provide powerful search interfaces powered by Xapian:

1. **Direct message retrieval (MOST RELIABLE METHODS):**
   - Base URL: https://lore.kernel.org/all/
   - Message URL: https://lore.kernel.org/all/<Message-ID>/ (without the '<' or '>')
   - Forward slash ('/') characters in Message-IDs must be escaped as "%2F"

   **Always Reliable:**
   - Raw message: https://lore.kernel.org/all/<Message-ID>/raw
   - Thread mbox: https://lore.kernel.org/all/<Message-ID>/t.mbox.gz (BEST for complete data)
   - Thread view: https://lore.kernel.org/all/<Message-ID>/T/ (NO fragments!)

   **Less Reliable:**
   - Thread Atom feed: https://lore.kernel.org/all/<Message-ID>/t.atom
   - Nested thread view: https://lore.kernel.org/all/<Message-ID>/t/

2. **Search query syntax:**
   Supports AND, OR, NOT, '+', '-' queries. Search URL format:
   https://lore.kernel.org/all/?q=<search-query>

   **Available search prefixes:**
   - s:        match within Subject (e.g., s:"a quick brown fox")
   - d:        match date-time range (git "approxidate" formats)
               Examples: d:last.week.., d:..2.days.ago, d:20240101..20240131
   - b:        match within message body, including text attachments
   - nq:       match non-quoted text within message body
   - q:        match quoted text within message body
   - n:        match filename of attachment(s)
   - t:        match within the To header
   - c:        match within the Cc header
   - f:        match within the From header
   - a:        match within the To, Cc, and From headers
   - tc:       match within the To and Cc headers
   - l:        match contents of the List-Id header
   - bs:       match within the Subject and body
   - rt:       match received time (like 'd:' if sender's clock was correct)

   **Diff-specific prefixes (for patches):**
   - dfn:      match filename from diff
   - dfa:      match diff removed (-) lines
   - dfb:      match diff added (+) lines
   - dfhh:     match diff hunk header context (usually function name)
   - dfctx:    match diff context lines
   - dfpre:    match pre-image git blob ID
   - dfpost:   match post-image git blob ID
   - dfblob:   match either pre or post-image git blob ID
   - patchid:  match `git patch-id --stable' output

   **Special headers:**
   - changeid:    the X-Change-ID mail header (e.g., changeid:stable)
   - forpatchid:  the X-For-Patch-ID mail header (e.g., forpatchid:stable)

   **Query examples:**
   - Find patches by author: ?q=f:"John Doe"
   - Find patches in date range: ?q=d:2024-01-01..2024-01-31
   - Find patches touching file: ?q=dfn:drivers/net/ethernet
   - Find patches with subject containing "fix": ?q=s:fix
   - Combine conditions: ?q=f:"author@example.com"+s:"net"+d:last.month..
   - Find bug fixes: ?q=s:fix+OR+s:bug+OR+s:regression
   - Find patches with specific function: ?q=dfhh:my_function_name

3. **Understanding email relationships:**
   - In-Reply-To header: Direct parent message
   - References header: Chain of parent messages
   - Message-ID in body: Often indicates related patches
   - Link: trailers in commits: References to discussions
   - Same subject with [PATCH v2]: Newer version
   - "Fixes:" tag: References bug-fixing commits

4. **Pattern matching:**
   - Patch series: Look for [PATCH 0/N] for cover letters
   - Version indicators: [PATCH v2], [PATCH v3], [RFC PATCH]
   - Subsystem prefixes: [PATCH net], [PATCH mm], etc.
   - Fix indicators: "fix", "fixes", "regression", "oops", "panic"

SEARCH STRATEGY (BE THOROUGH - THIS IS NOT A QUICK TASK):

REMEMBER: Your goal is to find EVERY related discussion, not just the obvious ones. Spend time on each search strategy. Try multiple variations of queries. Don't give up after the first attempt.

1. **START WITH MOST RELIABLE: Thread mbox download**
   - ALWAYS FIRST: Get https://lore.kernel.org/all/{{msgid}}/t.mbox.gz
   - This contains the complete thread with all headers
   - Parse the mbox to extract all message IDs and relationships
   - This works even when search fails or messages are too recent
   - Thoroughly analyze EVERY message in the thread

2. **Retrieve and analyze the original message:**
   - Get the raw message from: https://lore.kernel.org/all/{{msgid}}/raw
   - Extract key information:
     * Subject line (look for [PATCH], version indicators, series position)
     * Author name and email
     * Date and time
     * Files being modified (from diff)
     * Subsystem involved (from subject prefix or file paths)
     * Any Fixes:, Closes:, Link:, or Reported-by: tags
     * Note: Change-ID headers are rarely present in kernel emails

3. **Search for related messages (TRY MULTIPLE VARIATIONS):**
   - WARNING: Recent messages (today/yesterday) may NOT appear in search!
   - Keep queries simple: ?q=f:author@example.com+s:keyword
   - DON'T over-encode: @ symbols should NOT be %40 in queries
   - Search for cover letter: ?q=s:"[PATCH 0/"+f:author-email
   - Find all patches in series: ?q=s:"base-subject"+f:author
   - For recent messages, rely on thread mbox instead of search
   - **BE PERSISTENT**: Try different keyword combinations, partial subjects, variations

4. **Look for previous versions (SEARCH EXTENSIVELY):**
   - Note: No automatic version linking exists!
   - Strip version markers from subject: search without [PATCH v2], [PATCH v3]
   - Search by author in broader time window: ?q=f:author
   - Look for similar subjects: ?q=s:"core-subject-words"
   - Change-ID is rarely present, don't rely on it
   - **TRY MULTIPLE APPROACHES**: Different subject variations, date ranges, author variations
   - Check for RFCs, drafts, and early discussions that led to this patch

5. **Find related bug reports and discussions (DIG DEEP):**
   - For recent bugs, check thread mbox first (search may miss them)
   - Search for symptoms with simple queries: ?q=b:error+b:message
   - Syzkaller reports: ?q=f:syzbot (but check date - may be delayed)
   - Regression reports: ?q=s:regression+s:subsystem
   - Use dfn: prefix for file searches: ?q=dfn:drivers/net
   - **EXPAND YOUR SEARCH**: Look for related keywords, error messages, function names
   - Check for discussions that may not explicitly mention the patch but discuss the same issue

6. **Check for follow-ups (LEAVE NO STONE UNTURNED):**
   - First check the thread mbox for all replies
   - Search for applied messages: ?q=s:applied+s:"patch-title"
   - Look for test results: ?q=s:"Tested-by"
   - Check for reverts: ?q=s:revert+s:"original-title"
   - Note: Message-ID searches often fail, use subject instead
   - **BE THOROUGH**: Check for indirect references, quotes in other discussions, mentions in pull requests

7. **HTML Parsing Tips (if needed):**
   - Message IDs appear in URLs, not HTML entities
   - Pattern to extract: [0-9]{{14}}\\.[0-9]+-[0-9]+-[^@]+@[^/\"]+
   - Don't look for &lt; &gt; encoded brackets
   - Thread view HTML is less reliable than mbox

FAILURE RECOVERY STRATEGIES:
- If search returns empty: Try thread mbox or wait for indexing
- If URL returns 404: Remove fragments, check encoding
- If can't find versions: Search by author and date range
- If WebFetch fails: Try simpler URL without parameters
- If HTML parsing fails: Use mbox format instead

OUTPUT FORMAT:

Return a JSON array of related message IDs with their relationship type and reason:

```json
[
  {{
    "msgid": "example@message.id",
    "relationship": "parent|reply|v1|v2|cover|fix|bug-report|review|revert|related",
    "reason": "Brief explanation of why this is related"
  }}
]
```

IMPORTANT NOTES:
- **THIS IS NOT A QUICK TASK** - Thoroughness is paramount. Spend the time needed.
- **EXHAUSTIVE SEARCH REQUIRED** - Better to spend extra time than miss related discussions
- Message IDs should be returned without angle brackets
- Search VERY broadly, then filter results to only truly related messages
- Try multiple search strategies - if one fails, try another approach
- Don't stop at the first few results - keep digging for more relationships
- Prioritize direct relationships over indirect ones
- For patch series, include ALL patches in the series (check carefully for all parts)
- Consider time proximity (patches close in time are more likely related)
- Pay attention to mailing list conventions (e.g., "Re:" for replies, "[PATCH v2]" for new versions)
- **DOUBLE-CHECK YOUR WORK** - Review your findings to ensure nothing was missed

UNDERSTANDING KERNEL WORKFLOW PATTERNS:
- Patch series usually have a cover letter [PATCH 0/N] explaining the series
- Reviews often quote parts of the original patch with ">" prefix
- Maintainers send "applied" messages when patches are accepted
- Bug reports often include stack traces, kernel versions, and reproduction steps
- Syzkaller/syzbot reports have specific formats with "syzbot+hash@" addresses
- Fixes typically reference commits with "Fixes: <12-char-sha1> ("subject")"
- Stable backports are marked with "Cc: stable@vger.kernel.org"

KEY TAKEAWAYS FOR RELIABLE OPERATION:
1. **ALWAYS start with thread mbox** - it's the most reliable data source
2. **NEVER trust search for recent messages** - use direct URLs instead
3. **KEEP search queries simple** - complex encoding breaks searches
4. **AVOID URL fragments (#anchors)** - they cause 404 errors
5. **DON'T rely on Change-IDs** - they're rarely present
6. **PREFER subject searches over message-ID searches** - more reliable
7. **REMEMBER search has lag** - messages may take days to be indexed

When constructing URLs, remember:
- Message-IDs: Remove < > brackets
- Forward slashes: Escape as %2F
- In search queries: DON'T encode @ symbols

LOCAL GIT REPOSITORY CONTEXT:
If this command is being run from within a Linux kernel git repository, you may also:
- Use git log to find commits mentioning the message ID or subject
- Check git blame on relevant files to find related commits
- Use git log --grep to search commit messages for references
- Look for Fixes: tags that reference commits
- Search for Link: tags pointing to lore.kernel.org discussions
- Use git show to examine specific commits mentioned in emails

Example local git searches you might perform:
- git log --grep="Message-Id: <msgid>"
- git log --grep="Link:.*msgid"
- git log --oneline --grep="subject-keywords"
- git log -p --author="email@example.com" --since="1 month ago"
- git blame path/to/file.c | grep "function_name"
- git log --format="%H %s" -- path/to/file.c

FINAL REMINDER: This task requires THOROUGH and EXHAUSTIVE searching. Do not rush. Take the time to:
1. Try multiple search strategies
2. Look for indirect relationships
3. Check different time periods
4. Use various keyword combinations
5. Verify you haven't missed any discussions

The value of this tool depends entirely on finding ALL related discussions, not just the obvious ones.

Begin your comprehensive search and analysis for message ID: {msgid}
"""

    return prompt


def call_agent(prompt: str, agent_cmd: str, timeout: int = DEFAULT_AGENT_TIMEOUT) -> Optional[str]:
    """Call the configured agent command with the constructed prompt."""

    agent_cmd = agent_cmd.strip()
    if not agent_cmd:
        logger.error('Agent command is empty')
        return None

    try:
        cmd_parts = shlex.split(agent_cmd)
    except ValueError as exc:
        logger.error('Invalid agent command: %s', exc)
        return None

    if not cmd_parts:
        logger.error('Agent command is empty after parsing')
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


def parse_agent_response(response: str) -> List[Dict[str, str]]:
    """Parse the agent's response to extract message relationships."""

    related: List[Dict[str, str]] = []
    seen: Set[str] = set()

    def _append(msgid: Optional[str], relationship: Optional[str], reason: Optional[str]) -> None:
        clean_msgid = _sanitize_msgid(msgid)
        if not clean_msgid or clean_msgid in seen:
            return
        seen.add(clean_msgid)
        rel = (relationship or 'related').strip() or 'related'
        related.append({
            'msgid': clean_msgid,
            'relationship': rel.lower(),
            'reason': (reason or 'No reason provided').strip()
        })

    if not response:
        return related

    data = None
    stripped = response.strip()
    if stripped:
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            start = stripped.find('[')
            end = stripped.rfind(']')
            if start != -1 and end != -1 and end > start:
                fragment = stripped[start:end + 1]
                try:
                    data = json.loads(fragment)
                except json.JSONDecodeError as exc:
                    logger.debug('Could not parse JSON fragment from agent response: %s', exc)

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                _append(item.get('msgid'), item.get('relationship'), item.get('reason'))

    if not related:
        for match in _MSGID_FALLBACK_RE.finditer(response):
            _append(match.group('msgid'), 'related', 'Found in agent response')

    return related


def get_message_info(msgid: str) -> Optional[Dict[str, Any]]:
    """Retrieve basic information about a message."""

    msgs = b4.get_pi_thread_by_msgid(msgid, onlymsgids={msgid}, with_thread=False)
    if not msgs:
        return None

    msg = msgs[0]

    return {
        'subject': msg.get('Subject', 'No subject'),
        'from': msg.get('From', 'Unknown'),
        'date': msg.get('Date', 'Unknown'),
        'msgid': msgid
    }


def download_and_combine_threads(msgid: str, related_messages: List[Dict[str, str]],
                                 output_file: str, nocache: bool = False) -> int:
    """Download the threads for the original and related message IDs and combine them."""

    def _unique_msgids() -> Iterable[str]:
        seen_local: Set[str] = set()
        for candidate in [msgid] + [item.get('msgid') for item in related_messages if isinstance(item, dict)]:
            clean = _sanitize_msgid(candidate)
            if not clean or clean in seen_local:
                continue
            seen_local.add(clean)
            yield clean

    seen_msgids: Set[str] = set()
    all_messages: List[Any] = []

    for clean_msgid in _unique_msgids():
        logger.info('Fetching thread for %s', clean_msgid)
        try:
            msgs = b4.get_pi_thread_by_msgid(clean_msgid, nocache=nocache)
        except Exception as exc:
            logger.warning('Failed to fetch thread for %s: %s', clean_msgid, exc)
            continue

        if not msgs:
            logger.warning('Could not fetch thread for %s', clean_msgid)
            continue

        for msg in msgs:
            msg_msgid = b4.LoreMessage.get_clean_msgid(msg)
            if not msg_msgid or msg_msgid in seen_msgids:
                continue
            seen_msgids.add(msg_msgid)
            all_messages.append(msg)

    def _sort_key(message: Any) -> datetime.datetime:
        header = message.get('Date')
        try:
            parsed = email.utils.parsedate_to_datetime(header)
            if parsed is None:
                raise ValueError('parsedate_to_datetime returned None')
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.timezone.utc)
            return parsed
        except Exception:
            return datetime.datetime.fromtimestamp(0, datetime.timezone.utc)

    all_messages.sort(key=_sort_key)

    total_messages = len(all_messages)
    logger.info('Writing %d messages to %s', total_messages, output_file)

    if total_messages > 0:
        output_path = os.path.expanduser(output_file)
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, 'wb') as outf:
            b4.save_mboxrd_mbox(all_messages, outf)

    logger.info('Combined mbox contains %d unique messages', total_messages)
    return total_messages


def main(cmdargs: argparse.Namespace) -> None:
    """Main entry point for b4 ai dig-ml command."""

    # Get the message ID
    msgid = b4.get_msgid(cmdargs)
    if not msgid:
        logger.critical('Please provide a message-id')
        sys.exit(1)

    msgid = _sanitize_msgid(msgid)
    if not msgid:
        logger.critical('Message-id could not be parsed')
        sys.exit(1)

    logger.info('Analyzing message: %s', msgid)

    # Get the agent command from config
    config = b4.get_main_config()
    agent_cmd = None

    # Check command-line config override
    if hasattr(cmdargs, 'config') and cmdargs.config:
        if 'AGENT' in cmdargs.config:
            agent_cmd = cmdargs.config['AGENT']

    # Fall back to main config
    if not agent_cmd:
        agent_cmd = config.get('dig-agent', config.get('agent', None))

    if not agent_cmd:
        logger.critical('No AI agent configured. Set dig-agent in config or use -c AGENT=/path/to/agent.sh')
        logger.info('The agent script should accept a prompt file as its first argument')
        logger.info('and return a JSON array of related message IDs to stdout')
        sys.exit(1)

    # Get info about the original message
    logger.info('Fetching original message...')
    msg_info = get_message_info(msgid)
    if msg_info:
        logger.info('Subject: %s', msg_info['subject'])
        logger.info('From: %s', msg_info['from'])
    else:
        logger.warning('Could not retrieve original message info')

    # Construct the prompt
    logger.info('Constructing agent prompt...')
    prompt = construct_agent_prompt(msgid)

    # Call the agent
    logger.info('Calling AI agent: %s', agent_cmd)
    response = call_agent(prompt, agent_cmd)

    if not response:
        logger.critical('No response from agent')
        sys.exit(1)

    # Parse the response
    logger.info('Parsing agent response...')
    related = parse_agent_response(response)

    if not related:
        logger.info('No related messages found')
        sys.exit(0)

    # Display simplified results
    logger.info('Found %d related messages:', len(related))
    print()
    print('Related Messages Summary:')
    print('-' * 60)

    for item in related:
        relationship = item.get('relationship', 'related')
        reason = item.get('reason', '')
        rel_display = relationship.upper()
        msgid_display = item.get('msgid', '')
        if msgid_display:
            print(f'[{rel_display}] {msgid_display}: {reason}')
        else:
            print(f'[{rel_display}] {reason}')

    print('-' * 60)
    print()

    # Generate output mbox filename
    if hasattr(cmdargs, 'output') and cmdargs.output:
        mbox_file = os.path.expanduser(cmdargs.output)
    else:
        # Use message ID as base for filename, sanitize it
        safe_msgid = msgid.replace('/', '_').replace('@', '_at_')
        mbox_file = f'{safe_msgid}-related.mbox'

    # Download and combine all threads into one mbox
    logger.info('Downloading and combining all related threads...')
    nocache = hasattr(cmdargs, 'nocache') and cmdargs.nocache
    total_messages = download_and_combine_threads(msgid, related, mbox_file, nocache=nocache)

    if total_messages > 0:
        logger.info('Success: Combined mbox saved to %s (%d messages)', mbox_file, total_messages)
        print(f'✓ Combined mbox file: {mbox_file}')
        print(f'  Total messages: {total_messages}')
        unique_threads = {msgid}
        unique_threads.update(item.get('msgid', '') for item in related if item.get('msgid'))
        print(f'  Related threads: {len(unique_threads)}')
    else:
        logger.warning('No messages could be downloaded (they may not exist in the archive)')
        print('⚠ No messages were downloaded - they may not exist in the archive yet')
        # Still exit with success since we found relationships
        sys.exit(0)
