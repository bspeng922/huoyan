from __future__ import annotations

import re


def _render_page(*, title: str, body: str, script: str) -> str:
    return (
        PAGE_TEMPLATE.replace("__TITLE__", title)
        .replace("__BODY__", body)
        .replace("__SCRIPT__", script)
    )


def _strip_home_explainer(body: str) -> str:
    pattern = re.compile(
        r"""
        \n\s*<div\ class="surface">\s*
        <div\ class="panel-head">\s*
        <h2>.*?</h2>\s*
        </div>\s*
        <div\ class="note-body">.*?<strong>Compare</strong>.*?</div>\s*
        </div>
        """,
        re.DOTALL | re.VERBOSE,
    )
    return pattern.sub("", body, count=1)


PAGE_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__TITLE__</title>
  <style>
    :root {
      --bg: #06070b;
      --bg-soft: rgba(255, 255, 255, 0.04);
      --bg-strong: rgba(255, 255, 255, 0.08);
      --line: rgba(255, 255, 255, 0.12);
      --line-strong: rgba(255, 122, 69, 0.42);
      --text: #f6f1e9;
      --muted: rgba(246, 241, 233, 0.68);
      --accent: #ff7a45;
      --accent-soft: rgba(255, 122, 69, 0.14);
      --pass: #62d6a6;
      --warn: #f3c86f;
      --fail: #ff6b63;
      --skip: #8f96a3;
      --error: #ff4d8b;
      --shadow: 0 24px 80px rgba(0, 0, 0, 0.42);
      --radius: 22px;
      --mono: "Cascadia Code", "JetBrains Mono", "Fira Code", monospace;
      --sans: "Aptos", "Microsoft YaHei UI", "PingFang SC", sans-serif;
    }

    * {
      box-sizing: border-box;
    }

    html, body {
      margin: 0;
      min-height: 100%;
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans);
    }

    body::before,
    body::after {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      z-index: -2;
    }

    body::before {
      background:
        radial-gradient(circle at 14% 16%, rgba(255, 122, 69, 0.26), transparent 28%),
        radial-gradient(circle at 84% 22%, rgba(255, 180, 124, 0.12), transparent 20%),
        radial-gradient(circle at 72% 82%, rgba(255, 122, 69, 0.1), transparent 22%),
        linear-gradient(135deg, #05070a 0%, #0b1018 46%, #13161c 100%);
    }

    body::after {
      z-index: -1;
      opacity: 0.22;
      background-image:
        linear-gradient(rgba(255, 255, 255, 0.04) 1px, transparent 1px),
        linear-gradient(90deg, rgba(255, 255, 255, 0.04) 1px, transparent 1px);
      background-size: 42px 42px;
      mask-image: linear-gradient(180deg, rgba(0, 0, 0, 0.94), transparent 96%);
    }

    .page {
      width: min(1320px, calc(100vw - 32px));
      margin: 0 auto;
      padding: 18px 0 44px;
    }

    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 4px 0 16px;
    }

    .brand {
      display: inline-flex;
      align-items: center;
      gap: 12px;
      color: var(--text);
      text-decoration: none;
    }

    .brand-mark {
      width: 34px;
      height: 34px;
      border-radius: 12px;
      border: 1px solid rgba(255, 255, 255, 0.16);
      background:
        radial-gradient(circle at 30% 30%, rgba(255, 255, 255, 0.55), transparent 38%),
        linear-gradient(135deg, rgba(255, 143, 87, 0.9), rgba(255, 106, 43, 0.78));
      box-shadow: 0 10px 24px rgba(255, 122, 69, 0.22);
    }

    .brand-copy {
      display: grid;
      gap: 2px;
    }

    .brand-copy strong {
      font-size: 14px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .brand-copy span {
      font-size: 12px;
      color: rgba(246, 241, 233, 0.56);
      letter-spacing: 0.04em;
    }

    .nav {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }

    .nav-link {
      display: inline-flex;
      align-items: center;
      min-height: 40px;
      padding: 0 16px;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.12);
      color: rgba(246, 241, 233, 0.78);
      text-decoration: none;
      background: rgba(255, 255, 255, 0.04);
      transition: border-color 180ms ease, transform 180ms ease, background 180ms ease;
    }

    .nav-link:hover {
      transform: translateY(-1px);
      border-color: rgba(255, 122, 69, 0.4);
    }

    .nav-link.current {
      color: #160b05;
      border-color: rgba(255, 122, 69, 0.32);
      background: linear-gradient(135deg, #ff8f57, #ff6a2b);
      box-shadow: 0 12px 28px rgba(255, 122, 69, 0.24);
    }

    .masthead {
      position: relative;
      overflow: hidden;
      border-radius: 28px;
      border: 1px solid rgba(255, 255, 255, 0.08);
      padding: 24px 26px;
      display: grid;
      grid-template-columns: minmax(0, 1.35fr) auto;
      align-items: end;
      gap: 18px;
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0.018)),
        linear-gradient(140deg, rgba(255, 122, 69, 0.12), transparent 38%);
      box-shadow: var(--shadow);
      isolation: isolate;
    }

    .masthead::before {
      content: "";
      position: absolute;
      inset: auto -4% 16% auto;
      width: min(34vw, 420px);
      aspect-ratio: 1;
      border-radius: 50%;
      background:
        radial-gradient(circle, rgba(255, 122, 69, 0.64) 0%, rgba(255, 122, 69, 0.12) 34%, transparent 66%);
      filter: blur(10px);
      z-index: -1;
    }

    .eyebrow {
      margin: 0 0 8px;
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.22em;
      text-transform: uppercase;
      color: rgba(246, 241, 233, 0.54);
    }

    .masthead h1 {
      margin: 0;
      font-size: clamp(32px, 5vw, 58px);
      line-height: 0.96;
      letter-spacing: -0.06em;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .masthead p {
      margin: 12px 0 0;
      max-width: 720px;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.78;
    }

    .status-stack {
      display: grid;
      justify-items: end;
      gap: 10px;
    }

    .hero-status {
      display: inline-flex;
      align-items: center;
      gap: 12px;
      width: fit-content;
      padding: 12px 18px;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.14);
      background: rgba(0, 0, 0, 0.24);
      backdrop-filter: blur(18px);
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }

    .hero-status::before {
      content: "";
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--accent);
      box-shadow: 0 0 0 0 rgba(255, 122, 69, 0.5);
      animation: pulse 1.6s infinite;
    }

    .masthead-note {
      max-width: 280px;
      color: rgba(246, 241, 233, 0.56);
      font-size: 12px;
      line-height: 1.7;
      text-align: right;
    }

    .workspace {
      margin-top: 18px;
      display: grid;
      gap: 18px;
    }

    .surface {
      position: relative;
      overflow: hidden;
      border-radius: var(--radius);
      border: 1px solid var(--line);
      background:
        linear-gradient(180deg, rgba(255, 255, 255, 0.03), rgba(255, 255, 255, 0.02)),
        rgba(0, 0, 0, 0.22);
      box-shadow: var(--shadow);
    }

    .surface::before {
      content: "";
      position: absolute;
      inset: 0 auto auto 0;
      width: 160px;
      height: 1px;
      background: linear-gradient(90deg, var(--accent), transparent);
      opacity: 0.75;
    }

    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      padding: 20px 22px 0;
      flex-wrap: wrap;
    }

    .panel-head h2,
    .panel-head h3 {
      margin: 0;
      font-size: 14px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: rgba(246, 241, 233, 0.68);
    }

    .status-dot {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      font-family: var(--mono);
      font-size: 12px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: rgba(246, 241, 233, 0.84);
    }

    .status-dot::before {
      content: "";
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--skip);
      box-shadow: 0 0 0 0 rgba(143, 150, 163, 0.4);
    }

    .status-dot.running::before {
      background: var(--accent);
      box-shadow: 0 0 0 0 rgba(255, 122, 69, 0.5);
      animation: pulse 1.4s infinite;
    }

    .status-dot.done::before {
      background: var(--pass);
      animation: none;
    }

    .status-dot.error::before {
      background: var(--fail);
      animation: none;
    }

    form {
      padding: 16px 22px 22px;
      display: grid;
      gap: 16px;
    }

    .field-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }

    .field-grid .full {
      grid-column: 1 / -1;
    }

    .field-grid label:has(input[readonly]:not([id])) {
      display: none;
    }

    label {
      display: grid;
      gap: 8px;
      font-size: 13px;
      color: var(--muted);
    }

    input,
    select {
      width: 100%;
      padding: 14px 16px;
      border-radius: 14px;
      border: 1px solid rgba(255, 255, 255, 0.1);
      background: rgba(255, 255, 255, 0.04);
      color: var(--text);
      font: inherit;
      outline: none;
      transition: border-color 180ms ease, transform 180ms ease, background 180ms ease;
    }

    input:focus,
    select:focus {
      border-color: var(--line-strong);
      background: rgba(255, 255, 255, 0.06);
      transform: translateY(-1px);
    }

    .chip-grid,
    .toggles,
    .pill-row,
    .download-row,
    .summary-pills {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
    }

    .chip {
      position: relative;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 12px;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.1);
      background: rgba(255, 255, 255, 0.04);
      cursor: pointer;
      user-select: none;
      transition: border-color 180ms ease, background 180ms ease, transform 180ms ease;
    }

    .chip:hover {
      border-color: rgba(255, 122, 69, 0.36);
      transform: translateY(-1px);
    }

    .chip input,
    .switch input,
    .compare-check {
      width: 16px;
      height: 16px;
      margin: 0;
      accent-color: var(--accent);
    }

    .switch {
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 12px 14px;
      border-radius: 16px;
      border: 1px solid rgba(255, 255, 255, 0.1);
      background: rgba(255, 255, 255, 0.04);
      color: var(--muted);
    }

    .form-actions,
    .toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
      flex-wrap: wrap;
    }

    .progress-card {
      display: none;
      gap: 12px;
      padding: 14px 16px;
      border-radius: 18px;
      border: 1px solid rgba(255, 255, 255, 0.1);
      background: rgba(255, 255, 255, 0.03);
    }

    .progress-card.active {
      display: grid;
    }

    .progress-meta {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 14px;
      flex-wrap: wrap;
    }

    .progress-title {
      display: grid;
      gap: 6px;
    }

    .progress-title strong {
      font-size: 13px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: rgba(246, 241, 233, 0.72);
    }

    .progress-title span {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.7;
    }

    .progress-value {
      font-family: var(--mono);
      font-size: 18px;
      color: var(--text);
    }

    .progress-track {
      position: relative;
      height: 10px;
      overflow: hidden;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.08);
    }

    .progress-fill {
      position: absolute;
      inset: 0 auto 0 0;
      width: 0%;
      border-radius: inherit;
      background: linear-gradient(90deg, #ff8f57, #ff6a2b);
      box-shadow: 0 0 24px rgba(255, 122, 69, 0.26);
      transition: width 320ms ease;
    }

    .hint {
      margin: 0;
      color: rgba(246, 241, 233, 0.52);
      font-size: 13px;
      line-height: 1.7;
    }

    button,
    .button-link {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      min-height: 46px;
      padding: 0 18px;
      border: none;
      border-radius: 999px;
      background: linear-gradient(135deg, #ff8f57, #ff6a2b);
      color: #160b05;
      font: inherit;
      font-weight: 700;
      cursor: pointer;
      text-decoration: none;
      transition: transform 180ms ease, box-shadow 180ms ease, opacity 180ms ease;
      box-shadow: 0 14px 32px rgba(255, 122, 69, 0.28);
    }

    button:hover,
    .button-link:hover {
      transform: translateY(-1px);
      box-shadow: 0 18px 34px rgba(255, 122, 69, 0.34);
    }

    button:disabled {
      opacity: 0.55;
      cursor: wait;
      transform: none;
      box-shadow: none;
    }

    .ghost-button {
      background: rgba(255, 255, 255, 0.05);
      color: var(--text);
      box-shadow: none;
      border: 1px solid rgba(255, 255, 255, 0.14);
    }

    .ghost-button:hover {
      box-shadow: none;
      border-color: rgba(255, 122, 69, 0.4);
    }

    .result-shell,
    .history-shell,
    .compare-shell {
      padding: 0 22px 22px;
    }

    .empty-state {
      padding: 20px 0 0;
      color: rgba(246, 241, 233, 0.56);
      line-height: 1.8;
    }

    .result-head {
      padding-top: 20px;
      display: grid;
      gap: 14px;
    }

    .result-title {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 16px;
      flex-wrap: wrap;
    }

    .result-title h3 {
      margin: 0;
      font-size: clamp(24px, 4vw, 36px);
      line-height: 1.05;
      letter-spacing: -0.04em;
    }

    .meta-line {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
      line-height: 1.7;
    }

    .status-pill,
    .mini-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.12);
      background: rgba(255, 255, 255, 0.05);
      font-size: 12px;
      line-height: 1;
      white-space: nowrap;
    }

    .status-pill.pass { color: var(--pass); border-color: rgba(98, 214, 166, 0.26); }
    .status-pill.warn { color: var(--warn); border-color: rgba(243, 200, 111, 0.26); }
    .status-pill.fail { color: var(--fail); border-color: rgba(255, 107, 99, 0.26); }
    .status-pill.skip { color: var(--skip); border-color: rgba(143, 150, 163, 0.26); }
    .status-pill.error { color: var(--error); border-color: rgba(255, 77, 139, 0.26); }

    .download-row a,
    .inline-link {
      display: inline-flex;
      align-items: center;
      min-height: 38px;
      padding: 0 14px;
      border-radius: 999px;
      border: 1px solid rgba(255, 255, 255, 0.12);
      color: var(--text);
      text-decoration: none;
      background: rgba(255, 255, 255, 0.04);
      transition: border-color 180ms ease, transform 180ms ease;
    }

    .download-row a:hover,
    .inline-link:hover {
      transform: translateY(-1px);
      border-color: rgba(255, 122, 69, 0.44);
    }

    .metric-strip {
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 18px;
      overflow: hidden;
    }

    .metric-cell {
      min-height: 112px;
      padding: 16px 16px 14px;
      background: linear-gradient(180deg, rgba(255, 255, 255, 0.03), transparent);
      border-right: 1px solid rgba(255, 255, 255, 0.08);
      display: grid;
      align-content: start;
      gap: 8px;
    }

    .metric-cell:last-child {
      border-right: none;
    }

    .metric-cell strong {
      font-size: 13px;
      color: rgba(246, 241, 233, 0.72);
    }

    .metric-cell span {
      font-family: var(--mono);
      font-size: 18px;
      line-height: 1.4;
    }

    .metric-cell em {
      font-style: normal;
      color: rgba(246, 241, 233, 0.52);
      font-size: 12px;
      line-height: 1.6;
    }

    .suite-stack {
      margin-top: 20px;
      display: grid;
      gap: 18px;
    }

    .suite-block {
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 18px;
      overflow: hidden;
      background: rgba(255, 255, 255, 0.02);
    }

    .suite-head {
      padding: 16px 18px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 14px;
      flex-wrap: wrap;
    }

    .suite-head h4 {
      margin: 0;
      font-size: 14px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: rgba(246, 241, 233, 0.76);
    }

    .probe-list {
      display: grid;
    }

    .probe-row {
      padding: 16px 18px;
      border-top: 1px solid rgba(255, 255, 255, 0.06);
      display: grid;
      gap: 12px;
    }

    .probe-row:first-child {
      border-top: none;
    }

    .probe-top {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      flex-wrap: wrap;
    }

    .probe-top strong {
      font-size: 15px;
    }

    .probe-value {
      margin-top: 4px;
      font-family: var(--mono);
      font-size: 13px;
      color: rgba(246, 241, 233, 0.72);
    }

    .probe-summary {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.8;
    }

    details {
      border-radius: 14px;
      background: rgba(0, 0, 0, 0.24);
      border: 1px solid rgba(255, 255, 255, 0.08);
    }

    summary {
      cursor: pointer;
      padding: 12px 14px;
      font-size: 13px;
      color: rgba(246, 241, 233, 0.76);
      user-select: none;
    }

    pre {
      margin: 0;
      padding: 0 14px 14px;
      overflow: auto;
      font-family: var(--mono);
      font-size: 12px;
      line-height: 1.68;
      color: #fbe7d8;
    }

    .history-table-wrap,
    .compare-table-wrap {
      padding-top: 18px;
      overflow: auto;
    }

    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 760px;
    }

    th,
    td {
      text-align: left;
      padding: 14px 12px;
      vertical-align: top;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      font-size: 13px;
      line-height: 1.7;
    }

    th {
      color: rgba(246, 241, 233, 0.58);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    tr.active-row td {
      background: rgba(255, 122, 69, 0.06);
    }

    .history-open {
      padding: 0;
      min-height: auto;
      border: none;
      background: none;
      box-shadow: none;
      color: var(--text);
      font-size: 15px;
      font-weight: 700;
      justify-content: flex-start;
    }

    .history-open:hover {
      transform: none;
      color: #ffb58f;
      box-shadow: none;
    }

    .record-meta {
      display: grid;
      gap: 4px;
      margin-top: 4px;
      color: rgba(246, 241, 233, 0.5);
      font-size: 12px;
      line-height: 1.6;
    }

    .summary-pills span {
      padding: 6px 9px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.05);
      font-family: var(--mono);
      font-size: 11px;
    }

    .focus-list {
      display: grid;
      gap: 8px;
    }

    .focus-item {
      color: rgba(246, 241, 233, 0.72);
      font-size: 12px;
    }

    .compare-cell {
      min-width: 220px;
      display: grid;
      gap: 8px;
    }

    .compare-cell p {
      margin: 0;
      color: rgba(246, 241, 233, 0.58);
      font-size: 12px;
      line-height: 1.7;
    }

    .mono {
      font-family: var(--mono);
    }

    .toast {
      position: fixed;
      right: 18px;
      bottom: 18px;
      max-width: min(360px, calc(100vw - 36px));
      padding: 14px 16px;
      border-radius: 16px;
      border: 1px solid rgba(255, 255, 255, 0.12);
      background: rgba(0, 0, 0, 0.72);
      color: var(--text);
      box-shadow: var(--shadow);
      opacity: 0;
      transform: translateY(12px);
      pointer-events: none;
      transition: opacity 180ms ease, transform 180ms ease;
      z-index: 30;
    }

    .toast.show {
      opacity: 1;
      transform: translateY(0);
    }

    .reveal {
      animation: rise 0.42s ease both;
    }

    @keyframes rise {
      from {
        opacity: 0;
        transform: translateY(14px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }

    @keyframes pulse {
      0% {
        box-shadow: 0 0 0 0 rgba(255, 122, 69, 0.45);
      }
      70% {
        box-shadow: 0 0 0 14px rgba(255, 122, 69, 0);
      }
      100% {
        box-shadow: 0 0 0 0 rgba(255, 122, 69, 0);
      }
    }

    @media (max-width: 980px) {
      .page {
        width: min(100vw - 20px, 1320px);
        padding-top: 10px;
      }

      .topbar,
      .masthead {
        gap: 14px;
      }

      .masthead {
        grid-template-columns: 1fr;
      }

      .status-stack {
        justify-items: start;
      }

      .masthead-note {
        text-align: left;
      }

      .field-grid {
        grid-template-columns: 1fr;
      }
    }

    @media (max-width: 640px) {
      .page {
        width: min(100vw - 16px, 1320px);
      }

      .topbar {
        align-items: flex-start;
        flex-direction: column;
      }

      .masthead {
        padding: 20px 18px;
      }

      .masthead h1 {
        font-size: clamp(28px, 8vw, 36px);
      }

      .panel-head,
      form,
      .note-body,
      .result-shell,
      .history-shell,
      .compare-shell {
        padding-left: 18px;
        padding-right: 18px;
      }

      .metric-strip {
        grid-template-columns: 1fr 1fr;
      }
    }
  </style>
</head>
<body>
__BODY__
  <div id="toast" class="toast" role="status" aria-live="polite"></div>
  <script>
__SCRIPT__
  </script>
</body>
</html>
"""


COMMON_JS = """
const toast = document.getElementById('toast');

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function prettyJson(value) {
  return escapeHtml(JSON.stringify(value, null, 2));
}

function suiteLabel(name) {
  return {
    scorecard: '评分卡',
    authenticity: '真实性与路由',
    performance: '性能与稳定性',
    agentic: '工具链路与上下文',
    cost_security: '计量与入口安全',
    security_audit: '中转安全审计',
  }[name] || name;
}

function showToast(message, tone = 'default') {
  toast.textContent = message;
  toast.style.borderColor = tone === 'error'
    ? 'rgba(255, 107, 99, 0.38)'
    : 'rgba(255, 122, 69, 0.38)';
  toast.classList.add('show');
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => {
    toast.classList.remove('show');
  }, 2800);
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json') ? await response.json() : null;
  if (!response.ok) {
    const detail = payload && payload.detail ? payload.detail : '请求失败';
    throw new Error(detail);
  }
  return payload;
}

function buildSummaryPills(summary) {
  const entries = Object.entries(summary || {});
  if (!entries.length) {
    return '<span class="mini-pill">无汇总</span>';
  }
  return entries
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([key, value]) => `<span class="mini-pill mono">${escapeHtml(key)}: ${escapeHtml(value)}</span>`)
    .join('');
}

function renderDownloads(record) {
  const urls = record.download_urls || {};
  const formats = Object.keys(urls);
  if (!formats.length) {
    return '';
  }
  return `
    <div class="download-row">
      ${formats.map((format) => (
        `<a href="${escapeHtml(urls[format])}" target="_blank" rel="noopener">导出 ${escapeHtml(format.toUpperCase())}</a>`
      )).join('')}
    </div>
  `;
}

function buildReportMarkup(report, record, options = {}) {
  const model = report.model;
  const focusCards = (model.focus_cards || []).map((item) => `
    <div class="metric-cell">
      <strong>${escapeHtml(item.label)}</strong>
      <span>${escapeHtml(item.value || '-')}</span>
      <em>${escapeHtml(item.status_label)} · ${escapeHtml(item.summary || '')}</em>
    </div>
  `).join('');

  const suites = (model.suites || []).map((suite) => `
    <section class="suite-block reveal">
      <div class="suite-head">
        <h4>${escapeHtml(suite.label)}</h4>
        <span class="mini-pill mono">${escapeHtml(suite.suite)}</span>
      </div>
      <div class="probe-list">
        ${(suite.probes || []).map((probe) => `
          <article class="probe-row">
            <div class="probe-top">
              <div>
                <strong>${escapeHtml(probe.label)}</strong>
                <div class="probe-value">${escapeHtml(probe.value || '-')}</div>
              </div>
              <span class="status-pill ${escapeHtml(probe.status)}">${escapeHtml(probe.status_label)}</span>
            </div>
            <div class="probe-summary">${escapeHtml(probe.summary || '')}</div>
            ${(Object.keys(probe.metrics || {}).length || Object.keys(probe.evidence || {}).length) ? `
              <details>
                <summary>查看 metrics / evidence</summary>
                ${Object.keys(probe.metrics || {}).length ? `<pre>${prettyJson(probe.metrics)}</pre>` : ''}
                ${Object.keys(probe.evidence || {}).length ? `<pre>${prettyJson(probe.evidence)}</pre>` : ''}
              </details>
            ` : ''}
          </article>
        `).join('')}
      </div>
    </section>
  `).join('');

  const recordPageLink = options.historyLink
    ? `<a class="inline-link" href="${escapeHtml(options.historyLink)}">打开测试记录页</a>`
    : '';

  return `
    <div class="result-head reveal">
      <div class="result-title">
        <div>
          <h3>${escapeHtml(model.name)}</h3>
          <div class="meta-line">
            <span>${escapeHtml(report.provider.base_url)}</span>
            <span>家族 ${escapeHtml(model.claimed_family || '-')}</span>
            <span>生成于 ${escapeHtml(record.generated_at)}</span>
            <span>Key ${escapeHtml(record.key_hint || '')}</span>
          </div>
        </div>
        <div class="pill-row">
          <span class="status-pill ${escapeHtml(model.overall_status)}">${escapeHtml(model.overall_status_label)}</span>
          ${buildSummaryPills(model.summary)}
        </div>
      </div>
      <div class="toolbar">
        ${recordPageLink}
        ${renderDownloads(record)}
      </div>
      ${focusCards ? `<div class="metric-strip">${focusCards}</div>` : ''}
      <div class="suite-stack">${suites || '<div class="empty-state">这条结果没有可展示的 suite。</div>'}</div>
    </div>
  `;
}

function renderComparisonMarkup(payload) {
  const rows = payload.rows || [];
  if (!rows.length) {
    return '<div class="empty-state">这些记录没有可对比的共同指标。</div>';
  }

  return `
    <div class="compare-table-wrap reveal">
      <table>
        <thead>
          <tr>
            <th>指标</th>
            ${payload.runs.map((run) => `
              <th>
                ${escapeHtml(run.model)}
                <div class="record-meta">
                  <span>${escapeHtml(run.generated_at)}</span>
                  <span>${escapeHtml(run.base_url)}</span>
                </div>
              </th>
            `).join('')}
          </tr>
        </thead>
        <tbody>
          ${rows.map((row) => `
            <tr>
              <td>
                <strong>${escapeHtml(row.label)}</strong>
                <div class="record-meta">
                  <span>${escapeHtml(suiteLabel(row.suite))}</span>
                  <span class="mono">${escapeHtml(row.probe)}</span>
                </div>
              </td>
              ${row.cells.map((cell) => `
                <td>
                  <div class="compare-cell">
                    <span class="status-pill ${escapeHtml(cell.status)}">${escapeHtml(cell.status_label)}</span>
                    <div class="mono">${escapeHtml(cell.value || '-')}</div>
                    <p>${escapeHtml(cell.summary || '')}</p>
                  </div>
                </td>
              `).join('')}
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;
}
"""


INDEX_BODY = """
  <div class="page" id="homePage">
    <header class="topbar">
      <a class="brand" href="/">
        <span class="brand-mark" aria-hidden="true"></span>
        <span class="brand-copy">
          <strong>Huoyan</strong>
          <span>Web Console</span>
        </span>
      </a>
      <nav class="nav">
        <a class="nav-link current" href="/">运行测试</a>
        <a class="nav-link" id="historyLink" href="/history">测试记录</a>
      </nav>
    </header>

    <section class="masthead">
      <div>
        <p class="eyebrow">Huoyan Web Console</p>
        <h1>中转站检测台</h1>
        <p>输入 baseUrl、模型与 key，直接复用 Huoyan 现有测试链路完成检测；当前测试结果在本页查看，历史记录与结果对比进入单独的记录页处理。</p>
      </div>
      <div class="status-stack">
        <div class="hero-status" id="heroStatus">idle / waiting</div>
        <div class="masthead-note"></div>
      </div>
    </section>

    <main class="workspace">
      <section class="surface">
          <div class="panel-head">
            <h2>运行设置</h2>
            <span id="runStatus" class="status-dot">待命</span>
          </div>
          <form id="runForm">
            <div class="field-grid">
              <label class="full">
                <span>Base URL</span>
                <input id="baseUrl" name="base_url" type="url" placeholder="https://your-relay.example.com/v1" required>
              </label>
              <label>
                <span>模型</span>
                <input id="modelName" name="model" type="text" placeholder="gpt-4o / glm-4.5 / qwen-max" required>
              </label>
              <label>
                <span>API 风格</span>
                <input type="text" value="根据 baseUrl 自动判断" readonly>
              </label>
              <label class="full">
                <span>API Key</span>
                <input id="apiKey" name="api_key" type="password" placeholder="sk-..." required>
              </label>
              <label>
                <span>声明家族（可选）</span>
                <input id="claimedFamily" name="claimed_family" type="text" placeholder="openai / claude / qwen">
              </label>
            </div>

            <div>
              <label>能力开关</label>
              <div class="toggles">
                <label class="switch"><input id="supportsStream" type="checkbox" checked> 支持流式</label>
                <label class="switch"><input id="supportsTools" type="checkbox" checked> 支持工具调用</label>
                <label class="switch"><input id="supportsVision" type="checkbox"> 支持视觉</label>
              </div>
            </div>

            <div>
              <label>测试套件</label>
              <div class="chip-grid">
                <label class="chip"><input class="suite-check" type="checkbox" value="authenticity" checked> authenticity</label>
                <label class="chip"><input class="suite-check" type="checkbox" value="performance" checked> performance</label>
                <label class="chip"><input class="suite-check" type="checkbox" value="agentic" checked> agentic</label>
                <label class="chip"><input class="suite-check" type="checkbox" value="cost_security" checked> cost_security</label>
                <label class="chip"><input class="suite-check" type="checkbox" value="security_audit" checked> security_audit</label>
              </div>
            </div>

            <div class="form-actions">
              <p class="hint">历史记录只保留脱敏后的报告与导出文件，不回存原始 key。</p>
              <button id="runButton" type="submit">开始测试</button>
            </div>
            <div id="progressCard" class="progress-card">
              <div class="progress-meta">
                <div class="progress-title">
                  <strong>测试进度</strong>
                  <span id="progressLabel">等待开始</span>
                </div>
                <div id="progressValue" class="progress-value">0 / 0</div>
              </div>
              <div class="progress-track">
                <div id="progressFill" class="progress-fill"></div>
              </div>
            </div>
          </form>
        
        <div class="surface">
          <div class="panel-head">
            <h2>页面分工</h2>
          </div>
          <div class="note-body">
            <h3>首页只做一件事：发起测试并看当前结果</h3>
            <p>测试记录页集中负责历史管理、导出下载和多记录对比。这样首页首屏更紧凑，运行过程也更聚焦。</p>
            <div class="signal-list">
              <div class="signal-item">
                <strong>Run</strong>
                <span>输入中转站参数后直接调用现有 runner，当前结果在下方即时展开。</span>
              </div>
              <div class="signal-item">
                <strong>Record</strong>
                <span>每次运行都会写入历史索引并生成 JSON、Markdown 与透明日志导出。</span>
              </div>
              <div class="signal-item">
                <strong>Compare</strong>
                <span>多结果对比迁移到测试记录页内，通过勾选记录统一生成矩阵。</span>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="surface">
        <div class="panel-head">
          <h2>当前结果</h2>
        </div>
        <div id="resultMount" class="result-shell">
          <div class="empty-state">尚未运行测试。提交表单后，结果会在这里按 suite 展开。</div>
        </div>
      </section>
    </main>
  </div>
"""


INDEX_SCRIPT = (
    COMMON_JS
    + """
const runForm = document.getElementById('runForm');
const runButton = document.getElementById('runButton');
const runStatus = document.getElementById('runStatus');
const heroStatus = document.getElementById('heroStatus');
const resultMount = document.getElementById('resultMount');
const historyLink = document.getElementById('historyLink');
const progressCard = document.getElementById('progressCard');
const progressLabel = document.getElementById('progressLabel');
const progressValue = document.getElementById('progressValue');
const progressFill = document.getElementById('progressFill');

let activePollTimer = null;

function setRunState(mode, text) {
  runStatus.className = 'status-dot';
  if (mode === 'running') {
    runStatus.classList.add('running');
  } else if (mode === 'done') {
    runStatus.classList.add('done');
  } else if (mode === 'error') {
    runStatus.classList.add('error');
  }
  runStatus.textContent = text;
  heroStatus.textContent = {
    running: 'running / probing',
    done: 'idle / report ready',
    error: 'halt / request failed',
  }[mode] || 'idle / waiting';
}

function setProgress(job) {
  if (!job) {
    progressCard.classList.remove('active');
    progressLabel.textContent = '等待开始';
    progressValue.textContent = '0 / 0';
    progressFill.style.width = '0%';
    return;
  }

  progressCard.classList.add('active');
  const currentLabel = job.current_probe_label || job.last_completed_probe_label || '准备中';
  progressLabel.textContent = job.status === 'completed'
    ? '测试已完成'
    : job.status === 'failed'
      ? '运行失败'
      : `当前项：${currentLabel}`;
  progressValue.textContent = `${job.progress_completed} / ${job.progress_total}`;
  progressFill.style.width = `${job.progress_percent || 0}%`;
}

function renderResult(report, record) {
  const historyHref = record && record.run_id
    ? `/history?selected=${encodeURIComponent(record.run_id)}`
    : '/history';
  resultMount.innerHTML = buildReportMarkup(report, record, { historyLink: historyHref });
}

function stopPolling() {
  if (activePollTimer) {
    window.clearTimeout(activePollTimer);
    activePollTimer = null;
  }
}

async function pollJob(jobId) {
  try {
    const payload = await fetchJson(`/api/run/jobs/${jobId}`);
    const job = payload.job;
    setProgress(job);

    if (job.status === 'completed' && job.result) {
      stopPolling();
      renderResult(job.result.report, job.result.record);
      historyLink.href = `/history?selected=${encodeURIComponent(job.result.record.run_id)}`;
      setRunState('done', '测试完成');
      resultMount.scrollIntoView({ behavior: 'smooth', block: 'start' });
      showToast('测试已完成，历史记录页可查看记录与对比。');
      runButton.disabled = false;
      return;
    }

    if (job.status === 'failed') {
      stopPolling();
      setRunState('error', '运行失败');
      resultMount.innerHTML = `<div class="empty-state reveal">运行失败：${escapeHtml(job.error || '未知错误')}</div>`;
      showToast(job.error || '运行失败', 'error');
      runButton.disabled = false;
      return;
    }

    activePollTimer = window.setTimeout(() => {
      pollJob(jobId);
    }, 800);
  } catch (error) {
    stopPolling();
    setRunState('error', '运行失败');
    resultMount.innerHTML = `<div class="empty-state reveal">运行失败：${escapeHtml(error.message)}</div>`;
    showToast(error.message, 'error');
    runButton.disabled = false;
  }
}

runForm.addEventListener('submit', async (event) => {
  event.preventDefault();

  const enabledSuites = Array.from(document.querySelectorAll('.suite-check:checked')).map((input) => input.value);
  const payload = {
    base_url: document.getElementById('baseUrl').value.trim(),
    model: document.getElementById('modelName').value.trim(),
    api_key: document.getElementById('apiKey').value.trim(),
    claimed_family: document.getElementById('claimedFamily').value.trim() || null,
    supports_stream: document.getElementById('supportsStream').checked,
    supports_tools: document.getElementById('supportsTools').checked,
    supports_vision: document.getElementById('supportsVision').checked,
    enabled_suites: enabledSuites,
  };

  stopPolling();
  runButton.disabled = true;
  setRunState('running', '测试进行中');
  setProgress({
    status: 'running',
    progress_completed: 0,
    progress_total: 0,
    progress_percent: 0,
  });
  resultMount.innerHTML = '<div class="empty-state reveal">正在运行测试，进度会在上方实时更新。结果会在当前区域自动刷新。</div>';
  setRunState('running', '测试进行中');
  resultMount.innerHTML = '<div class="empty-state reveal">正在运行测试，请保持页面开启。结果会在当前区域自动刷新。</div>';

  try {
    const response = await fetchJson('/api/run/start', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    setProgress(response.job);
    await pollJob(response.job.job_id);
    return;
    renderResult(response.report, response.record);
    historyLink.href = `/history?selected=${encodeURIComponent(response.record.run_id)}`;
    setRunState('done', '测试完成');
    resultMount.scrollIntoView({ behavior: 'smooth', block: 'start' });
    showToast('测试已完成，历史记录页可查看记录与对比。');
  } catch (error) {
    setRunState('error', '运行失败');
    resultMount.innerHTML = `<div class="empty-state reveal">运行失败：${escapeHtml(error.message)}</div>`;
    showToast(error.message, 'error');
  } finally {
    runButton.disabled = false;
  }
});
"""
)


HISTORY_BODY = """
  <div class="page" id="historyPage">
    <header class="topbar">
      <a class="brand" href="/">
        <span class="brand-mark" aria-hidden="true"></span>
        <span class="brand-copy">
          <strong>Huoyan</strong>
          <span>Web Console</span>
        </span>
      </a>
      <nav class="nav">
        <a class="nav-link" href="/">运行测试</a>
        <a class="nav-link current" href="/history">测试记录</a>
      </nav>
    </header>

    <section class="masthead">
      <div>
        <p class="eyebrow">History & Compare</p>
        <h1>测试记录</h1>
        <p>在这里查看历史结果、下载导出文件，并通过勾选多条记录生成对比矩阵。详情与对比合并在同一页内完成。</p>
      </div>
      <div class="status-stack">
        <div class="hero-status">history / compare</div>
        <div class="masthead-note">点击某条记录查看详情；勾选两条及以上记录后点击右上角按钮生成横向对比。</div>
      </div>
    </section>

    <main class="workspace">
      <section class="surface">
        <div class="panel-head">
          <h2>记录列表</h2>
          <button id="compareButton" type="button" class="ghost-button">对比所选记录</button>
        </div>
        <div id="historyMount" class="history-shell">
          <div class="empty-state">正在读取历史记录。</div>
        </div>
      </section>

      <section class="surface">
        <div class="panel-head">
          <h2>记录详情</h2>
          <span id="detailStatus" class="status-dot">未选中</span>
        </div>
        <div id="detailMount" class="result-shell">
          <div class="empty-state">选择一条测试记录后，在这里查看详情。</div>
        </div>
      </section>

      <section class="surface">
        <div class="panel-head">
          <h2>结果对比</h2>
        </div>
        <div id="compareMount" class="compare-shell">
          <div class="empty-state">勾选至少两条历史记录后，在这里输出对比矩阵。</div>
        </div>
      </section>
    </main>
  </div>
"""


HISTORY_SCRIPT = (
    COMMON_JS
    + """
const historyMount = document.getElementById('historyMount');
const detailMount = document.getElementById('detailMount');
const compareMount = document.getElementById('compareMount');
const compareButton = document.getElementById('compareButton');
const detailStatus = document.getElementById('detailStatus');

const state = {
  activeRunId: null,
  history: [],
  selectedIds: new Set(),
};

function setDetailStatus(mode, text) {
  detailStatus.className = 'status-dot';
  if (mode === 'running') {
    detailStatus.classList.add('running');
  } else if (mode === 'done') {
    detailStatus.classList.add('done');
  } else if (mode === 'error') {
    detailStatus.classList.add('error');
  }
  detailStatus.textContent = text;
}

function renderDetail(report, record) {
  detailMount.innerHTML = buildReportMarkup(report, record);
}

function renderHistory(records) {
  if (!records.length) {
    historyMount.innerHTML = '<div class="empty-state">暂无历史记录。</div>';
    return;
  }

  historyMount.innerHTML = `
    <div class="history-table-wrap reveal">
      <table>
        <thead>
          <tr>
            <th>选择</th>
            <th>记录</th>
            <th>状态</th>
            <th>汇总</th>
            <th>重点指标</th>
            <th>导出</th>
          </tr>
        </thead>
        <tbody>
          ${records.map((record) => `
            <tr class="${record.run_id === state.activeRunId ? 'active-row' : ''}">
              <td>
                <input class="compare-check" type="checkbox" value="${escapeHtml(record.run_id)}" ${state.selectedIds.has(record.run_id) ? 'checked' : ''}>
              </td>
              <td>
                <button type="button" class="history-open" data-run-id="${escapeHtml(record.run_id)}">${escapeHtml(record.model)}</button>
                <div class="record-meta">
                  <span>${escapeHtml(record.base_url)}</span>
                  <span>${escapeHtml(record.generated_at)}</span>
                </div>
              </td>
              <td>
                <span class="status-pill ${escapeHtml(record.overall_status)}">${escapeHtml(record.status_label)}</span>
              </td>
              <td>
                <div class="summary-pills">${buildSummaryPills(record.summary)}</div>
              </td>
              <td>
                <div class="focus-list">
                  ${(record.focus_metrics || []).slice(0, 3).map((item) => `
                    <div class="focus-item">${escapeHtml(item.label)} · ${escapeHtml(item.value || '-')}</div>
                  `).join('')}
                </div>
              </td>
              <td>${renderDownloads(record)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    </div>
  `;

  historyMount.querySelectorAll('.compare-check').forEach((input) => {
    input.addEventListener('change', () => {
      if (input.checked) {
        state.selectedIds.add(input.value);
      } else {
        state.selectedIds.delete(input.value);
      }
    });
  });

  historyMount.querySelectorAll('.history-open').forEach((button) => {
    button.addEventListener('click', async () => {
      await openRecord(button.dataset.runId);
    });
  });
}

async function refreshHistory() {
  const payload = await fetchJson('/api/history');
  state.history = payload.records || [];
  state.selectedIds = new Set(Array.from(state.selectedIds).filter((id) => state.history.some((record) => record.run_id === id)));
  renderHistory(state.history);
}

async function openRecord(runId) {
  setDetailStatus('running', '载入中');
  try {
    const payload = await fetchJson(`/api/history/${runId}`);
    state.activeRunId = runId;
    renderDetail(payload.report, payload.record);
    renderHistory(state.history);
    setDetailStatus('done', '已载入');
    detailMount.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    setDetailStatus('error', '载入失败');
    detailMount.innerHTML = `<div class="empty-state reveal">读取失败：${escapeHtml(error.message)}</div>`;
    showToast(error.message, 'error');
  }
}

compareButton.addEventListener('click', async () => {
  const ids = Array.from(state.selectedIds);
  if (ids.length < 2) {
    showToast('至少勾选两条记录才能对比。', 'error');
    return;
  }

  compareButton.disabled = true;
  compareMount.innerHTML = '<div class="empty-state reveal">正在生成对比矩阵。</div>';

  try {
    const payload = await fetchJson('/api/compare', {
      method: 'POST',
      body: JSON.stringify({ ids }),
    });
    compareMount.innerHTML = renderComparisonMarkup(payload);
    compareMount.scrollIntoView({ behavior: 'smooth', block: 'start' });
    showToast('对比矩阵已更新。');
  } catch (error) {
    compareMount.innerHTML = `<div class="empty-state reveal">对比失败：${escapeHtml(error.message)}</div>`;
    showToast(error.message, 'error');
  } finally {
    compareButton.disabled = false;
  }
});

async function bootstrapHistoryPage() {
  await refreshHistory();
  const params = new URLSearchParams(window.location.search);
  const selectedRunId = params.get('selected');
  if (selectedRunId && state.history.some((record) => record.run_id === selectedRunId)) {
    await openRecord(selectedRunId);
    return;
  }
  if (state.history.length) {
    setDetailStatus('', '未选中');
  }
}

bootstrapHistoryPage().catch((error) => {
  historyMount.innerHTML = `<div class="empty-state reveal">读取失败：${escapeHtml(error.message)}</div>`;
  showToast(error.message, 'error');
});
"""
)


INDEX_HTML = _render_page(
    title="Huoyan Web Console",
    body=_strip_home_explainer(INDEX_BODY),
    script=INDEX_SCRIPT,
)

HISTORY_HTML = _render_page(
    title="Huoyan Test History",
    body=HISTORY_BODY,
    script=HISTORY_SCRIPT,
)
