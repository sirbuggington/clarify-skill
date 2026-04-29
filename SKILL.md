---
name: clarify
description: The /clarify command — manage the always-on clarification rule. Reminds Claude to evaluate whether its planned response rests on unverifiable assumptions and to ask the user (via the AskUserQuestion tool) only about things the user is the unique source for — across every domain, not just code. Operates via a UserPromptSubmit hook (~/.claude/hooks/clarify-route.py). Modes: on / off. Independent of /peer; the hook explicitly skips /peer* prompts so /peer's own integrated rule isn't double-fired.
disable-model-invocation: true
argument-hint: [on | off | status | help]
---

# Clarify Skill

Always-on enforcement layer for "ask the user when you genuinely cannot give a complete, grounded answer without input only the user can provide — and only after checking whether available tools could resolve the gap themselves."

**This skill is domain-neutral.** It applies to every kind of request — code, advice, decisions, plans, recommendations, analysis, writing help, personal-life questions, anything. Ambiguity isn't a property of prompt shape (length, keywords, structure) — it's a property of the gap between the user's prompt and the response Claude is about to give. Only Claude (with full context: system prompt, tools, files, history) can judge that gap. The hook's job is to remind Claude of the rule; Claude's job is to apply it.

The mechanism is a UserPromptSubmit hook at `~/.claude/hooks/clarify-route.py` that runs on every prompt. It does NOT use regex heuristics on prompt content. It only skips the obvious cases (`/peer*` and `/clarify*` invocations, mode = off, prompts under 5 chars, pure acknowledgements like "thanks" / "ok"). For everything else, it injects the rule and lets Claude decide.

This skill is the user-facing control surface: it documents the modes, exposes the slash-command grammar for switching modes, and explains the hook's behavior. The actual rule injection is done by the hook, not by this skill.

---

## OPERATING PROTOCOL (must survive compaction)

### Modes

The clarify hook reads its current mode from `<config-dir>/clarify-mode.json` on every invocation, where `<config-dir>` is `$CLAUDE_CONFIG_DIR` if set, else `~/.claude`. Modes:

- **`on`** — the default. Hook fires on every prompt that isn't a slash-command invocation, sub-5-char filler, or pure acknowledgement.
- **`off`** — never fire. Hook exits silently. Use when you want to rip through known work without interruption. The skill remains installed and the wrapper still works (so `/clarify on` re-enables it).

Legacy mode names from earlier versions (`strict`, `light`, `default`) are accepted at read time and treated as `on` for backward compatibility — no behavioral difference vs. the merged mode.

The hook always skips:
- Prompts under 5 characters
- Pure acknowledgements: `hello, hi, hey, yo, thanks, thank you, thx, ty, ok, okay, k, kk, yes, yep, yeah, yup, no, nope, nah, cool, nice, lol, alright, all right, sounds good, got it, understood, done, perfect`. Trailing punctuation is stripped before matching, so `ok!` and `thanks.` also skip.
- Any prompt starting with `/peer` or `/peer-*` (handled by peer-route hook + /peer SKILL.md's own rule). Case-insensitive.
- Any prompt starting with `/clarify` or `/clarify-*` (the skill owns those turns). Case-insensitive.

### What the hook injects

> **CLARIFY (active).** Before committing to a response, plan, or recommendation, check whether what you're about to say rests on assumptions you can't verify from the user's words. Evaluate the response you're about to produce — not the prompt's tone, length, or casual phrasing.
>
> **Rule.** If your planned response would include a recommendation, plan, advice, prescription, diagnosis, choice between options, or "next steps," AND any user-unique fact (situation, goal, history, constraints, preferences) could materially change it, your FIRST action MUST be `AskUserQuestion`. Answering first and acknowledging the missing context afterward is a protocol violation, not a graceful answer.
>
> **Two-step gate:**
> 1. **Resolve it yourself?** Use available tools — read files, search the web, check existing context, run any tool that might fill the gap. If a tool could answer, use the tool instead of asking. If no tool could plausibly resolve the missing info (personal context, user intent, situation-specific facts), go to step 2.
> 2. **Ask the user** via `AskUserQuestion` (not chat text). Cover only what the user uniquely knows: situation, history, intent, preferences, constraints, stakes, hard-to-reverse choices. State a default per question ("If unspecified, I will assume X") so they can one-click approve. Ask only the minimum; avoid intrusive details that don't directly affect the answer. **Keep each question and each option as short as possible without losing meaning** — one short sentence per question, short labels per option, full detail only when meaning genuinely requires it.
>
> Applies to every kind of request — code, advice, decisions, plans, recommendations, analysis, writing help, anything. Skip for trivial reversible details and genuinely unambiguous prompts. Goal: ask only when you cannot give a complete, grounded answer without input the user alone can provide.

### How "on/off" works for this skill

Claude Code skills don't have a global on/off — they auto-activate when their description matches user intent. What's actually toggleable is the **hook**, which is just code that reads the mode file every prompt. So `/clarify off` writes `{"mode": "off"}` to the mode file; the hook reads it and exits silently. The skill stays installed; the wrapper still responds; only the always-on injection layer is dormant. To re-enable: `/clarify on`.

### Slash command grammar

- `/clarify` — show current mode + version + brief help. Same as `/clarify status`.
- `/clarify status` — read-only display of current mode + version.
- `/clarify on` — enable (or re-enable) the hook.
- `/clarify off` — disable. Persistent until re-enabled (NOT per-session).
- `/clarify help` — display the help block (see "Help output" below).

When the user invokes any of the above, this skill is responsible for:
1. Resolving config dir: `$CLAUDE_CONFIG_DIR` if set, else `~/.claude`.
2. For `on`/`off`: writing `{"mode": "<chosen>"}` to `<config-dir>/clarify-mode.json` (overwrite atomically). Confirming in chat with one short line: `Clarify mode: <new-mode>`.
3. For `status` or empty: print the version banner first (`clarify-skill v<VERSION>`, read from `<skill-dir>/VERSION` — the same directory this SKILL.md lives in; fallback `unknown` if file missing or unreadable), then one line summarizing the current mode + a one-line hint at the other modes. Do NOT modify the file.
4. For `help`: print the Help output block below verbatim.
5. NEVER re-inject the rule into your own current turn — the user is configuring, not working on a task.

If anything fails (file write, parse error), surface the specific error and suggest the manual fix. Do not fail silently.

### Help output

When `/clarify help` (or `/clarify-help`) is invoked, render the help using markdown formatting (headers, bold, lists, inline code) — NOT a fenced code block. Use this exact content and structure verbatim:

---

## `/clarify` — always-on clarification rule for Claude Code

**What it does**

Runs a UserPromptSubmit hook on every prompt and (for non-trivial prompts) injects a rule that tells Claude to evaluate whether its planned response would rest on assumptions it can't verify. If yes, Claude is directed to **either** use available tools (file read, web search, etc.) to resolve the gap **or** ask the user via the `AskUserQuestion` tool. Domain-neutral — applies to code, advice, decisions, plans, anything.

**Commands**

- `/clarify` — show current mode (alias for `/clarify status`)
- `/clarify status` — show current mode
- `/clarify on` — enable the rule (default state)
- `/clarify off` — disable; hook stays installed but exits silently
- `/clarify help` — show this help (also: `/clarify-help`)

**What gets skipped automatically** (regardless of mode)

- Prompts starting with `/peer` or `/peer-*` (peer has its own rule)
- Prompts starting with `/clarify` or `/clarify-*`
- Prompts under 5 characters
- Pure acknowledgements (`thanks`, `ok`, `hello`, `sure`, etc.)

**Files**

- **Hook:** `~/.claude/hooks/clarify-route.py`
- **Skill:** `~/.claude/skills/clarify/SKILL.md`
- **Wrapper:** `~/.claude/commands/clarify.md` (and `clarify-help.md`)
- **Mode state:** `~/.claude/clarify-mode.json` (or `$CLAUDE_CONFIG_DIR` equivalent)

**Independence**

`/clarify` is fully independent of `/peer`. Either can be installed without the other. When both are installed, `/peer` prompts skip the clarify hook so `/peer`'s own integrated rule handles them.

**Failure mode**

The hook fails **OPEN** — any error path exits 0 with no output, so your prompt always reaches Claude. If the hook seems silently inactive, check that `~/.claude/settings.json` registers the hook under `hooks.UserPromptSubmit`.

---

Do not wrap the help in triple backticks. The code/file paths should be inline `code` only.

### Mode file format

`<config-dir>/clarify-mode.json`:

```json
{"mode": "on"}
```

Valid `mode` values: `on`, `off`. Legacy values (`strict`, `light`, `default`) are accepted and treated as `on`. Any other value falls back to `on`. If the file does not exist, the hook treats the mode as `on`.

### Why this is independent of /peer

`/peer` has its own clarification rule baked into `~/.claude/skills/peer/SKILL.md` (the "Clarifying questions" subsection). That rule is specialized — it references `## Caller Constraints / User Decisions`, REVISE markers, and worker-mode handoff specifics that don't generalize.

The clarify hook explicitly skips `/peer*` prompts. This means:
- `/peer` works exactly as before, with its own integrated rule.
- `/clarify` covers everything that isn't `/peer`.
- Either skill works alone — neither depends on the other.
- Together: full coverage with no double-injection.

### Failure mode

The hook fails OPEN: any error path exits 0 with no output, so the user's prompt always reaches Claude. Better to lose the rule injection on a rare parse error than to silently swallow a user message. The hook also strips a leading UTF-8 BOM before JSON parsing to avoid silent loss on BOM-prefixed stdin.

### Triple-guard architecture (intentional)

`/clarify` invocations are guarded by three independent layers:
1. **`disable-model-invocation: true`** in this SKILL.md frontmatter — prevents the model from auto-invoking the skill on prompts that aren't actual `/clarify` slash commands.
2. **`commands/clarify.md`** wrapper — the explicit entry point that catches user-typed `/clarify ...`.
3. **`clarify-route.py`** self-skip regex — the hook explicitly does not inject on `/clarify*` prompts so the configuration command isn't itself wrapped in the clarify rule.

Each guard solves a different problem; do not "simplify" by removing one. Removing #1 would let the model invoke /clarify mid-task on coding prompts; removing #2 would break the slash command; removing #3 would inject the clarify rule on top of a mode-config command, which is nonsensical.

---

## Bootstrap (on first invocation in a session)

If `<config-dir>/clarify-mode.json` does not exist, treat the mode as `on` and offer to create the file with that value. This is opportunistic — not required for the hook to work (it falls back to `on` automatically).

If the hook is not registered in `<config-dir>/settings.json` under `hooks.UserPromptSubmit`, surface this to the user and offer to register it. Required hook entry on Windows:

```json
{
  "matcher": "",
  "hooks": [
    {
      "type": "command",
      "command": "py -3 \"C:\\Users\\Parker's PC\\.claude\\hooks\\clarify-route.py\""
    }
  ]
}
```

On macOS/Linux, substitute `python3` for `py -3` and use the POSIX path:

```json
{
  "matcher": "",
  "hooks": [
    {
      "type": "command",
      "command": "python3 ~/.claude/hooks/clarify-route.py"
    }
  ]
}
```

---

## Uninstall

To remove /clarify cleanly:
1. Delete the `clarify-route.py` entry from `<config-dir>/settings.json` under `hooks.UserPromptSubmit`.
2. `rm <config-dir>/hooks/clarify-route.py`
3. `rm -rf <config-dir>/skills/clarify/ <config-dir>/commands/clarify.md <config-dir>/commands/clarify-help.md`
4. `rm <config-dir>/clarify-mode.json`

The /peer skill is unaffected.
