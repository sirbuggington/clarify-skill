# /clarify

A Claude Code skill that reminds Claude to ask before assuming. On every prompt, a hook injects a small rule into Claude's context that tells it to evaluate whether its planned response would rest on assumptions it can't verify — and if so, either resolve the gap with available tools (file read, web search, etc.) or use the `AskUserQuestion` tool to ask you.

**The big idea:** ambiguity isn't a property of the prompt's shape (length, keywords, structure) — it's a property of the gap between the prompt and the response Claude is about to give. Only Claude has the full context to judge that gap. The hook's job is to remind Claude of the rule on every turn; Claude's job is to apply it. No regex heuristics, no brittle keyword matching — Claude makes the call.

Domain-neutral by design: works for code, advice, decisions, plans, recommendations, analysis, writing help, personal-life questions, anything.

---

## Table of contents

- [Why](#why)
- [Install](#install)
- [Commands](#commands)
- [How it works](#how-it-works)
- [What gets skipped automatically](#what-gets-skipped-automatically)
- [Independence from /peer](#independence-from-peer)
- [Uninstall](#uninstall)
- [Troubleshooting](#troubleshooting)
- [Design notes](#design-notes)

---

## Why

Without /clarify, Claude tends to silently make assumptions when given an under-specified task. You ask "fix the bug" and Claude picks a bug, picks an interpretation of "fix," and proceeds — sometimes correctly, sometimes not. When wrong, you get rework.

With /clarify, Claude is reminded on every non-trivial prompt to first check: *"Do I actually have what I need to give a complete, grounded answer?"* If yes, proceed. If no, either resolve via tools (`Read`, `Grep`, `WebSearch`, etc.) or ask via the structured `AskUserQuestion` UI. You see only the questions that matter — nothing trickled across turns, nothing about trivial reversible details.

Example. Prompt: *"my girlfriend is really mad at me for coming home late what do i do?"* Without /clarify, Claude answers based on whatever assumptions feel reasonable. With /clarify, Claude recognizes that no tool can answer "how late was 'late' relative to what you said?", "is this recurring?", "what outcome do you want?" — and asks those via `AskUserQuestion` before responding. The advice that follows is grounded in your actual situation instead of generic relationship-advice patterns.

---

## Install

### Prereqs

- [Claude Code](https://docs.claude.com/en/docs/claude-code) installed and logged in (`claude --version` works).
- Python 3.9+ on PATH.
  - **Windows:** `py -3 --version` should work after installing Python from [python.org](https://www.python.org/downloads/) with the "py launcher" option checked.
  - **macOS / Linux:** `python3 --version` should work. macOS 13+ ships with 3.9; otherwise `brew install python@3.11` or your distro's package manager.

### Steps

1. **Clone or download this repo.**

   ```bash
   git clone https://github.com/sirbuggington/clarify-skill.git
   cd clarify-skill
   ```

2. **Copy the files into Claude Code's config dir.** Default config dir is `~/.claude`; if you've set `$CLAUDE_CONFIG_DIR`, use that instead.

   **Windows (PowerShell):**

   ```powershell
   $cfg = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { "$env:USERPROFILE\.claude" }
   New-Item -ItemType Directory -Force -Path "$cfg\skills\clarify", "$cfg\commands", "$cfg\hooks" | Out-Null
   Copy-Item SKILL.md "$cfg\skills\clarify\SKILL.md"
   Copy-Item commands\clarify.md "$cfg\commands\clarify.md"
   Copy-Item commands\clarify-help.md "$cfg\commands\clarify-help.md"
   Copy-Item hooks\clarify-route.py "$cfg\hooks\clarify-route.py"
   '{"mode": "on"}' | Out-File -Encoding utf8 "$cfg\clarify-mode.json"
   ```

   **macOS / Linux:**

   ```bash
   cfg="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
   mkdir -p "$cfg/skills/clarify" "$cfg/commands" "$cfg/hooks"
   cp SKILL.md "$cfg/skills/clarify/SKILL.md"
   cp commands/clarify.md "$cfg/commands/clarify.md"
   cp commands/clarify-help.md "$cfg/commands/clarify-help.md"
   cp hooks/clarify-route.py "$cfg/hooks/clarify-route.py"
   echo '{"mode": "on"}' > "$cfg/clarify-mode.json"
   ```

3. **Register the hook in `<config-dir>/settings.json`.** Add an entry under `hooks.UserPromptSubmit`. If the section already exists (e.g. you have other UserPromptSubmit hooks like /peer), add this entry alongside them — do NOT replace the array.

   **Windows:**

   ```json
   {
     "hooks": {
       "UserPromptSubmit": [
         {
           "matcher": "",
           "hooks": [
             {
               "type": "command",
               "command": "py -3 \"%USERPROFILE%\\.claude\\hooks\\clarify-route.py\""
             }
           ]
         }
       ]
     }
   }
   ```

   **macOS / Linux:**

   ```json
   {
     "hooks": {
       "UserPromptSubmit": [
         {
           "matcher": "",
           "hooks": [
             {
               "type": "command",
               "command": "python3 ~/.claude/hooks/clarify-route.py"
             }
           ]
         }
       ]
     }
   }
   ```

4. **Restart Claude Code** (or open a new session) so the hook registration takes effect.

5. **Verify.** Type `/clarify status`. You should see something like `Clarify mode: on`. Then type a non-trivial prompt — Claude's response should reflect the rule (either it asks via `AskUserQuestion`, resolves via tools, or proceeds with confidence based on what you provided).

---

## Commands

| Command | Effect |
|---|---|
| `/clarify` | Show current mode (alias for `/clarify status`). |
| `/clarify status` | Show current mode. |
| `/clarify on` | Enable the rule (default state). |
| `/clarify off` | Disable. Hook stays installed but exits silently. Persistent across sessions. |
| `/clarify help` | Show the help block. Same as `/clarify-help`. |
| `/clarify-help` | Wrapper alias for `/clarify help`. |

`/clarify off` is useful when you want to rip through known mechanical work without interruption — say, applying a batch of small refactors you've already planned. Re-enable with `/clarify on` when you're back to ambiguous tasks.

---

## How it works

There are three pieces, each doing one job:

1. **`hooks/clarify-route.py`** is a UserPromptSubmit hook. Claude Code runs it on every prompt you submit, piping a JSON payload `{"prompt": "<your text>", ...}` on stdin. The hook decides whether to inject the rule.

2. **`skills/clarify/SKILL.md`** is the skill definition. It documents the modes, the slash-command grammar, and the on/off architecture. Claude reads it when you invoke `/clarify <subcommand>` to know how to respond.

3. **`commands/clarify.md`** and **`commands/clarify-help.md`** are slash-command wrappers. They route `/clarify` and `/clarify-help` to the skill.

The hook's decision tree is simple. It skips:

- Prompts starting with `/peer` or `/peer-*` (case-insensitive) — /peer has its own integrated rule.
- Prompts starting with `/clarify` or `/clarify-*` (case-insensitive) — the skill owns those turns.
- Mode = `off`.
- Prompts under 5 characters.
- Pure acknowledgements: `hello, hi, hey, yo, thanks, thank you, thx, ty, ok, okay, k, kk, yes, yep, yeah, yup, no, nope, nah, cool, nice, lol, alright, all right, sounds good, got it, understood, done, perfect`. Trailing punctuation stripped before matching.

For everything else, the hook injects this rule into Claude's context for the turn:

> **CLARIFY (active).** Before committing to a response, plan, or recommendation, check whether what you're about to say rests on assumptions you can't verify from what the user wrote.
>
> **Two-step gate:**
> 1. **Can you resolve it yourself?** Use available tools — read files, search the web, check existing context, run any tool that might fill the gap. If a tool could answer, use the tool instead of asking. If no available tool could plausibly resolve the missing info (personal context, user intent, situation-specific facts), skip directly to step 2.
> 2. **Otherwise →** the user is the unique source. Use the `AskUserQuestion` tool (not chat text) to ask. Cover only what the user uniquely knows: their situation, history, intent, preferences, constraints, stakes, or hard-to-reverse choices. State a default per question ("If unspecified, I will assume X") so they can one-click approve. Ask only the minimum needed; avoid intrusive details that don't directly affect the answer.
>
> This applies to every kind of request — code, advice, decisions, plans, recommendations, analysis, writing help, anything. Skip for trivial reversible details and prompts that are genuinely unambiguous. Goal: ask only when you cannot give a complete, grounded answer without input the user alone can provide.

Claude reads this rule on every non-trivial turn and applies it to the response it's about to give.

---

## What gets skipped automatically

Listed above. The skip list is conservative — it only skips clearly trivial inputs. If you find /clarify firing on something genuinely trivial that should be skipped, open an issue with the prompt that triggered it.

---

## Independence from /peer

This skill is fully independent of [/peer](https://github.com/sirbuggington/peer-skill). Either can be installed without the other.

When both are installed, `/peer*` prompts skip the clarify hook (the regex is case-insensitive: `/peer`, `/Peer`, `/PEER`, `/peer-light`, etc. all skip). /peer has its own integrated clarification rule that's specialized for its iteration protocol — it references REVISE markers, Caller Constraints, and worker-mode handoff. Double-injection would just be noise, so the clarify hook stays out of /peer's lane.

If you uninstall /peer, /clarify still works on every other prompt unchanged. If you uninstall /clarify, /peer still has its own internal rule. Each skill's removal procedure is independent.

---

## Uninstall

1. Delete the `clarify-route.py` entry from `<config-dir>/settings.json` under `hooks.UserPromptSubmit`. Leave any `peer-route.py` or other entries in place.
2. Delete the files:

   **Windows:**
   ```powershell
   $cfg = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { "$env:USERPROFILE\.claude" }
   Remove-Item "$cfg\hooks\clarify-route.py"
   Remove-Item -Recurse "$cfg\skills\clarify"
   Remove-Item "$cfg\commands\clarify.md", "$cfg\commands\clarify-help.md"
   Remove-Item "$cfg\clarify-mode.json"
   ```

   **macOS / Linux:**
   ```bash
   cfg="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
   rm "$cfg/hooks/clarify-route.py"
   rm -rf "$cfg/skills/clarify"
   rm "$cfg/commands/clarify.md" "$cfg/commands/clarify-help.md"
   rm "$cfg/clarify-mode.json"
   ```

3. Restart Claude Code.

The /peer skill (if installed) is unaffected.

---

## Troubleshooting

**The hook doesn't seem to fire.**

- Run `/clarify status`. If it errors or shows nothing, the wrapper isn't installed correctly. Re-check step 2 of [Install](#install).
- Check `<config-dir>/settings.json` has the hook registered under `hooks.UserPromptSubmit`. The `command` value must point at the actual `clarify-route.py` file.
- Type a clearly non-trivial prompt (e.g. "implement a feature that adds caching to the API"). Claude should either ask via `AskUserQuestion` or proceed with explicit acknowledgement of the rule. If neither happens, the hook isn't reaching Claude.
- Test the hook directly: `echo '{"prompt":"please implement caching"}' | py -3 ~/.claude/hooks/clarify-route.py` (Windows: `py -3` ; macOS/Linux: `python3`). You should see a JSON object on stdout containing `"CLARIFY (active)"`.

**Claude asks too many questions.**

- The rule says "ask only when you cannot give a complete, grounded answer without input the user alone can provide." If Claude is over-asking, that's a model-judgment issue, not a hook issue. Push back in chat: *"That was clear from the prompt — please proceed."* Claude will adjust.
- If you want to disable the rule entirely for a session, run `/clarify off`.

**Claude doesn't ask when it should.**

- The rule fires only on non-trivial prompts. If your prompt was very short (under 5 chars) or matched the acknowledgement skip list, the rule didn't reach Claude. Reword.
- The rule is a reminder, not an enforcement. Claude can still choose to proceed without asking if it judges (incorrectly) that it has enough context. If this happens often on your workflow, you can manually invoke `AskUserQuestion`-style prompts: *"What questions do you have before you start?"*

**Conflict with /peer.**

- There shouldn't be one. /clarify hook explicitly skips `/peer*` prompts. If you see clarify rule injected on a `/peer-light` prompt, the regex isn't matching — likely because of leading whitespace or some unusual prefix. Open an issue with the exact prompt.

**Mode file in the wrong place.**

- The hook reads from `$CLAUDE_CONFIG_DIR/clarify-mode.json` if `CLAUDE_CONFIG_DIR` is set, else `~/.claude/clarify-mode.json`. If you've relocated Claude Code's config dir but the mode file is still at `~/.claude/`, move it.

---

## Design notes

A few decisions worth surfacing for anyone considering forking or modifying:

- **No heuristics on prompt content.** An earlier version had regexes for "task verbs" and "question prefixes" to pre-judge ambiguity. It missed exactly the cases it was supposed to catch (short imperatives like "fix the bug") and over-fired on chat (`thanks, that worked great...`). The fundamental issue: ambiguity is not a property of the prompt's shape. Only Claude can judge it. The current design accepts that and just reminds Claude to do the judging.

- **`AskUserQuestion` over chat text.** Asking via the structured tool gives the user a clean per-question UI with one-click answers and a default per question. Posting a numbered list in chat and waiting for free-form text was the v1 approach; it produced messy back-and-forth.

- **Self-research-first.** Step 1 of the gate ("can you resolve it yourself?") is the difference between "/clarify makes Claude ask intrusive questions" and "/clarify makes Claude pause and think." Without it, Claude defaults to asking even when reading a file or running a search would suffice. With it, the user only sees questions when they're the unique source.

- **Two modes (`on` / `off`), not three.** A previous version had `strict` / `light` / `off` — but `strict` and `light` differed only by injection text strength, with no measurable behavioral difference. Cutting to `on` / `off` is honest about what the skill actually does. If a real "extra strict" mode is needed later, it should be a different mechanism (e.g. always force at least one question), not just stronger wording.

- **Fail-open hook.** Any error path in `clarify-route.py` exits 0 with no output. Better to lose the rule injection on a rare parse error than to silently swallow your prompt.

---

## License

MIT — see [LICENSE](LICENSE).
