import { Component, computed, effect, inject, signal } from '@angular/core';
import { Api, AuditRecord, Briefing, Finding, ToolCard } from '../api';

/**
 * The governance page: what a reviewer, a risk officer or an auditor turns up asking for.
 *
 * <p>Four things, each of which is usually buried in a log or a build output.
 *
 * <p>The <strong>conformance report</strong> is the output of the gate that runs at startup, so
 * what the registry refused to serve is visible rather than inferred from a stack trace.
 *
 * <p>The <strong>generated briefing</strong> is the system prompt as the model will receive it,
 * today, for this role. Not a copy kept in a wiki — the thing itself.
 *
 * <p>The <strong>kill switches</strong> are live. Trip one and the tool leaves the catalog on the
 * next turn; the agent does not see it, so there is no error to retry.
 *
 * <p>The <strong>audit trail</strong> shows what was called, by whom, with which approval, and
 * under what retention. Argument names appear; argument values never do.
 */
@Component({
  selector: 'app-governance',
  template: `
    <h1>Governance</h1>

    <h2>Conformance</h2>
    @if (findings().length === 0) {
      <p class="pill ok">
        14 descriptors &middot; 0 errors &middot; 0 warnings. The registry refuses to start
        otherwise.
      </p>
    } @else {
      <table>
        <thead><tr><th>rule</th><th>severity</th><th>tool</th><th>finding</th></tr></thead>
        <tbody>
          @for (finding of findings(); track finding.rule + finding.tool + finding.message) {
            <tr>
              <td><code>{{ finding.rule }}</code></td>
              <td><span class="pill" [class.bad]="finding.severity === 'ERROR'"
                        [class.warn]="finding.severity === 'WARNING'">
                {{ finding.severity }}</span></td>
              <td><code class="small">{{ finding.tool }}</code>
                @if (finding.where) { <div class="faint small">{{ finding.where }}</div> }
              </td>
              <td class="small">{{ finding.message }}</td>
            </tr>
          }
        </tbody>
      </table>
    }

    <h2>Evaluation</h2>
    <p class="muted small">
      Read from each descriptor's governance block, which is where the suite writes its scores. A
      tier 2 or 3 tool that cites no suite does not pass the linter, which is how these numbers
      stay attached to something.
    </p>
    <table>
      <thead>
        <tr><th>tool</th><th>tier</th><th>level</th><th>selection accuracy</th><th>switch</th></tr>
      </thead>
      <tbody>
        @for (tool of tools(); track tool.name) {
          <tr>
            <td><code>{{ tool.name }}</code></td>
            <td><span class="tier" [class]="'tier tier-' + tool.riskTier">T{{ tool.riskTier }}</span></td>
            <td>{{ tool.conformanceLevel }}</td>
            <td>
              @if (tool.selectionAccuracy !== null) {
                {{ (tool.selectionAccuracy * 100).toFixed(0) }}%
              } @else {
                <span class="faint">not measured</span>
              }
            </td>
            <td class="small">
              @if (tool.killSwitch) {
                <label class="row" style="gap: 0.35rem">
                  <input type="checkbox" [checked]="!tool.enabled"
                         (change)="toggle(tool, $any($event.target).checked)" />
                  <code>{{ tool.killSwitch }}</code>
                </label>
              } @else {
                <span class="faint">none</span>
              }
            </td>
          </tr>
        }
      </tbody>
    </table>

    <h2>The generated briefing</h2>
    @if (briefing(); as brief) {
      <p class="muted small">
        {{ brief.characters }} characters, {{ brief.visible }} tools visible to a
        {{ brief.role }} session. Generated from the ontology on every turn, so nothing in it can
        disagree with the catalog. Per-tool advice is deliberately absent: that belongs in each
        descriptor, where it is reviewed and versioned with the tool.
      </p>
      <pre>{{ brief.briefing }}</pre>
      @if (hidden().length) {
        <p class="small muted">
          Hidden from this role: @for (entry of hidden(); track entry.tool) {
            <code>{{ entry.tool }}</code><span class="faint"> ({{ entry.missing.join(', ') }})</span>
            @if (!$last) { <span>, </span> }
          }
        </p>
      }
    }

    <h2>Audit trail</h2>
    <p class="muted small">
      Written before the handler runs and completed after, so a call that hangs still leaves
      evidence it was attempted. Argument names are recorded; values are not.
    </p>
    <div class="row" style="margin-bottom: 0.5rem">
      <button (click)="refresh()">Refresh</button>
      <span class="faint small">{{ audit().length }} records</span>
    </div>
    <table>
      <thead>
        <tr>
          <th>when</th><th>tool</th><th>tier</th><th>outcome</th><th>arguments</th>
          <th>approval</th><th>retention</th><th>ms</th>
        </tr>
      </thead>
      <tbody>
        @for (record of audit(); track record.requestId) {
          <tr>
            <td class="small faint">{{ record.at }}</td>
            <td><code class="small">{{ record.tool }}</code>
              <div class="faint small">{{ record.auditEvent }}</div>
            </td>
            <td><span class="tier" [class]="'tier tier-' + record.riskTier">
              T{{ record.riskTier }}</span></td>
            <td>
              <span class="pill" [class.ok]="record.outcome === 'OK'"
                    [class.bad]="record.outcome !== 'OK'">{{ record.outcome }}</span>
            </td>
            <td class="small faint">{{ record.argumentFields.join(', ') || '&mdash;' }}</td>
            <td class="small">
              @if (record.approval) {
                <code>{{ record.approval }}</code>
                <div class="faint">{{ record.approvalMode }}</div>
              } @else {
                <span class="faint">&mdash;</span>
              }
            </td>
            <td class="small faint">{{ record.retention }}</td>
            <td class="small faint">{{ record.latencyMs }}</td>
          </tr>
        } @empty {
          <tr><td colspan="8" class="faint">
            Nothing yet. Call a tool from the Catalog page, or talk to the agent.
          </td></tr>
        }
      </tbody>
    </table>
  `,
  styles: `
    table { margin-bottom: 1.5rem; }
    pre { max-height: 28rem; overflow-y: auto; }
  `,
})
export class GovernancePage {
  private readonly api = inject(Api);
  protected readonly findings = signal<Finding[]>([]);
  protected readonly tools = signal<ToolCard[]>([]);
  protected readonly briefing = signal<Briefing | null>(null);
  protected readonly audit = signal<AuditRecord[]>([]);

  protected readonly hidden = computed(() => {
    const brief = this.briefing();
    return brief
      ? Object.entries(brief.hidden).map(([tool, missing]) => ({ tool, missing }))
      : [];
  });

  constructor() {
    this.api.conformance().subscribe((findings) => this.findings.set(findings));
    effect(() => {
      const role = this.api.role();
      this.api.briefing(role).subscribe((brief) => this.briefing.set(brief));
      this.api.catalog('TRADING').subscribe((tools) => this.tools.set(tools));
    });
    this.refresh();
  }

  protected refresh(): void {
    this.api.audit(50).subscribe((records) => this.audit.set(records));
  }

  protected toggle(tool: ToolCard, off: boolean): void {
    if (!tool.killSwitch) {
      return;
    }
    this.api.toggleSwitch(tool.killSwitch, off).subscribe(() => {
      this.api.catalog('TRADING').subscribe((tools) => this.tools.set(tools));
    });
  }
}
