import { Component, inject, signal } from '@angular/core';
import { Api, Pending, TraceStep } from '../api';

interface Turn {
  who: 'customer' | 'assistant' | 'system';
  text: string;
  trace?: TraceStep[];
  promptTokens?: number | null;
  completionTokens?: number | null;
}

/**
 * The agent page: the conversation on the left, the trace on the right.
 *
 * <p>That layout is the teaching point of the whole console. The most common way to misjudge an
 * agent is to read only its prose: a reply that says "your buying power is 579,099.35" looks
 * exactly as trustworthy whether the model read it from a tool or produced it from the shape of
 * the conversation. The trace is the only thing that tells them apart, so it is not behind a
 * toggle.
 *
 * <p>The approvals panel is live. When the agent reaches a tier 2 or 3 tool it gets
 * {@code APPROVAL_REQUIRED} and stops; the pending request appears here with the priced summary
 * the customer is meant to read; and the next turn can go through once it is granted. That is the
 * real loop, not a simulation of it.
 */
@Component({
  selector: 'app-agent',
  template: `
    <h1>Agent</h1>
    @if (!available()) {
      <p class="pill warn">
        No chat model is configured. Set ANTHROPIC_API_KEY and restart the service. Everything else
        in the console works without it.
      </p>
    } @else {
      <p class="muted">
        Talking to {{ model() }} as a {{ api.role() }} session. The reply is on the left; what it
        actually called is on the right.
      </p>
    }

    <div class="layout">
      <section class="stack">
        @for (turn of turns(); track $index) {
          <div class="turn" [class]="turn.who">
            <div class="faint small">{{ turn.who }}</div>
            <div class="text">{{ turn.text }}</div>
            @if (turn.promptTokens) {
              <div class="faint small">
                {{ turn.promptTokens }} tokens in, {{ turn.completionTokens }} out
              </div>
            }
          </div>
        } @empty {
          <div class="card">
            <h3 style="margin-top: 0">Try one of these</h3>
            @for (example of examples; track example) {
              <button class="small" style="margin: 0 0.3rem 0.3rem 0"
                      (click)="send(example)">{{ example }}</button>
            }
          </div>
        }

        <div class="row">
          <input style="flex: 1" placeholder="Ask about the account, or ask to trade"
                 [value]="draft()" (input)="draft.set($any($event.target).value)"
                 (keydown.enter)="send(draft())" />
          <button class="primary" (click)="send(draft())"
                  [disabled]="busy() || !available()">Send</button>
          <button (click)="reset()">Reset</button>
        </div>
      </section>

      <section class="stack">
        @if (pending().length) {
          <div class="card">
            <h3 style="margin-top: 0">Waiting for you</h3>
            @for (request of pending(); track request.reference) {
              <div class="approval">
                <div class="row" style="justify-content: space-between">
                  <code>{{ request.tool }}</code>
                  <span class="tier" [class]="'tier tier-' + request.riskTier">
                    T{{ request.riskTier }}</span>
                </div>
                <pre class="small">{{ request.summary }}</pre>
                <div class="row">
                  <button class="primary" (click)="approve(request)">Approve</button>
                  <button (click)="deny(request)">Decline</button>
                  <span class="faint small">expires {{ request.expiresAt }}</span>
                </div>
              </div>
            }
          </div>
        }

        <div class="card">
          <h3 style="margin-top: 0">The trace</h3>
          @if (lastTrace().length) {
            @for (step of lastTrace(); track step.step) {
              <div class="step">
                <div class="row" style="justify-content: space-between">
                  <span><span class="faint">{{ step.step }}.</span> <code>{{ step.tool }}</code></span>
                  <span class="pill" [class.ok]="step.ok" [class.bad]="!step.ok">
                    {{ step.ok ? 'ok' : step.errorCode }} &middot; {{ step.latencyMs }}ms
                  </span>
                </div>
                <pre class="small">{{ pretty(step.arguments) }}</pre>
              </div>
            }
          } @else {
            <p class="faint small">
              Nothing yet. A turn with no tool calls is worth noticing in its own right: it means
              the answer came from the model rather than from your data.
            </p>
          }
        </div>
      </section>
    </div>
  `,
  styles: `
    .layout { display: grid; grid-template-columns: 1fr 24rem; gap: 1.5rem; align-items: start; }
    .turn { border-left: 3px solid var(--border-strong); padding: 0.15rem 0 0.15rem 0.75rem; }
    .turn.customer { border-left-color: var(--accent); }
    .turn.assistant { border-left-color: var(--tier-0); }
    .turn.system { border-left-color: var(--bad); }
    .turn .text { white-space: pre-wrap; }
    .step { padding: 0.5rem 0; border-bottom: 1px solid var(--border); }
    .step:last-child { border-bottom: none; }
    .step pre { margin: 0.35rem 0 0; }
    .approval { padding: 0.5rem 0; border-bottom: 1px solid var(--border); }
    .approval:last-child { border-bottom: none; }
    @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
  `,
})
export class AgentPage {
  protected readonly api = inject(Api);
  protected readonly turns = signal<Turn[]>([]);
  protected readonly draft = signal('');
  protected readonly busy = signal(false);
  protected readonly pending = signal<Pending[]>([]);
  protected readonly lastTrace = signal<TraceStep[]>([]);
  protected readonly available = signal(false);
  protected readonly model = signal('');

  protected readonly examples = [
    'What accounts do I have?',
    'How much can I spend in the everyday brokerage account?',
    'What am I holding, and am I too concentrated in anything?',
    'What would 50 Apple shares cost me?',
    'Buy 50 Apple at market in the everyday brokerage account',
    'Is Nvidia a good buy right now?',
    'Cancel my Microsoft order',
  ];

  constructor() {
    this.api.chatStatus().subscribe((status) => {
      this.available.set(status.modelAvailable);
      this.model.set(status.model);
    });
    this.refreshApprovals();
  }

  protected send(message: string): void {
    const text = message.trim();
    if (!text || this.busy()) {
      return;
    }
    this.draft.set('');
    this.turns.update((turns) => [...turns, { who: 'customer', text }]);
    this.busy.set(true);
    this.api.chat(text).subscribe({
      next: (reply) => {
        this.turns.update((turns) => [
          ...turns,
          {
            who: 'assistant',
            text: reply.reply,
            trace: reply.trace,
            promptTokens: reply.promptTokens,
            completionTokens: reply.completionTokens,
          },
        ]);
        this.lastTrace.set(reply.trace);
        this.busy.set(false);
        this.refreshApprovals();
      },
      error: (error) => {
        this.turns.update((turns) => [
          ...turns,
          { who: 'system', text: error?.error?.error ?? String(error?.message ?? error) },
        ]);
        this.busy.set(false);
      },
    });
  }

  protected approve(request: Pending): void {
    this.api.grant(request.reference).subscribe(() => {
      this.refreshApprovals();
      this.turns.update((turns) => [
        ...turns,
        { who: 'system', text: 'You approved ' + request.tool + '. Ask the agent to go ahead.' },
      ]);
    });
  }

  protected deny(request: Pending): void {
    this.api.deny(request.reference).subscribe(() => {
      this.refreshApprovals();
      this.turns.update((turns) => [
        ...turns,
        { who: 'system', text: 'You declined ' + request.tool + '.' },
      ]);
    });
  }

  protected reset(): void {
    this.api.resetChat().subscribe(() => {
      this.turns.set([]);
      this.lastTrace.set([]);
      this.refreshApprovals();
    });
  }

  private refreshApprovals(): void {
    this.api.approvals().subscribe((pending) => this.pending.set(pending));
  }

  /** Arguments are shown as the model sent them, re-indented but never corrected. */
  protected pretty(json: string): string {
    try {
      return JSON.stringify(JSON.parse(json || '{}'), null, 1);
    } catch {
      return json;
    }
  }
}
