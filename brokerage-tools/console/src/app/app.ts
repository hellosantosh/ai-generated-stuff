import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { Api, Role } from './api';

/**
 * The shell: a title, four tabs, and the role selector.
 *
 * <p>The role selector is in the shell rather than on one page because it is the console's main
 * point. The same fourteen descriptors, the same handlers and the same pipeline look like three
 * different products depending on one set of entitlements, and nothing is reconfigured to make
 * that happen.
 */
@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <header>
      <div class="brand">
        <strong>Brokerage Tools</strong>
        <span class="faint small">tool registry &amp; agent console</span>
      </div>
      <nav>
        <a routerLink="/catalog" routerLinkActive="on">Catalog</a>
        <a routerLink="/ontology" routerLinkActive="on">Ontology</a>
        <a routerLink="/agent" routerLinkActive="on">Agent</a>
        <a routerLink="/governance" routerLinkActive="on">Governance</a>
      </nav>
      <label class="role">
        <span class="faint small">session</span>
        <select [value]="api.role()" (change)="setRole($event)">
          <option value="RESEARCH">RESEARCH &mdash; market data only</option>
          <option value="SERVICE">SERVICE &mdash; reads, no trading</option>
          <option value="TRADING">TRADING &mdash; the full catalog</option>
        </select>
      </label>
    </header>
    <main>
      <router-outlet />
    </main>
  `,
  styles: `
    header {
      display: flex;
      align-items: center;
      gap: 1.5rem;
      flex-wrap: wrap;
      padding: 0.7rem 1.25rem;
      border-bottom: 1px solid var(--border);
      background: var(--surface);
      position: sticky;
      top: 0;
      z-index: 10;
    }
    .brand { display: flex; flex-direction: column; line-height: 1.2; }
    nav { display: flex; gap: 0.25rem; margin-right: auto; }
    nav a {
      padding: 0.3rem 0.65rem;
      border-radius: var(--radius);
      text-decoration: none;
      color: var(--muted);
      font-size: 0.9rem;
    }
    nav a:hover { background: var(--surface-2); color: var(--text); }
    nav a.on { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
    .role { display: flex; align-items: center; gap: 0.4rem; }
    main { padding: 1.5rem 1.25rem 4rem; max-width: 1180px; margin: 0 auto; }
    @media (max-width: 720px) {
      header { gap: 0.7rem; }
      nav { width: 100%; overflow-x: auto; }
      main { padding: 1rem 1rem 3rem; }
    }
  `,
})
export class App {
  protected readonly api = inject(Api);

  protected setRole(event: Event): void {
    this.api.role.set((event.target as HTMLSelectElement).value as Role);
  }
}
