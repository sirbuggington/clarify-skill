# clarify-route.py
# UserPromptSubmit hook for the /clarify skill.
#
# Always-on enforcement layer for "ask the user when you genuinely cannot give
# a complete, grounded answer without input only the user can provide." Runs on
# every prompt the user submits.
#
# v2 architecture: the hook does NOT pre-judge the prompt with regex heuristics.
# Ambiguity is a property of the gap between the user's prompt and the response
# Claude is about to give — only Claude has the full context (system prompt,
# tools, files, history) to make that call. The hook's job is to remind Claude
# of the rule; Claude's job is to apply it.
#
# Hook decisions:
#   1. Skip on /peer* and /clarify* invocations (avoid double-injection)
#   2. Skip if mode == "off"
#   3. Skip if prompt is trivially short (< 5 chars)
#   4. Skip if prompt is a pure acknowledgement (exact-match against ACK_LIST)
#   5. Otherwise inject the rule
#
# Modes are simply "on" / "off". Legacy mode names (strict/light/default) from
# earlier versions are accepted at read time and treated as "on" — no behavioral
# difference vs. the merged mode.
#
# Output schema: https://docs.claude.com/en/docs/claude-code/hooks
#
# Failure mode: this hook fails OPEN. Any error path exits 0 with no output so
# the user's prompt always reaches Claude. Better to lose the override on a rare
# parse failure than to silently swallow a user message.

import json
import os
import re
import sys
from pathlib import Path

# Python 3.9+ required
if sys.version_info < (3, 9):
    sys.exit(0)  # fail-open

if hasattr(sys.stdin, 'reconfigure'):
    sys.stdin.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


# Resolve config dir from CLAUDE_CONFIG_DIR (Claude Code's documented relocation
# env var) with ~/.claude as the fallback. Hook and the /clarify slash command
# must agree on this path or they'll write to one file and read from another.
CLAUDE_CONFIG_DIR = Path(
    os.environ.get('CLAUDE_CONFIG_DIR') or os.path.expanduser('~/.claude')
)
CLARIFY_MODE_FILE = CLAUDE_CONFIG_DIR / 'clarify-mode.json'

DEFAULT_MODE = 'on'
VALID_MODES = ('on', 'off')
# Legacy mode names accepted at read time for backward compatibility; they map to "on".
LEGACY_ON_MODES = ('strict', 'light', 'default')

# Pure acknowledgements — case-insensitive exact match against the stripped
# prompt. The hook skips these because they're conversational filler, not task
# starts. False-skip risk: a one-word "yes" answering a prior AskUserQuestion
# would match — but in that case Claude is in "absorbing the answer" mode, not
# "starting a task" mode, so the rule would be off-target anyway.
#
# Note: "sure" deliberately NOT in this list — Codex flagged it as ambiguous
# (could mean "yes, proceed" in response to a clarification, causing false skip).
ACK_LIST = frozenset({
    'hello', 'hi', 'hey', 'yo',
    'thanks', 'thank you', 'thx', 'ty',
    'ok', 'okay', 'k', 'kk',
    'yes', 'yep', 'yeah', 'yup',
    'no', 'nope', 'nah',
    'cool', 'nice', 'lol',
    'alright', 'all right', 'sounds good',
    'got it', 'understood', 'done', 'perfect',
})

INJECTION_TEXT = (
    "**CLARIFY (active).** Before committing to a response, plan, or "
    "recommendation, check whether what you're about to say rests on "
    "assumptions you can't verify from what the user wrote.\n\n"
    "**Two-step gate:**\n"
    "1. **Can you resolve it yourself?** Use available tools — read files, "
    "search the web, check existing context, run any tool that might fill "
    "the gap. If a tool could answer, use the tool instead of asking. If no "
    "available tool could plausibly resolve the missing info (personal "
    "context, user intent, situation-specific facts), skip directly to step 2.\n"
    "2. **Otherwise →** the user is the unique source. Use the "
    "`AskUserQuestion` tool (not chat text) to ask. Cover only what the user "
    "uniquely knows: their situation, history, intent, preferences, "
    "constraints, stakes, or hard-to-reverse choices. State a default per "
    "question (\"If unspecified, I will assume X\") so they can one-click "
    "approve. Ask only the minimum needed; avoid intrusive details that "
    "don't directly affect the answer.\n\n"
    "This applies to every kind of request — code, advice, decisions, plans, "
    "recommendations, analysis, writing help, anything. Skip for trivial "
    "reversible details and prompts that are genuinely unambiguous. Goal: "
    "ask only when you cannot give a complete, grounded answer without input "
    "the user alone can provide."
)


def read_mode() -> str:
    """Return the current clarify mode. Falls back to DEFAULT_MODE on any error.

    Accepts legacy mode names (strict/light/default) and maps them to "on" so
    older mode files continue to work after the strict/light merge.
    """
    try:
        if not CLARIFY_MODE_FILE.exists():
            return DEFAULT_MODE
        data = json.loads(CLARIFY_MODE_FILE.read_text(encoding='utf-8'))
        mode = data.get('mode', DEFAULT_MODE)
        if mode in LEGACY_ON_MODES:
            return 'on'
        if mode not in VALID_MODES:
            return DEFAULT_MODE
        return mode
    except Exception:
        return DEFAULT_MODE


def should_inject(prompt: str, mode: str) -> bool:
    """Decide whether to inject the clarify rule for this prompt.

    No heuristics on prompt content — the gate is just:
      - mode != off
      - prompt non-trivial (>= 5 chars after strip)
      - prompt isn't a pure acknowledgement
    Claude does the actual ambiguity evaluation in-context.
    """
    if mode == 'off':
        return False

    stripped = prompt.strip()
    if len(stripped) < 5:
        return False

    # Strip trailing punctuation for ack matching ("ok!", "thanks." etc. should match)
    ack_candidate = stripped.rstrip('.!?,;:').lower()
    if ack_candidate in ACK_LIST:
        return False

    return True  # light and strict both reach here; text differs in main()


def main() -> None:
    raw = sys.stdin.read()
    if not raw or not raw.strip():
        sys.exit(0)

    # Strip UTF-8 BOM if present (rare but defends against silent fail-open)
    raw = raw.lstrip('﻿')

    try:
        payload = json.loads(raw)
    except Exception:
        sys.exit(0)

    prompt = payload.get('prompt', '')
    if not isinstance(prompt, str):
        prompt = str(prompt) if prompt is not None else ''
    if not prompt or not prompt.strip():
        sys.exit(0)

    # Skip /peer* invocations — peer-route.py handles those, and /peer's SKILL.md
    # has its own integrated clarification rule. Avoid double-injection.
    # Case-insensitive: Claude Code's slash-command dispatcher is case-insensitive.
    if re.search(r'^\s*/peer($|\s|-\w)', prompt, re.IGNORECASE):
        sys.exit(0)

    # Skip /clarify* invocations — the skill itself owns those turns.
    if re.search(r'^\s*/clarify($|\s|-\w)', prompt, re.IGNORECASE):
        sys.exit(0)

    mode = read_mode()
    if not should_inject(prompt, mode):
        sys.exit(0)

    result = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": INJECTION_TEXT,
        }
    }

    print(json.dumps(result, separators=(',', ':'), ensure_ascii=False))


try:
    main()
except Exception:
    sys.exit(0)
