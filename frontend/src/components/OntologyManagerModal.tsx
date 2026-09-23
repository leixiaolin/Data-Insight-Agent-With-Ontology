import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  apiService,
  extractErrorDetail,
  ontologyDownloadUrl,
} from '../services/api';
import { OntologyStructureEditor } from './OntologyStructureEditor';
import type {
  GeneratorDraftResult,
  GeneratorTablesResult,
  OntologyErrorDetail,
  OntologyFileInfo,
  OntologyListing,
  OntologyStructureModel,
  OntologyValidationReport,
} from '../types';

interface Props {
  onClose: () => void;
  /** Called after a successful activation that reset server-side sessions. */
  onActivated: () => void;
}

const CHANGE_LABELS: Record<string, string> = {
  added: '新增待激活',
  modified: '修改待激活',
  removed: '已移除待激活',
};

function formatSize(size: number): string {
  if (size >= 1024 * 1024) return `${(size / 1024 / 1024).toFixed(1)} MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${size} B`;
}

function formatDate(value: string | null): string {
  if (!value) return '从未激活';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function ValidationReportView({ report }: { report: OntologyValidationReport }) {
  return (
    <div className="ontology-report" role="region" aria-label="校验报告">
      <div className={`ontology-report-summary ${report.valid ? 'ok' : 'bad'}`}>
        {report.valid ? '校验通过' : '校验未通过'} · 类 {report.class_count} · 属性 {report.property_count} · 个体 {report.individual_count}
        {report.ontology_iri ? ` · ${report.ontology_iri}` : ''}
      </div>
      {report.checks.length > 0 && (
        <div className="ontology-report-checks">
          {report.checks.map(check => (
            <span key={check.name} className={`check chip-${check.status}`}>
              {check.name}: {check.status}
            </span>
          ))}
        </div>
      )}
      {report.errors.map((issue, index) => (
        <div key={`e${index}`} className="ontology-report-issue error">
          <span className="issue-code">{issue.code}</span> {issue.message}
          {issue.file ? <span className="issue-file">（{issue.file}）</span> : null}
        </div>
      ))}
      {report.warnings.map((issue, index) => (
        <div key={`w${index}`} className="ontology-report-issue warning">
          <span className="issue-code">{issue.code}</span> {issue.message}
          {issue.file ? <span className="issue-file">（{issue.file}）</span> : null}
        </div>
      ))}
    </div>
  );
}

export function OntologyManagerModal({ onClose, onActivated }: Props) {
  const [listing, setListing] = useState<OntologyListing | null>(null);
  const [showDeleted, setShowDeleted] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedEntry, setSelectedEntry] = useState<OntologyFileInfo | null>(null);
  const [editorText, setEditorText] = useState('');
  const [loadedText, setLoadedText] = useState('');
  const [loadedRevision, setLoadedRevision] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('正在加载…');
  const [report, setReport] = useState<OntologyValidationReport | null>(null);
  const [compareText, setCompareText] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<OntologyFileInfo | null>(null);
  const [confirmActivate, setConfirmActivate] = useState(false);
  const [activationReport, setActivationReport] = useState<OntologyValidationReport | null>(null);
  const [uploadName, setUploadName] = useState('');
  const [uploadOverwrite, setUploadOverwrite] = useState(false);
  const [tab, setTab] = useState<'files' | 'generate'>('files');
  const [structureModel, setStructureModel] = useState<OntologyStructureModel | null>(null);
  const [structureOriginal, setStructureOriginal] = useState<OntologyStructureModel | null>(null);
  const [viewMode, setViewMode] = useState<'structure' | 'xml'>('structure');
  const [draftView, setDraftView] = useState<'structure' | 'xml'>('structure');
  const [draftModel, setDraftModel] = useState<OntologyStructureModel | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ─── P1 generation state ──────────────────────────────────────────────────
  const [generatorTables, setGeneratorTables] = useState<GeneratorTablesResult | null>(null);
  const [generatorStatus, setGeneratorStatus] = useState('');
  const [generatorBusy, setGeneratorBusy] = useState(false);
  const [generatorAttempted, setGeneratorAttempted] = useState(false);
  const [selectedTables, setSelectedTables] = useState<Set<string>>(new Set());
  const [namespace, setNamespace] = useState('');
  const [draft, setDraft] = useState<GeneratorDraftResult | null>(null);
  const [draftName, setDraftName] = useState('');
  const [draftOverwrite, setDraftOverwrite] = useState(false);

  const refresh = useCallback(async (keepSelection = true): Promise<OntologyListing | null> => {
    try {
      const next = await apiService.listOntologyFiles();
      setListing(next);
      if (!keepSelection) setSelectedId(null);
      return next;
    } catch (error) {
      setStatus(`无法加载本体列表：${extractErrorDetail(error).message}`);
      return null;
    }
  }, []);

  useEffect(() => {
    void refresh(false).then(value => {
      if (value) setStatus('');
    });
  }, [refresh]);

  const loadFile = useCallback(async (fileId: string) => {
    setBusy(true);
    setStatus('正在读取文件…');
    try {
      const entry = listing?.files.find(item => item.id === fileId);
      const file = entry?.deleted
        ? { ...(await apiService.getOntologyBackup(fileId)), revision: entry.revision }
        : await apiService.getOntologyFile(fileId);
      setSelectedId(fileId);
      setEditorText(file.content);
      setLoadedText(file.content);
      setLoadedRevision(file.revision);
      setReport(null);
      setCompareText(null);
      setViewMode('structure');
      try {
        // Parse exactly the content/revision we loaded, not a second server snapshot.
        const structure = await apiService.parseOntologyStructure(file.content);
        setStructureModel(structure.model);
        setStructureOriginal(structure.model);
      } catch {
        setStructureModel(null);
        setStructureOriginal(null);
      }
      setStatus('');
    } catch (error) {
      setStatus(`无法读取文件：${extractErrorDetail(error).message}`);
    } finally {
      setBusy(false);
    }
  }, [listing]);

  useEffect(() => {
    if (!listing || !selectedId) {
      setSelectedEntry(null);
      return;
    }
    setSelectedEntry(listing.files.find(item => item.id === selectedId) ?? null);
  }, [listing, selectedId]);

  const visibleFiles = useMemo(() => {
    if (!listing) return [];
    return listing.files.filter(item => showDeleted || !item.deleted);
  }, [listing, showDeleted]);

  const pendingChanges = useMemo(() => {
    if (!listing) return [];
    return listing.files.filter(item => item.change);
  }, [listing]);

  const runMutation = useCallback(async (
    action: () => Promise<unknown>,
    successMessage: string,
  ): Promise<boolean> => {
    setBusy(true);
    setStatus('正在提交…');
    try {
      await action();
      await refresh();
      setStatus(successMessage);
      return true;
    } catch (error) {
      const detail: OntologyErrorDetail = extractErrorDetail(error);
      if (detail.issues) setReport(detail.issues);
      if (detail.code === 'revision_conflict' && selectedId) {
        // Keep the local text; fetch the server copy for a read-only comparison.
        try {
          const server = await apiService.getOntologyFile(selectedId);
          setCompareText(server.content);
        } catch {
          setCompareText(null);
        }
        setStatus('版本已变化：本地编辑已保留，请对照服务器版本后刷新重试。');
      } else {
        setStatus(detail.message);
      }
      return false;
    } finally {
      setBusy(false);
    }
  }, [refresh, selectedId]);

  const structureDirty = structureModel != null && structureModel !== structureOriginal;
  const editorDirty = structureDirty || editorText !== loadedText;

  const currentContent = async () => viewMode === 'structure' && structureModel && structureDirty
    ? (await apiService.serializeOntologyStructure(editorText, structureModel)).content
    : editorText;

  const saveSelected = async () => {
    if (!selectedEntry || !listing) return;
    const saved = await runMutation(
      async () => apiService.saveOntologyFile(
        selectedEntry.id, await currentContent(), listing.workspace_revision, loadedRevision),
      '已保存到工作副本（未激活）。',
    );
    if (saved && selectedId) {
      await loadFile(selectedId);
    }
  };

  const switchViewMode = async (mode: 'structure' | 'xml') => {
    if (busy || mode === viewMode) return;
    setBusy(true);
    setStatus('正在切换编辑视图…');
    try {
      if (mode === 'xml') {
        setEditorText(await currentContent());
        setStructureOriginal(structureModel);
      } else {
        const result = await apiService.parseOntologyStructure(editorText);
        setStructureModel(result.model);
        setStructureOriginal(result.model);
      }
      setViewMode(mode);
      setReport(null);
      setStatus('视图已切换，修改仍保留在本地；保存后写入工作副本。');
    } catch (error) {
      // Stay in the current view with the draft intact when conversion fails.
      setStatus(`无法切换视图，编辑内容已保留：${extractErrorDetail(error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const validateSelected = async () => {
    setBusy(true);
    setStatus('正在校验…');
    try {
      const baseline = await currentContent();
      const result = await apiService.validateOntology({
        content: baseline,
        name: selectedEntry?.name,
        ...(selectedEntry && !selectedEntry.deleted && selectedEntry.enabled
          ? { file_id: selectedEntry.id, scope: 'publication' as const }
          : {}),
      });
      setReport(result);
      setStatus(result.valid ? '校验通过。' : '校验发现错误，详见报告。');
    } catch (error) {
      setStatus(`校验失败：${extractErrorDetail(error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const toggleEnabled = async (file: OntologyFileInfo) => {
    if (!listing) return;
    await runMutation(
      () => apiService.setOntologyEnabled(file.id, !file.enabled, listing.workspace_revision, file.revision),
      file.enabled ? '已停用（不影响当前发布版本）。' : '已启用；激活后才会生效。',
    );
  };

  const deleteSelected = async (file: OntologyFileInfo) => {
    if (!listing) return;
    setPendingDelete(null);
    const done = await runMutation(
      () => apiService.deleteOntologyFile(file.id, listing.workspace_revision, file.revision),
      '已删除；可从“已删除”列表恢复。',
    );
    if (done && selectedId === file.id) {
      setSelectedId(null);
      setEditorText('');
    }
  };

  const restoreSelected = async (file: OntologyFileInfo) => {
    if (!listing) return;
    const done = await runMutation(
      () => apiService.restoreOntologyFile(file.id, listing.workspace_revision, file.revision),
      '已恢复到工作副本（不会自动激活；激活的是整个启用集）。',
    );
    if (done) await loadFile(file.id);
  };

  const activateFlow = async () => {
    if (!listing) return;
    setConfirmActivate(false);
    setBusy(true);
    setStatus('正在激活…');
    const operationId = crypto.randomUUID().replace(/-/g, '');
    const timer = window.setInterval(() => {
      void apiService.getOntologyOperation(operationId).then(operation => {
        setStatus(`操作 ${operationId.slice(0, 8)} · ${operation.stage} · ${operation.status}`);
      }).catch(() => { /* The request may not have reached the server yet. */ });
    }, 700);
    try {
      const result = await apiService.activateOntology(listing.workspace_revision, listing.active_revision, operationId);
      await refresh();
      if (result.changed && result.sessions_reset) {
        setStatus(`激活成功（${result.elapsed_seconds ?? '?'}s，操作 ${operationId.slice(0, 8)}）。所有会话已重置。${result.warnings.join('；')}`);
        onActivated();
      } else {
        setStatus('没有待激活的差异，当前发布版本保持不变。');
      }
    } catch (error) {
      const detail = extractErrorDetail(error);
      const current = await refresh();
      if (current && current.active_revision !== listing.active_revision) {
        setStatus('发布版本已改变，已按服务端实际状态同步会话。');
        onActivated();
        return;
      }
      setStatus(detail.code === 'active_queries'
        ? '有查询正在进行，请等待结束后再激活。'
        : `激活失败：${detail.message}`);
    } finally {
      window.clearInterval(timer);
      setBusy(false);
    }
  };

  const reviewActivation = async () => {
    setBusy(true);
    setStatus('正在校验整个启用集…');
    try {
      const result = await apiService.validateOntology({ scope: 'publication' });
      setActivationReport(result);
      setConfirmActivate(true);
      setStatus('');
    } catch (error) {
      setStatus(extractErrorDetail(error).message);
    } finally {
      setBusy(false);
    }
  };

  const readUpload = async (file: File) => {
    if (!listing) return;
    const name = uploadName.trim() || file.name;
    const content = '';
    const target = listing.files.find(item => !item.deleted && item.name.toLowerCase() === name.toLowerCase());
    if (uploadOverwrite && target) {
      const done = await runMutation(
        () => apiService.uploadOntologyFile({
          name, content, file, overwrite: true, file_id: target.id,
          expected_workspace: listing.workspace_revision, expected_file: target.revision,
        }),
        `已覆盖 ${name}（保持原启用状态，未激活）。`,
      );
      if (done) await loadFile(target.id);
    } else {
      const done = await runMutation(
        () => apiService.uploadOntologyFile({ name, content, file, expected_workspace: listing.workspace_revision }),
        `已上传 ${name}（默认停用；启用并激活后生效）。`,
      );
      if (done && target) {
        setStatus('同名文件已存在：勾选“覆盖同名文件”后重新上传可显式覆盖。');
      }
    }
    setUploadName('');
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  // ─── P1: load tables when the generation tab opens ─────────────────────────
  useEffect(() => {
    if (tab !== 'generate' || generatorTables || generatorBusy || generatorAttempted) return;
    setGeneratorAttempted(true);
    setGeneratorBusy(true);
    setGeneratorStatus('正在读取 MySQL 表清单…');
    apiService.listGeneratorTables()
      .then(result => {
        setGeneratorTables(result);
        setGeneratorStatus('');
      })
      .catch(error => {
        const detail = extractErrorDetail(error);
        setGeneratorStatus(detail.code === 'unsupported_source'
          ? `当前数据源不支持结构生成：${detail.message}`
          : `无法读取表清单：${detail.message}`);
      })
      .finally(() => setGeneratorBusy(false));
  }, [tab, generatorTables, generatorBusy, generatorAttempted]);

  // ─── Draft structure preview (parsed in memory, read-only) ──────────────────
  useEffect(() => {
    if (!draft || draftView !== 'structure') return;
    let cancelled = false;
    apiService.parseOntologyStructure(draft.content)
      .then(result => { if (!cancelled) setDraftModel(result.model); })
      .catch(() => { if (!cancelled) setDraftModel(null); });
    return () => { cancelled = true; };
  }, [draft, draftView]);

  const toggleTable = (fullName: string) => {
    setSelectedTables(previous => {
      const next = new Set(previous);
      if (next.has(fullName)) next.delete(fullName);
      else next.add(fullName);
      return next;
    });
  };

  const generateDraft = async () => {
    if (!generatorTables || selectedTables.size === 0) return;
    setGeneratorBusy(true);
    setGeneratorStatus('正在生成草稿…');
    setDraft(null);
    try {
      const result = await apiService.generateOntologyDraft(
        [...selectedTables],
        generatorTables.source_revision,
        namespace.trim() || undefined,
      );
      setDraft(result);
      setDraftName(result.filename_hint);
      setGeneratorStatus('草稿已生成（仅保存在内存，需审阅后保存为工作副本）。');
    } catch (error) {
      const detail = extractErrorDetail(error);
      setGeneratorStatus(detail.code === 'source_changed'
        ? '数据源已变化，请刷新表清单后重新生成。'
        : `生成失败：${detail.message}`);
    } finally {
      setGeneratorBusy(false);
    }
  };

  const saveDraft = async () => {
    if (!draft || !listing || !draftName.trim()) return;
    const name = draftName.trim();
    const target = listing.files.find(item => !item.deleted && item.name.toLowerCase() === name.toLowerCase());
    const done = await runMutation(
      () => apiService.uploadOntologyFile({
        name,
        content: draft.content,
        source_revision: draft.source_revision,
        overwrite: draftOverwrite && !!target,
        file_id: draftOverwrite && target ? target.id : undefined,
        expected_workspace: listing.workspace_revision,
        expected_file: draftOverwrite && target ? target.revision : undefined,
      }),
      `草稿已保存为 ${name}（默认停用；启用并激活后生效）。`,
    );
    if (done) {
      setTab('files');
      setDraft(null);
    }
  };

  const readOnly = selectedEntry ? selectedEntry.protected || selectedEntry.deleted : true;
  const health = listing?.health;
  const unsupportedGenerate = !!generatorStatus && generatorStatus.includes('不支持');

  return (
    <div className="business-layer-overlay" onClick={onClose}>
      <div className="business-layer-modal ontology-manager-modal" onClick={event => event.stopPropagation()}>
        <div className="business-layer-header">
          <div className="business-layer-title">本体管理</div>
          <button className="icon-btn" onClick={onClose} aria-label="关闭">✕</button>
        </div>
        <div className="business-layer-hint">
          工作副本保存后不会立即生效；激活会将整个启用集发布为新版本，并清空所有会话。
          内置本体受保护：可查看、下载、启停，但不能编辑或删除。
        </div>

        {listing && (
          <div className="ontology-summary">
            <span>工作集 <code>{listing.workspace_revision.slice(0, 8)}</code></span>
            <span>发布版本 <code>{listing.active_revision ? listing.active_revision.slice(0, 8) : '无'}</code></span>
            <span>激活时间 {formatDate(listing.activated_at)}</span>
            <span className={listing.has_changes ? 'pending' : ''}>
              {listing.has_changes ? `${pendingChanges.length} 项待激活差异` : '无待激活差异'}
            </span>
            {health && (
              <span className={health.available ? '' : 'bad'}>
                运行时 {health.available ? `正常（${health.entity_count} 实体）` : '不可用'}
              </span>
            )}
            <button
              className="icon-btn ontology-activate-btn"
              disabled={busy || !listing.has_changes}
              title={listing.has_changes ? '发布整个启用集并重置会话' : '没有待激活的差异'}
              onClick={reviewActivation}
            >
              激活
            </button>
          </div>
        )}
        {listing?.migration_error && (
          <div className="ontology-migration-error">
            种子导入失败（metadata-only 运行）：{listing.migration_error.message}
          </div>
        )}

        <div className="ontology-tabs">
          <button className={tab === 'files' ? 'active' : ''} onClick={() => setTab('files')}>文件</button>
          <button className={tab === 'generate' ? 'active' : ''} onClick={() => setTab('generate')}>结构草稿</button>
        </div>

        {tab === 'files' ? (
          <div className="ontology-manager-body">
            <div className="ontology-file-list">
              <label className="ontology-deleted-filter">
                <input
                  type="checkbox"
                  checked={showDeleted}
                  onChange={event => setShowDeleted(event.target.checked)}
                />
                显示已删除（可恢复）
              </label>
              {visibleFiles.map(file => (
                <button
                  key={file.id}
                  className={`ontology-file-item ${file.id === selectedId ? 'active' : ''}`}
                  disabled={busy}
                  onClick={() => loadFile(file.id)}
                >
                  <span className="file-name" title={file.name}>{file.name}</span>
                  <span className="file-meta">
                    {formatSize(file.size)}
                    {file.protected ? ' · 受保护' : ''}
                    {file.deleted ? ' · 已删除' : file.enabled ? ' · 已启用' : ' · 已停用'}
                    {file.has_backup ? ' · 有备份' : ''}
                  </span>
                  {file.change && (
                    <span className={`change-badge ${file.change}`}>{CHANGE_LABELS[file.change]}</span>
                  )}
                </button>
              ))}
              <div className="ontology-upload">
                <input
                  className="ontology-upload-name"
                  placeholder="文件名，如 sales.owl"
                  value={uploadName}
                  onChange={event => setUploadName(event.target.value)}
                />
                <label className="ontology-deleted-filter">
                  <input
                    type="checkbox"
                    checked={uploadOverwrite}
                    onChange={event => setUploadOverwrite(event.target.checked)}
                  />
                  覆盖同名文件
                </label>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".owl,application/rdf+xml,text/xml"
                  disabled={busy || !listing}
                  onChange={event => {
                    const file = event.target.files?.[0];
                    if (file) void readUpload(file);
                  }}
                />
              </div>
            </div>

            <div className="ontology-editor-pane">
              {selectedEntry ? (
                <>
                  <div className="ontology-editor-toolbar">
                    <span className="ontology-editor-title">
                      {selectedEntry.name}
                      {readOnly ? '（只读）' : ''}
                    </span>
                    <div className="view-mode-toggle" role="tablist" aria-label="编辑视图">
                      <button role="tab" aria-selected={viewMode === 'structure'}
                              disabled={busy}
                              className={viewMode === 'structure' ? 'active' : ''}
                              onClick={() => switchViewMode('structure')}>结构</button>
                      <button role="tab" aria-selected={viewMode === 'xml'}
                              disabled={busy}
                              className={viewMode === 'xml' ? 'active' : ''}
                              onClick={() => switchViewMode('xml')}>XML 源码</button>
                    </div>
                    <button className="icon-btn" disabled={busy} onClick={validateSelected}>校验</button>
                    <button
                      className="icon-btn"
                      disabled={busy || readOnly || !editorDirty}
                      title={viewMode === 'structure' ? '保存结构修改到工作副本' : '保存 XML 到工作副本'}
                      onClick={saveSelected}
                    >
                      保存{editorDirty ? ' ●' : ''}
                    </button>
                    <button
                      className="icon-btn"
                      disabled={busy || selectedEntry.deleted}
                      onClick={() => toggleEnabled(selectedEntry)}
                      title={selectedEntry.enabled ? '停用工作副本' : '启用工作副本'}
                    >
                      {selectedEntry.enabled ? '停用' : '启用'}
                    </button>
                    <button
                      className="icon-btn danger"
                      disabled={busy || selectedEntry.protected || selectedEntry.deleted}
                      onClick={() => setPendingDelete(selectedEntry)}
                    >
                      删除
                    </button>
                    <button
                      className="icon-btn"
                      disabled={busy || !selectedEntry.has_backup}
                      onClick={() => restoreSelected(selectedEntry)}
                      title="恢复上一版到工作副本（不会自动激活）"
                    >
                      恢复
                    </button>
                    <a
                      className="icon-btn"
                      href={ontologyDownloadUrl(selectedEntry.id, selectedEntry.deleted ? 'backup' : 'workspace')}
                      download
                    >
                      下载
                    </a>
                    {selectedEntry.has_backup && (
                      <a className="icon-btn" href={ontologyDownloadUrl(selectedEntry.id, 'backup')} download>下载备份</a>
                    )}
                    {selectedEntry.active && (
                      <a
                        className="icon-btn"
                        href={ontologyDownloadUrl(selectedEntry.id, 'active')}
                        download
                      >
                        发布版
                      </a>
                    )}
                  </div>
                  {viewMode === 'structure' ? (
                    structureModel ? (
                      <OntologyStructureEditor
                        model={structureModel}
                        original={structureOriginal}
                        readOnly={readOnly || busy}
                        onChange={setStructureModel}
                        readOnlyNotice={selectedEntry.protected
                          ? '内置本体受保护：可查看、下载、启停，但不能编辑内容。'
                          : selectedEntry.deleted
                            ? '文件已删除：以下为删除前备份，恢复后才能编辑。'
                            : undefined}
                      />
                    ) : (
                      <div className="ontology-editor-empty">此文件暂无结构视图（可能不是 RDF/XML），请使用 XML 源码视图。</div>
                    )
                  ) : (
                    <textarea
                      className="ontology-editor"
                      value={editorText}
                      spellCheck={false}
                      readOnly={readOnly || busy}
                      onChange={event => setEditorText(event.target.value)}
                      placeholder="UTF-8 RDF/XML 本体内容"
                    />
                  )}
                  {compareText !== null && (
                    <details className="ontology-compare" open>
                      <summary>服务器当前版本（只读对照；本地编辑未覆盖）</summary>
                      <textarea className="ontology-editor small" value={compareText} readOnly />
                    </details>
                  )}
                  {report && <ValidationReportView report={report} />}
                </>
              ) : (
                <div className="ontology-editor-empty">
                  选择左侧文件查看、编辑或校验；上传的新文件默认停用。
                </div>
              )}
            </div>
          </div>
        ) : (
          <div className="ontology-manager-body generate">
            <div className="ontology-generator">
              <button className="icon-btn" disabled={generatorBusy} onClick={() => {
                setGeneratorTables(null);
                setGeneratorAttempted(false);
                setSelectedTables(new Set());
              }}>刷新表清单</button>
              {generatorTables ? (
                <>
                  <div className="ontology-generator-source">
                    来源：MySQL {generatorTables.source_identity.host ?? ''} ·
                    数据库 {generatorTables.source_identity.databases?.join(', ')} ·
                    已选 {selectedTables.size}/{generatorTables.limits.max_tables} 表（列上限 {generatorTables.limits.max_columns}）
                  </div>
                  <div className="ontology-generator-list">
                    {generatorTables.tables.map(table => (
                      <label
                        key={table.full_name}
                        className={`ontology-generator-item ${table.supported ? '' : 'unsupported'}`}
                      >
                        <input
                          type="checkbox"
                          disabled={!table.supported}
                          checked={selectedTables.has(table.full_name)}
                          onChange={() => toggleTable(table.full_name)}
                        />
                        <span className="file-name">{table.full_name}</span>
                        {table.comment ? <span className="file-meta">{table.comment}</span> : null}
                        {!table.supported && <span className="file-meta">视图暂不支持</span>}
                      </label>
                    ))}
                  </div>
                  <div className="ontology-generator-controls">
                    <input
                      placeholder="自定义 namespace（可选，须以 # 或 / 结尾）"
                      value={namespace}
                      onChange={event => setNamespace(event.target.value)}
                    />
                    <button
                      className="icon-btn"
                      disabled={generatorBusy || selectedTables.size === 0 || selectedTables.size > generatorTables.limits.max_tables}
                      onClick={generateDraft}
                    >
                      生成草稿
                    </button>
                  </div>
                </>
              ) : (
                <div className="ontology-editor-empty">
                  {generatorBusy ? '正在加载…' : unsupportedGenerate ? generatorStatus : '从 MySQL 结构生成本体草稿。'}
                </div>
              )}
            </div>
            <div className="ontology-editor-pane">
              {draft ? (
                <>
                  <div className="ontology-editor-toolbar">
                    <span className="ontology-editor-title">草稿预览（SCHEMA DRAFT — requires human review）</span>
                    <div className="view-mode-toggle" role="tablist" aria-label="草稿视图">
                      <button role="tab" aria-selected={draftView === 'structure'}
                              className={draftView === 'structure' ? 'active' : ''}
                              onClick={() => setDraftView('structure')}>结构</button>
                      <button role="tab" aria-selected={draftView === 'xml'}
                              className={draftView === 'xml' ? 'active' : ''}
                              onClick={() => setDraftView('xml')}>XML 源码</button>
                    </div>
                  </div>
                  <div className="ontology-report">
                    <div className="ontology-report-summary ok">
                      {draft.report.table_count} 表 · {draft.report.column_count} 列 · namespace {draft.namespace}
                    </div>
                    {draft.report.adjusted_iri_count > 0 && (
                      <div className="ontology-report-issue warning">
                        <span className="issue-code">iri_adjusted</span> 编码碰撞消解了 {draft.report.adjusted_iri_count} 个 IRI
                      </div>
                    )}
                    {draft.report.missing_relations.map(relation => (
                      <div key={relation.constraint} className="ontology-report-issue warning">
                        <span className="issue-code">relation_target_not_selected</span>
                        {relation.constraint}: {relation.from} → {relation.to}（目标表未选中）
                      </div>
                    ))}
                    {draft.report.warnings.map((warning, index) => (
                      <div key={index} className="ontology-report-issue warning">
                        <span className="issue-code">warning</span> {warning}
                      </div>
                    ))}
                  </div>
                  {draftView === 'structure' ? (
                    draftModel ? (
                      <OntologyStructureEditor
                        model={draftModel}
                        original={draftModel}
                        readOnly
                        onChange={() => undefined}
                        readOnlyNotice="草稿为只读预览；可在 XML 源码视图修订，或保存为工作副本后再编辑。"
                      />
                    ) : (
                      <div className="ontology-editor-empty">正在解析草稿结构…</div>
                    )
                  ) : (
                    <textarea className="ontology-editor" value={draft.content} spellCheck={false}
                      onChange={event => setDraft({ ...draft, content: event.target.value })} />
                  )}
                  <div className="ontology-generator-controls">
                    <input
                      placeholder="保存为工作副本的文件名"
                      value={draftName}
                      onChange={event => setDraftName(event.target.value)}
                    />
                    <label className="ontology-deleted-filter">
                      <input
                        type="checkbox"
                        checked={draftOverwrite}
                        onChange={event => setDraftOverwrite(event.target.checked)}
                      />
                      覆盖同名文件
                    </label>
                    <button className="icon-btn" disabled={busy || !draftName.trim()} onClick={saveDraft}>
                      保存为工作副本（默认停用）
                    </button>
                  </div>
                </>
              ) : (
                <div className="ontology-editor-empty">
                  {generatorStatus || '勾选表后生成草稿；草稿只保存在内存，保存后默认停用，永不自动激活。'}
                </div>
              )}
            </div>
          </div>
        )}

        <div className="business-layer-footer">
          <span className="business-layer-status" role="status">{status}</span>
        </div>

        {pendingDelete && (
          <div className="ontology-confirm-overlay" onClick={event => event.stopPropagation()}>
            <div className="ontology-confirm">
              <p>确认删除 <strong>{pendingDelete.name}</strong>？删除前的内容与启用状态会保留一份备份，可从“已删除”列表恢复。已发布版本不受影响。</p>
              <div className="ontology-confirm-actions">
                <button className="icon-btn" onClick={() => setPendingDelete(null)}>取消</button>
                <button className="icon-btn danger" disabled={busy} onClick={() => deleteSelected(pendingDelete)}>删除</button>
              </div>
            </div>
          </div>
        )}

        {confirmActivate && listing && (
          <div className="ontology-confirm-overlay" onClick={event => event.stopPropagation()}>
            <div className="ontology-confirm">
              <p><strong>激活将发布整个启用集，并清空所有现有会话（含其他标签页）。</strong></p>
              {pendingChanges.length > 0 ? (
                <ul className="ontology-confirm-diff">
                  {pendingChanges.map(file => (
                    <li key={file.id}>
                      <span className={`change-badge ${file.change}`}>{CHANGE_LABELS[file.change ?? ''] ?? ''}</span> {file.name}
                    </li>
                  ))}
                </ul>
              ) : (
                <p>待激活差异：停用/删除导致的整集变化。</p>
              )}
              <div className="ontology-confirm-actions">
                <button className="icon-btn" onClick={() => setConfirmActivate(false)}>取消</button>
                <button className="icon-btn ontology-activate-btn" disabled={busy || !activationReport?.valid} onClick={activateFlow}>
                  确认激活
                </button>
              </div>
              {activationReport && <ValidationReportView report={activationReport} />}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
