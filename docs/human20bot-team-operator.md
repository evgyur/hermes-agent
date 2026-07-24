# Human20Bot team-operator contract

Status: P04 draft. This document is not an activation instruction and does not modify the live profile.

## Capability contract

Authorized members use the existing `human20team` Hermes profile with its complete capability surface. Membership changes admission only; it does not select a reduced member tier, custom router, second scheduler, or alternate memory system.

The sealed baseline capability-config SHA-256 is:

`82f73b50867a7e2ccf5b20f002c43cbec26f397fa3db28dc6b081e79bfe8317c`

Required first-class tool families:

- terminal and code execution
- file read, search, patch and write
- web and browser discovery
- cron and goal continuation
- delegation
- persistent memory and session search
- media generation and delivery
- messaging and Telegram wrappers
- deferred service wrappers through tool discovery

The staged overlay must not contain `tools`, `toolsets`, `plugins`, `mcp_servers`, `providers`, model credentials, or any tool allow/deny list. Those fields remain inherited unchanged from the profile baseline.

## Admission and session boundaries

- Team admission is resolved from current membership in the approved authority supergroup.
- A static Telegram allowlist is not authoritative for team access.
- DM sessions remain sender-scoped.
- Group and topic sessions use the configured per-user session mode.
- Profile, chat, topic and actor boundaries must not collapse into another member's session key.
- Revocation cancels only the removed actor's active personal continuation and pending work. Other members' work remains intact.

## Memory boundary

Shared project memory and explicit project SSOT artifacts are team resources. Raw DM turns, sender-scoped session history, internal lifecycle text and private identifiers are not project memory and must not be copied into shared artifacts.

## Team artifact ownership

Cron and Goal artifacts explicitly created as team work are owned by the team profile rather than the member who initiated the request. They survive that member leaving the authority group. A removed actor cannot list, mutate, run, or steer them because membership denial happens before agent or tool execution.

Personal continuations remain actor-scoped and stop on revocation. Revocation does not delete shared project artifacts and does not cancel another authorized member's session.

## Protected effects

Full tools do not waive approval boundaries. The following effects remain review-gated:

- payments and financial transfers
- access grants or membership changes
- production code, config, service or routing mutations
- mass, channel, public, or external sends

The profile overlay must not weaken existing approval policy, add blanket auto-approval, or encode credentials that could bypass these boundaries.

## Quiet Telegram behavior

- Internal tool progress, self-review, interruption, compaction and lifecycle scaffolds are not standalone Telegram replies.
- One scoped inbound Telegram update executes and produces a final delivery at most once.
- Explicit multi-message tools remain the only supported path for intentional multiple separate deliveries.

## Staged overlay policy

Allowed overlay fields are limited to the evidence-backed Telegram membership policy, approved authority chat binding, per-user group/topic session settings, and quiet-progress defaults.

The overlay generator must:

1. read the existing profile config without modifying it;
2. read the consumed private approval receipt;
3. emit only the allowed overlay keys;
4. redact credentials and private transport values;
5. fail on unknown keys, missing approval bindings, secrets, or output symlinks;
6. write a deterministic manifest with source and output hashes;
7. leave the live config and service untouched.

## Activation boundary

P04 only stages and verifies candidate artifacts. Production activation requires the later production approvals, manifest binding, rollback approval, preflight, service restart, and redacted live smoke checks defined by the SuperGoal contract.
