import { HttpClient } from '@angular/common/http';
import { Injectable, signal } from '@angular/core';
import { Observable } from 'rxjs';

/** The three roles the service publishes. The console can pick one; it cannot invent scopes. */
export type Role = 'RESEARCH' | 'SERVICE' | 'TRADING';

/** One tool as the catalog page lists it: the governance a human needs, not the schema. */
export interface ToolCard {
  name: string;
  title: string;
  catalogId: string;
  riskTier: number;
  conformanceLevel: string;
  lifecycle: string;
  readOnly: boolean;
  destructive: boolean;
  idempotent: boolean;
  openWorld: boolean;
  approvalMode: string;
  entitlements: string[];
  owner: string;
  operation: string;
  entity: string;
  consumes: string[];
  produces: string[];
  costClass: string | null;
  p95LatencyMs: number | null;
  perMinute: number | null;
  killSwitch: string | null;
  enabled: boolean;
  selectionAccuracy: number | null;
  descriptionCharacters: number;
  inputProperties: number;
}

export interface Finding {
  rule: string;
  severity: 'ERROR' | 'WARNING' | 'INFO';
  tool: string;
  where: string | null;
  message: string;
}

/** The ontology, typed. A `Record<string, any>` would compile and then lie about every field. */
export interface Ontology {
  id: string;
  version: string;
  domain: string;
  description: string;
  identifierTypes: Record<string, IdentifierType>;
  entities: Record<string, Entity>;
  operations: Record<string, Operation>;
  capabilities: Capability[];
  policies: Policies;
}

export interface IdentifierType {
  description: string;
  pattern: string;
  example: string;
  opaque: boolean;
}

export interface Entity {
  label: string;
  term: string;
  collectionTerm: string;
  identifier: string;
  description: string;
  classification: string;
  entitlement: string;
  mutable: boolean;
  relations: Relation[];
}

export interface Relation {
  name: string;
  target: string;
  cardinality: string;
  description: string;
}

export interface Operation {
  gloss: string;
  readOnly: boolean;
  idempotent: boolean;
  baseRiskTier: number;
  semantics: string;
}

export interface Capability {
  tool: string;
  operation: string;
  entity: string;
  cardinality: string;
  consumes: string[];
  produces: string[];
  riskTier: number | null;
  note?: string;
}

export interface Policies {
  approvalByRiskTier: Record<string, string>;
  evidenceByRiskTier: Record<string, string>;
  maxTierWithoutApproval: number;
  dualControlAbove: string[];
}

export interface Graph {
  producers: Record<string, string[]>;
  prerequisites: Record<string, string[]>;
  unreachable: string[];
}

export interface Briefing {
  role: Role;
  visible: number;
  hidden: Record<string, string[]>;
  characters: number;
  briefing: string;
}

export interface TraceStep {
  step: number;
  tool: string;
  arguments: string;
  ok: boolean;
  errorCode: string | null;
  latencyMs: number;
  approvalReference: string | null;
}

export interface ChatReply {
  sessionId: string;
  reply: string;
  trace: TraceStep[];
  promptTokens: number | null;
  completionTokens: number | null;
  model: string;
}

export interface Pending {
  reference: string;
  tool: string;
  title: string;
  summary: string;
  riskTier: number;
  mode: string;
  requestedAt: string;
  expiresAt: string;
  sessionId: string;
}

export interface AuditRecord {
  requestId: string;
  at: string;
  tool: string;
  riskTier: number;
  auditEvent: string;
  retention: string;
  subject: string;
  sessionId: string;
  argumentFields: string[];
  outcome: string;
  latencyMs: number;
  approval: string | null;
  approvalMode: string | null;
}

export interface InvokeResult {
  tool: string;
  ok: boolean;
  latencyMs: number;
  approvalReference: string | null;
  result: unknown;
}

/**
 * Everything the console knows about the service.
 *
 * <p>The role lives here as a signal rather than in each page, because it is the one piece of
 * state every page cares about: the whole console is a demonstration that the same catalog looks
 * different to different sessions, and a role that reset when you changed tabs would hide that.
 */
@Injectable({ providedIn: 'root' })
export class Api {
  readonly role = signal<Role>('TRADING');
  readonly sessionId = signal<string>('console-' + Math.random().toString(36).slice(2, 9));

  constructor(private readonly http: HttpClient) {}

  catalog(role: Role): Observable<ToolCard[]> {
    return this.http.get<ToolCard[]>('/api/catalog', { params: { role } });
  }

  descriptor(name: string): Observable<Record<string, unknown>> {
    return this.http.get<Record<string, unknown>>(`/api/catalog/${name}`);
  }

  ontology(): Observable<Ontology> {
    return this.http.get<Ontology>('/api/catalog/ontology');
  }

  graph(): Observable<Graph> {
    return this.http.get<Graph>('/api/catalog/graph');
  }

  conformance(): Observable<Finding[]> {
    return this.http.get<Finding[]>('/api/catalog/conformance');
  }

  briefing(role: Role): Observable<Briefing> {
    return this.http.get<Briefing>('/api/catalog/briefing', { params: { role } });
  }

  invoke(tool: string, args: unknown): Observable<InvokeResult> {
    return this.http.post<InvokeResult>('/api/invoke', {
      tool,
      arguments: args,
      role: this.role(),
      sessionId: this.sessionId(),
    });
  }

  chat(message: string): Observable<ChatReply> {
    return this.http.post<ChatReply>('/api/chat', {
      sessionId: this.sessionId(),
      message,
      role: this.role(),
    });
  }

  chatStatus(): Observable<{ modelAvailable: boolean; model: string }> {
    return this.http.get<{ modelAvailable: boolean; model: string }>('/api/chat/status');
  }

  resetChat(): Observable<unknown> {
    return this.http.delete(`/api/chat/${this.sessionId()}`);
  }

  approvals(): Observable<Pending[]> {
    return this.http.get<Pending[]>('/api/approvals');
  }

  grant(reference: string): Observable<unknown> {
    return this.http.post(`/api/approvals/${reference}/grant`, {});
  }

  deny(reference: string): Observable<unknown> {
    return this.http.post(`/api/approvals/${reference}/deny`, {});
  }

  audit(limit = 50): Observable<AuditRecord[]> {
    return this.http.get<AuditRecord[]>('/api/audit', { params: { limit } });
  }

  toggleSwitch(name: string, off: boolean): Observable<unknown> {
    return this.http.post(`/api/catalog/switches/${name}`, null, {
      params: { off },
    });
  }
}
