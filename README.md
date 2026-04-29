# /clarify

A Claude Code skill that makes Claude ask before assuming. Three layers — a UserPromptSubmit hook reminder, a Stop-hook detector that catches violations after the fact, and a custom output style that places the rule at system-prompt position. Together they push Claude to call `AskUserQuestion` *before* producing recommendations on prompts where user-unique context would change the answer.

**The big idea:** ambiguity isn't a property of the prompt's shape (length, keywords, structure) — it's a property of the gap between the prompt and the response Claude is about to give. The injected rule reminds Claude to evaluate the *response* it's about to produce, not the *prompt*'s tone. Casually-worded prompts ("what do you think?", "any thoughts?") often demand personalized advice to answer well, and that's exactly the failure mode this skill exists to prevent.

Domain-neutral by design: works for code, advice, decisions, plans, recommendations, analysis, writing help, personal-life questions, anything.

---

## Why three layers?

Earlier versions had only the UserPromptSubmit hook. Three iterations of progressively stronger rule wording (passive evaluation → self-test → explicit "FIRST action MUST be AskUserQuestion" with protocol-violation framing) all failed the same test: model reads the rule, classifies the prompt as answerable, produces a recommendation anyway. Then admits post-hoc it could name 3+ user-unique facts that would have changed the advice.

Diagnosis: text injection at user-message position is a soft nudge. The model's trained "be helpful, don't burden the user with questions" gradient on advice prompts beats it. So the v2 architecture adds two stronger levers:

1. **Output style** (the heavy hammer) — places the rule at *system-prompt position*, which has substantially stronger weight at generation time.
2. **Stop-hook detector** (the safety net) — runs after each assistant turn, regex-pre-filters the response, classifies confirmed-recommendation-without-AskUserQuestion via a Haiku call, logs the violation and surfaces a warning on your *next* prompt via a marker file the UserPromptSubmit hook reads.

The original UserPromptSubmit hook stays as a redundant nudge.

---

## Table of contents

- [Install](#install)
- [Commands](#commands)
- [How it works](#how-it-works)
- [What gets skipped automatically](#what-gets-skipped-automatically)
- [Independence from /peer](#independence-from-peer)
- [Uninstall](#uninstall)
- [Troubleshooting](#troubleshooting)
- [Design notes](#design-notes)

---

## Install

### Prereqs

- [Claude Code](https://docs.claude.com/en/docs/claude-code) installed and logged in (`claude --version` works).
- Python 3.9+ on PATH.
  - **Windows:** `py -3 --version` after installing Python from [python.org](https://www.python.org/downloads/) with the "py launcher" option checked.
  - **macOS / Linux:** `python3 --version`.

### Steps

1. **Clone the repo.**

   ```bash
   git clone https://github.com/sirbuggington/clarify-skill.git
   cd clarify-skill
   ```

2. **Copy files into Claude Code's config dir.** Default is `~/.claude`; if you've set `$CLAUDE_CONFIG_DIR`, use that.

   **Windows (PowerShell):**

   ```powershell
   $cfg = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { "$env:USERPROFILE\.claude" }
   New-Item -ItemType Directory -Force -Path "$cfg\skills\clarify", "$cfg\commands", "$cfg\hooks", "$cfg\output-styles" | Out-Null
   Copy-Item SKILL.md "$cfg\skills\clarify\SKILL.md"
   Copy-Item commands\clarify.md "$cfg\commands\clarify.md"
   Copy-Item commands\clarify-help.md "$cfg\commands\clarify-help.md"
   Copy-Item hooks\clarify-route.py "$cfg\hooks\clarify-route.py"
   Copy-Item hooks\clarify-stop-check.py "$cfg\hooks\clarify-stop-check.py"
   Copy-Item output-styles\clarify-strict.md "$cfg\output-styles\clarify-strict.md"
   '{"mode": "on"}' | Out-File -Encoding utf8 "$cfg\clarify-mode.json"
   ```

   **macOS / Linux:**

   ```bash
   cfg="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
   mkdir -p "$cfg/skills/clarify" "$cfg/commands" "$cfg/hooks" "$cfg/output-styles"
   cp SKILL.md "$cfg/skills/clarify/SKILL.md"
   cp commands/clarify.md "$cfg/commands/clarify.md"
   cp commands/clarify-help.md "$cfg/commands/clarify-help.md"
   cp hooks/clarify-route.py "$cfg/hooks/clarify-route.py"
   cp hooks/clarify-stop-check.py "$cfg/hooks/clarify-stop-check.py"
   cp output-styles/clarify-strict.md "$cfg/output-styles/clarify-strict.md"
   echo '{"mode": "on"}' > "$cfg/clarify-mode.json"
   ```

3. **Register both hooks in `<config-dir>/settings.json`.** Add entries under `hooks.UserPromptSubmit` AND `hooks.Stop`. If those sections already exist, add alongside — don't replace existing arrays.

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
       ],
       "Stop": [
         {
           "matcher": "",
           "hooks": [
             {
               "type": "command",
               "command": "py -3 \"%USERPROFILE%\\.claude\\hooks\\clarify-stop-check.py\""
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
       ],
       "Stop": [
         {
           "matcher": "",
           "hooks": [
             {
               "type": "command",
               "command": "python3 ~/.claude/hooks/clarify-stop-check.py"
             }
           ]
         }
       ]
     }
   }
   ```

4. **Activate the output style** (the strongest lever). Either:

   - Run `/config` in Claude Code, navigate to **Output style**, select **Clarify Strict**, OR
   - Add `"outputStyle": "Clarify Strict"` to `<config-dir>/settings.json` (case-sensitive — must match the `name` in the frontmatter).

5. **Restart Claude Code** (or open a new chat). Output styles bake into the system prompt at session start; existing chats won't pick up the change.

6. **Verify.**
   - `/clarify status` → shows `Clarify mode: on`.
   - Ask an open-ended advice question. Claude should call `AskUserQuestion` *before* answering.

---

## Commands

| Command | Effect |
|---|---|
| `/clarify` | Show current mode (alias for `/clarify status`). |
| `/clarify status` | Show current mode. |
| `/clarify on` | Enable the rule (default state). |
| `/clarify off` | Disable. UserPromptSubmit hook stays installed but exits silently. Persistent across sessions. The Stop-hook detector and the output style still operate independently. |
| `/clarify help` | Show the help block. Same as `/clarify-help`. |
| `/clarify-help` | Wrapper alias for `/clarify help`. |

To deactivate the output style entirely: `/config` → Output style → Default → new chat.

---

## How it works

Three pieces, three jobs:

### 1. UserPromptSubmit hook (`hooks/clarify-route.py`)

Runs on every prompt. Decides whether to inject the rule into Claude's context. Skips:

- Prompts starting with `/peer` or `/peer-*` (case-insensitive) — /peer has its own integrated rule.
- Prompts starting with `/clarify` or `/clarify-*` — the skill owns those turns.
- Mode = `off`.
- Prompts under 5 characters.
- Pure acknowledgements: `hello, hi, hey, yo, thanks, thank you, thx, ty, ok, okay, k, kk, yes, yep, yeah, yup, no, nope, nah, cool, nice, lol, alright, all right, sounds good, got it, understood, done, perfect`.

For everything else, injects this rule:

> **CLARIFY (active).** Before committing to a response, plan, or recommendation, check whether what you're about to say rests on assumptions you can't verify from what the user wrote. Evaluate the response you are about to produce, not the prompt's tone, length, or casual phrasing.
>
> **Behavioral rule.** If your planned response would include a recommendation, plan, advice, prescription, diagnosis, choice between options, or "next steps," AND any user-unique fact (situation, goal, history, constraints, preferences) could materially change that response, your FIRST action MUST be `AskUserQuestion`. Answering first and acknowledging the missing context afterward is a protocol violation, not a graceful answer.
>
> **Two-step gate:**
> 1. **Can you resolve it yourself?** Use available tools — read files, search the web, check existing context, run any tool that might fill the gap. If a tool could answer, use the tool instead of asking. If no available tool could plausibly resolve the missing info (personal context, user intent, situation-specific facts), skip directly to step 2.
> 2. **Otherwise →** the user is the unique source. Use the `AskUserQuestion` tool (not chat text) to ask. Cover only what the user uniquely knows: their situation, history, intent, preferences, constraints, stakes, or hard-to-reverse choices. State a default per question ("If unspecified, I will assume X") so they can one-click approve. Ask only the minimum needed; avoid intrusive details that don't directly affect the answer. **Keep each question and each option as short as possible without losing meaning — one short sentence per question, short labels per option, full detail only when meaning genuinely requires it.**
>
> This applies to every kind of request — code, advice, decisions, plans, recommendations, analysis, writing help, anything. Skip for trivial reversible details and prompts that are genuinely unambiguous. Goal: ask only when you cannot give a complete, grounded answer without input the user alone can provide.

The hook also reads a violation marker file (`<config-dir>/.clarify-violation-pending`) if present, prepends a `[CLARIFY VIOLATION DETECTED — prior turn]` warning to the rule, and consumes the marker. The marker is written by the Stop-hook detector below.

A small diagnostic logger appends one line per invocation to `<config-dir>/clarify-debug.log`. Useful for verifying the hook fires; safe to delete the file at any time.

### 2. Stop-hook detector (`hooks/clarify-stop-check.py`)

Runs after each assistant turn finishes. Reads the transcript, finds the most recent user prompt + every assistant text and tool use since. If `AskUserQuestion` was called → exits silently (no violation).

Otherwise: regex pre-filter on the assistant text. If recommendation-shaped patterns hit ("I recommend," "I'd consider," "What I'd actually do," numbered/bulleted advice lists, soft-critique-then-advice phrases, "want me to dig into" continuations), invokes a Haiku classifier via `claude -p` to confirm whether the response is a recommendation that depended on user-unique context. On confirmed YES:

- Appends to `<config-dir>/clarify-violations.log` (timestamp + session id + prompt/reply snippets).
- Writes `<config-dir>/.clarify-violation-pending` marker. The next UserPromptSubmit hook fire reads it, surfaces a warning, and consumes it.

Failure mode: fail-open. Any error path exits 0 silently. The detector is warn-only — it never blocks the model's output.

The Haiku classifier shells out to the existing `claude` CLI, so it uses your existing Claude Code OAuth — no API key configuration needed. Each classifier call is small (~$0.001 of Haiku tokens).

### 3. Output style (`output-styles/clarify-strict.md`)

The strongest lever. Output styles modify Claude Code's system prompt directly — substantially stronger weight at generation time than UserPromptSubmit `additionalContext`, which arrives as a system-reminder block.

Frontmatter:

- `name: Clarify Strict` — display name and selection key.
- `keep-coding-instructions: true` — preserves Claude Code's default coding-related system-prompt content. Setting this to `false` would strip those, which you don't want.

Body content: a tighter restatement of the same behavioral rule (FIRST action MUST be AskUserQuestion when recommendation-shaped + user-unique facts would change it), restructured for system-prompt placement.

Activate via `/config` → Output style → Clarify Strict, or by setting `"outputStyle": "Clarify Strict"` in `settings.json`. **Restart Claude Code (or open a new chat)** for it to take effect — output styles bake into the system prompt at session start.

### `commands/clarify.md` and `commands/clarify-help.md`

Slash-command wrappers. Route `/clarify` and `/clarify-help` to the skill.

---

## What gets skipped automatically

Listed under [How it works](#1-userpromptsubmit-hook-hooksclarify-routepy). The skip list is conservative — it only skips clearly trivial inputs. If you find /clarify firing on something genuinely trivial, open an issue with the prompt that triggered it.

---

## Independence from /peer

Fully independent of [/peer](https://github.com/sirbuggington/peer-skill). Either can be installed without the other.

When both are installed, `/peer*` prompts skip the clarify hook (case-insensitive). /peer has its own integrated clarification rule specialized for its iteration protocol — double-injection would just be noise.

If you uninstall /peer, /clarify still works on every other prompt unchanged. If you uninstall /clarify, /peer still has its own internal rule.

---

## Uninstall

1. Delete the `clarify-route.py` and `clarify-stop-check.py` entries from `<config-dir>/settings.json` under `hooks.UserPromptSubmit` and `hooks.Stop`. Leave any `peer-route.py` or other entries in place.

2. Remove `"outputStyle": "Clarify Strict"` from `<config-dir>/settings.json` if you set it. Or via `/config` → Output style → Default.

3. Delete the files:

   **Windows:**
   ```powershell
   $cfg = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { "$env:USERPROFILE\.claude" }
   Remove-Item "$cfg\hooks\clarify-route.py", "$cfg\hooks\clarify-stop-check.py"
   Remove-Item -Recurse "$cfg\skills\clarify"
   Remove-Item "$cfg\commands\clarify.md", "$cfg\commands\clarify-help.md"
   Remove-Item "$cfg\output-styles\clarify-strict.md"
   Remove-Item "$cfg\clarify-mode.json", "$cfg\clarify-debug.log", "$cfg\clarify-violations.log", "$cfg\.clarify-violation-pending" -ErrorAction SilentlyContinue
   ```

   **macOS / Linux:**
   ```bash
   cfg="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
   rm -f "$cfg/hooks/clarify-route.py" "$cfg/hooks/clarify-stop-check.py"
   rm -rf "$cfg/skills/clarify"
   rm -f "$cfg/commands/clarify.md" "$cfg/commands/clarify-help.md"
   rm -f "$cfg/output-styles/clarify-strict.md"
   rm -f "$cfg/clarify-mode.json" "$cfg/clarify-debug.log" "$cfg/clarify-violations.log" "$cfg/.clarify-violation-pending"
   ```

4. Restart Claude Code.

The /peer skill (if installed) is unaffected.

---

## Troubleshooting

**The UserPromptSubmit hook doesn't seem to fire.**

- Check `<config-dir>/clarify-debug.log`. If a non-trivial prompt isn't producing a `decision=inject` line, the hook isn't reaching Claude Code. Re-check step 3 of [Install](#install).
- Run `/clarify status`. If it errors, the wrapper isn't installed. Re-check step 2.
- Test directly: `echo '{"prompt":"please implement caching"}' | py -3 ~/.claude/hooks/clarify-route.py` (Windows) or `python3 ~/.claude/hooks/clarify-route.py` (macOS/Linux). You should see JSON on stdout containing `"CLARIFY (active)"`.

**Claude still answers without asking, even with the rule visible.**

- Confirm the output style is active. `/config` → Output style — should show **Clarify Strict** as selected. If it's still on Default, the strongest lever isn't engaged.
- Confirm you're in a chat that started *after* the output style was selected. Existing chats use the system prompt from when they started.
- Check `clarify-violations.log`. If the Stop-hook detector caught the violation, the next prompt should show a `[CLARIFY VIOLATION DETECTED]` banner. If neither happens, check the regex coverage — the detector's pre-filter may be missing the recommendation pattern. PR welcome.

**Claude asks too many questions on coding work.**

- The Behavioral rule fires on prompts where user-unique context would change the response. Routine coding tasks ("add a print statement," "rename this variable") shouldn't trigger it. If they do consistently, the output style is misfiring — switch back to Default or open an issue with the prompt.
- For a one-shot bypass without changing settings, prefix with a clarifying assertion: *"No need to ask — just do X with assumption Y."*

**The Stop-hook detector flags too many false positives.**

- Open `clarify-violations.log` and look at the flagged turns. If the regex is matching descriptive prose (e.g., "the team **recommends** X" in a doc summary), the regex needs narrowing.
- Disable temporarily: comment out the Stop entry in `settings.json` under `hooks.Stop`.

**The Stop-hook detector misses real violations.**

- Open `clarify-stop-debug.log`. Look for `regex-clean (no violation)` on a turn that was actually a violation — that means the regex pre-filter missed. Add a pattern.
- The patterns are heuristics, not exhaustive. False negatives are expected for novel phrasings.

**Conflict with /peer.**

- There shouldn't be one. The clarify hooks explicitly skip `/peer*` prompts. If you see clarify rule injected on a `/peer-light` prompt, the regex isn't matching — likely leading whitespace. Open an issue.

**Mode file in the wrong place.**

- The hook reads from `$CLAUDE_CONFIG_DIR/clarify-mode.json` if set, else `~/.claude/clarify-mode.json`. If you've relocated Claude Code's config but the mode file is still at `~/.claude/`, move it.

---

## Design notes

- **Why three layers, not one.** A single text-rule injection at user-message position is a soft nudge that the model can rationalize past on advice prompts. Three iterations of progressively stronger imperative wording all failed the same test. The output style at system-prompt position is the actual lever; the UserPromptSubmit hook is a redundant nudge; the Stop-hook detector is a safety net for cases where both fail. Belt, suspenders, and a third backup.

- **No heuristics on prompt content for the rule itself.** Ambiguity isn't a property of the prompt's shape; only Claude can judge the gap between prompt and planned response. The injected rule explicitly tells Claude to evaluate the *response* it's about to produce, not the *prompt*'s tone.

- **The Stop-hook detector *does* use heuristics**, because that's downstream measurement, not the rule itself. The regex pre-filter exists only as a cost optimization — the LLM classifier is the authoritative gate. False positives are fine (classifier rejects them); false negatives are the failure mode the heuristics lean against.

- **`AskUserQuestion` over chat text.** Asking via the structured tool gives the user a clean per-question UI with one-click answers and a default per question. The brevity rule on question/option text is enforced because long deliberation menus are worse UX than no menu at all.

- **Self-research-first.** Step 1 of the gate ("can you resolve it yourself?") is the difference between "Claude asks intrusive questions" and "Claude pauses and thinks." Without it, Claude defaults to asking even when a file read or search would suffice.

- **Two modes (`on` / `off`), not three.** `strict` / `light` / `off` was the v1 design but `strict` and `light` differed only by injection text strength with no measurable behavioral difference. v2 cuts to `on` / `off`. The actual strength dial is now whether the output style is active.

- **Fail-open hooks.** Any error path in either hook exits 0 silently. Better to lose the rule injection on a rare parse error than to swallow a user message or block the model's output.

- **Why the violation marker is global, not session-scoped.** Marker file at `<config-dir>/.clarify-violation-pending` fires on the next UserPromptSubmit in *any* chat, not just the chat that produced the violation. Trade-off: occasionally surfaces a warning in a chat that didn't produce the violation. In practice, the next prompt is almost always in the same chat anyway, and the trade is worth it for the simple file-based mechanism.

---

## License

MIT — see [LICENSE](LICENSE).
