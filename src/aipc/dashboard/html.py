"""Semantic HTML5 dashboard rendering with strict XSS escaping."""

import html
from typing import Any

from aipc.dashboard.models import ProjectDashboardSnapshot, ProjectStatusView


def _esc(val: Any) -> str:
    """Escape text for safe HTML rendering."""
    if val is None:
        return ""
    return html.escape(str(val), quote=True)


def render_projects_index_html(projects: tuple[ProjectStatusView, ...]) -> str:
    """Render index page listing all registered projects."""
    rows = []
    for p in projects:
        rows.append(
            "<tr>"
            f"<td><strong><a href='/projects/{_esc(p.project_id)}'>"
            f"{_esc(p.project_name)}</a></strong></td>"
            f"<td><code>{_esc(p.project_id)}</code></td>"
            f"<td><code>{_esc(p.run_id)}</code></td>"
            f"<td><span class='badge status-{_esc(p.status).lower()}'>{_esc(p.status)}</span></td>"
            f"<td>{_esc(p.updated_at.isoformat())}</td>"
            f"<td><a href='/api/v1/projects/{_esc(p.project_id)}' "
            "class='api-link'>JSON API</a></td>"
            "</tr>"
        )
    rows_html = "\n".join(rows) if rows else "<tr><td colspan='6'>No projects registered.</td></tr>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AIPC Projects Dashboard</title>
  <style>
    :root {{
      --bg: #0f172a;
      --card-bg: #1e293b;
      --border: #334155;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #38bdf8;
      --success: #22c55e;
      --warning: #f59e0b;
      --danger: #ef4444;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 2rem;
    }}
    .container {{ max-width: 1200px; margin: 0 auto; }}
    h1 {{ color: var(--primary); margin-bottom: 0.5rem; }}
    .subtitle {{ color: var(--text-muted); margin-bottom: 2rem; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 1rem;
      background: var(--card-bg);
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      padding: 0.75rem 1rem;
      text-align: left;
      border-bottom: 1px solid var(--border);
    }}
    th {{
      background: #111827;
      color: var(--text-muted);
      font-size: 0.85rem;
      text-transform: uppercase;
    }}
    a {{ color: var(--primary); text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .badge {{
      display: inline-block;
      padding: 0.2rem 0.5rem;
      border-radius: 4px;
      font-size: 0.8rem;
      font-weight: 600;
    }}
    .status-running {{ background: #0369a1; color: #e0f2fe; }}
    .status-completed {{ background: #15803d; color: #dcfce7; }}
    .status-waiting_approval {{ background: #b45309; color: #fef3c7; }}
    .status-waiting_human {{ background: #b91c1c; color: #fee2e2; }}
    .status-failed {{ background: #991b1b; color: #fee2e2; }}
    .api-link {{ font-size: 0.85rem; color: var(--text-muted); }}
  </style>
</head>
<body>
  <div class="container">
    <h1>AI Project Controller</h1>
    <div class="subtitle">Provider-Neutral Orchestration Dashboard &bull; Read-Only API</div>
    <table>
      <thead>
        <tr>
          <th>Project</th>
          <th>Project ID</th>
          <th>Run ID</th>
          <th>Status</th>
          <th>Updated At</th>
          <th>Links</th>
        </tr>
      </thead>
      <tbody>
        {rows_html}
      </tbody>
    </table>
  </div>
</body>
</html>"""


def render_dashboard_html(snapshot: ProjectDashboardSnapshot) -> str:
    """Render full project dashboard containing all 11 required sections."""
    ps = snapshot.project_status
    cp = snapshot.current_phase
    w = snapshot.worker
    r = snapshot.reviewer
    appr = snapshot.approvals
    ts = snapshot.test_status
    rc = snapshot.retry_count
    c = snapshot.cost

    # 5. Approvals Table
    appr_rows = []
    for ev in appr.events:
        res_badge = f"<span class='badge'>{_esc(ev.resolution or 'PENDING')}</span>"
        risk_cls = _esc(ev.risk_level).lower()
        appr_rows.append(
            "<tr>"
            f"<td><code>{_esc(ev.approval_id)}</code></td>"
            f"<td>{_esc(ev.phase_id)}</td>"
            f"<td>{_esc(ev.attempt)}</td>"
            f"<td>{_esc(ev.action_type)}</td>"
            f"<td><span class='badge risk-{risk_cls}'>{_esc(ev.risk_level)}</span></td>"
            f"<td>{_esc(ev.route)}</td>"
            f"<td>{_esc(ev.state)}</td>"
            f"<td>{res_badge}</td>"
            f"<td>{_esc(ev.timestamp.isoformat())}</td>"
            "</tr>"
        )
    appr_rows_html = (
        "\n".join(appr_rows)
        if appr_rows
        else "<tr><td colspan='9'>No approval events recorded.</td></tr>"
    )

    # 7. Timeline Table
    timeline_rows = []
    for ev in snapshot.event_timeline:
        timeline_rows.append(
            "<tr>"
            f"<td>{_esc(ev.phase_id)}</td>"
            f"<td>{_esc(ev.attempt)}</td>"
            f"<td><span class='badge stage'>{_esc(ev.stage)}</span></td>"
            f"<td><code>{_esc(ev.task_id or '-')}</code></td>"
            f"<td><code>{_esc(ev.worker_run_id or '-')}</code></td>"
            f"<td>{_esc(ev.timestamp.isoformat())}</td>"
            "</tr>"
        )
    timeline_rows_html = (
        "\n".join(timeline_rows)
        if timeline_rows
        else "<tr><td colspan='6'>No timeline events recorded.</td></tr>"
    )

    # 9. Cost Agents Table
    agent_rows = []
    for ag in c.agents:
        agent_rows.append(
            "<tr>"
            f"<td><strong>{_esc(ag.agent_id)}</strong></td>"
            f"<td>{_esc(ag.provider)}</td>"
            f"<td>{ag.total_runs}</td>"
            f"<td>{ag.successful_runs}</td>"
            f"<td>{ag.success_rate * 100:.1f}%</td>"
            f"<td>${ag.total_estimated_cost_usd:.4f}</td>"
            f"<td>{ag.total_wall_time_seconds:.1f}s</td>"
            "</tr>"
        )
    agent_rows_html = (
        "\n".join(agent_rows)
        if agent_rows
        else "<tr><td colspan='7'>No per-agent statistics recorded.</td></tr>"
    )

    # 10. Logs Table
    log_rows = []
    for log_item in snapshot.logs:
        log_rows.append(
            "<tr>"
            f"<td>{_esc(log_item.timestamp.isoformat())}</td>"
            f"<td><span class='badge log-{_esc(log_item.level).lower()}'>"
            f"{_esc(log_item.level)}</span></td>"
            f"<td>{_esc(log_item.phase_id or '-')}</td>"
            f"<td>{_esc(log_item.attempt or '-')}</td>"
            f"<td>{_esc(log_item.event or '-')}</td>"
            f"<td><code>{_esc(log_item.message)}</code></td>"
            "</tr>"
        )
    log_rows_html = (
        "\n".join(log_rows) if log_rows else "<tr><td colspan='6'>No log entries.</td></tr>"
    )

    # 11. Phase History Table
    history_rows = []
    for p in snapshot.phase_history:
        history_rows.append(
            "<tr>"
            f"<td><strong>{_esc(p.phase_name)}</strong></td>"
            f"<td><code>{_esc(p.phase_id)}</code></td>"
            f"<td><span class='badge status-{_esc(p.status).lower()}'>{_esc(p.status)}</span></td>"
            f"<td>{_esc(p.attempts)}</td>"
            f"<td>{_esc(p.outcome or '-')}</td>"
            f"<td>{_esc(p.review_verdict or '-')}</td>"
            f"<td><code>{_esc(p.stable_checkpoint_id or '-')}</code></td>"
            "</tr>"
        )
    history_rows_html = (
        "\n".join(history_rows)
        if history_rows
        else "<tr><td colspan='7'>No phase history.</td></tr>"
    )

    approval_pending_info = ""
    if appr.pending_approval_id:
        approval_pending_info += (
            f"&bull; Pending ID: <code>{_esc(appr.pending_approval_id)}</code> "
        )
    if appr.pending_risk_level:
        risk_badge_cls = _esc(appr.pending_risk_level).lower()
        approval_pending_info += (
            f"&bull; Risk: <span class='badge risk-{risk_badge_cls}'>"
            f"{_esc(appr.pending_risk_level)}</span>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AIPC Project: {_esc(ps.project_name)}</title>
  <style>
    :root {{
      --bg: #0b0f19;
      --card-bg: #151e32;
      --border: #23314f;
      --text: #f1f5f9;
      --text-muted: #94a3b8;
      --primary: #38bdf8;
      --success: #22c55e;
      --warning: #f59e0b;
      --danger: #ef4444;
    }}
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      padding: 1.5rem;
    }}
    .container {{ max-width: 1400px; margin: 0 auto; }}
    header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 1.5rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 1rem;
    }}
    h1 {{ margin: 0; color: var(--primary); font-size: 1.6rem; }}
    h2 {{ color: var(--primary); font-size: 1.15rem; margin: 1.5rem 0 0.75rem 0; }}
    .header-links a {{
      color: var(--primary);
      text-decoration: none;
      margin-left: 1rem;
      font-size: 0.9rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
      gap: 1rem;
      margin-bottom: 1rem;
    }}
    .card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1rem;
    }}
    .card h3 {{
      margin-top: 0;
      color: var(--text-muted);
      font-size: 0.85rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .kv {{ display: flex; justify-content: space-between; margin: 0.4rem 0; font-size: 0.9rem; }}
    .kv-label {{ color: var(--text-muted); }}
    .kv-value {{ font-weight: 500; }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
      margin-top: 0.5rem;
      font-size: 0.85rem;
    }}
    th, td {{ padding: 0.6rem 0.8rem; text-align: left; border-bottom: 1px solid var(--border); }}
    th {{
      background: #0f172a;
      color: var(--text-muted);
      font-size: 0.75rem;
      text-transform: uppercase;
    }}
    .badge {{
      display: inline-block;
      padding: 0.15rem 0.4rem;
      border-radius: 4px;
      font-size: 0.75rem;
      font-weight: 600;
    }}
    .status-running {{ background: #0369a1; color: #e0f2fe; }}
    .status-completed {{ background: #15803d; color: #dcfce7; }}
    .status-waiting_approval {{ background: #b45309; color: #fef3c7; }}
    .status-waiting_human {{ background: #b91c1c; color: #fee2e2; }}
    .status-failed {{ background: #991b1b; color: #fee2e2; }}
    .risk-low {{ background: #065f46; color: #d1fae5; }}
    .risk-medium {{ background: #854d0e; color: #fef9c3; }}
    .risk-high {{ background: #9a3412; color: #ffedd5; }}
    .risk-critical {{ background: #991b1b; color: #fee2e2; }}
    .stage {{ background: #1e293b; color: #94a3b8; border: 1px solid #334155; }}
    .log-info {{ background: #0369a1; color: #e0f2fe; }}
    .log-warning {{ background: #b45309; color: #fef3c7; }}
    .log-error {{ background: #991b1b; color: #fee2e2; }}
    code {{
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 0.85em;
      background: rgba(0,0,0,0.25);
      padding: 0.1rem 0.3rem;
      border-radius: 3px;
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>{_esc(ps.project_name)}</h1>
        <div style="color: var(--text-muted); font-size: 0.85rem; margin-top: 0.25rem;">
          Project ID: <code>{_esc(ps.project_id)}</code> &bull;
          Run ID: <code>{_esc(ps.run_id)}</code>
        </div>
      </div>
      <div class="header-links">
        <a href="/">All Projects</a>
        <a href="/api/v1/projects/{_esc(ps.project_id)}">API Snapshot</a>
      </div>
    </header>

    <!-- Top Grid: Sections 1-4 & 8 -->
    <div class="grid">
      <!-- Section 1: Project Status -->
      <div class="card">
        <h3>1. Project Status</h3>
        <div class="kv">
          <span class="kv-label">Status:</span>
          <span class="kv-value">
            <span class="badge status-{_esc(ps.status).lower()}">{_esc(ps.status)}</span>
          </span>
        </div>
        <div class="kv">
          <span class="kv-label">Updated At:</span>
          <span class="kv-value">{_esc(ps.updated_at.isoformat())}</span>
        </div>
        <div class="kv">
          <span class="kv-label">Schema:</span>
          <span class="kv-value"><code>1.0</code></span>
        </div>
      </div>

      <!-- Section 2: Current Phase -->
      <div class="card">
        <h3>2. Current Phase</h3>
        <div class="kv">
          <span class="kv-label">Phase:</span>
          <span class="kv-value">{_esc(cp.phase_name or cp.phase_id or "None")}</span>
        </div>
        <div class="kv">
          <span class="kv-label">Attempt:</span>
          <span class="kv-value">{_esc(cp.attempt)}</span>
        </div>
        <div class="kv">
          <span class="kv-label">Stage:</span>
          <span class="kv-value"><span class="badge stage">{_esc(cp.stage or "None")}</span></span>
        </div>
        <div class="kv">
          <span class="kv-label">Active Task:</span>
          <span class="kv-value"><code>{_esc(cp.active_task_id or "-")}</code></span>
        </div>
        <div class="kv">
          <span class="kv-label">Stable Base:</span>
          <span class="kv-value"><code>{_esc(cp.last_stable_checkpoint or "-")}</code></span>
        </div>
      </div>

      <!-- Section 3: Worker Status -->
      <div class="card">
        <h3>3. Worker</h3>
        <div class="kv">
          <span class="kv-label">Configured:</span>
          <span class="kv-value"><code>{_esc(w.configured_worker_id)}</code></span>
        </div>
        <div class="kv">
          <span class="kv-label">Active:</span>
          <span class="kv-value"><code>{_esc(w.active_worker_id)}</code></span>
        </div>
        <div class="kv">
          <span class="kv-label">Provider:</span>
          <span class="kv-value">{_esc(w.provider or "-")}</span>
        </div>
        <div class="kv">
          <span class="kv-label">Routed:</span>
          <span class="kv-value">{_esc(w.routed)}</span>
        </div>
        <div class="kv">
          <span class="kv-label">Agent Status:</span>
          <span class="kv-value">{_esc(w.agent_status or "-")}</span>
        </div>
      </div>

      <!-- Section 4: Reviewer Status -->
      <div class="card">
        <h3>4. Reviewer</h3>
        <div class="kv">
          <span class="kv-label">Configured:</span>
          <span class="kv-value"><code>{_esc(r.configured_reviewer_id)}</code></span>
        </div>
        <div class="kv">
          <span class="kv-label">Authority:</span>
          <span class="kv-value">{_esc(r.review_authority or "-")}</span>
        </div>
        <div class="kv">
          <span class="kv-label">Latest Verdict:</span>
          <span class="kv-value"><span class="badge">{_esc(r.latest_verdict or "-")}</span></span>
        </div>
        <div class="kv">
          <span class="kv-label">Consensus:</span>
          <span class="kv-value">{_esc(r.consensus_mode or "single")}</span>
        </div>
        <div class="kv">
          <span class="kv-label">Next Phase:</span>
          <span class="kv-value">{_esc(r.next_phase_allowed)}</span>
        </div>
      </div>

      <!-- Section 8: Retry Count -->
      <div class="card">
        <h3>8. Retry Count</h3>
        <div class="kv">
          <span class="kv-label">Phase Revisions:</span>
          <span class="kv-value">{_esc(rc.phase_revision)}</span>
        </div>
        <div class="kv">
          <span class="kv-label">Provider Retries:</span>
          <span class="kv-value">{_esc(rc.provider_retry)}</span>
        </div>
        <div class="kv">
          <span class="kv-label">Review Retries:</span>
          <span class="kv-value">{_esc(rc.review_retry)}</span>
        </div>
      </div>

      <!-- Section 6: Test Status -->
      <div class="card">
        <h3>6. Test Status</h3>
        <div class="kv">
          <span class="kv-label">Reported:</span>
          <span class="kv-value">{_esc(ts.reported)}</span>
        </div>
        <div class="kv">
          <span class="kv-label">Passed / Total:</span>
          <span class="kv-value">{_esc(ts.passed or 0)} / {_esc(ts.total or 0)}</span>
        </div>
        <div class="kv">
          <span class="kv-label">Failed:</span>
          <span class="kv-value">{_esc(ts.failed or 0)}</span>
        </div>
        <div class="kv">
          <span class="kv-label">Skipped:</span>
          <span class="kv-value">{_esc(ts.skipped or 0)}</span>
        </div>
        <div class="kv">
          <span class="kv-label">Commands:</span>
          <span class="kv-value">
            {_esc(ts.commands_observed)} (failed: {_esc(ts.failed_commands)})
          </span>
        </div>
      </div>
    </div>

    <!-- Section 5: Approvals -->
    <h2>5. Approvals</h2>
    <div style="margin-bottom: 0.5rem; font-size: 0.9rem;">
      Waiting Approval: <strong>{_esc(appr.waiting_approval)}</strong>
      {approval_pending_info}
    </div>
    <table>
      <thead>
        <tr>
          <th>ID</th>
          <th>Phase</th>
          <th>Attempt</th>
          <th>Action</th>
          <th>Risk</th>
          <th>Route</th>
          <th>State</th>
          <th>Resolution</th>
          <th>Timestamp</th>
        </tr>
      </thead>
      <tbody>
        {appr_rows_html}
      </tbody>
    </table>

    <!-- Section 7: Event Timeline -->
    <h2>7. Event Timeline</h2>
    <table>
      <thead>
        <tr>
          <th>Phase</th>
          <th>Attempt</th>
          <th>Stage</th>
          <th>Task ID</th>
          <th>Worker Run ID</th>
          <th>Timestamp</th>
        </tr>
      </thead>
      <tbody>
        {timeline_rows_html}
      </tbody>
    </table>

    <!-- Section 9: Cost & Usage -->
    <h2>9. Cost &amp; Usage Tracking</h2>
    <div style="margin-bottom: 0.5rem; font-size: 0.9rem;">
      Total Cost: <strong>${c.total_estimated_cost_usd:.4f}</strong> &bull;
      Total Wall Time: <strong>{c.total_wall_time_seconds:.1f}s</strong> &bull;
      Execution Records: <strong>{_esc(c.total_records)}</strong>
    </div>
    <table>
      <thead>
        <tr>
          <th>Agent ID</th>
          <th>Provider</th>
          <th>Runs</th>
          <th>Passed</th>
          <th>Success Rate</th>
          <th>Estimated Cost</th>
          <th>Wall Time</th>
        </tr>
      </thead>
      <tbody>
        {agent_rows_html}
      </tbody>
    </table>

    <!-- Section 11: Phase History -->
    <h2>11. Phase History</h2>
    <table>
      <thead>
        <tr>
          <th>Phase Name</th>
          <th>ID</th>
          <th>Status</th>
          <th>Attempts</th>
          <th>Outcome</th>
          <th>Verdict</th>
          <th>Stable Checkpoint</th>
        </tr>
      </thead>
      <tbody>
        {history_rows_html}
      </tbody>
    </table>

    <!-- Section 10: Logs -->
    <h2>10. Structured Logs (Secret-Redacted)</h2>
    <table>
      <thead>
        <tr>
          <th>Timestamp</th>
          <th>Level</th>
          <th>Phase</th>
          <th>Attempt</th>
          <th>Event</th>
          <th>Message</th>
        </tr>
      </thead>
      <tbody>
        {log_rows_html}
      </tbody>
    </table>
  </div>
</body>
</html>"""
