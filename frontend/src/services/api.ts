import axios from 'axios';
import type {
  AnalysisStatus,
  GeneratorDraftResult,
  GeneratorTablesResult,
  MySQLSettings,
  OntologyActivateResult,
  OntologyBackupInfo,
  OntologyErrorDetail,
  OntologyFileContent,
  OntologyListing,
  OntologyMutationResult,
  OntologyOperation,
  OntologyStructureFile,
  OntologyStructureModel,
  OntologyStructureSaveResult,
  OntologyValidationReport,
  RuntimeConfig,
  SkillInfo,
  StructureDiffSummary,
  ThreadSummary,
  ThreadHistoryMessage,
} from '../types';

export function parseAnalysisStatus(value: unknown): AnalysisStatus | undefined {
  return value === 'completed' || value === 'partial' || value === 'insufficient' || value === 'failed'
    ? value : undefined;
}

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api';

const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

/** Error carrying the backend's structured detail={code,message,...} body. */
export class ApiDetailError extends Error {
  constructor(public detail: OntologyErrorDetail) {
    super(detail?.message || '请求失败');
  }
}

export function extractErrorDetail(error: unknown): OntologyErrorDetail {
  if (error instanceof ApiDetailError) return error.detail;
  if (axios.isAxiosError<{ detail?: OntologyErrorDetail }>(error)) {
    const detail = error.response?.data?.detail;
    if (detail && typeof detail.code === 'string') return detail;
    return { code: 'http_error', message: detail ? String((detail as { message?: string }).message ?? '') || '请求失败' : `请求失败（${error.response?.status ?? '网络错误'}）` };
  }
  return { code: 'unknown', message: error instanceof Error ? error.message : String(error) };
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const container = body as { detail?: OntologyErrorDetail } | null;
    throw new ApiDetailError(container?.detail ?? { code: 'http_error', message: `请求失败（${response.status}）` });
  }
  return body as T;
}

export const apiService = {
  async getMySQLSettings(): Promise<MySQLSettings> {
    const response = await apiClient.get<MySQLSettings>('/config/mysql');
    return response.data;
  },

  async saveMySQLSettings(settings: Pick<MySQLSettings, 'host' | 'port' | 'user' | 'databases'> & { password: string }): Promise<void> {
    await apiClient.put('/config/mysql', settings);
  },
  // Skills endpoints
  async listSkills(): Promise<SkillInfo[]> {
    const response = await apiClient.get<SkillInfo[]>('/skills');
    return response.data;
  },

  // Thread endpoints
  async listThreads(): Promise<ThreadSummary[]> {
    const response = await apiClient.get<ThreadSummary[]>('/threads');
    return response.data;
  },

  async getThreadHistory(threadId: string): Promise<{ thread_id: string; messages: ThreadHistoryMessage[] }> {
    const response = await apiClient.get<{ thread_id: string; messages: ThreadHistoryMessage[] }>(`/threads/${threadId}/history`);
    return response.data;
  },

  async createThread(threadId?: string): Promise<{ thread_id: string; created_at: string }> {
    const response = await apiClient.post('/threads/new', { thread_id: threadId });
    return response.data;
  },

  async deleteThread(threadId: string): Promise<void> {
    await apiClient.delete(`/threads/${threadId}`);
  },

  async stopThread(threadId: string): Promise<{ thread_id: string; stopped: boolean }> {
    const response = await apiClient.post(`/threads/${threadId}/stop`);
    return response.data;
  },

  // Workspace business semantic layer (shared by every session)
  async getBusinessLayer(): Promise<{ content: string }> {
    const response = await apiClient.get<{ content: string }>('/business-layer');
    return response.data;
  },

  async saveBusinessLayer(content: string): Promise<{ ok: boolean; length: number }> {
    const response = await apiClient.put<{ ok: boolean; length: number }>('/business-layer', { content });
    return response.data;
  },

  // Health check
  async healthCheck(): Promise<{ status: string; agent_initialized: boolean }> {
    const response = await apiClient.get('/health');
    return response.data;
  },

  async getRuntimeConfig(): Promise<RuntimeConfig> {
    const response = await apiClient.get<RuntimeConfig>('/config');
    return response.data;
  },

  // ─── Ontology management (PRD P0) ─────────────────────────────────────────
  async listOntologyFiles(): Promise<OntologyListing> {
    const response = await apiClient.get<OntologyListing>('/ontology/files');
    return response.data;
  },

  async getOntologyFile(fileId: string, version: 'workspace' | 'active' = 'workspace'): Promise<OntologyFileContent> {
    const response = await apiClient.get<OntologyFileContent>(`/ontology/files/${fileId}`, { params: { version } });
    return response.data;
  },

  async saveOntologyFile(fileId: string, content: string, expectedWorkspace: string, expectedFile: string): Promise<OntologyMutationResult> {
    const response = await apiClient.put<OntologyMutationResult>(`/ontology/files/${fileId}`, {
      content, expected_workspace: expectedWorkspace, expected_file: expectedFile,
    });
    return response.data;
  },

  async deleteOntologyFile(fileId: string, expectedWorkspace: string, expectedFile: string): Promise<OntologyMutationResult> {
    const response = await apiClient.delete<OntologyMutationResult>(`/ontology/files/${fileId}`, {
      headers: { 'X-Expected-Workspace': expectedWorkspace, 'X-Expected-File': expectedFile },
    });
    return response.data;
  },

  async validateOntology(body: { content?: string; name?: string; file_id?: string; scope?: 'file' | 'publication' }): Promise<OntologyValidationReport> {
    const response = await apiClient.post<OntologyValidationReport>('/ontology/validate', body);
    return response.data;
  },

  async setOntologyEnabled(fileId: string, enabled: boolean, expectedWorkspace: string, expectedFile: string): Promise<OntologyMutationResult> {
    const response = await apiClient.put<OntologyMutationResult>(`/ontology/files/${fileId}/enabled`, {
      enabled, expected_workspace: expectedWorkspace, expected_file: expectedFile,
    });
    return response.data;
  },

  async getOntologyBackup(fileId: string): Promise<OntologyBackupInfo> {
    const response = await apiClient.get<OntologyBackupInfo>(`/ontology/files/${fileId}/backup`);
    return response.data;
  },

  async restoreOntologyFile(fileId: string, expectedWorkspace: string, expectedFile: string): Promise<OntologyMutationResult> {
    const response = await apiClient.post<OntologyMutationResult>(`/ontology/files/${fileId}/restore`, {
      expected_workspace: expectedWorkspace, expected_file: expectedFile,
    });
    return response.data;
  },

  async getOntologyOperation(operationId: string): Promise<OntologyOperation> {
    return (await apiClient.get<OntologyOperation>(`/ontology/operations/${operationId}`)).data;
  },

  async activateOntology(expectedWorkspace: string, expectedActive: string, operationId?: string): Promise<OntologyActivateResult> {
    const response = await apiClient.post<OntologyActivateResult>('/ontology/activate', {
      expected_workspace: expectedWorkspace, expected_active: expectedActive,
    }, { headers: operationId ? { 'X-Operation-Id': operationId } : {} });
    return response.data;
  },

  /**
   * Multipart upload. Uses raw fetch on purpose: the shared axios instance has a
   * global JSON Content-Type default, which would make axios JSON-serialize the
   * FormData instead of sending multipart.
   */
  async uploadOntologyFile(payload: {
    name: string;
    content: string;
    file?: File;
    source_revision?: string;
    overwrite?: boolean;
    file_id?: string;
    expected_workspace: string;
    expected_file?: string;
  }): Promise<OntologyMutationResult> {
    const form = new FormData();
    form.append('file', payload.file ?? new Blob([payload.content], { type: 'application/rdf+xml' }), payload.name);
    if (payload.source_revision) form.append('source_revision', payload.source_revision);
    form.append('name', payload.name);
    if (payload.overwrite) form.append('overwrite', 'true');
    if (payload.file_id) form.append('file_id', payload.file_id);
    form.append('expected_workspace', payload.expected_workspace);
    if (payload.expected_file) form.append('expected_file', payload.expected_file);
    return fetchJson<OntologyMutationResult>(`${API_BASE_URL}/ontology/files`, { method: 'POST', body: form });
  },

  // ─── MySQL schema draft generation (PRD P1) ────────────────────────────────
  async listGeneratorTables(): Promise<GeneratorTablesResult> {
    const response = await apiClient.get<GeneratorTablesResult>('/ontology/generate-draft/tables');
    return response.data;
  },

  async generateOntologyDraft(tables: string[], sourceRevision: string, namespace?: string): Promise<GeneratorDraftResult> {
    const response = await apiClient.post<GeneratorDraftResult>('/ontology/generate-draft', {
      tables, source_revision: sourceRevision, ...(namespace ? { namespace } : {}),
    });
    return response.data;
  },

  // ─── Structured view/edit (classes, properties, relationships) ─────────────
  async getOntologyStructure(fileId: string, version: 'workspace' | 'active' | 'backup' = 'workspace'): Promise<OntologyStructureFile> {
    const response = await apiClient.get<OntologyStructureFile>(`/ontology/files/${fileId}/structure`, { params: { version } });
    return response.data;
  },

  async putOntologyStructure(fileId: string, model: OntologyStructureModel, expectedWorkspace: string, expectedFile: string): Promise<OntologyStructureSaveResult> {
    const response = await apiClient.put<OntologyStructureSaveResult>(`/ontology/files/${fileId}/structure`, {
      model, expected_workspace: expectedWorkspace, expected_file: expectedFile,
    });
    return response.data;
  },

  async parseOntologyStructure(content: string): Promise<{ model: OntologyStructureModel }> {
    const response = await apiClient.post<{ model: OntologyStructureModel }>('/ontology/structure/parse', { content });
    return response.data;
  },

  async serializeOntologyStructure(content: string, model: OntologyStructureModel): Promise<{ content: string; diff: StructureDiffSummary }> {
    const response = await apiClient.post<{ content: string; diff: StructureDiffSummary }>('/ontology/structure/serialize', { content, model });
    return response.data;
  },
};

/** Direct download URL for a file version; the server sets the attachment name. */
export function ontologyDownloadUrl(fileId: string, version: 'workspace' | 'active' | 'backup'): string {
  if (version === 'backup') return `${API_BASE_URL}/ontology/files/${fileId}/backup?download=true`;
  return `${API_BASE_URL}/ontology/files/${fileId}?version=${version}&download=true`;
}
