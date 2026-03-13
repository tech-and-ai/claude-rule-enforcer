"""
Claude Rule Enforcer — CLI entry point.

Usage:
    cre init                    Auto-configure hooks in ~/.claude/settings.json
    cre status                  Show gate status, rule counts, env vars
    cre dashboard               Start web dashboard on :8766
    cre test "ssh root@prod"    Test a command against rules
    cre gate                    Hook entry point (reads stdin, outputs decision)
    cre scan                    Scan conversation history, suggest rules
    cre scan --refine           Also analyse enforcement history for refinements
    cre import CLAUDE.md        Import rules as suggestions (review in dashboard)
    cre import --direct FILE    Import directly to rules.json (bypass review)
    cre memory                  View CRE.md enforcement memory
    cre memory clear            Clear enforcement memory
    cre memory stats            Show memory stats
    cre rules list              Show all rules
    cre rules add --block "pattern" --reason "why"
    cre rules add --review "pattern" --context "what to check"
    cre enable / cre disable    Toggle gate
"""

import argparse
import json
import sys

from . import __version__


def cmd_init(args):
    """Auto-configure hooks in ~/.claude/settings.json."""
    import os

    settings_path = os.path.expanduser("~/.claude/settings.json")
    hook_command = "cre gate"

    # Build the hooks config
    cre_hooks = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": hook_command}]
            },
            {
                "matcher": "Write|Edit",
                "hooks": [{"type": "command", "command": hook_command}]
            },
            {
                "matcher": "WebSearch|WebFetch|Agent|ToolSearch",
                "hooks": [{"type": "command", "command": hook_command}]
            }
        ]
    }

    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                settings = json.load(f)
        except Exception:
            settings = {}
    else:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        settings = {}

    existing_hooks = settings.get("hooks", {})
    existing_pre = existing_hooks.get("PreToolUse", [])

    # Remove any existing CRE hooks
    cleaned = [h for h in existing_pre if "cre gate" not in json.dumps(h)
               and "policy-gate" not in json.dumps(h)
               and "policy_gate" not in json.dumps(h)]

    # Add new CRE hooks
    cleaned.extend(cre_hooks["PreToolUse"])

    existing_hooks["PreToolUse"] = cleaned
    settings["hooks"] = existing_hooks

    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")

    print(f"Hooks configured in {settings_path}")
    print(f"  Bash → cre gate")
    print(f"  Write|Edit → cre gate")
    print(f"  WebSearch|WebFetch|Agent|ToolSearch → cre gate")

    # Also enable the gate
    from . import config
    config.enable()
    print(f"Gate enabled ({config.CRE_ENABLED_PATH})")


def cmd_status(args):
    """Show gate status, rule counts, env vars."""
    from . import config

    rules = config.load_rules()
    enabled = config.is_enabled()

    print(f"Claude Rule Enforcer v{__version__}")
    print(f"")
    print(f"Gate:           {'ENABLED' if enabled else 'DISABLED'}")

    if rules:
        print(f"LLM Review:     {'ON' if rules.get('llm_review_enabled', True) else 'OFF'}")
        print(f"Alignment:      {'ON' if rules.get('alignment_check_enabled', True) else 'OFF'}")
        print(f"Self-Learning:  {'ON' if rules.get('self_learning', False) else 'OFF'}")
        print(f"")
        print(f"Rules:")
        print(f"  always_block:     {len(rules.get('always_block', []))}")
        print(f"  always_allow:     {len(rules.get('always_allow', []))}")
        print(f"  needs_llm_review: {len(rules.get('needs_llm_review', []))}")
        print(f"  learned_rules:    {len(rules.get('learned_rules', []))}")
        print(f"  preferences:      {len(rules.get('preferences', []))}")
        print(f"  suggestions:      {len(rules.get('suggested_rules', []))}")
    else:
        print(f"Rules:          NOT LOADED (check CRE_RULES_PATH)")

    # CRE.md memory stats
    from .gate import get_cre_md_stats
    stats = get_cre_md_stats()
    print(f"")
    print(f"Memory (CRE.md):")
    if stats["exists"]:
        print(f"  Entries:  {stats['entries']}")
        print(f"  Size:     {stats['size']} bytes")
        print(f"  Path:     {stats['path']}")
    else:
        print(f"  Not created yet ({stats['path']})")

    # Knowledge base stats
    from .knowledge import load_kb
    kb = load_kb()
    kb_patterns = kb.get("context_patterns", [])
    print(f"")
    print(f"Knowledge Base:")
    print(f"  Patterns:    {len(kb_patterns)}")
    if kb.get("last_synced"):
        print(f"  Last synced: {kb['last_synced'][:19]}")
    else:
        print(f"  Last synced: never")
    print(f"  Path:        {config.KB_PATH}")

    print(f"")
    print(f"Config:")
    env = config.get_env_display()
    for k, v in env.items():
        val = v["value"] if v["is_set"] else f"{v['default']} (default)"
        print(f"  {k}: {val}")

    print(f"")
    print(f"Rules file: {config.RULES_PATH}")
    print(f"Log file:   {config.LOG_PATH}")
    print(f"Toggle:     {config.CRE_ENABLED_PATH}")


def cmd_dashboard(args):
    """Start the web dashboard."""
    from .dashboard import main as dashboard_main
    dashboard_main(port=args.port)


def cmd_test(args):
    """Test a command against rules."""
    from . import config
    from .gate import regex_check

    rules = config.load_rules()
    if not rules:
        print("ERROR: Could not load rules.json")
        sys.exit(1)

    command = args.test_command
    decision, reason = regex_check(command, rules)

    colors = {"allow": "\033[32m", "deny": "\033[31m", "review": "\033[33m"}
    reset = "\033[0m"
    color = colors.get(decision, "")

    print(f"{color}{decision.upper()}{reset} — {reason}")
    if decision == "review":
        print(f"  (Would escalate to Layer 2 LLM review in live mode)")


def cmd_gate(args):
    """Hook entry point — reads stdin JSON, outputs decision."""
    from .gate import gate_main
    adapter_name = getattr(args, 'format', None)
    gate_main(adapter_name=adapter_name)


def cmd_enable(args):
    """Enable the gate."""
    from . import config
    config.enable()
    print(f"Gate ENABLED ({config.CRE_ENABLED_PATH})")


def cmd_disable(args):
    """Disable the gate."""
    from . import config
    config.disable()
    print(f"Gate DISABLED (removed {config.CRE_ENABLED_PATH})")


def cmd_scan(args):
    """Scan conversation history, suggest rules."""
    from .learner import run_scan

    hours = args.hours if hasattr(args, 'hours') and args.hours else None
    refine = getattr(args, 'refine', False)

    label = f"(last {hours}h)" if hours else "(all)"
    if refine:
        label += " + refinement analysis"
    print(f"Scanning conversation history {label}...")

    num_messages, num_suggestions = run_scan(hours=hours, refine=refine)

    print(f"Processed: {num_messages} messages")
    print(f"New suggestions: {num_suggestions}")

    if num_suggestions > 0:
        print(f"\nRun 'cre dashboard' to review suggestions.")


def cmd_import(args):
    """Import rules from instruction files (CLAUDE.md, agent.md, etc.)."""
    from .importer import run_import

    files = args.files
    if not files:
        print("Usage: cre import CLAUDE.md [agent.md] [rules.md]")
        sys.exit(1)

    dry_run = args.dry_run
    direct = getattr(args, 'direct', False)
    num_added, num_found = run_import(files, dry_run=dry_run, direct=direct)

    if not dry_run and num_added > 0:
        if direct:
            print(f"\nRun 'cre rules list' to see all rules.")
        else:
            print(f"\nRun 'cre dashboard' to review and approve suggestions.")


def cmd_memory(args):
    """View/manage CRE.md enforcement memory."""
    from .gate import get_cre_md_stats, clear_cre_md, _load_cre_context
    from . import config

    action = getattr(args, 'memory_action', None)

    if action == "clear":
        ok = clear_cre_md()
        if ok:
            print(f"CRE.md cleared ({config.CRE_MD_PATH})")
        else:
            print(f"ERROR: Could not clear CRE.md")
        return

    if action == "stats":
        stats = get_cre_md_stats()
        if stats["exists"]:
            print(f"Path:    {stats['path']}")
            print(f"Size:    {stats['size']} bytes")
            print(f"Entries: {stats['entries']}")
        else:
            print(f"CRE.md does not exist yet ({stats['path']})")
        return

    # Default: show content
    content = _load_cre_context()
    if not content:
        stats = get_cre_md_stats()
        print(f"CRE.md is empty or doesn't exist yet ({stats['path']})")
        print(f"It will be populated automatically as L2 makes decisions.")
        return

    print(content)


def cmd_kb(args):
    """Manage knowledge base."""
    from .knowledge import load_kb, save_kb, match_context, match_context_for_tool, sync_from_servers_md, KB_PATH

    action = getattr(args, 'kb_action', None)

    if action == "list":
        kb = load_kb()
        patterns = kb.get("context_patterns", [])
        if not patterns:
            print("Knowledge base is empty.")
            return
        print(f"Knowledge base ({len(patterns)} patterns):\n")
        for i, entry in enumerate(patterns):
            cat = entry.get("category", "unknown")
            src = f" [{entry['source']}]" if entry.get("source") else ""
            print(f"  [{i}] ({cat}{src}) {entry.get('pattern', '')}")
            print(f"       → {entry.get('context', '')}")
            print()
        if kb.get("last_synced"):
            print(f"Last synced: {kb['last_synced']}")

    elif action == "test":
        test_input = args.test_input
        kb = load_kb()
        context = match_context(test_input, kb)
        if context:
            print(f"MATCH — Context injected:\n\n{context}")
        else:
            print(f"NO MATCH — No context for: {test_input}")

    elif action == "sync":
        from . import knowledge
        servers_count = sync_from_servers_md(servers_md_path=getattr(args, 'servers_md', None))
        claude_count = knowledge.sync_from_claude_md()
        memory_count = knowledge.sync_from_memory_md()
        skills_count = knowledge.sync_from_skills()
        prefs_count = knowledge.sync_from_preferences()
        total = servers_count + claude_count + memory_count + skills_count + prefs_count
        print(f"Synced {total} patterns (servers:{servers_count} claude:{claude_count} memory:{memory_count} skills:{skills_count} prefs:{prefs_count})")

    elif action == "add":
        pattern = args.pattern
        context = args.context
        category = getattr(args, 'category', 'workflow') or 'workflow'

        kb = load_kb()
        kb.setdefault("context_patterns", []).append({
            "pattern": pattern,
            "context": context,
            "category": category,
        })
        save_kb(kb)
        print(f"Added KB pattern: {pattern}")
        print(f"  Context: {context}")

    elif action == "remove":
        idx = int(args.index)
        kb = load_kb()
        patterns = kb.get("context_patterns", [])
        if 0 <= idx < len(patterns):
            removed = patterns.pop(idx)
            save_kb(kb)
            print(f"Removed [{idx}]: {removed.get('pattern', '')}")
        else:
            print(f"Invalid index {idx}. Use 'cre kb list' to see indices.")
            sys.exit(1)

    else:
        print("Usage: cre kb list | cre kb test 'command' | cre kb sync | cre kb add 'pattern' 'context' | cre kb remove <id>")


def cmd_sync(args):
    """Sync knowledge base from all sources."""
    import subprocess
    from . import knowledge

    # Handle --status flag
    if getattr(args, 'status', False):
        kb = knowledge.load_kb()
        print(f"Last synced: {kb.get('last_synced', 'Never')}")
        print(f"Total patterns: {len(kb.get('context_patterns', []))}")
        sources = {}
        for p in kb.get('context_patterns', []):
            src = p.get('source', 'manual')
            sources[src] = sources.get(src, 0) + 1
        if sources:
            print("\nPatterns by source:")
            for src, count in sorted(sources.items()):
                print(f"  {src}: {count}")
        try:
            result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
            if result.returncode == 0 and 'cre sync' in result.stdout:
                print("\nCron: Installed (daily at 3am)")
            else:
                print("\nCron: Not installed")
        except Exception:
            print("\nCron: Unable to check")
        return

    # Handle --install-cron flag
    if getattr(args, 'install_cron', False):
        cron_entry = "0 3 * * * $(which cre) sync >> /tmp/cre_sync.log 2>&1"
        try:
            result = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
            current = result.stdout if result.returncode == 0 else ""
        except Exception:
            current = ""
        if 'cre sync' in current:
            print("Cron entry already exists")
        else:
            new_cron = current.rstrip() + "\n" + cron_entry + "\n"
            try:
                subprocess.run(['crontab', '-'], input=new_cron, text=True, check=True)
                print(f"Cron installed: {cron_entry}")
            except Exception as e:
                print(f"Error installing cron: {e}")
        return

    print("Syncing knowledge base...")

    servers_count = knowledge.sync_from_servers_md(
        getattr(args, 'servers_md', None)
    )
    print(f"  servers.md:  {servers_count} patterns")

    claude_count = knowledge.sync_from_claude_md(
        getattr(args, 'claude_md', None)
    )
    print(f"  CLAUDE.md:   {claude_count} patterns")

    memory_count = knowledge.sync_from_memory_md(
        getattr(args, 'memory_md', None)
    )
    print(f"  MEMORY.md:   {memory_count} patterns")

    skills_count = knowledge.sync_from_skills(
        getattr(args, 'skills_dir', None)
    )
    print(f"  Skills:      {skills_count} patterns")

    prefs_count = knowledge.sync_from_preferences()
    print(f"  Preferences: {prefs_count} patterns")

    total = servers_count + claude_count + memory_count + skills_count + prefs_count
    print(f"\nTotal: {total} patterns synced")


def cmd_rules(args):
    """Manage rules."""
    from . import config

    if args.rules_action == "list":
        rules = config.load_rules()
        if not rules:
            print("ERROR: Could not load rules.json")
            sys.exit(1)

        for category in ["always_block", "always_allow", "needs_llm_review"]:
            items = rules.get(category, [])
            print(f"\n{category} ({len(items)}):")
            for r in items:
                pattern = r.get("pattern", "")
                extra = r.get("reason", r.get("context", ""))
                if extra:
                    print(f"  {pattern}  —  {extra}")
                else:
                    print(f"  {pattern}")

        prefs = rules.get("preferences", [])
        if prefs:
            print(f"\npreferences ({len(prefs)}):")
            for p in prefs:
                print(f"  {p.get('rule', '')}  ({p.get('confidence', '')})")

        suggested = [s for s in rules.get("suggested_rules", []) if s.get("status") == "pending"]
        if suggested:
            print(f"\npending suggestions ({len(suggested)}):")
            for s in suggested:
                source_tag = f" [{s.get('source', '')}]" if s.get('source') else ""
                target_tag = f" → {s.get('target_category', '')}" if s.get('target_category') else ""
                print(f"  [{s.get('id', '?')}]{source_tag}{target_tag} {s.get('proposed_rule', '')}  ({s.get('confidence', '')})")

    elif args.rules_action == "add":
        rules = config.load_rules()
        if not rules:
            print("ERROR: Could not load rules.json")
            sys.exit(1)

        if args.block:
            rule = {"pattern": args.block, "reason": args.reason or "Added via CLI"}
            rules.setdefault("always_block", []).append(rule)
            config.save_rules(rules)
            print(f"Added to always_block: {args.block}")

        elif args.review:
            rule = {"pattern": args.review, "context": args.context or "Added via CLI"}
            rules.setdefault("needs_llm_review", []).append(rule)
            config.save_rules(rules)
            print(f"Added to needs_llm_review: {args.review}")

        elif args.allow:
            rule = {"pattern": args.allow}
            rules.setdefault("always_allow", []).append(rule)
            config.save_rules(rules)
            print(f"Added to always_allow: {args.allow}")

        else:
            print("Specify --block, --review, or --allow with a pattern")
            sys.exit(1)
    else:
        print("Usage: cre rules list | cre rules add --block/--review/--allow 'pattern'")


def main():
    parser = argparse.ArgumentParser(
        prog="cre",
        description="Claude Rule Enforcer — A PA for your AI"
    )
    parser.add_argument("--version", action="version", version=f"cre {__version__}")

    sub = parser.add_subparsers(dest="subcommand")

    # init
    sub.add_parser("init", help="Auto-configure hooks in ~/.claude/settings.json")

    # status
    sub.add_parser("status", help="Show gate status, rule counts, env vars")

    # dashboard
    p_dash = sub.add_parser("dashboard", help="Start web dashboard")
    p_dash.add_argument("--port", type=int, default=8766, help="Port (default: 8766)")

    # test
    p_test = sub.add_parser("test", help="Test a command against rules")
    p_test.add_argument("test_command", help="Command to test")

    # gate (hook entry point)
    p_gate = sub.add_parser("gate", help="Hook entry point (reads stdin JSON)")
    p_gate.add_argument("--format", choices=["claude-code", "generic"],
                        default=None, help="Input format (auto-detect if omitted)")
    p_gate.add_argument("extra", nargs="*", help="(ignored — accepts extra args from hook runners)")

    # enable / disable
    sub.add_parser("enable", help="Enable the gate")
    sub.add_parser("disable", help="Disable the gate")

    # scan
    p_scan = sub.add_parser("scan", help="Scan conversation history, suggest rules")
    p_scan.add_argument("--hours", type=int, default=None, help="Scan last N hours only")
    p_scan.add_argument("--refine", action="store_true", help="Also analyse enforcement history for L1 refinements")

    # import
    p_import = sub.add_parser("import", help="Import rules from CLAUDE.md, agent.md, etc.")
    p_import.add_argument("files", nargs="*", help="Instruction files to parse")
    p_import.add_argument("--dry-run", action="store_true", help="Preview extracted rules without writing")
    p_import.add_argument("--direct", action="store_true", help="Write directly to rules.json (bypass suggestion review)")

    # memory
    p_memory = sub.add_parser("memory", help="View/manage CRE.md enforcement memory")
    p_memory_sub = p_memory.add_subparsers(dest="memory_action")
    p_memory_sub.add_parser("clear", help="Clear enforcement memory")
    p_memory_sub.add_parser("stats", help="Show memory stats")

    # kb (knowledge base)
    p_kb = sub.add_parser("kb", help="Manage knowledge base")
    p_kb_sub = p_kb.add_subparsers(dest="kb_action")
    p_kb_sub.add_parser("list", help="List all KB patterns")
    p_kb_test = p_kb_sub.add_parser("test", help="Test what context would be injected")
    p_kb_test.add_argument("test_input", help="Command or text to test")
    p_kb_sync = p_kb_sub.add_parser("sync", help="Sync from servers.md")
    p_kb_sync.add_argument("--servers-md", help="Path to servers.md (default: ~/.claude/servers.md)")
    p_kb_add = p_kb_sub.add_parser("add", help="Add a KB pattern")
    p_kb_add.add_argument("pattern", help="Regex pattern to match")
    p_kb_add.add_argument("context", help="Context to inject when matched")
    p_kb_add.add_argument("--category", default="workflow", help="Category (server/email/credential/workflow/tool/project)")
    p_kb_remove = p_kb_sub.add_parser("remove", help="Remove a KB pattern by index")
    p_kb_remove.add_argument("index", help="Pattern index (from 'cre kb list')")

    # sync
    p_sync = sub.add_parser("sync", help="Sync KB from servers.md, CLAUDE.md, MEMORY.md, skills")
    p_sync.add_argument("--servers-md", help="Path to servers.md")
    p_sync.add_argument("--claude-md", help="Path to CLAUDE.md")
    p_sync.add_argument("--memory-md", help="Path to MEMORY.md")
    p_sync.add_argument("--skills-dir", help="Path to skills directory")
    p_sync.add_argument("--install-cron", action="store_true", help="Install daily 3am cron")
    p_sync.add_argument("--status", action="store_true", help="Show sync status")

    # rules
    p_rules = sub.add_parser("rules", help="Manage rules")
    p_rules_sub = p_rules.add_subparsers(dest="rules_action")
    p_rules_sub.add_parser("list", help="List all rules")
    p_rules_add = p_rules_sub.add_parser("add", help="Add a rule")
    p_rules_add.add_argument("--block", help="Pattern to always block")
    p_rules_add.add_argument("--review", help="Pattern to send to LLM review")
    p_rules_add.add_argument("--allow", help="Pattern to always allow")
    p_rules_add.add_argument("--reason", help="Block reason")
    p_rules_add.add_argument("--context", help="Review context")

    args = parser.parse_args()

    if not args.subcommand:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "init": cmd_init,
        "status": cmd_status,
        "dashboard": cmd_dashboard,
        "test": cmd_test,
        "gate": cmd_gate,
        "enable": cmd_enable,
        "disable": cmd_disable,
        "scan": cmd_scan,
        "sync": cmd_sync,
        "import": cmd_import,
        "memory": cmd_memory,
        "kb": cmd_kb,
        "rules": cmd_rules,
    }

    handler = dispatch.get(args.subcommand)
    if handler:
        handler(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
