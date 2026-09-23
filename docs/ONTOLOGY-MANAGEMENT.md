# 本体管理（Ontology Management）实施说明

对应 `docs/PRD-ONTOLOGY-MANAGEMENT.md`（P0 管理闭环 + P1 MySQL 结构草稿）。本文记录落地后的配置、存储布局、运维语义与 API 摘要。

## 运行模型

- 启动只信任 `active.json` 发布指针：`OntologyStore.initialize()` 首次从种子目录（`ONTOLOGY_DIR`/`ONTOLOGY_FILE_GLOB`）做一次性导入，此后修改种子或重启都不会重新导入。
- 生效的本体运行在独立子进程中（`ManagedOntologyRuntime`），查询经管道 RPC 服务，超时与终止受 `ONTOLOGY_MANAGEMENT_TIMEOUT_SECONDS` 预算约束；Windows 上通过 `taskkill /T` 结束进程树（覆盖 JVM 推理器）。
- 激活 = 构建 candidate 运行时与全套 Agent → 持久化 release → 提交 `active.json` → 同步安装内存引用 → 清空会话。提交前失败保留旧版本与会话；提交后清理失败仅告警。
- 种子文件按受控身份保护（导入时写入 manifest 的 `protected` 标志，不依赖文件名匹配）：可查看、下载、启停，不可编辑/删除/覆盖/恢复写入。

## 存储布局（`ONTOLOGY_MANAGEMENT_DIR`，默认 `data/ontology-management/`，不入库）

```text
workspace-current.json   # 当前工作集指针（sha256 校验）
active.json              # 当前/上一发布指针
workspaces/<revision>/   # 不可变工作集 manifest（正文、单槽备份、tombstone）
releases/<revision>/     # 不可变启用集快照
```

- 每次写入构建完整新 generation，验证并持久化后原子替换指针；`collect()` 回收旧 generation，仅保留当前工作集与当前/上一发布。
- 恢复只写回工作副本，不会自动激活；激活作用于整个启用集。
- 受管理路径拒绝符号链接/junction；存储损坏时管理接口返回 503，运行时保持最后完整发布。

## 配置（.env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `ONTOLOGY_MANAGEMENT_DIR` | `data/ontology-management` | 受管存储根目录（必须在种子扫描树之外） |
| `ONTOLOGY_MANAGEMENT_MAX_FILE_BYTES` | `5242880` | 单文件上限（5 MiB） |
| `ONTOLOGY_MANAGEMENT_MAX_FILES` | `100` | 现存文件数上限 |
| `ONTOLOGY_MANAGEMENT_MAX_TOTAL_BYTES` | `52428800` | 正文总量上限（50 MiB） |
| `ONTOLOGY_MANAGEMENT_DISK_BYTES` | `536870912` | 受管目录磁盘配额（512 MiB） |
| `ONTOLOGY_MANAGEMENT_TIMEOUT_SECONDS` | `30` | 校验/激活执行预算 |
| `ONTOLOGY_GENERATOR_MAX_TABLES` | `50` | 结构草稿表数上限 |
| `ONTOLOGY_GENERATOR_MAX_COLUMNS` | `2000` | 结构草稿列数上限 |

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/ontology/files` | 列表 + 双版本 + 差异 + 运行时健康度 |
| POST | `/ontology/files` | multipart 上传 / 显式覆盖（`overwrite`+`file_id`+预期版本） |
| GET | `/ontology/files/{id}` | `version=workspace\|active`；`download=true` 返回附件 |
| PUT | `/ontology/files/{id}` | 保存正文（422 附分层报告；409 保留本地文本） |
| DELETE | `/ontology/files/{id}` | 头 `X-Expected-Workspace` / `X-Expected-File` 携带版本 |
| POST | `/ontology/validate` | `scope=file`（进程内）或 `publication`（严格加载） |
| PUT | `/ontology/files/{id}/enabled` | 指定目标 enabled 值 |
| GET | `/ontology/files/{id}/backup` | 单槽备份内容/元数据 |
| POST | `/ontology/files/{id}/restore` | 仅恢复工作副本（恢复前重校验） |
| POST | `/ontology/activate` | 整批发布；返回 `changed/sessions_reset/warnings` |
| GET | `/ontology/generate-draft/tables` | MySQL 表清单 + 来源身份版本 |
| POST | `/ontology/generate-draft` | 生成内存草稿（P1；不落盘、不启用） |
| GET | `/ontology/files/{id}/structure` | 结构化模型（类/属性/个体/注解；`version=workspace\|active\|backup`） |
| PUT | `/ontology/files/{id}/structure` | 结构化编辑：补丁序列化 → 校验 → 正常保存链路 |
| POST | `/ontology/structure/parse` | 内存解析任意 RDF/XML 内容为结构模型 |
| POST | `/ontology/structure/serialize` | 结构模型 → XML（脏模型预览用） |

## 结构化查看/编辑（structure）

- 后端 `src/ontology/structure.py` 把 RDF/XML 解析为实体模型（IRI、多语言 label/注释、注解、父类、domain/range、特性、个体类型与取值），保存时**就地补丁**：仅重建被编辑的简单字段，`equivalentClass`/`Restriction`/`disjointWith` 等复杂公理子树、XML 注释与未触碰实体的空白**字节级保留**。
- 保真契约：语义恒等的模型保存不改动原文件；序列化幂等；逐实体 `tostring` 字节稳定。首次结构化保存会有一次性的根标签命名空间顺序规范化（`/>` 前加空格等），属预期。
- 已知 Owlready2 行为：无前缀注解元素与相对 `rdf:about` 不按 `xml:base` 解析，因此重建/新增的注解一律用绝对 IRI + 文档自身前缀（种子无前缀默认命名空间、生成草稿 `oda:`）。
- 删除实体保留悬空引用并在 diff 汇总中给出 `dangling_reference` 告警；IRI 不支持改名（v1），新增实体在本体命名空间下铸造并做碰撞消解。
- 前端：`OntologyStructureEditor` 三栏视图（类目导航 → 搜索列表 → 详情表单）为默认编辑界面，"XML 源码"切换保留原始文本编辑；结构草稿预览同样使用结构视图（只读）。

- 错误统一 `detail={code,message,issues?,current_revision?}`；版本缺失 428、过期 409、保护 403、超限 413、内容无效 422、恢复中/存储不可用 503、执行超时 504。
- 写操作与 MySQL 切换、聊天注册共用 configuration 锁；存在活跃查询时写操作 409。
- 激活成功后旧 thread_id 进入失效名单，`/chat/stream` 返回 `{"type":"error","code":"session_invalidated"}`，前端据此重置会话；同源标签页经 `BroadcastChannel('oda-ontology')` 同步。

## 结构草稿（P1）

- 仅读取 information_schema 的表/列/注释/外键，不读业务行；外键查询受 allowlist 约束（`MySQLMetadataProvider.list_foreign_keys`）。
- `source_revision` 为不含凭据的数据源身份摘要；生成与保存时校验，变化即 409 要求重新生成。
- 实体 IRI 携带 `库.表.列` 作用域并做百分号编码，编码碰撞自动消解并计数；原始物理名保留在 `rdfs:label` 与注解中。
- 映射注解（`sourceTable`/`sourceColumn`/`sourceType`/`sourceColumnPair`）被 `get_schema_mapping` 识别为置信度 1.0 的显式映射；但生成 SQL 前 MetadataAgent 仍会验证物理表列，显式映射不替代物理验证。
- 注意：Owlready2 不按 `xml:base` 解析无前缀注解元素与相对 `rdf:about`，因此草稿中的注解声明一律使用绝对 IRI，使用处带 `oda:` 前缀。

## 测试

```bash
venv/Scripts/python.exe -m pytest test_script/test_ontology_store.py \
  test_script/test_ontology_validation.py test_script/test_ontology_management_api.py \
  test_script/test_ontology_generator.py -v
```

API 测试通过真实 ASGI（TestClient）覆盖 multipart、下载头、状态码矩阵、激活故障注入（候选 Agent 构建失败保旧、空启用集 409、活跃查询 409、重启加载发布版）。生成器测试全部离线（内存 provider fixture）。依赖 Docker/Java 的既有测试按 AGENTS.md 约定如实 skip。
