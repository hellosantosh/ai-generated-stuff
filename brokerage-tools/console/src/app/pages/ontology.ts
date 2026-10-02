import { Component, computed, inject, signal } from '@angular/core';
import { Api, Capability, Graph, Ontology } from '../api';

/**
 * The ontology page: what this catalog is supposed to cover, and what has to happen before what.
 *
 * <p>Two things here are worth a reviewer's time and are not visible anywhere else.
 *
 * <p>The <strong>capability table</strong> is the catalog's specification. Each row is an
 * (operation, entity) pair with the identifiers it needs and the identifiers it hands back, and
 * the tool name is derived from the row rather than chosen. A question like "is this catalog
 * complete" becomes a question about this table instead of a question about somebody's memory.
 *
 * <p>The <strong>producer graph</strong> is the answer to "how does the agent know what to call
 * first". It is drawn from the same two maps the generated system briefing is written from, so
 * what the picture says and what the model is told cannot disagree. The edge from
 * {@code brokerage_order_preview} to the three tools that change something is the one to look at:
 * it is why preview-before-place is structural here rather than advisory.
 */
@Component({
  selector: 'app-ontology',
  template: `
    @if (ontology(); as model) {
      <h1>{{ model.id }} <span class="faint small">v{{ model.version }}</span></h1>
      <p class="muted">{{ model.description }}</p>

      <h2>Identifier types</h2>
      <p class="muted small">
        Declared once, referenced by every schema. This is what stops one descriptor accepting
        <code>^acct_[A-Z0-9]+$</code> while its neighbour accepts <code>^ACC-\\d&#123;4&#125;$</code>.
      </p>
      <table>
        <thead>
          <tr><th>type</th><th>pattern</th><th>example</th><th>produced by</th></tr>
        </thead>
        <tbody>
          @for (entry of identifiers(); track entry.name) {
            <tr>
              <td><code>{{ entry.name }}</code>
                @if (entry.opaque) { <span class="pill">opaque</span> }
              </td>
              <td><code class="small">{{ entry.pattern }}</code></td>
              <td><code class="small">{{ entry.example }}</code></td>
              <td class="small">
                @if (entry.producers.length) {
                  @for (tool of entry.producers; track tool) {
                    <div><code>{{ tool }}</code></div>
                  }
                } @else {
                  <span class="pill bad">nothing</span>
                }
              </td>
            </tr>
          }
        </tbody>
      </table>

      <h2>The capability table</h2>
      <p class="muted small">
        The tool name is derived from the entity and the operation, which is why
        <code>brokerage_order_cancel</code> could not have been called <code>cancelOrder</code>.
        The risk tier comes from the operation, and every exception is an override with a note.
      </p>
      <table>
        <thead>
          <tr>
            <th>tool</th><th>operation</th><th>entity</th><th>card.</th>
            <th>consumes</th><th>produces</th><th>tier</th>
          </tr>
        </thead>
        <tbody>
          @for (capability of model.capabilities; track capability.tool) {
            <tr>
              <td><code>{{ capability.tool }}</code>
                @if (capability.note) {
                  <div class="faint small">{{ capability.note }}</div>
                }
              </td>
              <td>{{ capability.operation }}</td>
              <td>{{ capability.entity }}</td>
              <td class="faint">{{ capability.cardinality }}</td>
              <td class="small">{{ capability.consumes.join(', ') || '&mdash;' }}</td>
              <td class="small">{{ capability.produces.join(', ') || '&mdash;' }}</td>
              <td>
                <span class="tier"
                      [class]="'tier tier-' + tierOf(capability)">T{{ tierOf(capability) }}</span>
              </td>
            </tr>
          }
        </tbody>
      </table>

      <h2>The producer graph</h2>
      <p class="muted small">
        An arrow from A to B means B needs a value only A can give it. Read the right-hand column
        first: three tools change something, and all three sit behind
        <code>brokerage_order_preview</code>.
      </p>
      @if (graph(); as edges) {
        <div class="card" style="overflow-x: auto">
          <svg [attr.viewBox]="'0 0 860 ' + height()" [attr.height]="height()"
               width="100%" role="img"
               aria-label="Which tool produces the identifiers each other tool needs">
            <defs>
              <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5"
                      markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" opacity="0.45" />
              </marker>
            </defs>
            @for (edge of lines(); track edge.key) {
              <path [attr.d]="edge.path" fill="none" stroke="currentColor" stroke-width="1"
                    opacity="0.35" marker-end="url(#arrow)" />
            }
            @for (node of nodes(); track node.name) {
              <g>
                <!-- Two rects: an opaque base so the edges behind do not show through the label,
                     then the tier tint on top of it. -->
                <rect [attr.x]="node.x" [attr.y]="node.y" [attr.width]="node.width" height="26"
                      rx="4" fill="var(--surface)" />
                <rect [attr.x]="node.x" [attr.y]="node.y" [attr.width]="node.width" height="26"
                      rx="4" [attr.fill]="'var(--tier-' + node.tier + ')'" opacity="0.16"
                      [attr.stroke]="'var(--tier-' + node.tier + ')'" stroke-width="1" />
                <text [attr.x]="node.x + 9" [attr.y]="node.y + 17"
                      font-family="var(--mono)" font-size="11" fill="currentColor">
                  {{ node.label }}
                </text>
              </g>
            }
            @for (column of columns(); track column.x) {
              <text [attr.x]="column.x" y="14" font-size="10" fill="currentColor" opacity="0.5"
                    font-family="var(--sans)">{{ column.label }}</text>
            }
          </svg>
        </div>
        @if (edges.unreachable.length) {
          <p class="pill bad">
            Unreachable identifiers: {{ edges.unreachable.join(', ') }}
          </p>
        }
      }

      <h2>Entities</h2>
      <table>
        <thead>
          <tr><th>entity</th><th>terms</th><th>identifier</th><th>classification</th>
              <th>entitlement</th><th>relations</th></tr>
        </thead>
        <tbody>
          @for (entity of entities(); track entity.name) {
            <tr>
              <td><strong>{{ entity.label }}</strong>
                <div class="faint small">{{ entity.description }}</div>
              </td>
              <td class="small"><code>{{ entity.term }}</code> /
                <code>{{ entity.collectionTerm }}</code></td>
              <td class="small"><code>{{ entity.identifier }}</code></td>
              <td class="small">{{ entity.classification }}</td>
              <td class="small"><code>{{ entity.entitlement }}</code></td>
              <td class="small">
                @for (relation of entity.relations; track relation.name) {
                  <div>{{ relation.name }} &rarr; {{ relation.target }}</div>
                }
              </td>
            </tr>
          }
        </tbody>
      </table>

      <h2>Operations</h2>
      <table>
        <thead><tr><th>verb</th><th>base tier</th><th>read-only</th><th>what it means</th></tr></thead>
        <tbody>
          @for (operation of operations(); track operation.name) {
            <tr>
              <td><code>{{ operation.name }}</code></td>
              <td><span class="tier" [class]="'tier tier-' + operation.baseRiskTier">
                T{{ operation.baseRiskTier }}</span></td>
              <td>{{ operation.readOnly ? 'yes' : 'no' }}</td>
              <td>
                {{ operation.gloss }}
                <div class="faint small">{{ operation.semantics }}</div>
              </td>
            </tr>
          }
        </tbody>
      </table>
    } @else {
      <p class="faint">Loading the ontology...</p>
    }
  `,
  styles: `
    th { border-bottom-color: var(--border-strong); }
    table { margin-bottom: 1.5rem; }
    svg { display: block; min-width: 860px; }
  `,
})
export class OntologyPage {
  private readonly api = inject(Api);
  protected readonly ontology = signal<Ontology | null>(null);
  protected readonly graph = signal<Graph | null>(null);

  /** Column x positions, one per step in the chain the agent walks. */
  private static readonly COLUMNS = [
    { x: 8, label: 'needs nothing' },
    { x: 226, label: 'needs an account or a symbol' },
    { x: 444, label: 'needs an order' },
    { x: 662, label: 'needs a confirmation' },
  ];

  constructor() {
    this.api.ontology().subscribe((model) => this.ontology.set(model));
    this.api.graph().subscribe((graph) => this.graph.set(graph));
  }

  protected readonly identifiers = computed(() => {
    const model = this.ontology();
    const producers = this.graph()?.producers ?? {};
    if (!model) {
      return [];
    }
    return Object.entries(model.identifierTypes).map(([name, value]) => ({
      name,
      pattern: value.pattern,
      example: value.example,
      opaque: value.opaque,
      producers: producers[name] ?? [],
    }));
  });

  protected readonly entities = computed(() => {
    const model = this.ontology();
    return model
      ? Object.entries(model.entities).map(([name, value]) => ({ name, ...value }))
      : [];
  });

  protected readonly operations = computed(() => {
    const model = this.ontology();
    return model
      ? Object.entries(model.operations).map(([name, value]) => ({ name, ...value }))
      : [];
  });

  protected tierOf(capability: Capability): number {
    const model = this.ontology();
    return capability.riskTier ?? model?.operations[capability.operation]?.baseRiskTier ?? 0;
  }

  /**
   * Lay the tools out in columns by how far they are from needing nothing, which is the order an
   * agent discovers them in. A tool with no prerequisites is column 0; everything else is one
   * past the deepest tool it depends on.
   */
  protected readonly nodes = computed(() => {
    const model = this.ontology();
    const prerequisites = this.graph()?.prerequisites ?? {};
    if (!model) {
      return [];
    }
    const depth = new Map<string, number>();
    const depthOf = (tool: string, seen: Set<string>): number => {
      if (depth.has(tool)) {
        return depth.get(tool)!;
      }
      if (seen.has(tool)) {
        return 0;
      }
      seen.add(tool);
      const needs = (prerequisites[tool] ?? []).filter((other) => other !== tool);
      const value = needs.length
        ? Math.min(3, 1 + Math.max(...needs.map((other) => depthOf(other, seen))))
        : 0;
      depth.set(tool, value);
      return value;
    };

    const rows = new Map<number, number>();
    return model.capabilities.map((capability) => {
      const column = depthOf(capability.tool, new Set());
      const row = rows.get(column) ?? 0;
      rows.set(column, row + 1);
      const layout = OntologyPage.COLUMNS[column];
      return {
        name: capability.tool,
        label: capability.tool.replace('brokerage_', ''),
        tier: this.tierOf(capability),
        x: layout.x,
        y: 26 + row * 34,
        width: 186,
      };
    });
  });

  protected readonly height = computed(() => {
    const nodes = this.nodes();
    return nodes.length ? Math.max(...nodes.map((node) => node.y)) + 44 : 80;
  });

  protected readonly columns = computed(() =>
    OntologyPage.COLUMNS.filter((column) => this.nodes().some((node) => node.x === column.x)),
  );

  protected readonly lines = computed(() => {
    const prerequisites = this.graph()?.prerequisites ?? {};
    const byName = new Map(this.nodes().map((node) => [node.name, node]));
    const edges: { key: string; path: string }[] = [];
    for (const [tool, needs] of Object.entries(prerequisites)) {
      const target = byName.get(tool);
      if (!target) {
        continue;
      }
      for (const need of needs) {
        const source = byName.get(need);
        // Only draw the edges that move rightward: the backward ones are the same fact again.
        if (!source || source.x >= target.x) {
          continue;
        }
        const x1 = source.x + source.width;
        const y1 = source.y + 13;
        const x2 = target.x;
        const y2 = target.y + 13;
        const bend = (x2 - x1) / 2;
        edges.push({
          key: need + '>' + tool,
          path: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2 - 3} ${y2}`,
        });
      }
    }
    return edges;
  });
}
