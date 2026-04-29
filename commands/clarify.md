---
name: clarify
description: /clarify — manage the always-on clarification rule. Switches the hook on/off or shows current status. The hook reminds Claude to evaluate whether its response rests on unverifiable assumptions and to use the AskUserQuestion tool when the user is the unique source. Equivalent to typing /clarify <subcommand>. Read-only when called with no args or `status`.
argument-hint: [on | off | status | help]
---

This is a wrapper that delegates to the main /clarify skill.

When invoked with /clarify <args>, treat the invocation as if the user typed
/clarify <args> and follow the rules in ~/.claude/skills/clarify/SKILL.md exactly.
Same mode-file format, same slash-command grammar, same fail-open behavior.

If the main /clarify skill is not installed at ~/.claude/skills/clarify/, abort with:

    /clarify wrapper requires the clarify skill at ~/.claude/skills/clarify/.
    The skill ships with the hook at ~/.claude/hooks/clarify-route.py and the
    mode file at ~/.claude/clarify-mode.json.
