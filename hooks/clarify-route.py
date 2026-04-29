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
    "**CLARIFY (active).** Before committing to a response, check whether "
    "it rests on assumptions you can't verify from the user's words. "
    "Evaluate the response you'll produce — not the prompt's tone, length, "
    "or phrasing.\n\n"
    "**Rule.** If your response would include a recommendation, plan, "
    "advice, prescription, diagnosis, choice, or \"next steps,\" AND any "
    "user-unique fact (situation, goal, history, constraints, preferences) "
    "could materially change it, your FIRST action MUST be "
    "`AskUserQuestion`. Answering first and acknowledging the gap is a "
    "protocol violation, not a graceful answer.\n\n"
    "**Gate:**\n"
    "1. **Tool can resolve?** Read files, search, check context. If a tool "
    "can answer, use it. If no tool could plausibly resolve the missing "
    "info (personal context, user intent, situation-specific facts), go "
    "to 2.\n"
    "2. **Ask via `AskUserQuestion`** (not chat). Cover only what the user "
    "uniquely knows: situation, history, intent, preferences, constraints, "
    "stakes, hard-to-reverse choices. State a default per question "
    "(\"If unspecified, I will assume X\") for one-click approval. Ask the "
    "minimum; skip intrusive details. **Keep each question and option as "
    "short as possible without losing meaning** — one short sentence per "
    "question, short labels per option, full detail only when meaning "
    "requires it.\n\n"
    "Applies to any request — code, advice, decisions, plans, analysis, "
    "writing, anything. Skip trivial reversible details and genuinely "
    "unambiguous prompts. Goal: ask only when you cannot give a complete, "
    "grounded answer without input only the user has."
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


def _debug_log(decision: str, prompt: str, mode: str = '?') -> None:
    """Append one line per hook invocation to ~/.claude/clarify-debug.log.

    TEMPORARY DIAGNOSTIC — added to verify whether the hook is firing on
    test prompts. Failures here are swallowed (fail-open, same as the hook
    overall). Remove this function and its call sites once diagnosis is done.
    """
    try:
        from datetime import datetime, timezone
        log_path = CLAUDE_CONFIG_DIR / 'clarify-debug.log'
        ts = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
        # First 80 chars of prompt, single-line, control chars stripped
        snippet = ' '.join(prompt.split())[:80]
        line = f'{ts}\tdecision={decision}\tmode={mode}\tprompt={snippet}\n'
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(line)
    except Exception:
        pass


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
        _debug_log('skip-peer', prompt)
        sys.exit(0)

    # Skip /clarify* invocations — the skill itself owns those turns.
    if re.search(r'^\s*/clarify($|\s|-\w)', prompt, re.IGNORECASE):
        _debug_log('skip-clarify', prompt)
        sys.exit(0)

    mode = read_mode()
    if not should_inject(prompt, mode):
        # Determine specific skip reason for the log
        if mode == 'off':
            reason = 'skip-mode-off'
        elif len(prompt.strip()) < 5:
            reason = 'skip-too-short'
        else:
            reason = 'skip-ack'
        _debug_log(reason, prompt, mode)
        sys.exit(0)

    # Check for a pending violation marker from the Stop hook backstop. If
    # present, prepend a visible warning to the injection text and consume
    # (delete) the marker so it only fires once.
    violation_warning = ''
    marker = CLAUDE_CONFIG_DIR / '.clarify-violation-pending'
    if marker.exists():
        try:
            data = json.loads(marker.read_text(encoding='utf-8'))
            snippet = data.get('prompt_snippet', '')
            ts = data.get('timestamp', '')
            violation_warning = (
                "**[CLARIFY VIOLATION DETECTED — prior turn]** The structural "
                "backstop flagged your last response as a recommendation "
                f"produced without `AskUserQuestion`. Prior prompt at {ts}: "
                f"`{snippet}`. The behavioral rule below requires "
                "`AskUserQuestion` as the FIRST action when those conditions "
                "are met. On this turn, comply with the rule strictly.\n\n"
                "---\n\n"
            )
        except Exception:
            pass
        try:
            marker.unlink()
        except Exception:
            pass

    result = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": violation_warning + INJECTION_TEXT,
        }
    }

    _debug_log('inject' + ('+violation' if violation_warning else ''), prompt, mode)
    print(json.dumps(result, separators=(',', ':'), ensure_ascii=False))


try:
    main()
except Exception:
    sys.exit(0)
