---
name: clarify-help
description: /clarify help — display the /clarify command cheat sheet. Read-only; no rule injection (the hook self-skips on /clarify* prompts). Equivalent to typing /clarify help.
---

This is a wrapper that delegates to the /clarify skill's help section.

When invoked with /clarify-help, treat the invocation as if the user typed
/clarify help and respond per the "Help output" section in
~/.claude/skills/clarify/SKILL.md.

If the main /clarify skill is not installed at ~/.claude/skills/clarify/, abort with:

    /clarify-help wrapper requires the clarify skill at ~/.claude/skills/clarify/.
