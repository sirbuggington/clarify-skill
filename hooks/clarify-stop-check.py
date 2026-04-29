# clarify-stop-check.py
# Stop hook for the /clarify skill — structural backstop.
#
# Runs after each assistant turn finishes. Detects "recommendation produced
# without AskUserQuestion called" and surfaces a warning on the user's NEXT
# turn (via a marker file the UserPromptSubmit hook reads).
#
# Pipeline:
#   1. Parse Stop hook payload (session_id, transcript_path).
#   2. Read transcript, find the most recent user prompt.
#   3. Collect all assistant text + tool uses since that user prompt.
#   4. If AskUserQuestion was called this turn -> no violation, exit 0.
#   5. Regex first-pass on assistant text. If no match -> exit 0.
#   6. LLM classifier (claude -p with haiku) confirms whether the response is
#      a recommendation that needed user-unique context. If NO -> exit 0.
#   7. Log violation, write marker file. Exit 0 (non-blocking).
#
# Failure mode: fail-open. Any error path exits 0 silently.

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone


CLAUDE_CONFIG_DIR = Path(
    os.environ.get('CLAUDE_CONFIG_DIR') or os.path.expanduser('~/.claude')
)
VIOLATION_LOG = CLAUDE_CONFIG_DIR / 'clarify-violations.log'
VIOLATION_MARKER = CLAUDE_CONFIG_DIR / '.clarify-violation-pending'
DEBUG_LOG = CLAUDE_CONFIG_DIR / 'clarify-stop-debug.log'

# Regex first-pass heuristics. Heuristics — false positives are OK because
# the LLM classifier is the authoritative gate. False negatives (missed
# recommendations) are the failure mode to minimize, so this leans permissive.
RECOMMENDATION_PATTERNS = [
    # Direct recommendation/suggestion verbs (with optional adverbs between
    # the pronoun and the verb — e.g., "I'd actually recommend",
    # "What I'd really suggest", "I would honestly advise").
    r'\bI(?:\'d| would)?(?:\s+\w+){0,3}\s+recommend\b',
    r'\b(?:I|you)(?:\'d| would)?(?:\s+\w+){0,3}\s+suggest\b',
    r'\bI(?:\'d| would)?(?:\s+\w+){0,3}\s+(?:advise|propose|lean toward|lean towards|push you toward)\b',
    r'\bI\'d(?:\s+\w+){0,3}\s+(?:go|start|focus|pick|choose|do|try|push|raise|lower|skip|drop|use|aim|stick|switch|charge|price|consider|stay|keep|stop|pivot|niche|target)\b',
    r'\byou should\b',
    r'\byou(?:\'d| would)?\s+(?:probably\s+)?want to\b',
    # Framing phrases ("What I'd actually recommend", "Here's what I'd do")
    r'\b(?:what|here(?:\'s| is)) (?:I|you)(?:\'d| would)?(?:\s+\w+){0,3}\s+(?:consider|do|recommend|suggest|advise|think|focus(?: on)?|go with)\b',
    r'\bnext steps?\b',
    r'\bthe (?:best|better|right|smart|smartest) (?:approach|option|move|path|choice|bet|play|thing)\b',
    r'\bmy (?:honest |actual |real |best )?(?:recommendation|suggestion|advice|take|read|call)\b',
    r'\b(?:honest|actual|real)(?:ly)? (?:take|recommendation|advice|read)\b',  # "honest take", "actually recommend"
    # Soft-critique patterns that imply needed change
    r'\b(?:need|needs)\s+(?:work|fixing|to change|to be fixed|attention)\b',
    r'\bisn(?:\'t| not) bad\b',
    r'\b(?:problem|issue|concern) (?:here|with this|with that) is\b',
    # Offer-continuation (model proposing more advice/work)
    r'\b(?:would|do) you (?:like|want)\s+(?:help|me to|my help)\b',
    r'\b(?:want|would you like) me to\b',  # "Want me to dig into", "Would you like me to..."
    r'\bI can (?:help|walk you|build|draft|write|set up)\b',
    # Imperative bulleted list items (2+ in a row signals advice list)
    # Matched separately by IMPERATIVE_BULLET_RX below.
]
RECOMMENDATION_RX = re.compile('|'.join(RECOMMENDATION_PATTERNS), re.IGNORECASE)
# Numbered advice list of 3+ items: "1." ... "2." ... "3." across lines
NUMBERED_LIST_RX = re.compile(r'^\s*1\.\s.+?^\s*2\.\s.+?^\s*3\.\s', re.MULTILINE | re.DOTALL)
# Bulleted imperatives — flags responses with 2+ bullets starting with an
# imperative verb. Captures the "advice list" pattern that's recommendation
# without using "I recommend"-style language.
IMPERATIVE_VERBS = (
    r'Raise|Lower|Increase|Decrease|Start|Stop|Skip|Use|Try|Avoid|Consider|Build|Focus|Stick|Drop|Add|Remove|'
    r'Switch|Replace|Charge|Price|Hire|Outsource|Test|Move|Pick|Choose|Make|Set|Keep|Get|Run|Send|Write|Create|'
    r'Reach out|Begin|Aim|Validate|Talk|Check|Find|Push|Pivot|Cut|Trim|Expand|Limit|Restrict|Negotiate|Verify|'
    r'Bump|Raise|Stay|Step|Position|Frame|Sell|Bundle|Productize|Niche down|Target|Lead with'
)
IMPERATIVE_BULLET_RX = re.compile(
    rf'(?im)(?:^\s*[*•\-]\s+(?:{IMPERATIVE_VERBS})\b.*?(?:\n|$)){{2,}}',
    re.MULTILINE | re.DOTALL,
)


def _debug(msg: str) -> None:
    try:
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        with open(DEBUG_LOG, 'a', encoding='utf-8') as f:
            f.write(f'{ts}\t{msg}\n')
    except Exception:
        pass


def _read_transcript(path: str):
    messages = []
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    messages.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return messages


def _is_user_prompt(msg: dict) -> bool:
    """A 'user prompt' = a user-role message whose content is not solely tool results."""
    if msg.get('type') == 'user' or msg.get('role') == 'user':
        # Claude Code transcripts wrap the actual message under 'message'
        inner = msg.get('message', msg)
        content = inner.get('content', '')
        if isinstance(content, str):
            return bool(content.strip())
        if isinstance(content, list):
            # If every content block is a tool_result, this is a tool response, not a prompt
            for block in content:
                if isinstance(block, dict) and block.get('type') != 'tool_result':
                    return True
            return False
    return False


def _extract_text(msg: dict) -> str:
    inner = msg.get('message', msg)
    content = inner.get('content', '')
    if isinstance(content, str):
        return content
    parts = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'text':
                parts.append(block.get('text', ''))
    return '\n'.join(parts)


def _scan_assistant_turn(messages: list, last_user_idx: int):
    """Return (assistant_text, askuserquestion_called) for messages after last_user_idx."""
    text_parts = []
    askuq = False
    for msg in messages[last_user_idx + 1:]:
        if msg.get('type') == 'assistant' or msg.get('role') == 'assistant':
            inner = msg.get('message', msg)
            content = inner.get('content', [])
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get('type')
                    if btype == 'text':
                        text_parts.append(block.get('text', ''))
                    elif btype == 'tool_use':
                        if block.get('name') == 'AskUserQuestion':
                            askuq = True
    return '\n'.join(text_parts), askuq


def _classify_with_llm(user_prompt: str, assistant_text: str) -> bool:
    """Call `claude -p` with Haiku to classify whether the response is a
    recommendation that needed user-unique context. Returns True/False;
    on any error returns False (conservative: don't false-flag)."""
    classifier_prompt = (
        "You are a classifier. Decide whether the assistant's response is a "
        "RECOMMENDATION that depended on user-unique context (the user's "
        "situation, goal, history, constraints, or preferences) that wasn't "
        "in the prompt.\n\n"
        "A response counts as a RECOMMENDATION when it tells the user what "
        "to do, what choice to make, or what next steps to take — in a way "
        "that would be materially different given different user-unique "
        "facts. Pure explanation, summary, technical answers, code, or "
        "factual reference are NOT recommendations.\n\n"
        "If the response is a recommendation that depended on user-unique "
        "context the user didn't provide, output exactly: YES\n"
        "Otherwise output exactly: NO\n\n"
        "Output only YES or NO. No other text.\n\n"
        "---\n"
        "USER PROMPT:\n"
        f"{user_prompt[:4000]}\n"
        "---\n"
        "ASSISTANT RESPONSE:\n"
        f"{assistant_text[:8000]}\n"
        "---"
    )
    try:
        result = subprocess.run(
            ['claude', '-p', classifier_prompt, '--model', 'claude-haiku-4-5'],
            capture_output=True,
            text=True,
            timeout=60,
            encoding='utf-8',
            errors='replace',
        )
        out = (result.stdout or '').strip().upper()
        _debug(f'classifier stdout={out[:50]!r} stderr={(result.stderr or "")[:100]!r}')
        # Look for YES / NO at start of any line
        for line in out.splitlines():
            s = line.strip()
            if s.startswith('YES'):
                return True
            if s.startswith('NO'):
                return False
        return False
    except Exception as exc:
        _debug(f'classifier-error: {exc!r}')
        return False


def _log_violation(session_id: str, user_prompt: str, assistant_text: str) -> None:
    try:
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        u_snip = ' '.join(user_prompt.split())[:200]
        a_snip = ' '.join(assistant_text.split())[:300]
        with open(VIOLATION_LOG, 'a', encoding='utf-8') as f:
            f.write(f'{ts}\tsession={session_id}\n')
            f.write(f'  prompt: {u_snip}\n')
            f.write(f'  reply : {a_snip}\n\n')
    except Exception:
        pass


def _write_marker(session_id: str, user_prompt: str) -> None:
    """Write the marker file the next UserPromptSubmit hook will read."""
    try:
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        u_snip = ' '.join(user_prompt.split())[:200]
        with open(VIOLATION_MARKER, 'w', encoding='utf-8') as f:
            f.write(json.dumps({
                'timestamp': ts,
                'session_id': session_id,
                'prompt_snippet': u_snip,
            }))
    except Exception:
        pass


def main() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        return

    try:
        payload = json.loads(raw)
    except Exception:
        return

    transcript_path = payload.get('transcript_path')
    session_id = payload.get('session_id', '?')
    if not transcript_path or not os.path.isfile(transcript_path):
        _debug(f'no transcript: {transcript_path!r}')
        return

    messages = _read_transcript(transcript_path)
    if not messages:
        return

    # Find last user prompt
    last_user_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if _is_user_prompt(messages[i]):
            last_user_idx = i
            break
    if last_user_idx is None:
        return

    assistant_text, askuq = _scan_assistant_turn(messages, last_user_idx)

    if askuq:
        _debug(f'session={session_id} askuq=True (no violation)')
        return

    if not assistant_text.strip():
        return

    if not (
        RECOMMENDATION_RX.search(assistant_text)
        or NUMBERED_LIST_RX.search(assistant_text)
        or IMPERATIVE_BULLET_RX.search(assistant_text)
    ):
        _debug(f'session={session_id} regex-clean (no violation)')
        return

    _debug(f'session={session_id} regex-match -> calling classifier')
    user_prompt = _extract_text(messages[last_user_idx])
    is_violation = _classify_with_llm(user_prompt, assistant_text)

    if not is_violation:
        _debug(f'session={session_id} classifier=NO (no violation)')
        return

    _debug(f'session={session_id} classifier=YES -> logging violation')
    _log_violation(session_id, user_prompt, assistant_text)
    _write_marker(session_id, user_prompt)


try:
    main()
except Exception as exc:
    try:
        _debug(f'top-level-error: {exc!r}')
    except Exception:
        pass
    sys.exit(0)
