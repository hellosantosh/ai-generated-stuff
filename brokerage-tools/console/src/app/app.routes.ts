import { Routes } from '@angular/router';

/**
 * Four pages, each answering a question that is otherwise hard to answer about a tool catalog.
 * Lazily loaded, so the page a reviewer opens is the only one that is fetched.
 */
export const routes: Routes = [
  { path: '', redirectTo: 'catalog', pathMatch: 'full' },
  {
    path: 'catalog',
    title: 'Catalog',
    loadComponent: () => import('./pages/catalog').then((m) => m.CatalogPage),
  },
  {
    path: 'ontology',
    title: 'Ontology',
    loadComponent: () => import('./pages/ontology').then((m) => m.OntologyPage),
  },
  {
    path: 'agent',
    title: 'Agent',
    loadComponent: () => import('./pages/agent').then((m) => m.AgentPage),
  },
  {
    path: 'governance',
    title: 'Governance',
    loadComponent: () => import('./pages/governance').then((m) => m.GovernancePage),
  },
  { path: '**', redirectTo: 'catalog' },
];
