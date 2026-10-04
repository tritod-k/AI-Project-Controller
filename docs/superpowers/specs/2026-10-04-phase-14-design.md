# Phase 14 — Claude Adapter design

The supplied Phase 14 implementation brief is the controlling specification. This
provider-only addition keeps AgentAdapter, canonical models, Controller, Evidence,
Reviewer, Gate, Version and Recovery unchanged. No Gemini, selection or consensus.

## Runtime evidence and conservative capability

PyPI currently offers 0.2.163 without a Windows wheel. The inspected Windows
package is claude-agent-sdk 0.2.159, bundled CLI 2.1.281, Python 3.13.0. Pin that
reviewed package as an optional extra. Native Windows lacks the official Bash
sandbox; failIfUnavailable must be true. Live execution is therefore blocked on
this host, without an unsandboxed fallback or automatic login.

The official transport advertises approval_events=false until an actual supported
runtime interception/denial compatibility probe is available. It denies tools in
both PreToolUse and can_use_tool. The same-session approval bridge is implemented
and tested with a verified synthetic transport, not represented as live proof.
No configuration boolean can turn the official transport's capability on.

## Provider boundary

ClaudeAdapter binds canonical tasks to the trusted exact managed workspace resolver,
disjoint primary/state roots, repository/workspace identity and a task/config digest.
An atomic bounded journal records session/dispatch/permission/interrupt intents,
acknowledgements and terminal projections. Ambiguous interrupted dispatch, approval
or interrupt requires human inspection. No destructive recovery or blind resend.
An observer owns the active stream. wait returns a nonterminal raw waiting envelope
when a live callback is pending; Controller obtains a canonical ApprovalRequest
through PendingApprovalSource. Router alone supplies a decision. The callback is
resolved in the same live session after authority and input revalidation.

## Permission and sandbox

allowed_tools=[], permission_mode=default, setting_sources=[], strict_mcp_config=true,
mcp_servers={}, add_dirs=[], no plugins/skills/subagents/web tools. A small explicit
tool surface prevents arbitrary tool expansion. Sandbox enabled=true,
failIfUnavailable=true, autoAllowBashIfSandboxed=false, allowUnsandboxedCommands=false,
no shell network domains or socket grants. File tools are outside the Bash sandbox:
the bridge validates exact paths and rejects traversal/links/protected roots; shell
authority remains explicit and unknown semantic effects are not guessed safe.

PreToolUse correlates session/cwd/tool_use_id/name/input and forces ASK only for a
verified transport. can_use_tool requires that exact hook observation, records a
canonical request and awaits one future. Missing/mismatched/parallel/unknown calls
fail closed. Decisions never install permission updates. Identical resolved replay
is harmless; conflicting replay fails. Cancellation invalidates pending requests.
Approval crash windows retain digest-only intent/ack facts; fresh processes cannot
recreate Python permission futures or resume deferred tools. Production never defers.

## Lifecycle and normalization

The public ClaudeSDKClient is owned by one runner task for connect/query/receive/
disconnect, avoiding cross-task AnyIO cancel-scope cleanup. interrupt uses its public
method. Interrupt dispatch does not itself prove cancellation. Terminal_reason must
explicitly confirm interruption; unknown error subtypes remain operational failures.
Fresh-client follow-up is explicit only after a verified successful terminal turn,
with exact original session/task/attempt/workspace ownership. Repairs start new sessions.

Raw projections omit thinking, prompts and credential fields. Terminal session,
subtype, public text, matched tool-use/results, usage/cost/duration/model facts pass
through ClaudeNormalizer and canonical validation. Missing tests remain None;
tool observations are claims, not authenticated command receipts. Raw references
remain external. Actual Git/test evidence and the existing Gate retain authority.

## Validation and handoff

Offline fake transport, pinned SDK wire configuration, settings bypass, permission
and sandbox regressions precede shared adapter/Controller/restart tests. Live smoke
requires AIPC_CLAUDE_LIVE=1 and external disposable fixtures; missing sandbox/auth
is BLOCKED, never PASS. Full 1445-test baseline and component suites must remain
green. Retain receipts, one fresh whole-branch technical review and one fix pass.
Final state AWAITING_REVIEW/PENDING; one attempt commit; no merge or PASS tag.
