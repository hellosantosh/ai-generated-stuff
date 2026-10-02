import { JsonPipe } from '@angular/common';
import { Component, computed, effect, inject, signal } from '@angular/core';
import { Api, InvokeResult, ToolCard } from '../api';

/**
 * The catalog page: what the model is being shown, and what happens when you call it yourself.
 *
 * <p>The two halves are deliberate. On the left, the tools this session can see, with the
 * governance a human needs in order to have an opinion about them. On the right, the descriptor
 * verbatim — not a rendering of it, the file — and a box to call the tool with arguments you type.
 *
 * <p>That second half is the most used thing in the whole console. When an agent gives a strange
 * answer, the question is always whether the tool was wrong or the model read it wrong, and this
 * is how you find out in ten seconds: paste the arguments from the trace and look at the envelope.
 */
@Component({
  selector: 'app-catalog',
  imports: [JsonPipe],
  template: `
    <h1>Catalog</h1>
    <p class="muted">
      {{ tools().length }} of 14 tools are visible to a {{ api.role() }} session. A tool this
      session cannot call is not shown to the model at all &mdash; publishing one would cost tokens
      and buy a wrong turn.
    </p>

    <div class="layout">
      <section>
        <div class="row" style="margin-bottom: 0.75rem">
          <input
            placeholder="filter by name, entity or operation"
            [value]="filter()"
            (input)="filter.set($any($event.target).value)"
            style="flex: 1; min-width: 12rem" />
          <label class="small muted row" style="gap: 0.3rem">
            <input type="checkbox" [checked]="writesOnly()"
                   (change)="writesOnly.set($any($event.target).checked)" />
            changes state only
          </label>
        </div>

        @for (tool of visible(); track tool.name) {
          <button class="tool" [class.on]="selected() === tool.name"
                  (click)="select(tool.name)">
            <div class="row" style="justify-content: space-between; width: 100%">
              <code>{{ tool.name }}</code>
              <span class="tier" [class]="'tier tier-' + tool.riskTier">T{{ tool.riskTier }}</span>
            </div>
            <div class="small muted">{{ tool.title }}</div>
            <div class="row small" style="gap: 0.3rem">
              <span class="pill">{{ tool.operation }} / {{ tool.entity }}</span>
              @if (tool.readOnly) {
                <span class="pill ok">read-only</span>
              } @else {
                <span class="pill bad">{{ tool.approvalMode }}</span>
              }
              @if (!tool.enabled) { <span class="pill bad">switched off</span> }
            </div>
          </button>
        } @empty {
          <p class="faint">Nothing matches.</p>
        }
      </section>

      <section>
        @if (card(); as tool) {
          <div class="card stack">
            <div>
              <h2 style="margin-top: 0">{{ tool.title }}</h2>
              <code class="muted">{{ tool.catalogId }}</code>
            </div>

            <table>
              <tbody>
                <tr><th>risk tier</th><td>
                  <span class="tier" [class]="'tier tier-' + tool.riskTier">T{{ tool.riskTier }}</span>
                  &nbsp;approval: {{ tool.approvalMode }}
                </td></tr>
                <tr><th>conformance</th><td>{{ tool.conformanceLevel }} &middot; {{ tool.lifecycle }}</td></tr>
                <tr><th>annotations</th><td>
                  readOnly {{ tool.readOnly }} &middot; destructive {{ tool.destructive }}
                  &middot; idempotent {{ tool.idempotent }} &middot; openWorld {{ tool.openWorld }}
                </td></tr>
                <tr><th>entitlements</th><td><code>{{ tool.entitlements.join(', ') }}</code></td></tr>
                <tr><th>owner</th><td>{{ tool.owner }}</td></tr>
                <tr><th>consumes</th><td>{{ tool.consumes.join(', ') || '&mdash;' }}</td></tr>
                <tr><th>produces</th><td>{{ tool.produces.join(', ') || '&mdash;' }}</td></tr>
                <tr><th>budget</th><td>
                  {{ tool.costClass }} &middot; p95 {{ tool.p95LatencyMs }}ms
                  &middot; {{ tool.perMinute }}/min
                </td></tr>
                <tr><th>description</th><td>{{ tool.descriptionCharacters }} characters,
                  {{ tool.inputProperties }} parameters</td></tr>
                @if (tool.selectionAccuracy !== null) {
                  <tr><th>last evaluated</th><td>
                    selection accuracy {{ (tool.selectionAccuracy * 100).toFixed(0) }}%
                  </td></tr>
                }
              </tbody>
            </table>

            <div>
              <h3>Call it</h3>
              <p class="small muted">
                This goes through the same pipeline the model's calls go through: the schema is
                validated, entitlements are checked, and a tier 2 or 3 tool still stops at the
                approval gate. There is no back door.
              </p>
              <textarea rows="5" style="width: 100%" [value]="args()"
                        (input)="args.set($any($event.target).value)"></textarea>
              <div class="row" style="margin-top: 0.5rem">
                <button class="primary" (click)="call()" [disabled]="running()">
                  {{ running() ? 'calling...' : 'Invoke' }}
                </button>
                @if (result(); as outcome) {
                  <span class="pill" [class.ok]="outcome.ok" [class.bad]="!outcome.ok">
                    {{ outcome.ok ? 'ok' : 'refused' }} &middot; {{ outcome.latencyMs }}ms
                  </span>
                }
              </div>
              @if (result(); as outcome) {
                <pre style="margin-top: 0.75rem">{{ outcome.result | json }}</pre>
              }
            </div>

            <div>
              <h3>The descriptor, verbatim</h3>
              <p class="small muted">
                This is the file, not a rendering of it. The four fields at the top are what the
                model sees; everything under <code>_meta</code> never reaches it.
              </p>
              <pre>{{ descriptor() | json }}</pre>
            </div>
          </div>
        } @else {
          <p class="faint">Pick a tool.</p>
        }
      </section>
    </div>
  `,
  styles: `
    .layout { display: grid; grid-template-columns: 22rem 1fr; gap: 1.5rem; align-items: start; }
    .tool {
      display: flex;
      flex-direction: column;
      align-items: flex-start;
      gap: 0.25rem;
      width: 100%;
      text-align: left;
      margin-bottom: 0.4rem;
      padding: 0.6rem 0.7rem;
      border-color: var(--border);
    }
    .tool.on { border-color: var(--accent); background: var(--accent-soft); }
    th { width: 9rem; border-bottom-color: var(--border); }
    @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
  `,
})
export class CatalogPage {
  protected readonly api = inject(Api);
  protected readonly tools = signal<ToolCard[]>([]);
  protected readonly filter = signal('');
  protected readonly writesOnly = signal(false);
  protected readonly selected = signal<string | null>(null);
  protected readonly descriptor = signal<unknown>(null);
  protected readonly args = signal('{}');
  protected readonly result = signal<InvokeResult | null>(null);
  protected readonly running = signal(false);

  protected readonly visible = computed(() => {
    const needle = this.filter().toLowerCase();
    return this.tools().filter(
      (tool) =>
        (!this.writesOnly() || !tool.readOnly) &&
        (needle === '' ||
          tool.name.includes(needle) ||
          tool.entity.toLowerCase().includes(needle) ||
          tool.operation.includes(needle)),
    );
  });

  protected readonly card = computed(() =>
    this.tools().find((tool) => tool.name === this.selected()) ?? null,
  );

  constructor() {
    // Reloads whenever the role changes, which is the behavior the page is there to show.
    effect(() => {
      const role = this.api.role();
      this.api.catalog(role).subscribe((tools) => {
        this.tools.set(tools);
        if (!tools.some((tool) => tool.name === this.selected())) {
          this.select(tools[0]?.name ?? null);
        }
      });
    });
  }

  protected select(name: string | null): void {
    this.selected.set(name);
    this.result.set(null);
    this.descriptor.set(null);
    if (!name) {
      return;
    }
    this.api.descriptor(name).subscribe((descriptor) => {
      this.descriptor.set(descriptor);
      this.args.set(JSON.stringify(this.exampleFor(descriptor), null, 2));
    });
  }

  /**
   * A starting point for the arguments box, built from the input schema's required fields and
   * whatever example the schema offers. It is a convenience, not a validator: whatever is typed
   * here goes to the real pipeline and gets the real answer.
   */
  private exampleFor(descriptor: any): Record<string, unknown> {
    const schema = descriptor?.inputSchema ?? {};
    const example: Record<string, unknown> = {};
    for (const name of schema.required ?? []) {
      const property = schema.properties?.[name] ?? {};
      example[name] = this.sampleFor(property);
    }
    return example;
  }

  private sampleFor(property: any): unknown {
    if (property.enum) {
      return property.enum[0];
    }
    if (property.type === 'integer' || property.type === 'number') {
      return property.minimum ?? 1;
    }
    if (property.type === 'array') {
      return [this.sampleFor(property.items ?? {})];
    }
    if (property.type === 'boolean') {
      return false;
    }
    return property.examples?.[0] ?? '';
  }

  protected call(): void {
    const name = this.selected();
    if (!name) {
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(this.args() || '{}');
    } catch {
      this.result.set({
        tool: name,
        ok: false,
        latencyMs: 0,
        approvalReference: null,
        result: { error: 'The arguments box does not contain valid JSON.' },
      });
      return;
    }
    this.running.set(true);
    this.api.invoke(name, parsed).subscribe({
      next: (outcome) => {
        this.result.set(outcome);
        this.running.set(false);
      },
      error: (error) => {
        this.result.set({
          tool: name,
          ok: false,
          latencyMs: 0,
          approvalReference: null,
          result: { error: String(error?.message ?? error) },
        });
        this.running.set(false);
      },
    });
  }
}
