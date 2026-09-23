// Type definitions for Azure Doc Agent frontend
export type AnalysisStatus = 'completed' | 'partial' | 'insufficient' | 'failed';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  analysisStatus?: AnalysisStatus;
}

export interface ThreadHistoryMessage {
  user: string;
  assistant: string;
  timestamp: string;
  analysis_status?: AnalysisStatus;
}

export interface SkillInfo {
  name: string;
  description: string;
  tags: string[];
}

export interface SessionInfo {
  id: string;
  name: string;
  created_at: string;
  message_count: number;
}

export interface ThreadSummary {
  id: string;
  message_count: number;
  last_updated: string;
}

export interface RuntimeConfig {
  default_enable_ontology: boolean;
  ontology: {
    available: boolean;
    reasoner_enabled?: boolean;
    reasoner?: string;
    reasoning_status: string;
    reasoning_error?: string | null;
    file_count?: number;
    ontology_count?: number;
    entity_count?: number;
  };
}

export interface MySQLSettings {
  host: string;
  port: number;
  user: string;
  databases: string;
  password_set: boolean;
  active: boolean;
}

// ─── Ontology management (PRD P0/P1) ──────────────────────────────────────────

export interface OntologyFileInfo {
  id: string;
  name: string;
  enabled: boolean;
  protected: boolean;
  revision: string;
  modified_at: string;
  deleted: boolean;
  size: number;
  has_backup: boolean;
  active: boolean;
  change: 'added' | 'modified' | 'removed' | null;
  draft_only: boolean;
}

export interface OntologyHealth {
  available: boolean;
  reasoner_enabled: boolean;
  reasoner: string;
  reasoning_status: string;
  reasoning_error: string | null;
  file_count: number;
  ontology_count: number;
  entity_count: number;
}

export interface OntologyListing {
  files: OntologyFileInfo[];
  workspace_revision: string;
  active_revision: string;
  activated_at: string | null;
  has_changes: boolean;
  migration_error: { code: string; message: string } | null;
  health: OntologyHealth;
}

export interface ValidationIssue {
  code: string;
  message: string;
  file: string;
  severity: 'error' | 'warning';
}

export interface ValidationCheck {
  name: string;
  status: string;
}

export interface OntologyValidationReport {
  scope: string;
  valid: boolean;
  ontology_iri: string | null;
  class_count: number;
  property_count: number;
  individual_count: number;
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  checks: ValidationCheck[];
}

export interface OntologyMutationResult {
  operation_id: string;
  file_id: string;
  workspace_revision: string;
  revision: string;
  report?: OntologyValidationReport;
}

export interface OntologyActivateResult {
  active_revision: string;
  workspace_revision: string;
  changed: boolean;
  sessions_reset: boolean;
  warnings: string[];
  operation_id: string;
  elapsed_seconds?: number;
}

export interface OntologyOperation {
  operation_id: string;
  stage: string;
  status: 'running' | 'succeeded' | 'failed' | 'cancelled';
  elapsed_seconds?: number;
  result?: OntologyActivateResult;
}

export interface OntologyFileContent {
  id: string;
  version: string;
  name: string;
  content: string;
  revision: string;
  enabled: boolean;
  protected: boolean;
  modified_at: string;
  size: number;
}

export interface OntologyBackupInfo {
  id: string;
  name: string;
  content: string;
  enabled: boolean;
  created_at: string;
  sha256: string;
  file_revision: string;
}

export interface OntologyErrorDetail {
  code: string;
  message: string;
  issues?: OntologyValidationReport;
  current_revision?: string;
}

export interface GeneratorTableInfo {
  full_name: string;
  table_type: string;
  comment: string;
  supported: boolean;
}

export interface GeneratorTablesResult {
  tables: GeneratorTableInfo[];
  source_revision: string;
  source_identity: { type: string; host?: string; databases?: string[] };
  limits: { max_tables: number; max_columns: number };
}

export interface GeneratorDraftReport {
  table_count: number;
  column_count: number;
  adjusted_iri_count: number;
  missing_relations: { constraint: string; from: string; to: string }[];
  warnings: string[];
}

export interface GeneratorDraftResult {
  source_revision: string;
  content: string;
  filename_hint: string;
  namespace: string;
  report: GeneratorDraftReport;
}

// ─── Structured ontology view/edit ────────────────────────────────────────────

export interface OntologyLangText {
  lang: string;
  text: string;
}

export interface OntologyAnnotationValue {
  property: string;
  value: string;
  lang: string;
  datatype: string;
}

export interface OntologyIndividualValue {
  property: string;
  value: string;
  datatype: string;
  lang: string;
  is_ref: boolean;
}

export interface OntologyComplexAxiom {
  kind: string;
  involved: string[];
  note: string;
}

export type OntologyEntityKind =
  | 'class' | 'datatype_property' | 'object_property'
  | 'annotation_property' | 'datatype' | 'individual';

export interface OntologyStructureEntity {
  iri: string;
  local_name: string;
  kind: OntologyEntityKind;
  labels: OntologyLangText[];
  comments: OntologyLangText[];
  annotations: OntologyAnnotationValue[];
  superclasses?: string[];
  domain?: string[];
  range?: string[];
  characteristics?: string[];
  types?: string[];
  values?: OntologyIndividualValue[];
  complex_axioms: OntologyComplexAxiom[];
}

export interface OntologyStructureModel {
  ontology_iri: string;
  base: string | null;
  version_info: string | null;
  header: {
    labels: OntologyLangText[];
    comments: OntologyLangText[];
    annotations: OntologyAnnotationValue[];
  };
  namespaces: { prefix: string; uri: string }[];
  counts: {
    classes: number; datatype_properties: number; object_properties: number;
    annotation_properties: number; datatypes: number; individuals: number;
  };
  entities: OntologyStructureEntity[];
}

export interface StructureDiffSummary {
  added: string[];
  removed: string[];
  changed: { iri: string; fields: string[] }[];
  header_changes?: string[];
  warnings: string[];
}

export interface OntologyStructureFile {
  id: string;
  version: string;
  name: string;
  revision: string;
  model: OntologyStructureModel;
}

export interface OntologyStructureSaveResult extends OntologyMutationResult {
  changed: boolean;
  diff: StructureDiffSummary;
}
