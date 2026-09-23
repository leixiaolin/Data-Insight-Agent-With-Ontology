import { useMemo, useState } from 'react';
import type {
  OntologyEntityKind,
  OntologyLangText,
  OntologyStructureEntity,
  OntologyStructureModel,
} from '../types';

interface Props {
  /** Working copy the user edits. */
  model: OntologyStructureModel;
  /** Parsed baseline for dirty markers; null disables diff badges. */
  original: OntologyStructureModel | null;
  readOnly: boolean;
  onChange: (next: OntologyStructureModel) => void;
  readOnlyNotice?: string;
}

const CATEGORY_DEFS: { key: OntologyEntityKind | 'header'; label: string; countKey?: keyof OntologyStructureModel['counts'] }[] = [
  { key: 'header', label: '本体' },
  { key: 'class', label: '类', countKey: 'classes' },
  { key: 'datatype_property', label: '数据属性', countKey: 'datatype_properties' },
  { key: 'object_property', label: '对象属性', countKey: 'object_properties' },
  { key: 'individual', label: '个体', countKey: 'individuals' },
  { key: 'annotation_property', label: '注解属性', countKey: 'annotation_properties' },
  { key: 'datatype', label: '数据类型', countKey: 'datatypes' },
];

const XSD_TYPES = [
  'http://www.w3.org/2001/XMLSchema#string',
  'http://www.w3.org/2001/XMLSchema#integer',
  'http://www.w3.org/2001/XMLSchema#decimal',
  'http://www.w3.org/2001/XMLSchema#double',
  'http://www.w3.org/2001/XMLSchema#boolean',
  'http://www.w3.org/2001/XMLSchema#date',
  'http://www.w3.org/2001/XMLSchema#dateTime',
];

const CHARACTERISTICS: { key: string; label: string }[] = [
  { key: 'functional', label: '函数性' },
  { key: 'inverse_functional', label: '逆函数性' },
  { key: 'transitive', label: '传递性' },
  { key: 'symmetric', label: '对称性' },
];

const KIND_LABELS: Record<OntologyEntityKind, string> = {
  class: '类', datatype_property: '数据属性', object_property: '对象属性',
  annotation_property: '注解属性', datatype: '数据类型', individual: '个体',
};

function localName(iri: string): string {
  const tail = iri.includes('#') ? iri.split('#').pop() : iri.split('/').pop();
  return tail || iri;
}

function sanitizeName(value: string): string {
  const cleaned = value.replace(/[^A-Za-z0-9_.-]/g, '_').replace(/^_+/, '');
  return cleaned || 'entity';
}

function canonical(entity: OntologyStructureEntity): string {
  const fields = ['labels', 'comments', 'annotations', 'superclasses', 'domain',
    'range', 'characteristics', 'types', 'values'];
  return JSON.stringify(fields.map(field => entity[field as keyof OntologyStructureEntity] ?? null));
}

function LangRows({ title, rows, readOnly, knownLangs, onChange }: {
  title: string;
  rows: OntologyLangText[];
  readOnly: boolean;
  knownLangs: string[];
  onChange: (next: OntologyLangText[]) => void;
}) {
  return (
    <div className="structure-section">
      <div className="structure-section-title">{title}</div>
      {rows.map((row, index) => (
        <div key={index} className="lang-row">
          <input
            className="lang-input" value={row.lang} placeholder="语言" list="structure-lang-options"
            readOnly={readOnly} onChange={event => onChange(rows.map((item, i) =>
              i === index ? { ...item, lang: event.target.value } : item))} />
          <input
            className="lang-text" value={row.text} placeholder="文本"
            readOnly={readOnly} onChange={event => onChange(rows.map((item, i) =>
              i === index ? { ...item, text: event.target.value } : item))} />
          {!readOnly && (
            <button className="icon-btn" title="移除此行"
                    onClick={() => onChange(rows.filter((_, i) => i !== index))}>✕</button>
          )}
        </div>
      ))}
      <datalist id="structure-lang-options">
        {knownLangs.map(lang => <option key={lang} value={lang} />)}
      </datalist>
      {!readOnly && (
        <button className="icon-btn structure-add-row" onClick={() => onChange([...rows, { lang: 'zh', text: '' }])}>
          ＋ 添加{title}
        </button>
      )}
    </div>
  );
}

export function OntologyStructureEditor({ model, original, readOnly, onChange, readOnlyNotice }: Props) {
  const [category, setCategory] = useState<string>('class');
  const [search, setSearch] = useState('');
  const [selectedIri, setSelectedIri] = useState('');
  const [visibleCount, setVisibleCount] = useState(100);
  const [showRelations, setShowRelations] = useState(false);
  const [addingName, setAddingName] = useState('');
  const [confirmDelete, setConfirmDelete] = useState<OntologyStructureEntity | null>(null);

  const classIris = useMemo(
    () => model.entities.filter(entity => entity.kind === 'class').map(entity => entity.iri),
    [model.entities]);
  const annotationProperties = useMemo(
    () => model.entities.filter(entity => entity.kind === 'annotation_property'),
    [model.entities]);
  const assertableProperties = useMemo(
    () => model.entities.filter(entity => entity.kind === 'datatype_property' || entity.kind === 'object_property'),
    [model.entities]);
  const knownLangs = useMemo(() => {
    const langs = new Set<string>();
    for (const entity of model.entities) {
      entity.labels?.forEach(row => row.lang && langs.add(row.lang));
      entity.comments?.forEach(row => row.lang && langs.add(row.lang));
    }
    model.header.labels.forEach(row => row.lang && langs.add(row.lang));
    return [...langs];
  }, [model]);

  const originalByIri = useMemo(() => {
    const map = new Map<string, OntologyStructureEntity>();
    original?.entities.forEach(entity => map.set(entity.iri, entity));
    return map;
  }, [original]);

  const dirtyIrIs = useMemo(() => {
    if (!original) return { changed: new Set<string>(), added: new Set<string>() };
    const changed = new Set<string>();
    const added = new Set<string>();
    for (const entity of model.entities) {
      const before = originalByIri.get(entity.iri);
      if (!before) added.add(entity.iri);
      else if (canonical(before) !== canonical(entity)) changed.add(entity.iri);
    }
    return { changed, added };
  }, [model.entities, original, originalByIri]);

  const categoryEntities = useMemo(
    () => model.entities.filter(entity => entity.kind === category),
    [model.entities, category]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return categoryEntities;
    return categoryEntities.filter(entity =>
      entity.local_name.toLowerCase().includes(needle)
      || entity.labels.some(row => row.text.toLowerCase().includes(needle)));
  }, [categoryEntities, search]);

  const visible = filtered.slice(0, visibleCount);
  const selected = selectedIri ? model.entities.find(entity => entity.iri === selectedIri) ?? null : null;

  const updateEntity = (iri: string, patch: Partial<OntologyStructureEntity>) => {
    onChange({ ...model, entities: model.entities.map(entity => entity.iri === iri ? { ...entity, ...patch } : entity) });
  };

  const updateHeader = (patch: Partial<OntologyStructureModel['header']>) => {
    onChange({ ...model, header: { ...model.header, ...patch } });
  };

  const addEntity = () => {
    const name = sanitizeName(addingName.trim());
    if (!addingName.trim()) return;
    const namespace = model.ontology_iri.endsWith('#') ? model.ontology_iri : `${model.ontology_iri}#`;
    let iri = namespace + name;
    let suffix = 2;
    while (model.entities.some(entity => entity.iri === iri)) {
      iri = `${namespace}${name}__${suffix}`;
      suffix += 1;
    }
    const entity: OntologyStructureEntity = {
      iri, local_name: localName(iri), kind: category as OntologyEntityKind,
      labels: [{ lang: 'zh', text: addingName.trim() }], comments: [], annotations: [],
      complex_axioms: [],
    };
    if (entity.kind === 'class') entity.superclasses = [];
    if (entity.kind === 'datatype_property' || entity.kind === 'object_property' || entity.kind === 'annotation_property') {
      entity.domain = [];
      entity.range = [];
      entity.characteristics = [];
    }
    if (entity.kind === 'individual') {
      entity.types = [];
      entity.values = [];
    }
    onChange({ ...model, entities: [...model.entities, entity] });
    setSelectedIri(iri);
    setAddingName('');
  };

  const deleteEntity = (entity: OntologyStructureEntity) => {
    setConfirmDelete(null);
    onChange({ ...model, entities: model.entities.filter(item => item.iri !== entity.iri) });
    if (selectedIri === entity.iri) setSelectedIri('');
  };

  const danglingFor = (entity: OntologyStructureEntity): string[] => {
    const targets = new Set<string>();
    for (const other of model.entities) {
      if (other.iri === entity.iri) continue;
      (other.superclasses ?? []).forEach(iri => iri === entity.iri && targets.add(other.iri));
      (other.domain ?? []).forEach(iri => iri === entity.iri && targets.add(other.iri));
      (other.range ?? []).forEach(iri => iri === entity.iri && targets.add(other.iri));
      (other.types ?? []).forEach(iri => iri === entity.iri && targets.add(other.iri));
    }
    return [...targets];
  };

  const renderReferenceChips = (title: string, iris: string[] | undefined, options: string[], field: 'superclasses' | 'domain' | 'range' | 'types') => {
    if (iris === undefined) return null;
    return (
      <div className="structure-section">
        <div className="structure-section-title">{title}</div>
        <div className="chip-row">
          {iris.map(iri => (
            <span key={iri} className="ref-chip" title={iri}>
              {localName(iri)}
              {!readOnly && (
                <button className="chip-remove" title="移除"
                        onClick={() => updateEntity(selected!.iri, { [field]: iris.filter(item => item !== iri) } as Partial<OntologyStructureEntity>)}>✕</button>
              )}
            </span>
          ))}
          {!readOnly && (
            <select className="chip-select" value="" title="添加引用"
                    onChange={event => {
                      if (event.target.value) {
                        updateEntity(selected!.iri, { [field]: [...iris, event.target.value] } as Partial<OntologyStructureEntity>);
                      }
                    }}>
              <option value="">＋ 添加…</option>
              {options.filter(iri => !iris.includes(iri)).map(iri => (
                <option key={iri} value={iri}>{localName(iri)}</option>
              ))}
            </select>
          )}
        </div>
      </div>
    );
  };

  const dirtyCount = dirtyIrIs.changed.size + dirtyIrIs.added.size;

  return (
    <div className="ontology-structure">
      {readOnly && (
        <div className="structure-readonly-banner">{readOnlyNotice || '此文件为只读：结构修改不可用。'}</div>
      )}
      <div className="structure-nav">
        {CATEGORY_DEFS.map(def => {
          const count = def.countKey ? model.counts[def.countKey] : null;
          const active = category === def.key;
          const kindDirty = def.key !== 'header'
            ? model.entities.filter(entity => entity.kind === def.key
              && (dirtyIrIs.added.has(entity.iri) || dirtyIrIs.changed.has(entity.iri))).length
            : 0;
          return (
            <button key={def.key} className={`structure-nav-item ${active ? 'active' : ''}`}
                    onClick={() => { setCategory(def.key); setSearch(''); setVisibleCount(100); setSelectedIri(''); }}>
              <span>{def.label}</span>
              {count !== null && <span className="count-badge">{count}</span>}
              {kindDirty > 0 && <span className="count-badge dirty">●{kindDirty}</span>}
            </button>
          );
        })}
      </div>

      <div className="structure-list">
        {category === 'header' ? (
          <button className={`structure-entity ${selectedIri === '' ? 'active' : ''}`}
                  onClick={() => setSelectedIri('')}>
            <span className="file-name">本体信息</span>
            <span className="file-meta">{localName(model.ontology_iri)}</span>
          </button>
        ) : (
          <>
            <input className="structure-search" placeholder="搜索名称或标签…" value={search}
                   onChange={event => { setSearch(event.target.value); setVisibleCount(100); }} />
            {category === 'object_property' && (
              <button className="structure-relations-toggle" onClick={() => setShowRelations(!showRelations)}>
                {showRelations ? '隐藏关系总览' : '查看关系总览'}
              </button>
            )}
            {showRelations && category === 'object_property' && (
              <table className="relation-table">
                <thead><tr><th>起点（domain）</th><th>关系</th><th>终点（range）</th></tr></thead>
                <tbody>
                  {categoryEntities.map(entity => (
                    <tr key={entity.iri} onClick={() => setSelectedIri(entity.iri)}>
                      <td>{(entity.domain ?? []).map(localName).join(', ') || '—'}</td>
                      <td title={entity.iri}>{entity.local_name}</td>
                      <td>{(entity.range ?? []).map(localName).join(', ') || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {visible.map(entity => (
              <button key={entity.iri} className={`structure-entity ${selectedIri === entity.iri ? 'active' : ''}`}
                      onClick={() => setSelectedIri(entity.iri)}>
                <span className="file-name" title={entity.iri}>{entity.local_name}</span>
                <span className="file-meta">
                  {entity.labels.map(row => row.text).filter(Boolean).slice(0, 2).join(' · ') || '无标签'}
                </span>
                {dirtyIrIs.added.has(entity.iri) && <span className="change-badge added">＋新增</span>}
                {!dirtyIrIs.added.has(entity.iri) && dirtyIrIs.changed.has(entity.iri) && (
                  <span className="change-badge modified">●已修改</span>
                )}
                {entity.complex_axioms.length > 0 && (
                  <span className="file-meta axiom-flag" title="含等价类/限制等复杂公理（只读保留）">
                    ⚙ {entity.complex_axioms.length} 项公理
                  </span>
                )}
              </button>
            ))}
            {filtered.length > visibleCount && (
              <button className="structure-more" onClick={() => setVisibleCount(visibleCount + 100)}>
                加载更多（{visibleCount}/{filtered.length}）
              </button>
            )}
            {!readOnly && (
              <div className="structure-add-bar">
                <input value={addingName} placeholder={`新增${CATEGORY_DEFS.find(def => def.key === category)?.label ?? ''}名称`}
                       onChange={event => setAddingName(event.target.value)}
                       onKeyDown={event => event.key === 'Enter' && addEntity()} />
                <button className="icon-btn" disabled={!addingName.trim()} onClick={addEntity}>新增</button>
              </div>
            )}
          </>
        )}
      </div>

      <div className="structure-detail">
        {category === 'header' && selectedIri === '' ? (
          <>
            <div className="structure-section">
              <div className="structure-section-title">本体 IRI</div>
              <code className="iri-line">{model.ontology_iri}</code>
            </div>
            <div className="structure-section">
              <div className="structure-section-title">版本（owl:versionInfo）</div>
              <input value={model.version_info ?? ''} readOnly={readOnly}
                     placeholder="如 1.0.0"
                     onChange={event => onChange({ ...model, version_info: event.target.value })} />
            </div>
            <LangRows title="标签" rows={model.header.labels} readOnly={readOnly} knownLangs={knownLangs}
                      onChange={next => updateHeader({ labels: next })} />
            <LangRows title="注释" rows={model.header.comments} readOnly={readOnly} knownLangs={knownLangs}
                      onChange={next => updateHeader({ comments: next })} />
          </>
        ) : selected ? (
          <>
            <div className="structure-section">
              <div className="structure-section-title">{KIND_LABELS[selected.kind]} · IRI（不可改名）</div>
              <code className="iri-line" title={selected.iri}>{selected.iri}</code>
            </div>
            <LangRows title="标签" rows={selected.labels} readOnly={readOnly} knownLangs={knownLangs}
                      onChange={next => updateEntity(selected.iri, { labels: next })} />
            <LangRows title="注释" rows={selected.comments} readOnly={readOnly} knownLangs={knownLangs}
                      onChange={next => updateEntity(selected.iri, { comments: next })} />

            {selected.kind === 'class' && renderReferenceChips('父类（subClassOf）', selected.superclasses, classIris, 'superclasses')}

            {(selected.kind === 'object_property' || selected.kind === 'annotation_property') && (
              <>
                {renderReferenceChips('定义域（domain）', selected.domain, classIris, 'domain')}
                {renderReferenceChips('值域（range）', selected.range, classIris, 'range')}
              </>
            )}
            {selected.kind === 'datatype_property' && (
              <>
                {renderReferenceChips('定义域（domain）', selected.domain, classIris, 'domain')}
                <div className="structure-section">
                  <div className="structure-section-title">取值类型（xsd 数据类型）</div>
                  <select value={selected.range?.[0] ?? ''} disabled={readOnly}
                          onChange={event => updateEntity(selected.iri, {
                            range: event.target.value ? [event.target.value] : [],
                          })}>
                    <option value="">未指定</option>
                    {XSD_TYPES.map(type => <option key={type} value={type}>{localName(type)}</option>)}
                  </select>
                </div>
              </>
            )}

            {(selected.kind === 'datatype_property' || selected.kind === 'object_property') && (
              <div className="structure-section">
                <div className="structure-section-title">特性</div>
                <div className="chip-row">
                  {CHARACTERISTICS.map(item => (
                    <label key={item.key} className="chip-checkbox">
                      <input type="checkbox" disabled={readOnly}
                             checked={(selected.characteristics ?? []).includes(item.key)}
                             onChange={event => updateEntity(selected.iri, {
                               characteristics: event.target.checked
                                 ? [...(selected.characteristics ?? []), item.key]
                                 : (selected.characteristics ?? []).filter(value => value !== item.key),
                             })} />
                      {item.label}
                    </label>
                  ))}
                </div>
              </div>
            )}

            {selected.kind === 'individual' && (
              <>
                {renderReferenceChips('类型（rdf:type）', selected.types, classIris, 'types')}
                <div className="structure-section">
                  <div className="structure-section-title">属性取值</div>
                  {(selected.values ?? []).map((row, index) => (
                    <div key={index} className="value-row">
                      <select value={row.property} disabled={readOnly} title={row.property}
                              onChange={event => updateEntity(selected.iri, {
                                values: (selected.values ?? []).map((item, i) =>
                                  i === index ? { ...item, property: event.target.value } : item),
                              })}>
                        {assertableProperties.map(property => (
                          <option key={property.iri} value={property.iri}>{property.local_name}</option>
                        ))}
                      </select>
                      <label className="chip-checkbox" title="作为引用（rdf:resource）而不是字面量">
                        <input type="checkbox" disabled={readOnly} checked={row.is_ref}
                               onChange={event => updateEntity(selected.iri, {
                                 values: (selected.values ?? []).map((item, i) =>
                                   i === index ? { ...item, is_ref: event.target.checked } : item),
                               })} />
                        引用
                      </label>
                      <input className="lang-text" value={row.value} placeholder="值或 IRI" readOnly={readOnly}
                             onChange={event => updateEntity(selected.iri, {
                               values: (selected.values ?? []).map((item, i) =>
                                 i === index ? { ...item, value: event.target.value } : item),
                             })} />
                      {!row.is_ref && (
                        <select value={row.datatype} disabled={readOnly} title="数据类型"
                                onChange={event => updateEntity(selected.iri, {
                                  values: (selected.values ?? []).map((item, i) =>
                                    i === index ? { ...item, datatype: event.target.value } : item),
                                })}>
                          <option value="">无类型</option>
                          {XSD_TYPES.map(type => <option key={type} value={type}>{localName(type)}</option>)}
                        </select>
                      )}
                      {!readOnly && (
                        <button className="icon-btn" title="移除此行"
                                onClick={() => updateEntity(selected.iri, {
                                  values: (selected.values ?? []).filter((_, i) => i !== index),
                                })}>✕</button>
                      )}
                    </div>
                  ))}
                  {!readOnly && (
                    <button className="icon-btn structure-add-row"
                            onClick={() => updateEntity(selected.iri, {
                              values: [...(selected.values ?? []), {
                                property: assertableProperties[0]?.iri ?? '', value: '',
                                datatype: '', lang: '', is_ref: false,
                              }],
                            })}>＋ 添加取值</button>
                  )}
                </div>
              </>
            )}

            <div className="structure-section">
              <div className="structure-section-title">注解</div>
              {selected.annotations.map((row, index) => (
                <div key={index} className="annotation-row">
                  <select value={row.property} disabled={readOnly} title={row.property}
                          onChange={event => updateEntity(selected.iri, {
                            annotations: selected.annotations.map((item, i) =>
                              i === index ? { ...item, property: event.target.value } : item),
                          })}>
                    {annotationProperties.map(property => (
                      <option key={property.iri} value={property.iri}>{property.local_name}</option>
                    ))}
                  </select>
                  <input className="lang-text" value={row.value} placeholder="值" readOnly={readOnly}
                         onChange={event => updateEntity(selected.iri, {
                           annotations: selected.annotations.map((item, i) =>
                             i === index ? { ...item, value: event.target.value } : item),
                         })} />
                  <input className="lang-input" value={row.lang} placeholder="语言" readOnly={readOnly}
                         onChange={event => updateEntity(selected.iri, {
                           annotations: selected.annotations.map((item, i) =>
                             i === index ? { ...item, lang: event.target.value } : item),
                         })} />
                  {!readOnly && (
                    <button className="icon-btn" title="移除此行"
                            onClick={() => updateEntity(selected.iri, {
                              annotations: selected.annotations.filter((_, i) => i !== index),
                            })}>✕</button>
                  )}
                </div>
              ))}
              {!readOnly && annotationProperties.length > 0 && (
                <button className="icon-btn structure-add-row"
                        onClick={() => updateEntity(selected.iri, {
                          annotations: [...selected.annotations, {
                            property: annotationProperties[0].iri, value: '', lang: '', datatype: '',
                          }],
                        })}>＋ 添加注解</button>
              )}
            </div>

            {selected.complex_axioms.length > 0 && (
              <div className="structure-section">
                <div className="structure-section-title">复杂公理（只读，保存时原样保留）</div>
                {selected.complex_axioms.map((axiom, index) => (
                  <div key={index} className="complex-axiom-card">
                    <span className="axiom-kind">{axiom.kind}</span>
                    <code>{axiom.note}</code>
                    {axiom.involved.length > 0 && (
                      <div className="axiom-involved">
                        涉及：{axiom.involved.map(localName).join('、')}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {!readOnly && (
              <div className="structure-section">
                <button className="icon-btn danger" onClick={() => setConfirmDelete(selected)}>删除此{KIND_LABELS[selected.kind]}</button>
                {confirmDelete?.iri === selected.iri && (
                  <div className="ontology-confirm-overlay inline">
                    <div className="ontology-confirm">
                      <p>
                        确认删除 <strong>{confirmDelete.local_name}</strong>？
                        {danglingFor(confirmDelete).length > 0 && (
                          <>有 {danglingFor(confirmDelete).length} 个实体仍引用它，删除后将产生悬空引用（保存时会再次提示）。</>
                        )}
                      </p>
                      <div className="ontology-confirm-actions">
                        <button className="icon-btn" onClick={() => setConfirmDelete(null)}>取消</button>
                        <button className="icon-btn danger" onClick={() => deleteEntity(confirmDelete)}>删除</button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        ) : (
          <div className="ontology-editor-empty">从左侧选择{CATEGORY_DEFS.find(def => def.key === category)?.label ?? '实体'}查看详情。</div>
        )}
        {!readOnly && dirtyCount > 0 && (
          <div className="structure-diff-bar">共 {dirtyCount} 处未保存修改</div>
        )}
      </div>
    </div>
  );
}
