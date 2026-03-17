# CRE

<p align="center">
  <img src="cre-logo.png" alt="CRE" width="200">
</p>

<p align="center">
  <strong>AI Agent Governance for the Enterprise</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Patent-GB2604445.3-blue.svg" alt="Patent">
  <img src="https://img.shields.io/badge/License-Proprietary-red.svg" alt="License">
</p>

---

Every AI coding agent in your organisation can run arbitrary commands, access production systems, and modify critical infrastructure. The only safeguard is a text prompt the AI can ignore.

**CRE is a mechanical enforcement layer that sits between AI agents and your systems. It blocks dangerous actions before they execute. The AI cannot bypass, disable, or modify it.**

## The Problem

- AI coding agents have unrestricted access to developer workstations
- Prompt-based safety rules are ignored when context windows fill up
- No audit trail of what AI agents do across your teams
- No way to enforce consistent security policies across multiple AI tools
- Compliance teams have no visibility into AI-assisted development

## How CRE Works

```
Developer <-> AI Agent <-> CRE Bridge <-> System
                            |
                     L1: Pattern Engine (<10ms)
                     L2: Intent Verification (2-5s)
                     KB: Organisational Context
                     Audit: Full SQLite Trail
```

**Layer 1** blocks dangerous patterns instantly. Recursive deletes, force pushes, privilege escalation, evasion attempts. Sub-10ms, no network calls, no false negatives on known threats.

**Layer 2** verifies intent against the conversation. "Did the developer actually ask for this?" Catches AI agents acting beyond their instructions.

**Knowledge Base** injects organisational context into every agent. Correct email addresses, server credentials, project conventions. One source of truth, every agent gets it.

**Audit Trail** logs every tool call, every decision, every override. Queryable SQLite database with web dashboard.

## Platform Support

CRE enforces policy across all major AI coding assistants through a single bridge architecture:

| Platform | Integration |
|----------|------------|
| Claude Code | Native hooks |
| Cursor | Native hooks |
| Windsurf | Native hooks |
| GitHub Copilot | Native hooks |
| Codex CLI | Native hooks |
| OpenClaw | Bridge + Plugin |
| Amp (Sourcegraph) | Delegate + Toolbox |
| Augment Code | Native hooks |

One set of rules. One audit trail. Every AI agent governed.

## Enterprise Features

- **On-premise deployment** with no external dependencies
- **Self-learning rules** that promote L2 decisions to L1 patterns automatically
- **PIN override** for authorised bypasses with full audit logging
- **Anti-evasion training** that prevents AI agents from circumventing controls
- **Dashboard** for real-time monitoring of all AI agent activity
- **Fail-closed design** that blocks all actions if the enforcement layer goes down

## Contact

**Website:** [ai-cre.uk](https://ai-cre.uk)
**Email:** cre@data4u.uk
**Patent:** GB2604445.3

CRE is proprietary software. All rights reserved.
To discuss licensing, integration, or a product demonstration, contact us at cre@data4u.uk.
