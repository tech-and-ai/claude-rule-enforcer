"""
Claude Rule Enforcer — Configuration Dashboard

Web UI for managing rules, viewing logs, testing commands, and reviewing suggestions.
Uses stdlib only (http.server, json, os, re) — zero dependencies beyond Python 3.

Usage:
    cre dashboard              # Start on port 8766
    cre dashboard --port 8800  # Custom port
"""

import json
import os
import re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

from . import config

# --- HTML Template ---
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Claude Rule Enforcer</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #0a0e1a; color: #c8ccd4; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; }
a { color: #d4a843; }

.header { background: linear-gradient(135deg, #0f1425, #1a1f35); border-bottom: 1px solid #d4a84333; padding: 10px 20px; display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.header h1 { font-size: 18px; color: #d4a843; font-weight: 600; white-space: nowrap; }
.header .ver { font-size: 12px; color: #6b7280; margin-left: 4px; }
.header .status { display: flex; gap: 8px; align-items: center; }
.header .toggles { display: flex; gap: 6px; margin-left: auto; align-items: center; flex-wrap: wrap; }
.toggle-item { display: flex; align-items: center; gap: 4px; }
.toggle-item .action-label { font-size: 11px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.3px; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.dot.on { background: #4ade80; box-shadow: 0 0 6px #4ade8066; }
.dot.off { background: #ef4444; box-shadow: 0 0 6px #ef444466; }
.badge { padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 500; }
.badge.on { background: #4ade8022; color: #4ade80; border: 1px solid #4ade8044; }
.badge.off { background: #ef444422; color: #ef4444; border: 1px solid #ef444444; }

.container { max-width: 1200px; margin: 0 auto; padding: 14px; }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
.full { grid-column: 1 / -1; }

.card { background: #111627; border: 1px solid #1e2640; border-radius: 8px; overflow: hidden; }
.card-head { padding: 10px 12px; border-bottom: 1px solid #1e2640; display: flex; align-items: center; justify-content: space-between; }
.card-head h2 { font-size: 14px; color: #d4a843; font-weight: 600; }
.card-body { padding: 12px; }

.stats-bar { background: #111627; border: 1px solid #1e2640; border-radius: 6px; padding: 8px 14px; margin-bottom: 10px; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.stats { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.stat { display: flex; align-items: baseline; gap: 3px; }
.stat-num { font-size: 16px; font-weight: 700; color: #d4a843; }
.stat-label { font-size: 10px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.3px; }
.stat-sep { color: #1e2640; font-size: 14px; margin: 0 4px; }

.tester-bar { background: #111627; border: 1px solid #1e2640; border-radius: 6px; padding: 6px 10px; margin-bottom: 10px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.tester-bar .tester-label { font-size: 11px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.3px; white-space: nowrap; }
.tester { display: flex; gap: 8px; flex: 1; min-width: 200px; }
.tester input { flex: 1; background: #0a0e1a; border: 1px solid #1e2640; color: #c8ccd4; padding: 5px 10px; border-radius: 4px; font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; font-size: 13px; }
.tester input:focus { outline: none; border-color: #d4a843; }
.test-result { padding: 5px 10px; border-radius: 4px; font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; font-size: 12px; display: none; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
.test-result.allow { background: #4ade8015; border: 1px solid #4ade8033; color: #4ade80; display: inline-block; }
.test-result.deny { background: #ef444415; border: 1px solid #ef444433; color: #ef4444; display: inline-block; }
.test-result.review { background: #f59e0b15; border: 1px solid #f59e0b33; color: #f59e0b; display: inline-block; }

.tabs { display: flex; gap: 0; border-bottom: 1px solid #1e2640; }
.tab { padding: 7px 14px; cursor: pointer; color: #6b7280; font-size: 13px; border-bottom: 2px solid transparent; transition: all 0.2s; }
.tab:hover { color: #c8ccd4; }
.tab.active { color: #d4a843; border-bottom-color: #d4a843; }
.tab-content { display: none; }
.tab-content.active { display: block; }

.rule-row { display: flex; align-items: center; gap: 8px; padding: 4px 0; border-bottom: 1px solid #1e264066; font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; font-size: 13px; }
.rule-row:last-child { border-bottom: none; }
.rule-pattern { color: #7dd3fc; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rule-sep { color: #4b5563; }
.rule-reason { color: #9ca3af; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.rule-del { background: none; border: none; color: #ef4444; cursor: pointer; font-size: 16px; padding: 2px 6px; border-radius: 4px; opacity: 0.5; transition: opacity 0.2s; }
.rule-del:hover { opacity: 1; background: #ef444422; }

.add-row { display: flex; gap: 8px; padding: 8px 0; border-top: 1px solid #1e2640; margin-top: 6px; }
.add-row input { background: #0a0e1a; border: 1px solid #1e2640; color: #c8ccd4; padding: 5px 10px; border-radius: 4px; font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; font-size: 13px; flex: 1; }
.add-row input:focus { outline: none; border-color: #d4a843; }
.add-row input::placeholder { color: #4b5563; }

.btn { padding: 5px 12px; border-radius: 4px; border: none; cursor: pointer; font-size: 13px; font-weight: 500; transition: all 0.2s; }
.btn-gold { background: #d4a843; color: #0a0e1a; }
.btn-gold:hover { background: #e0b85a; }
.btn-sm { padding: 3px 8px; font-size: 11px; }
.btn-outline { background: transparent; border: 1px solid #1e2640; color: #c8ccd4; }
.btn-outline:hover { border-color: #d4a843; color: #d4a843; }
.btn-danger { background: #ef444422; color: #ef4444; border: 1px solid #ef444444; }
.btn-danger:hover { background: #ef444444; }
.btn-success { background: #4ade8022; color: #4ade80; border: 1px solid #4ade8044; }
.btn-success:hover { background: #4ade8044; }

.actions { display: flex; gap: 10px; flex-wrap: wrap; }
.action-item { display: flex; align-items: center; gap: 8px; }
.action-label { font-size: 13px; color: #9ca3af; }

.log-box { background: #050810; border: 1px solid #1e2640; border-radius: 4px; padding: 10px; font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; font-size: 12px; max-height: 450px; overflow-y: auto; line-height: 1.5; white-space: pre-wrap; word-break: break-all; }
.log-allow { color: #4ade80; }
.log-deny { color: #ef4444; }
.log-l2 { color: #f59e0b; }
.log-line { color: #6b7280; }

.lr-table { width: 100%; font-size: 13px; border-collapse: collapse; }
.lr-table th { text-align: left; color: #d4a843; font-weight: 500; padding: 5px 8px; border-bottom: 1px solid #1e2640; }
.lr-table td { padding: 5px 8px; border-bottom: 1px solid #1e264066; }
.lr-table .mono { font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; color: #7dd3fc; }

.env-row { display: flex; gap: 12px; padding: 4px 0; border-bottom: 1px solid #1e264066; font-size: 13px; }
.env-name { color: #7dd3fc; font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; min-width: 180px; }
.env-val { color: #9ca3af; font-family: 'SF Mono', 'Fira Code', 'Consolas', monospace; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.env-set { color: #4ade80; }
.env-default { color: #6b7280; font-style: italic; }

/* Suggestions */
.sug-card { background: #0d1220; border: 1px solid #1e2640; border-radius: 6px; padding: 12px; margin-bottom: 10px; }
.sug-card.approved { border-color: #4ade8044; }
.sug-card.dismissed { opacity: 0.5; border-color: #ef444433; }
.sug-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.sug-id { font-size: 11px; color: #6b7280; font-family: monospace; }
.sug-confidence { font-size: 11px; padding: 2px 8px; border-radius: 8px; font-weight: 500; }
.sug-confidence.high { background: #4ade8022; color: #4ade80; }
.sug-confidence.medium { background: #f59e0b22; color: #f59e0b; }
.sug-confidence.low { background: #6b728022; color: #9ca3af; }
.sug-rule { font-size: 14px; color: #e2e8f0; margin-bottom: 8px; font-weight: 500; }
.sug-interpretation { font-size: 13px; color: #9ca3af; margin-bottom: 8px; font-style: italic; }
.sug-evidence { font-size: 12px; color: #6b7280; font-family: monospace; margin-bottom: 8px; padding: 8px; background: #080c16; border-radius: 4px; }
.sug-evidence div { margin-bottom: 4px; }
.sug-actions { display: flex; gap: 8px; }

/* Preferences */
.pref-row { display: flex; align-items: center; gap: 8px; padding: 5px 0; border-bottom: 1px solid #1e264066; font-size: 13px; }
.pref-rule { flex: 1; color: #e2e8f0; }
.pref-meta { color: #6b7280; font-size: 12px; }

.source-badge { font-size: 10px; padding: 1px 6px; border-radius: 6px; font-weight: 500; margin-left: 4px; }
.source-badge.import { background: #d4a84322; color: #d4a843; border: 1px solid #d4a84344; }
.source-badge.cli { background: #7dd3fc22; color: #7dd3fc; border: 1px solid #7dd3fc44; }
.source-badge.learned { background: #4ade8022; color: #4ade80; border: 1px solid #4ade8044; }

.toast { position: fixed; bottom: 20px; right: 20px; padding: 10px 18px; border-radius: 6px; font-size: 13px; z-index: 1000; transition: opacity 0.3s; }
.toast.success { background: #4ade8022; color: #4ade80; border: 1px solid #4ade8044; }
.toast.error { background: #ef444422; color: #ef4444; border: 1px solid #ef444444; }

::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: #0a0e1a; }
::-webkit-scrollbar-thumb { background: #1e2640; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #2a3352; }
</style>
</head>
<body>

<div class="header">
    <h1>Claude Rule Enforcer <span class="ver">v""" + __import__('cre').__version__ + """</span></h1>
    <div class="status">
        <span class="dot" id="gateDot"></span>
        <span class="badge" id="gateBadge"></span>
    </div>
    <div class="toggles">
        <div class="toggle-item">
            <span class="action-label">Gate</span>
            <button class="btn btn-sm" id="btnGate" onclick="toggleGate()">...</button>
        </div>
        <div class="toggle-item">
            <span class="action-label">LLM</span>
            <button class="btn btn-sm" id="btnLLM" onclick="toggleLLM()">...</button>
        </div>
        <div class="toggle-item">
            <span class="action-label">Align</span>
            <button class="btn btn-sm" id="btnAlign" onclick="toggleAlign()">...</button>
        </div>
        <div class="toggle-item">
            <span class="action-label">Learn</span>
            <button class="btn btn-sm" id="btnLearn" onclick="toggleLearn()">...</button>
        </div>
    </div>
</div>

<div class="container">

<!-- Stats bar -->
<div class="stats-bar">
    <div class="stats" id="stats"></div>
</div>

<!-- Rule Tester bar -->
<div class="tester-bar">
    <span class="tester-label">Test</span>
    <div class="tester">
        <input type="text" id="testCmd" placeholder="Command to test (e.g. ssh root@prod, git push --force)" onkeydown="if(event.key==='Enter')testCmd()">
        <button class="btn btn-sm btn-gold" onclick="testCmd()">Test</button>
    </div>
    <div class="test-result" id="testResult"></div>
</div>

<!-- Main Tabs: Rules / Suggestions / Preferences / Import / Logs / Settings -->
<div class="card full">
    <div class="tabs" id="mainTabs">
        <div class="tab active" onclick="switchMainTab('rules')">Rules</div>
        <div class="tab" onclick="switchMainTab('suggestions')">Suggestions <span id="sugCount" style="font-size:11px;color:#f59e0b"></span></div>
        <div class="tab" onclick="switchMainTab('preferences')">Preferences</div>
        <div class="tab" onclick="switchMainTab('kb')">Knowledge Base <span id="kbCount" style="font-size:11px;color:#22d3ee"></span></div>
        <div class="tab" onclick="switchMainTab('import')">Import</div>
        <div class="tab" onclick="switchMainTab('logs')">Logs</div>
        <div class="tab" onclick="switchMainTab('settings')">Settings</div>
    </div>
    <div class="card-body">
        <!-- Rules sub-tabs -->
        <div class="tab-content active" id="main-rules">
            <div class="tabs" id="ruleTabs" style="margin:-12px -12px 10px -12px; padding:0 12px;">
                <div class="tab active" onclick="switchRuleTab('block')">always_block</div>
                <div class="tab" onclick="switchRuleTab('allow')">always_allow</div>
                <div class="tab" onclick="switchRuleTab('review')">needs_llm_review</div>
                <div class="tab" onclick="switchRuleTab('learned')">learned</div>
            </div>
            <div class="tab-content active" id="tab-block"></div>
            <div class="tab-content" id="tab-allow"></div>
            <div class="tab-content" id="tab-review"></div>
            <div class="tab-content" id="tab-learned" style="overflow-x:auto">
                <div id="learnedBox"><p style="color:#6b7280">No learned rules yet.</p></div>
            </div>
        </div>
        <!-- Suggestions -->
        <div class="tab-content" id="main-suggestions"></div>
        <!-- Preferences -->
        <div class="tab-content" id="main-preferences"></div>
        <!-- Knowledge Base -->
        <div class="tab-content" id="main-kb"></div>
        <!-- Import -->
        <div class="tab-content" id="main-import">
            <p style="color:#9ca3af;margin-bottom:12px">Import enforceable rules from instruction files. Scan your project or paste content manually.</p>
            <div id="importScanBox" style="margin-bottom:16px">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">
                    <button class="btn btn-outline" onclick="scanFiles()">Scan for Instruction Files</button>
                    <span id="scanStatus" style="color:#6b7280;font-size:13px"></span>
                </div>
                <div id="scanResults" style="display:none"></div>
            </div>
            <div style="border-top:1px solid #1e2640;padding-top:14px;margin-bottom:8px">
                <span style="color:#6b7280;font-size:12px;text-transform:uppercase;letter-spacing:0.5px">Or paste manually</span>
            </div>
            <div style="margin-bottom:8px">
                <input type="text" id="importFilename" placeholder="Filename (e.g. CLAUDE.md)" style="background:#0a0e1a;border:1px solid #1e2640;color:#c8ccd4;padding:6px 10px;border-radius:4px;font-size:13px;width:250px;margin-bottom:8px">
            </div>
            <textarea id="importContent" rows="12" placeholder="Paste instruction file content here..." style="width:100%;background:#0a0e1a;border:1px solid #1e2640;color:#c8ccd4;padding:10px;border-radius:4px;font-family:'SF Mono','Fira Code','Consolas',monospace;font-size:13px;resize:vertical"></textarea>
            <div style="display:flex;gap:8px;margin-top:10px">
                <button class="btn btn-gold" onclick="parseImport()" id="btnParse">Parse Rules</button>
                <button class="btn btn-outline" onclick="clearImport()">Clear</button>
                <span id="importStatus" style="color:#6b7280;font-size:13px;line-height:32px;margin-left:8px"></span>
            </div>
            <div id="importPreview" style="display:none;margin-top:16px">
                <h3 style="color:#d4a843;font-size:14px;margin-bottom:10px">Extracted Rules</h3>
                <div id="importResults"></div>
                <div style="display:flex;gap:8px;margin-top:12px">
                    <button class="btn btn-success" onclick="applyImport()">Apply Selected</button>
                    <button class="btn btn-outline" onclick="clearImport()">Discard</button>
                </div>
            </div>
            <div id="importedSummary" style="margin-top:20px;border-top:1px solid #1e2640;padding-top:14px"></div>
        </div>
        <!-- Logs -->
        <div class="tab-content" id="main-logs">
            <div style="display:flex;justify-content:flex-end;margin-bottom:8px">
                <button class="btn btn-sm btn-outline" onclick="loadLogs()">Refresh</button>
            </div>
            <div class="log-box" id="logBox">Loading...</div>
        </div>
        <!-- Settings -->
        <div class="tab-content" id="main-settings">
            <div id="settingsBox"></div>
        </div>
    </div>
</div>

</div>

<div class="toast" id="toast" style="opacity:0"></div>

<script>
let rulesData = {};
let currentRuleTab = 'block';
let currentMainTab = 'rules';

const TAB_MAP = { block: 'always_block', allow: 'always_allow', review: 'needs_llm_review' };

async function api(path, opts) {
    const r = await fetch(path, opts);
    return r.json();
}

function toast(msg, type='success') {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast ' + type;
    t.style.opacity = '1';
    setTimeout(() => t.style.opacity = '0', 2500);
}

let kbData = {};

async function loadAll() {
    const [status, rules, logs, kb] = await Promise.all([
        api('/api/status'), api('/api/rules'), api('/api/logs'), api('/api/kb')
    ]);
    rulesData = rules;
    kbData = kb;
    renderStatus(status);
    renderRules();
    renderLogs(logs);
    renderSettings(status);
    renderLearned();
    renderSuggestions();
    renderPreferences();
    renderKB();
    renderImportedSummary();
}

function renderImportedSummary() {
    const el = document.getElementById('importedSummary');
    if (!el) return;
    const cats = ['always_block','always_allow','needs_llm_review'];
    let imported = [];
    cats.forEach(cat => {
        (rulesData[cat] || []).forEach((r, i) => {
            if (r.source === 'import') imported.push({category: cat, index: i, pattern: r.pattern, reason: r.reason || r.context || ''});
        });
    });
    (rulesData.preferences || []).forEach((p, i) => {
        if (p.source === 'import') imported.push({category: 'preferences', index: i, pattern: '', reason: p.rule || ''});
    });
    if (imported.length === 0) {
        el.innerHTML = '<p style="color:#6b7280;font-size:13px">No imported rules yet.</p>';
        return;
    }
    const colors = {always_block:'#ef4444', needs_llm_review:'#f59e0b', always_allow:'#4ade80', preferences:'#22d3ee'};
    const labels = {always_block:'BLOCK', needs_llm_review:'REVIEW', always_allow:'ALLOW', preferences:'PREF'};
    let html = `<h3 style="color:#d4a843;font-size:14px;margin-bottom:10px">Imported Rules (${imported.length})</h3>`;
    imported.forEach(r => {
        const col = colors[r.category] || '#6b7280';
        const label = labels[r.category] || r.category;
        html += `<div class="rule-row">
            <span style="color:${col};font-size:11px;font-weight:600;min-width:60px">${esc(label)}</span>`;
        if (r.pattern) html += `<span class="rule-pattern" title="${esc(r.pattern)}">/${esc(r.pattern)}/</span>`;
        if (r.reason) html += `<span class="rule-sep">&mdash;</span><span class="rule-reason" title="${esc(r.reason)}">${esc(r.reason)}</span>`;
        html += `<button class="rule-del" title="Delete imported rule" onclick="delImported('${esc(r.category)}',${r.index})">&#x2715;</button>`;
        html += '</div>';
    });
    html += `<div style="margin-top:10px"><button class="btn btn-sm btn-danger" onclick="delAllImported()">Delete All Imported</button></div>`;
    el.innerHTML = html;
}

async function delImported(category, idx) {
    if (!confirm('Delete this imported rule?')) return;
    rulesData[category].splice(idx, 1);
    await saveRules();
    toast('Imported rule deleted');
}

async function delAllImported() {
    if (!confirm('Delete ALL imported rules? This cannot be undone.')) return;
    ['always_block','always_allow','needs_llm_review'].forEach(cat => {
        rulesData[cat] = (rulesData[cat] || []).filter(r => r.source !== 'import');
    });
    rulesData.preferences = (rulesData.preferences || []).filter(p => p.source !== 'import');
    await saveRules();
    toast('All imported rules deleted');
}

function renderStatus(s) {
    const on = s.gate_enabled;
    document.getElementById('gateDot').className = 'dot ' + (on ? 'on' : 'off');
    document.getElementById('gateBadge').className = 'badge ' + (on ? 'on' : 'off');
    document.getElementById('gateBadge').textContent = on ? 'ENABLED' : 'DISABLED';

    document.getElementById('btnGate').textContent = on ? 'Disable' : 'Enable';
    document.getElementById('btnGate').className = 'btn btn-sm ' + (on ? 'btn-danger' : 'btn-success');

    const llm = s.llm_review_enabled;
    document.getElementById('btnLLM').textContent = llm ? 'Disable' : 'Enable';
    document.getElementById('btnLLM').className = 'btn btn-sm ' + (llm ? 'btn-danger' : 'btn-success');

    const align = s.alignment_check_enabled;
    document.getElementById('btnAlign').textContent = align ? 'Disable' : 'Enable';
    document.getElementById('btnAlign').className = 'btn btn-sm ' + (align ? 'btn-danger' : 'btn-success');

    const learn = s.self_learning;
    document.getElementById('btnLearn').textContent = learn ? 'Disable' : 'Enable';
    document.getElementById('btnLearn').className = 'btn btn-sm ' + (learn ? 'btn-danger' : 'btn-success');

    document.getElementById('stats').innerHTML = `
        <div class="stat"><div class="stat-num">${s.rule_counts.always_block}</div><div class="stat-label">Block</div></div><span class="stat-sep">|</span>
        <div class="stat"><div class="stat-num">${s.rule_counts.always_allow}</div><div class="stat-label">Allow</div></div><span class="stat-sep">|</span>
        <div class="stat"><div class="stat-num">${s.rule_counts.needs_llm_review}</div><div class="stat-label">Review</div></div><span class="stat-sep">|</span>
        <div class="stat"><div class="stat-num">${s.rule_counts.learned_rules}</div><div class="stat-label">Learned</div></div><span class="stat-sep">|</span>
        <div class="stat"><div class="stat-num">${s.rule_counts.suggestions || 0}</div><div class="stat-label">Suggested</div></div><span class="stat-sep">|</span>
        <div class="stat"><div class="stat-num">${s.rule_counts.preferences || 0}</div><div class="stat-label">Prefs</div></div><span class="stat-sep">|</span>
        <div class="stat"><div class="stat-num">${s.rule_counts.kb_patterns || 0}</div><div class="stat-label">KB</div></div>
    `;
    const kbCountEl = document.getElementById('kbCount');
    if (kbCountEl) kbCountEl.textContent = s.rule_counts.kb_patterns ? `(${s.rule_counts.kb_patterns})` : '';
}

function renderRules() {
    renderRuleList('block', rulesData.always_block || [], true);
    renderRuleList('allow', rulesData.always_allow || [], false);
    renderRuleList('review', rulesData.needs_llm_review || [], true);
}

function renderRuleList(tab, rules, hasExtra) {
    const el = document.getElementById('tab-' + tab);
    const extraField = tab === 'block' ? 'reason' : 'context';
    const extraPlaceholder = tab === 'block' ? 'Reason...' : 'Context...';
    let html = '';
    rules.forEach((r, i) => {
        const srcBadge = r.source ? `<span class="source-badge ${esc(r.source)}">${esc(r.source)}</span>` : '';
        html += `<div class="rule-row">
            <span class="rule-pattern" title="${esc(r.pattern)}">${esc(r.pattern)}</span>${srcBadge}`;
        if (hasExtra) {
            html += `<span class="rule-sep">&mdash;</span>
                <span class="rule-reason" title="${esc(r[extraField]||'')}">${esc(r[extraField]||'')}</span>`;
        }
        html += `<button class="rule-del" title="Delete rule" onclick="delRule('${tab}',${i})">&#x2715;</button></div>`;
    });
    html += `<div class="add-row">
        <input type="text" id="addPat-${tab}" placeholder="Regex pattern...">`;
    if (hasExtra) {
        html += `<input type="text" id="addExtra-${tab}" placeholder="${extraPlaceholder}">`;
    }
    html += `<button class="btn btn-sm btn-gold" onclick="addRule('${tab}')">Add</button></div>`;
    el.innerHTML = html;
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

// --- Main tab switching ---
function switchMainTab(tab) {
    currentMainTab = tab;
    const allTabs = ['rules','suggestions','preferences','import','logs','settings'];
    document.querySelectorAll('#mainTabs .tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('#mainTabs .tab')[allTabs.indexOf(tab)].classList.add('active');
    allTabs.forEach(t => {
        document.getElementById('main-' + t).classList.toggle('active', t === tab);
    });
}

function switchRuleTab(tab) {
    currentRuleTab = tab;
    const allRuleTabs = ['block','allow','review','learned'];
    document.querySelectorAll('#ruleTabs .tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('#ruleTabs .tab')[allRuleTabs.indexOf(tab)].classList.add('active');
    allRuleTabs.forEach(t => {
        document.getElementById('tab-' + t).classList.toggle('active', t === tab);
    });
}

async function addRule(tab) {
    const key = TAB_MAP[tab];
    const pat = document.getElementById('addPat-' + tab).value.trim();
    if (!pat) return;
    const rule = { pattern: pat };
    if (tab === 'block') rule.reason = (document.getElementById('addExtra-' + tab)?.value || '').trim();
    if (tab === 'review') rule.context = (document.getElementById('addExtra-' + tab)?.value || '').trim();
    rulesData[key] = rulesData[key] || [];
    rulesData[key].push(rule);
    await saveRules();
    toast('Rule added');
}

async function delRule(tab, idx) {
    if (!confirm('Delete this rule?')) return;
    const key = TAB_MAP[tab];
    rulesData[key].splice(idx, 1);
    await saveRules();
    toast('Rule deleted');
}

async function saveRules() {
    await api('/api/rules', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(rulesData) });
    await loadAll();
}

// --- Suggestions ---
const SOURCE_COLORS = {scan:'#3b82f6', import:'#d4a843', l2_promotion:'#4ade80', l2_refinement:'#f97316'};
const TYPE_COLORS = {new_rule:'#6b7280', broaden:'#f59e0b', narrow:'#3b82f6', remove:'#ef4444'};

function renderSuggestions() {
    const sug = rulesData.suggested_rules || [];
    const pending = sug.filter(s => s.status === 'pending');
    const el = document.getElementById('main-suggestions');
    const countEl = document.getElementById('sugCount');
    countEl.textContent = pending.length > 0 ? `(${pending.length})` : '';

    if (sug.length === 0) {
        el.innerHTML = '<p style="color:#6b7280">No suggestions yet. Run <code>cre scan</code> or <code>cre import</code> to generate suggestions.</p>';
        return;
    }

    let html = '';
    sug.forEach((s, i) => {
        const statusClass = s.status === 'approved' ? 'approved' : (s.status === 'dismissed' ? 'dismissed' : '');
        const srcColor = SOURCE_COLORS[s.source] || '#6b7280';
        const typeColor = TYPE_COLORS[s.suggestion_type] || '#6b7280';
        const sugType = s.suggestion_type || 'new_rule';

        html += `<div class="sug-card ${statusClass}">
            <div class="sug-header">
                <span class="sug-id">${esc(s.id || '?')}`;
        // Source badge
        if (s.source) html += ` <span style="font-size:10px;padding:1px 6px;border-radius:6px;background:${srcColor}22;color:${srcColor};border:1px solid ${srcColor}44">${esc(s.source)}</span>`;
        // Type badge
        if (sugType !== 'new_rule') html += ` <span style="font-size:10px;padding:1px 6px;border-radius:6px;background:${typeColor}22;color:${typeColor};border:1px solid ${typeColor}44">${esc(sugType)}</span>`;
        html += `</span>
                <span class="sug-confidence ${(s.confidence||'').toLowerCase()}">${esc(s.confidence || '?')}</span>
            </div>`;

        // Conflict warning
        if (s.conflict_note) {
            html += `<div style="background:#f59e0b15;border:1px solid #f59e0b33;color:#f59e0b;padding:6px 10px;border-radius:4px;font-size:12px;margin-bottom:8px">&#x26A0; ${esc(s.conflict_note)}</div>`;
        }

        html += `<div class="sug-rule">${esc(s.proposed_rule || '')}</div>
            <div class="sug-interpretation">${esc(s.interpretation || '')}</div>`;

        // Show pattern for block/review/allow targets
        if (s.pattern) html += `<div style="font-size:12px;color:#7dd3fc;font-family:monospace;margin-bottom:4px">Pattern: /${esc(s.pattern)}/</div>`;
        // Show old vs new for narrow/broaden
        if (s.old_pattern && sugType !== 'new_rule') html += `<div style="font-size:12px;color:#9ca3af;font-family:monospace;margin-bottom:4px">Old: /${esc(s.old_pattern)}/ &rarr; New: /${esc(s.pattern || s.new_pattern || '?')}/</div>`;

        if (s.evidence && s.evidence.length > 0) {
            html += '<div class="sug-evidence">';
            s.evidence.forEach(e => {
                html += `<div>[${esc((e.timestamp||'').slice(0,19))}] ${esc(e.content || '')}</div>`;
            });
            html += '</div>';
        }

        if (s.status === 'pending') {
            if (sugType === 'remove') {
                html += `<div class="sug-actions">
                    <button class="btn btn-sm btn-danger" onclick="approveSug('${esc(s.id)}','${esc(s.target_category||'')}')">Remove Rule</button>
                    <button class="btn btn-sm btn-outline" onclick="dismissSug('${esc(s.id)}')">Dismiss</button>
                </div>`;
            } else {
                // Target category dropdown
                const tc = s.target_category || 'preference';
                html += `<div class="sug-actions" style="flex-wrap:wrap;gap:6px">
                    <select id="tc-${esc(s.id)}" style="background:#0a0e1a;border:1px solid #1e2640;color:#c8ccd4;padding:4px 8px;border-radius:4px;font-size:12px">
                        <option value="always_block" ${tc==='always_block'?'selected':''}>Block (L1)</option>
                        <option value="needs_llm_review" ${tc==='needs_llm_review'?'selected':''}>Review (L2)</option>
                        <option value="always_allow" ${tc==='always_allow'?'selected':''}>Allow (L1)</option>
                        <option value="preference" ${tc==='preference'?'selected':''}>Preference</option>
                    </select>
                    <button class="btn btn-sm btn-success" onclick="approveSug('${esc(s.id)}')">Approve</button>
                    <button class="btn btn-sm btn-danger" onclick="dismissSug('${esc(s.id)}')">Dismiss</button>
                </div>`;
            }
        } else {
            html += `<div style="font-size:12px;color:#6b7280;margin-top:4px">${esc(s.status)} ${s.approved_at ? 'at ' + s.approved_at.slice(0,19) : (s.dismissed_at ? 'at ' + s.dismissed_at.slice(0,19) : '')}</div>`;
        }

        html += '</div>';
    });
    el.innerHTML = html;
}

async function approveSug(id, forceCategory) {
    const tcEl = document.getElementById('tc-' + id);
    const targetCategory = forceCategory || (tcEl ? tcEl.value : 'preference');
    await api('/api/suggestions/approve', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({id: id, target_category: targetCategory}) });
    const labels = {always_block:'Block rules',needs_llm_review:'Review rules',always_allow:'Allow rules',preference:'Preferences'};
    toast('Approved → ' + (labels[targetCategory] || targetCategory));
    await loadAll();
}

async function dismissSug(id) {
    await api('/api/suggestions/dismiss', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({id: id}) });
    toast('Suggestion dismissed');
    await loadAll();
}

// --- Preferences ---
let editingPrefIdx = -1;

function renderPreferences() {
    const prefs = rulesData.preferences || [];
    const el = document.getElementById('main-preferences');

    let html = '';
    prefs.forEach((p, i) => {
        const srcBadge = p.source ? `<span class="source-badge ${esc(p.source)}">${esc(p.source)}</span>` : '';
        if (editingPrefIdx === i) {
            const conf = p.confidence || 'high';
            html += `<div class="pref-row" style="flex-wrap:wrap;gap:6px">
                <input type="text" id="editPrefVal" value="${esc(p.rule || '')}" style="flex:1;background:#0a0e1a;border:1px solid #d4a843;color:#c8ccd4;padding:5px 10px;border-radius:4px;font-size:13px;min-width:200px">
                <select id="editPrefConf" style="background:#0a0e1a;border:1px solid #1e2640;color:#c8ccd4;padding:5px 8px;border-radius:4px;font-size:12px">
                    <option value="high" ${conf==='high'?'selected':''}>High</option>
                    <option value="medium" ${conf==='medium'?'selected':''}>Medium</option>
                    <option value="low" ${conf==='low'?'selected':''}>Low</option>
                </select>
                <button class="btn btn-sm btn-gold" onclick="savePrefEdit(${i})">Save</button>
                <button class="btn btn-sm btn-outline" onclick="cancelPrefEdit()">Cancel</button>
            </div>`;
        } else {
            html += `<div class="pref-row">
                <span class="pref-rule" ondblclick="startPrefEdit(${i})" title="Double-click to edit" style="cursor:pointer">${esc(p.rule || '')}${srcBadge}</span>
                <span class="pref-meta">${esc(p.confidence || '')} &middot; ${esc(p.source || '')} &middot; ${esc((p.approved_at||'').slice(0,10))}</span>
                <button class="btn btn-sm btn-outline" onclick="startPrefEdit(${i})" title="Edit" style="padding:2px 6px;font-size:12px">&#x270E;</button>
                <button class="rule-del" onclick="delPref(${i})">&#x2715;</button>
            </div>`;
        }
    });
    html += `<div class="add-row">
        <input type="text" id="addPrefRule" placeholder="New preference rule..." style="flex:1">
        <button class="btn btn-sm btn-gold" onclick="addPref()">Add</button>
    </div>
    <div style="border-top:1px solid #1e2640;margin-top:10px;padding-top:10px">
        <span style="color:#6b7280;font-size:12px;text-transform:uppercase;letter-spacing:0.5px">Bulk add (one rule per line)</span>
        <textarea id="bulkPrefRules" rows="5" placeholder="Paste rules here, one per line..." style="width:100%;margin-top:6px;background:#0a0e1a;border:1px solid #1e2640;color:#c8ccd4;padding:8px 10px;border-radius:4px;font-size:13px;resize:vertical"></textarea>
        <button class="btn btn-sm btn-gold" onclick="bulkAddPrefs()" style="margin-top:6px">Add All</button>
    </div>`;
    el.innerHTML = html;
    if (editingPrefIdx >= 0) document.getElementById('editPrefVal')?.focus();
}

function startPrefEdit(idx) {
    editingPrefIdx = idx;
    renderPreferences();
}

function cancelPrefEdit() {
    editingPrefIdx = -1;
    renderPreferences();
}

async function savePrefEdit(idx) {
    const val = document.getElementById('editPrefVal').value.trim();
    if (!val) return;
    rulesData.preferences[idx].rule = val;
    rulesData.preferences[idx].confidence = document.getElementById('editPrefConf').value;
    editingPrefIdx = -1;
    await saveRules();
    toast('Preference updated');
}

async function addPref() {
    const val = document.getElementById('addPrefRule').value.trim();
    if (!val) return;
    rulesData.preferences = rulesData.preferences || [];
    rulesData.preferences.push({rule: val, confidence: 'high', source: 'manual'});
    await saveRules();
    toast('Preference added');
}

async function bulkAddPrefs() {
    const text = document.getElementById('bulkPrefRules').value.trim();
    if (!text) return;
    const lines = text.split('\\n').map(l => l.replace(/^[-*•\\d.]+\\s*/, '').trim()).filter(l => l.length > 0);
    if (lines.length === 0) return;
    rulesData.preferences = rulesData.preferences || [];
    lines.forEach(l => rulesData.preferences.push({rule: l, confidence: 'high', source: 'manual'}));
    await saveRules();
    toast(lines.length + ' preferences added');
}

async function delPref(idx) {
    if (!confirm('Remove this preference?')) return;
    rulesData.preferences.splice(idx, 1);
    await saveRules();
    toast('Preference removed');
}

// --- Knowledge Base ---
let editingKBIdx = -1;

function renderKB() {
    const patterns = (kbData.context_patterns || []);
    const el = document.getElementById('main-kb');
    const cats = {};
    patterns.forEach(p => { const c = p.category || 'other'; cats[c] = (cats[c]||0)+1; });
    const catBadges = Object.entries(cats).map(([c,n]) => `<span class="source-badge" style="background:#1e2640;color:#7dd3fc;margin-right:4px">${esc(c)} (${n})</span>`).join('');

    let html = `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;flex-wrap:wrap;gap:8px">
        <div>${catBadges}</div>
        <div style="display:flex;gap:6px">
            <button class="btn btn-sm btn-outline" onclick="syncKB()">Sync from servers.md</button>
            <button class="btn btn-sm btn-outline" onclick="testKB()">Test</button>
        </div>
    </div>
    <div id="kbTestBox" style="display:none;margin-bottom:12px;padding:10px;background:#0a0e1a;border:1px solid #1e2640;border-radius:4px">
        <div style="display:flex;gap:6px;margin-bottom:8px">
            <input type="text" id="kbTestInput" placeholder="ssh admin@10.0.0.1" style="flex:1;background:#12162a;border:1px solid #1e2640;color:#c8ccd4;padding:5px 10px;border-radius:4px;font-size:13px">
            <button class="btn btn-sm btn-gold" onclick="runKBTest()">Match</button>
        </div>
        <div id="kbTestResult" style="font-size:13px;color:#6b7280"></div>
    </div>`;

    patterns.forEach((p, i) => {
        const catColor = {server:'#4ade80',email:'#f59e0b',credential:'#ef4444',workflow:'#22d3ee',tool:'#a78bfa',project:'#fb923c'}[p.category] || '#6b7280';
        const srcBadge = p.source ? `<span class="source-badge ${esc(p.source)}" style="font-size:10px">${esc(p.source)}</span>` : '';
        if (editingKBIdx === i) {
            html += `<div class="pref-row" style="flex-wrap:wrap;gap:6px">
                <input type="text" id="editKBPattern" value="${esc(p.pattern)}" placeholder="Regex pattern" style="flex:1;min-width:200px;background:#0a0e1a;border:1px solid #d4a843;color:#7dd3fc;padding:5px 10px;border-radius:4px;font-family:monospace;font-size:12px">
                <input type="text" id="editKBContext" value="${esc(p.context)}" placeholder="Context to inject" style="flex:2;min-width:300px;background:#0a0e1a;border:1px solid #d4a843;color:#c8ccd4;padding:5px 10px;border-radius:4px;font-size:12px">
                <select id="editKBCat" style="background:#0a0e1a;border:1px solid #1e2640;color:#c8ccd4;padding:5px 8px;border-radius:4px;font-size:12px">
                    ${['server','email','credential','workflow','tool','project'].map(c => `<option value="${c}" ${p.category===c?'selected':''}>${c}</option>`).join('')}
                </select>
                <button class="btn btn-sm btn-gold" onclick="saveKBEdit(${i})">Save</button>
                <button class="btn btn-sm btn-outline" onclick="cancelKBEdit()">Cancel</button>
            </div>`;
        } else {
            html += `<div class="pref-row" style="align-items:flex-start">
                <span style="color:${catColor};font-size:11px;min-width:70px;text-transform:uppercase">${esc(p.category||'other')}</span>
                <span class="mono" style="font-size:12px;min-width:200px;cursor:pointer" ondblclick="startKBEdit(${i})" title="Double-click to edit">${esc(p.pattern)}</span>
                <span class="pref-rule" style="flex:1;font-size:12px" ondblclick="startKBEdit(${i})">${esc(p.context)} ${srcBadge}</span>
                <button class="btn btn-sm btn-outline" onclick="startKBEdit(${i})" title="Edit" style="padding:2px 6px;font-size:12px">&#x270E;</button>
                <button class="rule-del" onclick="delKB(${i})">&#x2715;</button>
            </div>`;
        }
    });

    html += `<div class="add-row" style="flex-wrap:wrap;gap:6px;margin-top:10px">
        <input type="text" id="addKBPattern" placeholder="Regex pattern..." style="flex:1;min-width:200px">
        <input type="text" id="addKBContext" placeholder="Context to inject..." style="flex:2;min-width:300px">
        <select id="addKBCat" style="background:#0a0e1a;border:1px solid #1e2640;color:#c8ccd4;padding:5px 8px;border-radius:4px;font-size:12px">
            ${['server','email','credential','workflow','tool','project'].map(c => `<option value="${c}">${c}</option>`).join('')}
        </select>
        <button class="btn btn-sm btn-gold" onclick="addKB()">Add</button>
    </div>`;

    if (kbData.last_synced) {
        html += `<div style="margin-top:12px;color:#6b7280;font-size:11px">Last synced: ${kbData.last_synced.slice(0,19)}</div>`;
    }

    el.innerHTML = html;
    if (editingKBIdx >= 0) document.getElementById('editKBPattern')?.focus();
}

function startKBEdit(idx) { editingKBIdx = idx; renderKB(); }
function cancelKBEdit() { editingKBIdx = -1; renderKB(); }

async function saveKBEdit(idx) {
    const pattern = document.getElementById('editKBPattern').value.trim();
    const context = document.getElementById('editKBContext').value.trim();
    const category = document.getElementById('editKBCat').value;
    if (!pattern || !context) return;
    kbData.context_patterns[idx].pattern = pattern;
    kbData.context_patterns[idx].context = context;
    kbData.context_patterns[idx].category = category;
    editingKBIdx = -1;
    await api('/api/kb', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(kbData) });
    toast('KB pattern updated');
    renderKB();
}

async function addKB() {
    const pattern = document.getElementById('addKBPattern').value.trim();
    const context = document.getElementById('addKBContext').value.trim();
    const category = document.getElementById('addKBCat').value;
    if (!pattern || !context) return;
    await api('/api/kb/add', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({pattern, context, category}) });
    kbData = await api('/api/kb');
    toast('KB pattern added');
    renderKB();
}

async function delKB(idx) {
    if (!confirm('Remove this KB pattern?')) return;
    await api('/api/kb/remove', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({index: idx}) });
    kbData = await api('/api/kb');
    toast('KB pattern removed');
    renderKB();
}

async function syncKB() {
    const r = await api('/api/kb/sync', { method: 'POST', headers: {'Content-Type':'application/json'}, body: '{}' });
    kbData = await api('/api/kb');
    toast(`Synced ${r.count} patterns from servers.md`);
    renderKB();
    const status = await api('/api/status');
    renderStatus(status);
}

function testKB() {
    const box = document.getElementById('kbTestBox');
    box.style.display = box.style.display === 'none' ? 'block' : 'none';
    if (box.style.display === 'block') document.getElementById('kbTestInput').focus();
}

async function runKBTest() {
    const input = document.getElementById('kbTestInput').value.trim();
    if (!input) return;
    const r = await api('/api/kb/test', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({input}) });
    const el = document.getElementById('kbTestResult');
    if (r.match) {
        el.innerHTML = `<span style="color:#4ade80">MATCH</span> — ${esc(r.context)}`;
    } else {
        el.innerHTML = `<span style="color:#6b7280">NO MATCH</span> — No context for: ${esc(input)}`;
    }
}

// --- Tester ---
async function testCmd() {
    const cmd = document.getElementById('testCmd').value.trim();
    if (!cmd) return;
    const r = await api('/api/test', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({command: cmd}) });
    const el = document.getElementById('testResult');
    el.className = 'test-result ' + r.decision;
    el.textContent = `${r.decision.toUpperCase()} — ${r.reason}` + (r.matched_pattern ? ` (pattern: ${r.matched_pattern})` : '');
    el.style.display = 'inline-block';
}

// --- Logs ---
async function loadLogs() {
    const logs = await api('/api/logs');
    renderLogs(logs);
}

function renderLogs(logs) {
    const box = document.getElementById('logBox');
    if (!logs.lines || logs.lines.length === 0) {
        box.innerHTML = '<span class="log-line">No log entries found.</span>';
        return;
    }
    box.innerHTML = logs.lines.map(l => {
        let cls = 'log-line';
        if (/ALLOW/i.test(l)) cls = 'log-allow';
        else if (/DENY|BLOCK/i.test(l)) cls = 'log-deny';
        else if (/L2|LLM/i.test(l)) cls = 'log-l2';
        return `<span class="${cls}">${esc(l)}</span>`;
    }).join('\\n');
    box.scrollTop = box.scrollHeight;
}

// --- Settings ---
function renderSettings(s) {
    const envs = s.env_vars || {};
    let html = '';
    for (const [k, v] of Object.entries(envs)) {
        const isSet = v.is_set;
        html += `<div class="env-row">
            <span class="env-name">${esc(k)}</span>
            <span class="env-val ${isSet ? 'env-set' : 'env-default'}">${isSet ? esc(v.value) : esc(v.default) + ' (default)'}</span>
        </div>`;
    }
    document.getElementById('settingsBox').innerHTML = html;
}

// --- Learned ---
function renderLearned() {
    const lr = rulesData.learned_rules || [];
    if (lr.length === 0) {
        document.getElementById('learnedBox').innerHTML = '<p style="color:#6b7280">No learned rules yet.</p>';
        return;
    }
    let html = '<table class="lr-table"><tr><th>Time</th><th>Type</th><th>Pattern</th><th>Context</th><th></th></tr>';
    lr.forEach((r, i) => {
        html += `<tr>
            <td>${esc((r.timestamp||'').slice(0,19))}</td>
            <td>${esc(r.type||'')}</td>
            <td class="mono">${esc(r.pattern||'')}</td>
            <td>${esc(r.context||'')}</td>
            <td><button class="rule-del" onclick="delLearned(${i})">&#x2715;</button></td>
        </tr>`;
    });
    html += '</table>';
    document.getElementById('learnedBox').innerHTML = html;
}

async function delLearned(idx) {
    if (!confirm('Delete this learned rule?')) return;
    rulesData.learned_rules.splice(idx, 1);
    await saveRules();
    toast('Learned rule deleted');
}

// --- Toggles ---
async function toggleGate() {
    await api('/api/toggle', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({target: 'gate'}) });
    toast('Gate toggled');
    await loadAll();
}

async function toggleLLM() {
    rulesData.llm_review_enabled = !rulesData.llm_review_enabled;
    await saveRules();
    toast('LLM review toggled');
}

async function toggleAlign() {
    rulesData.alignment_check_enabled = !rulesData.alignment_check_enabled;
    await saveRules();
    toast('Alignment check toggled');
}

async function toggleLearn() {
    rulesData.self_learning = !rulesData.self_learning;
    await saveRules();
    toast('Self-learning toggled');
}

// --- Import ---
let importedRules = [];

async function scanFiles() {
    document.getElementById('scanStatus').textContent = 'Scanning...';
    try {
        const r = await api('/api/import/scan');
        if (!r.files || r.files.length === 0) {
            document.getElementById('scanStatus').textContent = 'No instruction files found';
            document.getElementById('scanResults').style.display = 'none';
            return;
        }
        document.getElementById('scanStatus').textContent = `Found ${r.files.length} file${r.files.length>1?'s':''}`;
        let html = '';
        r.files.forEach(f => {
            const sizeKB = f.size > 1024 ? (f.size/1024).toFixed(1) + ' KB' : f.size + ' B';
            html += `<div style="display:flex;align-items:center;gap:10px;padding:8px;background:#0d1220;border:1px solid #1e2640;border-radius:4px;margin-bottom:6px">
                <span style="color:#7dd3fc;font-family:monospace;font-size:13px;flex:1">${esc(f.path)}</span>
                <span style="color:#6b7280;font-size:12px">${sizeKB}</span>
                <button class="btn btn-sm btn-gold" onclick="loadScannedFile('${esc(f.path)}')">Load</button>
            </div>`;
        });
        document.getElementById('scanResults').innerHTML = html;
        document.getElementById('scanResults').style.display = 'block';
    } catch(e) {
        document.getElementById('scanStatus').textContent = 'Scan failed: ' + e;
    }
}

async function loadScannedFile(path) {
    try {
        const r = await api('/api/import/read', {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({path: path})
        });
        if (r.error) { toast(r.error, 'error'); return; }
        document.getElementById('importFilename').value = path.split('/').pop();
        document.getElementById('importContent').value = r.content;
        toast('Loaded ' + path.split('/').pop());
    } catch(e) {
        toast('Failed to load: ' + e, 'error');
    }
}

async function parseImport() {
    const content = document.getElementById('importContent').value.trim();
    const filename = document.getElementById('importFilename').value.trim() || 'unknown';
    if (!content) { toast('Paste file content first', 'error'); return; }

    document.getElementById('btnParse').textContent = 'Parsing...';
    document.getElementById('btnParse').disabled = true;
    document.getElementById('importStatus').textContent = 'Sending to LLM for analysis...';

    try {
        const r = await api('/api/import/parse', {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({content: content, filename: filename})
        });

        if (r.error) {
            toast(r.error, 'error');
            document.getElementById('importStatus').textContent = r.error;
            return;
        }

        importedRules = r.rules || [];
        document.getElementById('importStatus').textContent = `Found ${importedRules.length} enforceable rules`;
        renderImportPreview();
    } catch(e) {
        toast('Parse failed: ' + e, 'error');
        document.getElementById('importStatus').textContent = 'Parse failed';
    } finally {
        document.getElementById('btnParse').textContent = 'Parse Rules';
        document.getElementById('btnParse').disabled = false;
    }
}

function renderImportPreview() {
    if (importedRules.length === 0) {
        document.getElementById('importPreview').style.display = 'none';
        return;
    }
    document.getElementById('importPreview').style.display = 'block';
    const colors = {always_block:'#ef4444', needs_llm_review:'#f59e0b', always_allow:'#4ade80', preference:'#22d3ee'};
    let html = '';
    importedRules.forEach((r, i) => {
        const cat = r.category || 'unknown';
        const col = colors[cat] || '#6b7280';
        const checked = r.enforceable !== false ? 'checked' : '';
        html += `<div style="display:flex;align-items:flex-start;gap:8px;padding:8px 0;border-bottom:1px solid #1e264066">
            <input type="checkbox" id="imp-${i}" ${checked} style="margin-top:4px">
            <div style="flex:1">
                <span style="color:${col};font-size:12px;font-weight:500">${esc(cat)}</span>`;
        if (r.pattern) html += ` <span style="color:#7dd3fc;font-family:monospace;font-size:13px">/${esc(r.pattern)}/</span>`;
        if (r.reason) html += ` <span style="color:#9ca3af;font-size:13px">&mdash; ${esc(r.reason)}</span>`;
        if (r.context) html += ` <span style="color:#9ca3af;font-size:13px">&mdash; ${esc(r.context)}</span>`;
        if (r.rule) html += ` <span style="color:#e2e8f0;font-size:13px">${esc(r.rule)}</span>`;
        if (r.source_text) html += `<div style="color:#6b7280;font-size:11px;margin-top:2px">line ${r.source_line || '?'}: "${esc(r.source_text.substring(0,80))}"</div>`;
        html += `</div></div>`;
    });
    document.getElementById('importResults').innerHTML = html;
}

async function applyImport() {
    const selected = importedRules.filter((r, i) => document.getElementById('imp-' + i)?.checked);
    if (selected.length === 0) { toast('No rules selected', 'error'); return; }

    try {
        const r = await api('/api/import/apply', {
            method: 'POST',
            headers: {'Content-Type':'application/json'},
            body: JSON.stringify({rules: selected})
        });
        toast(`Applied ${r.num_added} rules`);
        clearImport();
        await loadAll();
    } catch(e) {
        toast('Apply failed: ' + e, 'error');
    }
}

function clearImport() {
    document.getElementById('importContent').value = '';
    document.getElementById('importFilename').value = '';
    document.getElementById('importPreview').style.display = 'none';
    document.getElementById('importStatus').textContent = '';
    importedRules = [];
}

loadAll();
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the CRE dashboard."""

    def log_message(self, format, *args):
        pass

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_html(self, html):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length).decode() if length else ""

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/" or path == "":
            self._send_html(HTML_TEMPLATE)

        elif path == "/api/status":
            rules = config.load_rules() or {}
            gate_enabled = config.is_enabled()

            from .knowledge import load_kb
            kb = load_kb()
            self._send_json({
                "gate_enabled": gate_enabled,
                "llm_review_enabled": rules.get("llm_review_enabled", True),
                "alignment_check_enabled": rules.get("alignment_check_enabled", True),
                "self_learning": rules.get("self_learning", False),
                "rule_counts": {
                    "always_block": len(rules.get("always_block", [])),
                    "always_allow": len(rules.get("always_allow", [])),
                    "needs_llm_review": len(rules.get("needs_llm_review", [])),
                    "learned_rules": len(rules.get("learned_rules", [])),
                    "suggestions": len([s for s in rules.get("suggested_rules", []) if s.get("status") == "pending"]),
                    "preferences": len(rules.get("preferences", [])),
                    "kb_patterns": len(kb.get("context_patterns", [])),
                },
                "env_vars": config.get_env_display(),
            })

        elif path == "/api/rules":
            self._send_json(config.load_rules() or {})

        elif path == "/api/import/scan":
            try:
                found = _scan_instruction_files()
                self._send_json({"files": found})
            except Exception as e:
                self._send_json({"error": str(e)}, 400)

        elif path == "/api/logs":
            lines = []
            try:
                with open(config.LOG_PATH) as f:
                    all_lines = f.readlines()
                    lines = [l.rstrip() for l in all_lines[-100:]]
            except FileNotFoundError:
                lines = []
            except Exception as e:
                lines = [f"Error reading log: {e}"]
            self._send_json({"lines": lines})

        elif path == "/api/kb":
            from .knowledge import load_kb
            self._send_json(load_kb())

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()

        if path == "/api/rules":
            try:
                rules = json.loads(body)
                config.save_rules(rules)
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        elif path == "/api/toggle":
            try:
                data = json.loads(body) if body else {}
            except json.JSONDecodeError:
                data = {}
            target = data.get("target", "gate")

            if target == "gate":
                if config.is_enabled():
                    config.disable()
                    self._send_json({"gate_enabled": False})
                else:
                    config.enable()
                    self._send_json({"gate_enabled": True})
            else:
                self._send_json({"error": "Unknown target"}, 400)

        elif path == "/api/test":
            try:
                data = json.loads(body)
                command = data.get("command", "")
                rules = config.load_rules() or {}
                decision, reason, pattern = _regex_test(command, rules)
                self._send_json({"decision": decision, "reason": reason, "matched_pattern": pattern})
            except Exception as e:
                self._send_json({"error": str(e)}, 400)

        elif path == "/api/import/read":
            try:
                data = json.loads(body)
                fpath = data.get("path", "")
                if not fpath or not os.path.isfile(fpath):
                    self._send_json({"error": "File not found"}, 404)
                    return
                with open(fpath, 'r') as f:
                    content = f.read()
                self._send_json({"content": content, "path": fpath})
            except Exception as e:
                self._send_json({"error": str(e)}, 400)

        elif path == "/api/import/parse":
            try:
                data = json.loads(body)
                content = data.get("content", "")
                filename = data.get("filename", "unknown")
                if not content:
                    self._send_json({"error": "No content provided"}, 400)
                    return
                from .importer import extract_rules
                rules_list, err = extract_rules(content, filename)
                if err:
                    self._send_json({"error": err}, 400)
                    return
                self._send_json({"rules": rules_list})
            except Exception as e:
                self._send_json({"error": str(e)}, 400)

        elif path == "/api/import/apply":
            try:
                data = json.loads(body)
                extracted = data.get("rules", [])
                direct = data.get("direct", False)
                if not extracted:
                    self._send_json({"error": "No rules to apply"}, 400)
                    return
                if direct:
                    from .importer import apply_rules
                    rules = config.load_rules() or {}
                    num_added, summary = apply_rules(extracted, rules)
                    config.save_rules(rules)
                    self._send_json({"ok": True, "num_added": num_added, "mode": "direct"})
                else:
                    from .importer import save_as_suggestions, cross_reference_with_existing
                    rules = config.load_rules() or {}
                    clean, conflicts = cross_reference_with_existing(extracted, rules)
                    num_saved = save_as_suggestions(clean, filename="dashboard") if clean else 0
                    self._send_json({"ok": True, "num_added": num_saved, "mode": "suggestions", "conflicts": len(conflicts)})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        elif path == "/api/suggestions/approve":
            try:
                data = json.loads(body)
                from .learner import approve_suggestion
                ok = approve_suggestion(data.get("id", ""), target_category=data.get("target_category"))
                self._send_json({"ok": ok})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        elif path == "/api/suggestions/dismiss":
            try:
                data = json.loads(body)
                from .learner import dismiss_suggestion
                ok = dismiss_suggestion(data.get("id", ""))
                self._send_json({"ok": ok})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        elif path == "/api/kb":
            from .knowledge import load_kb, save_kb
            try:
                kb = json.loads(body)
                save_kb(kb)
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        elif path == "/api/kb/add":
            from .knowledge import load_kb, save_kb
            try:
                data = json.loads(body)
                kb = load_kb()
                kb.setdefault("context_patterns", []).append({
                    "pattern": data.get("pattern", ""),
                    "context": data.get("context", ""),
                    "category": data.get("category", "workflow"),
                })
                save_kb(kb)
                self._send_json({"ok": True})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        elif path == "/api/kb/remove":
            from .knowledge import load_kb, save_kb
            try:
                data = json.loads(body)
                idx = int(data.get("index", -1))
                kb = load_kb()
                patterns = kb.get("context_patterns", [])
                if 0 <= idx < len(patterns):
                    patterns.pop(idx)
                    save_kb(kb)
                    self._send_json({"ok": True})
                else:
                    self._send_json({"ok": False, "error": "Invalid index"}, 400)
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        elif path == "/api/kb/sync":
            from .knowledge import sync_from_servers_md
            try:
                count = sync_from_servers_md()
                self._send_json({"ok": True, "count": count})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        elif path == "/api/kb/test":
            from .knowledge import load_kb, match_context
            try:
                data = json.loads(body)
                kb = load_kb()
                ctx = match_context(data.get("input", ""), kb)
                self._send_json({"match": ctx is not None, "context": ctx or ""})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        else:
            self.send_response(404)
            self.end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


def _scan_instruction_files():
    """Scan common locations for instruction files that CRE can import."""
    home = os.path.expanduser("~")
    cwd = os.getcwd()

    # Known filenames to check in project dirs
    project_files = [
        "CLAUDE.md", "agent.md", "agents.md", "AGENTS.md",
        "rules.md", "RULES.md", "CONVENTIONS.md", "CONTRIBUTING.md",
        ".cursorrules", ".cursorignore", ".windsurfrules", ".clinerules",
        ".github/copilot-instructions.md",
    ]

    candidates = []

    # 1. Home-level Claude config
    candidates.append(os.path.join(home, ".claude", "CLAUDE.md"))
    candidates.append(os.path.join(home, ".claude", "core_rules.md"))

    # 2. Current working directory
    for f in project_files:
        candidates.append(os.path.join(cwd, f))

    # 3. Parent directory (user might run dashboard from a subdirectory)
    parent = os.path.dirname(cwd)
    for f in ["CLAUDE.md", "agent.md", "rules.md"]:
        candidates.append(os.path.join(parent, f))

    # 4. Claude project-level configs
    claude_dir = os.path.join(cwd, ".claude")
    if os.path.isdir(claude_dir):
        for f in os.listdir(claude_dir):
            if f.endswith(".md"):
                candidates.append(os.path.join(claude_dir, f))

    # 5. .claude/agents/*.md in cwd
    agents_dir = os.path.join(cwd, ".claude", "agents")
    if os.path.isdir(agents_dir):
        for f in os.listdir(agents_dir):
            if f.endswith(".md"):
                candidates.append(os.path.join(agents_dir, f))

    found = []
    seen = set()
    for c in candidates:
        path = c if os.path.isabs(c) else os.path.join(os.getcwd(), c)
        path = os.path.abspath(path)
        if path in seen:
            continue
        seen.add(path)
        if os.path.isfile(path):
            try:
                size = os.path.getsize(path)
                found.append({"path": path, "name": os.path.basename(path), "size": size})
            except OSError:
                pass

    return found


def _regex_test(command, rules):
    """Layer 1 regex test (mirrors gate.py regex_check)."""
    for rule in rules.get("always_block", []):
        pattern = rule.get("pattern", "")
        try:
            if re.search(pattern, command):
                return ("deny", f"Blocked: {rule.get('reason', 'matches block rule')}", pattern)
        except re.error:
            if pattern in command:
                return ("deny", f"Blocked: {rule.get('reason', 'matches block rule')}", pattern)

    for rule in rules.get("always_allow", []):
        pattern = rule.get("pattern", "")
        if not pattern:
            continue
        try:
            if re.search(pattern, command):
                return ("allow", "Safe command pattern", pattern)
        except re.error:
            pass

    for rule in rules.get("needs_llm_review", []):
        pattern = rule.get("pattern", "")
        try:
            if re.search(pattern, command):
                return ("review", rule.get("context", "Needs review"), pattern)
        except re.error:
            if pattern in command:
                return ("review", rule.get("context", "Needs review"), pattern)

    return ("allow", "No policy rule matched", "")


def main(port=8766):
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"Claude Rule Enforcer Dashboard running at http://localhost:{port}")
    print(f"Rules: {config.RULES_PATH}")
    print(f"Logs:  {config.LOG_PATH}")
    print(f"Gate:  {config.CRE_ENABLED_PATH}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
