import React, { useState, useEffect, useRef } from 'react';
import { apiService, parseAnalysisStatus } from './services/api';
import type { AnalysisStatus, ChatMessage, SessionInfo } from './types';
import './styles/global.css';
import './styles/App.css';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { ActivityPanel } from './components/ActivityPanel';
import { MySQLSettingsModal } from './components/MySQLSettingsModal';
import type { ActivityItem, ActivityKind, ActivityState } from './types/activity';

// Example questions covering the Databricks analytics and ontology-driven data insight paths
const EXAMPLE_QUERIES = [
  "哪个客户在2023年的消费是最高的",
  "按月看2023年的销售额趋势",
  "哪个地区的成交量是最高的，在这个地区那个产品销量最高，并且结合数据分析原因",
  "按企业定义分析 2023 年高价值订单的月度趋势、订单占比和销售额贡献率。",
  "按地区同时比较订单数、销量、销售额和平均客单价，并解释最高地区领先第二名的主要结构因素。",
  "2023年高价值订单主要来自哪类客户、发往哪些地区、集中在哪些顶层产品大类？",
  "按顶层产品大类比较折扣明细行的成交额占比、折扣明细的成交量贡献，以及每单成交额；并说明折扣是否集中在少数大类。",
  "2023年各顶层产品大类的销量和订单明细分别是多少？并找出每个顶层大类贡献最高的子类目及其贡献率。"
];

interface MessageWithThinking extends ChatMessage {
  thinking?: ActivityItem[];
  thinkingCollapsed?: boolean;
}

const EMPTY_MESSAGES: MessageWithThinking[] = [];

const tableRowCells = (row: string): string[] | null => {
  const stripped = row.trim();
  if (stripped.length < 2 || !stripped.startsWith('|') || !stripped.endsWith('|')) {
    return null;
  }
  return stripped.slice(1, -1).split('|');
};

const isTableSeparatorRow = (cells: string[]): boolean =>
  cells.length > 0 && cells.every(cell => /^\s*:?-{3,}:?\s*$/.test(cell));

const splitCollapsedTableRow = (
  line: string,
  expectedCells: number | null
): string[] | null => {
  const candidates = line.replace(/\|\s*\|/g, '|\n|').split('\n');
  if (candidates.length < 2) return null;

  const parsed = candidates.map(tableRowCells);
  if (parsed.some(cells => cells === null)) return null;

  const widths = new Set(parsed.map(cells => (cells as string[]).length));
  if (widths.size !== 1) return null;

  const width = widths.values().next().value as number;
  if (expectedCells !== null) {
    // A row with genuinely empty cells splits into the wrong width, so it stays intact.
    return width === expectedCells ? candidates : null;
  }
  return parsed.some(cells => isTableSeparatorRow(cells as string[])) ? candidates : null;
};

export const repairCollapsedMarkdownTables = (content: string): string => {
  if (!content || !content.includes('|')) return content;

  const repairedLines: string[] = [];
  let inCodeFence = false;
  let expectedCells: number | null = null;

  for (const line of content.split('\n')) {
    if (line.trimStart().startsWith('```')) {
      inCodeFence = !inCodeFence;
      expectedCells = null;
      repairedLines.push(line);
      continue;
    }

    const cells = inCodeFence ? null : tableRowCells(line);
    if (cells === null) {
      expectedCells = null;
      repairedLines.push(line);
      continue;
    }

    const rows = splitCollapsedTableRow(line, expectedCells);
    if (rows === null) {
      repairedLines.push(line);
      if (isTableSeparatorRow(cells)) expectedCells = cells.length;
      continue;
    }

    repairedLines.push(...rows);
    const firstRow = tableRowCells(rows[0]);
    expectedCells = firstRow ? firstRow.length : null;
  }

  return repairedLines.join('\n');
};

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '');
const apiUrl = (path: string) => `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`;

// Ontology mark: a hub-and-satellite knowledge graph inside an orbiting inference ring.
const OntologyMark: React.FC = () => (
  <svg viewBox="0 0 120 120" role="img" aria-label="Ontology Data Agent" focusable="false">
    <defs>
      <linearGradient id="onto-grad" x1="0%" y1="0%" x2="100%" y2="100%">
        <stop offset="0%" stopColor="#4a9eff" />
        <stop offset="55%" stopColor="#06b6d4" />
        <stop offset="100%" stopColor="#a855f7" />
      </linearGradient>
      <radialGradient id="onto-core" cx="35%" cy="30%" r="80%">
        <stop offset="0%" stopColor="#cfe9ff" />
        <stop offset="45%" stopColor="#4a9eff" />
        <stop offset="100%" stopColor="#a855f7" />
      </radialGradient>
      <filter id="onto-glow" x="-60%" y="-60%" width="220%" height="220%">
        <feGaussianBlur stdDeviation="3.4" result="blur" />
        <feMerge>
          <feMergeNode in="blur" />
          <feMergeNode in="SourceGraphic" />
        </feMerge>
      </filter>
    </defs>

    <circle cx="60" cy="60" r="52" fill="url(#onto-grad)" opacity="0.07" />

    <g stroke="url(#onto-grad)" fill="none" strokeLinecap="round">
      <circle
        cx="60"
        cy="60"
        r="52"
        strokeWidth="1.6"
        strokeDasharray="10 12"
        opacity="0.65"
      >
        <animateTransform
          attributeName="transform"
          type="rotate"
          from="0 60 60"
          to="360 60 60"
          dur="24s"
          repeatCount="indefinite"
        />
      </circle>

      {/* hexagonal relation ring */}
      <polygon
        points="60,20 94.6,40 94.6,80 60,100 25.4,80 25.4,40"
        strokeWidth="1.8"
        opacity="0.45"
      />

      {/* hub-to-class edges */}
      <g strokeWidth="2.6" opacity="0.9">
        <line x1="60" y1="60" x2="60" y2="20" />
        <line x1="60" y1="60" x2="94.6" y2="80" />
        <line x1="60" y1="60" x2="25.4" y2="80" />
      </g>
    </g>

    <g fill="url(#onto-grad)" filter="url(#onto-glow)">
      <circle cx="60" cy="20" r="7.5" />
      <circle cx="94.6" cy="80" r="7.5" />
      <circle cx="25.4" cy="80" r="7.5" />
    </g>

    <g fill="url(#onto-grad)" opacity="0.55">
      <circle cx="94.6" cy="40" r="4.2" />
      <circle cx="60" cy="100" r="4.2" />
      <circle cx="25.4" cy="40" r="4.2" />
    </g>

    <circle cx="60" cy="60" r="15" fill="url(#onto-core)" filter="url(#onto-glow)" />

    {/* data grain inside the hub */}
    <g fill="#0b1220" opacity="0.75">
      <rect x="54.5" y="61" width="3" height="7" rx="1.4" />
      <rect x="58.5" y="56" width="3" height="12" rx="1.4" />
      <rect x="62.5" y="58.5" width="3" height="9.5" rx="1.4" />
    </g>

    <circle cx="60" cy="60" r="21" fill="none" stroke="url(#onto-grad)" strokeWidth="1.4" opacity="0.5">
      <animate attributeName="r" values="21;25;21" dur="3.6s" repeatCount="indefinite" />
      <animate attributeName="opacity" values="0.5;0.05;0.5" dur="3.6s" repeatCount="indefinite" />
    </circle>
  </svg>
);

function App() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [inputValue, setInputValue] = useState('');
  const [currentSessionId, setCurrentSessionId] = useState<string>('');
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [sessionCounter, setSessionCounter] = useState(1);
  const [autoScroll, setAutoScroll] = useState(true);
  const [sessionMessages, setSessionMessages] = useState<Map<string, MessageWithThinking[]>>(new Map());
  const [defaultEnableOntology, setDefaultEnableOntology] = useState(true);
  const [sessionOntologyModes, setSessionOntologyModes] = useState<Map<string, boolean>>(new Map());
  const [loadingSessionIds, setLoadingSessionIds] = useState<Set<string>>(new Set());
  const [businessLayerOpen, setBusinessLayerOpen] = useState(false);
  const [mysqlSettingsOpen, setMysqlSettingsOpen] = useState(false);
  const [businessLayerDraft, setBusinessLayerDraft] = useState('');
  const [businessLayerStatus, setBusinessLayerStatus] = useState('');
  const [businessLayerBusy, setBusinessLayerBusy] = useState(false);
  const chatContainerRef = useRef<HTMLDivElement>(null);
  const autoScrollRef = useRef(true);
  const programmaticScrollRef = useRef(false);
  const abortControllersRef = useRef<Map<string, AbortController>>(new Map());
  const initialSessionRequestedRef = useRef(false);

  const messages = sessionMessages.get(currentSessionId) ?? EMPTY_MESSAGES;
  const isLoading = loadingSessionIds.has(currentSessionId);
  const ontologyEnabled = sessionOntologyModes.get(currentSessionId) ?? defaultEnableOntology;

  const updateSessionMessages = (
    sessionId: string,
    updater: (previous: MessageWithThinking[]) => MessageWithThinking[]
  ) => {
    setSessionMessages(previous => {
      const next = new Map(previous);
      next.set(sessionId, updater(next.get(sessionId) ?? []));
      return next;
    });
  };

  const openBusinessLayer = async () => {
    setBusinessLayerOpen(true);
    setBusinessLayerBusy(true);
    setBusinessLayerStatus('正在加载…');
    try {
      const { content } = await apiService.getBusinessLayer();
      setBusinessLayerDraft(content);
      setBusinessLayerStatus('');
    } catch {
      setBusinessLayerStatus('无法加载文档。');
    } finally {
      setBusinessLayerBusy(false);
    }
  };

  const persistBusinessLayer = async () => {
    setBusinessLayerBusy(true);
    setBusinessLayerStatus('正在保存…');
    try {
      const { length } = await apiService.saveBusinessLayer(businessLayerDraft);
      setBusinessLayerStatus(`已保存（${length} 字符），将从下一个问题开始生效。`);
    } catch {
      setBusinessLayerStatus('保存失败。');
    } finally {
      setBusinessLayerBusy(false);
    }
  };

  useEffect(() => {
    // Create the initial session on mount
    if (sessions.length === 0 && !initialSessionRequestedRef.current) {
      initialSessionRequestedRef.current = true;
      initializeApplication();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- initialization is mount-only
  }, []);

  useEffect(() => {
    autoScrollRef.current = autoScroll;
  }, [autoScroll]);

  // Reads the ref, not the state, so a streaming update never re-applies a stale intent.
  useEffect(() => {
    const container = chatContainerRef.current;
    if (!container || !autoScrollRef.current) return;
    programmaticScrollRef.current = true;
    container.scrollTop = container.scrollHeight;
  }, [messages]);

  // 监听用户滚动
  useEffect(() => {
    const container = chatContainerRef.current;
    if (!container) return;

    const handleScroll = () => {
      if (programmaticScrollRef.current) {
        programmaticScrollRef.current = false;
        return;
      }
      const { scrollTop, scrollHeight, clientHeight } = container;
      const isNearBottom = scrollHeight - scrollTop - clientHeight < 100;
      autoScrollRef.current = isNearBottom;
      setAutoScroll(isNearBottom);
    };

    // Scrolling up is unambiguous intent to read, so release the stream immediately.
    const handleWheel = (event: WheelEvent) => {
      if (event.deltaY < 0 && autoScrollRef.current) {
        autoScrollRef.current = false;
        setAutoScroll(false);
      }
    };

    container.addEventListener('scroll', handleScroll);
    container.addEventListener('wheel', handleWheel, { passive: true });
    return () => {
      container.removeEventListener('scroll', handleScroll);
      container.removeEventListener('wheel', handleWheel);
    };
  }, []);

  const initializeApplication = async () => {
    let ontologyDefault = true;
    try {
      const runtimeConfig = await apiService.getRuntimeConfig();
      ontologyDefault = runtimeConfig.default_enable_ontology;
      setDefaultEnableOntology(ontologyDefault);
    } catch (error) {
      console.error('Failed to load runtime config; using ontology default:', error);
    }
    await createInitialSession(ontologyDefault);
  };

  const createInitialSession = async (ontologyDefault: boolean) => {
    try {
      const result = await apiService.createThread();
      const newSession: SessionInfo = {
        id: result.thread_id,
        name: '会话 1',
        created_at: new Date().toISOString(),
        message_count: 0
      };
      setCurrentSessionId(result.thread_id);
      setSessions([newSession]);
      setSessionCounter(2);
      setSessionMessages(new Map([[result.thread_id, []]]));
      setSessionOntologyModes(new Map([[result.thread_id, ontologyDefault]]));
    } catch (error) {
      initialSessionRequestedRef.current = false;
      console.error('Failed to create initial session:', error);
    }
  };

  const createNewSession = async () => {
    try {
      const result = await apiService.createThread();
      const newSession: SessionInfo = {
        id: result.thread_id,
        name: `会话 ${sessionCounter}`,
        created_at: new Date().toISOString(),
        message_count: 0
      };
      setCurrentSessionId(result.thread_id);
      setSessions(prev => [...prev, newSession]);
      setSessionCounter(prev => prev + 1);
      setSessionMessages(prev => new Map(prev).set(result.thread_id, []));
      setSessionOntologyModes(prev => new Map(prev).set(result.thread_id, defaultEnableOntology));
    } catch (error) {
      console.error('Failed to create session:', error);
    }
  };

  const switchSession = (sessionId: string) => {
    setCurrentSessionId(sessionId);
    setAutoScroll(true);
  };

  const deleteSession = async (sessionId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    
    // 如果只剩一个session，不允许删除
    if (sessions.length === 1) {
      alert('至少需要保留一个会话');
      return;
    }

    try {
      abortControllersRef.current.get(sessionId)?.abort();
      abortControllersRef.current.delete(sessionId);
      await apiService.stopThread(sessionId).catch(() => undefined);
      await apiService.deleteThread(sessionId);

      // 删除session的消息记录
      setSessionMessages(prev => {
        const newMap = new Map(prev);
        newMap.delete(sessionId);
        return newMap;
      });
      setSessionOntologyModes(prev => {
        const next = new Map(prev);
        next.delete(sessionId);
        return next;
      });

      setSessions(prev => prev.filter(s => s.id !== sessionId));
      setLoadingSessionIds(previous => {
        const next = new Set(previous);
        next.delete(sessionId);
        return next;
      });
      
      // 如果删除的是当前session，切换到第一个session
      if (currentSessionId === sessionId) {
        const remainingSessions = sessions.filter(s => s.id !== sessionId);
        if (remainingSessions.length > 0) {
          setCurrentSessionId(remainingSessions[0].id);
        }
      }
    } catch (error) {
      console.error('Failed to delete session:', error);
    }
  };

  const sendMessageStream = async () => {
    if (!inputValue.trim() || isLoading) return;

    const sessionId = currentSessionId;
    if (!sessionId) return;
    const requestOntologyMode = sessionOntologyModes.get(sessionId) ?? defaultEnableOntology;

    const userMessage: MessageWithThinking = {
      role: 'user',
      content: inputValue,
      timestamp: new Date().toISOString()
    };

    const currentInput = inputValue;
    setInputValue('');
    setLoadingSessionIds(previous => new Set(previous).add(sessionId));
    setAutoScroll(true); // 开始新消息时启用自动滚动

    // 先创建一个空的assistant消息框架，thinking在前面
    const initialAssistantMessage: MessageWithThinking = {
      role: 'assistant',
      content: '',
      timestamp: new Date().toISOString(),
      thinking: [],
      thinkingCollapsed: false
    };
    updateSessionMessages(sessionId, previous => [
      ...previous,
      userMessage,
      initialAssistantMessage,
    ]);

    const abortController = new AbortController();
    abortControllersRef.current.set(sessionId, abortController);

    try {
      const response = await fetch(apiUrl('/chat/stream'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: currentInput,
          thread_id: sessionId,
          enable_ontology: requestOntologyMode
        }),
        signal: abortController.signal,
      });

      if (!response.ok) {
        const errBody = await response.text().catch(() => '');
        throw new Error(`Failed to send message (${response.status}): ${errBody || response.statusText}`);
      }

      const reader = response.body?.getReader();
      const decoder = new TextDecoder();
      let assistantContent = '';
      let thinkingForMessage: ActivityItem[] = [];
      let sseBuffer = '';
      let activitySequence = 0;
      let didFinalize = false;

      const updateAssistantMessage = (content: string, thinking: ActivityItem[]) => {
        updateSessionMessages(sessionId, prev => {
          const newMessages = [...prev];
          const lastMessage = newMessages[newMessages.length - 1];
          if (lastMessage && lastMessage.role === 'assistant') {
            newMessages[newMessages.length - 1] = {
              ...lastMessage,
              content,
              thinking: [...thinking]
            };
          }
          return newMessages;
        });
      };

      const upsertThinking = (data: Record<string, unknown>) => {
        const content = typeof data.message === 'string' ? data.message : '';
        if (!content.trim() && data.append !== true) return;
        if (!content) return;

        const id = typeof data.id === 'string' && data.id
          ? data.id
          : `activity-${++activitySequence}`;
        const supportedKinds: ActivityKind[] = ['narration', 'agent', 'stage', 'tool', 'skill', 'reasoning', 'status'];
        const kind: ActivityKind = typeof data.kind === 'string' && supportedKinds.includes(data.kind as ActivityKind)
          ? data.kind as ActivityKind
          : 'tool';
        const state: ActivityState = data.state === 'completed' || data.state === 'error'
          ? data.state
          : 'running';
        const existingIndex = thinkingForMessage.findIndex(item => item.id === id);

        if (existingIndex >= 0) {
          const existing = thinkingForMessage[existingIndex];
          const nextContent = data.append === true ? existing.content + content : content;
          thinkingForMessage = thinkingForMessage.map((item, index) => index === existingIndex
            ? {
                ...item,
                content: nextContent,
                state,
                category: typeof data.category === 'string' ? data.category : item.category,
                agent: typeof data.agent === 'string' ? data.agent : item.agent,
                parentId: typeof data.parent_id === 'string' ? data.parent_id : item.parentId,
                detail: typeof data.detail === 'string' ? data.detail : item.detail,
                summary: typeof data.summary === 'string' ? data.summary : item.summary,
                durationMs: typeof data.duration_ms === 'number' ? data.duration_ms : item.durationMs,
                metrics: typeof data.metrics === 'object' && data.metrics !== null
                  ? data.metrics as Record<string, unknown>
                  : item.metrics
              }
            : item);
        } else {
          thinkingForMessage = [...thinkingForMessage, {
            id,
            kind,
            category: typeof data.category === 'string' ? data.category : kind,
            state,
            agent: typeof data.agent === 'string' ? data.agent : undefined,
            parentId: typeof data.parent_id === 'string' ? data.parent_id : undefined,
            content,
            detail: typeof data.detail === 'string' ? data.detail : undefined,
            summary: typeof data.summary === 'string' ? data.summary : undefined,
            durationMs: typeof data.duration_ms === 'number' ? data.duration_ms : undefined,
            metrics: typeof data.metrics === 'object' && data.metrics !== null
              ? data.metrics as Record<string, unknown>
              : {},
            timestamp: new Date().toISOString()
          }];
        }

        updateAssistantMessage(assistantContent, thinkingForMessage);
      };

      const completeThinking = () => {
        thinkingForMessage = thinkingForMessage.map(item => item.state === 'running'
          ? { ...item, state: 'completed' }
          : item);
        updateAssistantMessage(assistantContent, thinkingForMessage);
      };

      const finalizeAssistantMessage = (finalContent?: string, analysisStatus?: AnalysisStatus) => {
        if (typeof finalContent === 'string' && finalContent.trim()) {
          assistantContent = finalContent;
        }

        thinkingForMessage = thinkingForMessage.map(item => item.state === 'running'
          ? { ...item, state: 'completed' }
          : item);
        assistantContent = repairCollapsedMarkdownTables(assistantContent);
        didFinalize = true;

        updateSessionMessages(sessionId, prev => {
          const newMessages = [...prev];
          const lastMsg = newMessages[newMessages.length - 1];
          if (lastMsg && lastMsg.role === 'assistant') {
            newMessages[newMessages.length - 1] = {
              ...lastMsg,
              content: assistantContent,
              thinking: [...thinkingForMessage],
              thinkingCollapsed: true,
              analysisStatus
            };
          }
          return newMessages;
        });
      };

      const handleSsePayload = async (payload: string) => {
        if (!payload.trim()) return;
        const data = JSON.parse(payload) as Record<string, unknown>;

        if (data.type === 'thinking') {
          upsertThinking(data);
        } else if (data.type === 'thinking_done') {
          completeThinking();
        } else if (data.type === 'text') {
          assistantContent += typeof data.content === 'string' ? data.content : '';
          updateAssistantMessage(assistantContent, thinkingForMessage);
        } else if (data.type === 'answer_reset') {
          assistantContent = '';
          updateAssistantMessage(assistantContent, thinkingForMessage);
        } else if (data.type === 'done') {
          finalizeAssistantMessage(
            typeof data.content === 'string' ? data.content : undefined,
            parseAnalysisStatus(data.analysis_status)
          );
        } else if (data.type === 'stopped') {
          upsertThinking({
            id: `stopped-${sessionId}`,
            kind: 'status',
            state: 'completed',
            message: data.message || '任务已停止'
          });
          finalizeAssistantMessage(assistantContent || '任务已停止。');
        } else if (data.type === 'error') {
          upsertThinking({
            id: `error-${++activitySequence}`,
            kind: 'status',
            state: 'error',
            message: data.message || '处理请求时发生错误'
          });
          throw new Error(typeof data.message === 'string' ? data.message : '处理请求时发生错误');
        }
      };

      if (reader) {
        let chunk = await reader.read();
        while (!chunk.done) {
          const { value } = chunk;
          sseBuffer += decoder.decode(value, { stream: true });
          const rawEvents = sseBuffer.split('\n\n');
          sseBuffer = rawEvents.pop() || '';

          for (const eventChunk of rawEvents) {
            const dataLines = eventChunk
              .split('\n')
              .filter(line => line.startsWith('data: '))
              .map(line => line.substring(6));

            if (dataLines.length === 0) continue;

            try {
              await handleSsePayload(dataLines.join('\n'));
            } catch (e) {
              console.error('Error parsing SSE data:', e);
            }
          }
          chunk = await reader.read();
        }

        // Flush any trailing SSE payload still in the buffer
        const tail = sseBuffer.trim();
        if (tail.startsWith('data: ')) {
          try {
            await handleSsePayload(tail.substring(6));
          } catch (e) {
            console.error('Error parsing trailing SSE data:', e);
          }
        }

        if (!didFinalize) {
          finalizeAssistantMessage();
        }
      }
    } catch (error) {
      const wasAborted = error instanceof DOMException && error.name === 'AbortError';
      if (!wasAborted) console.error('Failed to send message:', error);
      const errMsg = error instanceof Error ? error.message : String(error);
      updateSessionMessages(sessionId, prev => {
        const newMessages = [...prev];
        const lastMsg = newMessages[newMessages.length - 1];
        if (lastMsg && lastMsg.role === 'assistant') {
          if (wasAborted) {
            lastMsg.content = lastMsg.content || '任务已停止。';
            lastMsg.thinkingCollapsed = true;
          } else if (lastMsg.content === '') {
            lastMsg.content = `请求失败：${errMsg}`;
          }
        }
        return newMessages;
      });
    } finally {
      if (abortControllersRef.current.get(sessionId) === abortController) {
        abortControllersRef.current.delete(sessionId);
      }
      setLoadingSessionIds(previous => {
        const next = new Set(previous);
        next.delete(sessionId);
        return next;
      });
    }
  };

  const stopCurrentTask = async () => {
    const sessionId = currentSessionId;
    if (!sessionId || !loadingSessionIds.has(sessionId)) return;

    const stopRequest = apiService.stopThread(sessionId).catch(error => {
      console.error('Failed to stop backend task:', error);
    });
    abortControllersRef.current.get(sessionId)?.abort();
    await stopRequest;
  };

  const handleExampleQuery = (query: string) => {
    setInputValue(query);
  };

  const setCurrentSessionOntology = (enabled: boolean) => {
    if (!currentSessionId) return;
    setSessionOntologyModes(previous => new Map(previous).set(currentSessionId, enabled));
  };

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessageStream();
    }
  };

  return (
    <div className="app">
      {/* Sidebar */}
      <div className={`sidebar ${sidebarCollapsed ? 'collapsed' : ''}`}>
        <div className="sidebar-header">
          <h1 className="sidebar-title gradient-text">本体数据智能体</h1>
          <button
            className="toggle-btn"
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          >
            {sidebarCollapsed ? '→' : '←'}
          </button>
        </div>

        <div className="sidebar-content">
          {/* Function Buttons Section */}
          <div className="nav-section">
            <div className="nav-section-title">功能</div>
            <button className="nav-item" onClick={createNewSession}>
              <span className="nav-icon">➕</span>
              <span className="nav-text">新建会话</span>
            </button>
          </div>

          {!sidebarCollapsed && (
            <div className="nav-section">
              <div className="nav-section-title">会话设置</div>
              <label className="session-toggle-row">
                <span className="session-toggle-label">本体模式</span>
                <input
                  className="session-toggle-input"
                  type="checkbox"
                  role="switch"
                  checked={ontologyEnabled}
                  disabled={!currentSessionId}
                  onChange={(event) => setCurrentSessionOntology(event.target.checked)}
                  aria-label="为本会话启用本体模式"
                />
                <span className="session-toggle-track" aria-hidden="true">
                  <span className="session-toggle-thumb" />
                </span>
              </label>
            </div>
          )}

          {/* Current Chat Section */}
          <div className="nav-section">
            <div className="nav-section-title">会话列表</div>
            {!sidebarCollapsed && sessions.map((session) => (
              <button
                key={session.id}
                className={`nav-item ${session.id === currentSessionId ? 'active' : ''}`}
                onClick={() => switchSession(session.id)}
                style={{
                  fontSize: '13px',
                  padding: '10px 12px',
                  marginBottom: '4px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between'
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flex: 1 }}>
                  <span className="nav-icon">💬</span>
                  <span className="nav-text" style={{ fontSize: '13px' }}>
                    {session.name}{loadingSessionIds.has(session.id) ? ' · 运行中' : ''}
                  </span>
                </div>
                <span
                  className="delete-session-btn"
                  onClick={(e) => deleteSession(session.id, e)}
                  style={{
                    fontSize: '16px',
                    opacity: 0.6,
                    cursor: 'pointer',
                    padding: '0 4px',
                    transition: 'opacity 0.2s'
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.opacity = '1'}
                  onMouseLeave={(e) => e.currentTarget.style.opacity = '0.6'}
                  title="删除会话"
                >
                  🗑️
                </span>
              </button>
            ))}
          </div>

          {!sidebarCollapsed && (
            <div className="nav-section">
              <div className="nav-section-title">示例问题</div>
              {EXAMPLE_QUERIES.map((query, idx) => (
                <button
                  key={idx}
                  className="nav-item example-query"
                  onClick={() => handleExampleQuery(query)}
                  style={{
                    fontSize: '11px',
                    padding: '8px 10px',
                    whiteSpace: 'normal',
                    textAlign: 'left',
                    lineHeight: '1.4',
                    wordWrap: 'break-word',
                    overflowWrap: 'break-word',
                    height: 'auto',
                    minHeight: '35px',
                    display: 'flex',
                    gap: '6px',
                    alignItems: 'flex-start'
                  }}
                >
                  <span style={{ flexShrink: 0, fontSize: '14px' }}>💡</span>
                  <span style={{ fontSize: '11px', flex: 1, wordBreak: 'break-word' }}>
                    {query}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Main Content */}
      <div className="main-content">
        <div className="chat-header">
          <div className="chat-title">
            {currentSessionId ? `会话：${currentSessionId.substring(0, 20)}...` : 'Ontology Data Agent'}
          </div>
          <div className="header-actions">
            <button className="icon-btn" onClick={() => setMysqlSettingsOpen(true)} title="配置 MySQL 连接">⚙ MySQL 配置</button>
            <button
              className="icon-btn"
              onClick={openBusinessLayer}
              title="全体会话共享的工作区业务语义"
            >
              📘 业务层文档
            </button>
          </div>
        </div>

        {(
          <>
            <div className="chat-container" ref={chatContainerRef}>
              {messages.length === 0 ? (
                <div className="empty-state">
                  <div className="empty-state-icon"><OntologyMark /></div>
                  <h2 className="empty-state-title">欢迎使用 Ontology Data Agent</h2>
                  <p className="empty-state-desc">
                    用日常语言提出你的业务问题。OWL 本体负责解析业务含义，数据目录负责校验物理表和列，
                    分析过程以只读 SQL 的方式运行。
                    <br />
                    可以在侧边栏切换本体模式，对比本体引导与仅元数据的分析效果，
                    也可以直接点击示例问题开始体验。
                  </p>
                </div>
              ) : (
                <>
                  {messages.map((msg, idx) => (
                    <div key={idx}>
                      {/* User message */}
                      {msg.role === 'user' && (
                        <div className="message user">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {msg.content}
                          </ReactMarkdown>
                          <span className="message-timestamp">
                            {new Date(msg.timestamp).toLocaleTimeString()}
                          </span>
                        </div>
                      )}
                      
                      {/* Assistant message with thinking */}
                      {msg.role === 'assistant' && (
                        <>
                          {/* Thinking section - always present, collapsible */}
                          {msg.thinking && msg.thinking.length > 0 && (
                            <ActivityPanel
                              key={`activity-${idx}-${msg.thinkingCollapsed ? 'complete' : 'active'}`}
                              activities={msg.thinking}
                              complete={Boolean(msg.thinkingCollapsed)}
                            />
                          )}
                          
                          {/* Assistant response */}
                          <div className="message assistant">
                            {msg.analysisStatus && msg.analysisStatus !== 'completed' && (
                              <p role="status" className="analysis-status">
                                {{ partial: '部分线索：请注意结论范围与限制', insufficient: '证据不足：尚不能完成判定', failed: '分析未完成' }[msg.analysisStatus]}
                              </p>
                            )}
                            <ReactMarkdown
                              remarkPlugins={[remarkGfm]}
                              urlTransform={(url) => url}
                              components={{
                                a: ({ href, node, children, ...props }) => {
                                  void node;
                                  const childArr = React.Children.toArray(children);
                                  const onlyImg =
                                    childArr.length === 1 &&
                                    React.isValidElement(childArr[0]) &&
                                    (childArr[0] as React.ReactElement).type === 'img';
                                  if (onlyImg) return <>{children}</>;
                                  // Fragment links (#...) are same-page footnote anchors — keep default navigation
                                  const isFragment = href?.startsWith('#');
                                  return (
                                    <a
                                      {...props}
                                      href={href}
                                      {...(!isFragment ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
                                    >
                                      {children}
                                    </a>
                                  );
                                }
                              }}
                            >
                              {repairCollapsedMarkdownTables(msg.content)}
                            </ReactMarkdown>
                            <span className="message-timestamp">
                              {new Date(msg.timestamp).toLocaleTimeString()}
                            </span>
                          </div>
                        </>
                      )}
                    </div>
                  ))}
                  
                  {/* Loading indicator under thinking */}
                  {isLoading && (
                    <div className="loading">
                      <div className="spinner"></div>
                    </div>
                  )}
                </>
              )}
            </div>

            <div className="input-container">
              <div className="input-wrapper">
                <textarea
                  className="chat-input"
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  onKeyPress={handleKeyPress}
                  placeholder="输入业务指标、数据分析或数据表结构相关的问题…"
                  disabled={isLoading}
                />
                <button
                  className={`send-btn ${isLoading ? 'stop' : ''}`}
                  onClick={isLoading ? stopCurrentTask : sendMessageStream}
                  disabled={!isLoading && !inputValue.trim()}
                  title={isLoading ? '停止当前会话的任务' : '发送消息'}
                >
                  {isLoading ? '■ 停止' : '发送'}
                </button>
              </div>
            </div>
        </>
        )}
      </div>

      {businessLayerOpen && (
        <div className="business-layer-overlay" onClick={() => setBusinessLayerOpen(false)}>
          <div className="business-layer-modal" onClick={(e) => e.stopPropagation()}>
            <div className="business-layer-header">
              <div className="business-layer-title">📘 业务层文档</div>
              <button className="icon-btn" onClick={() => setBusinessLayerOpen(false)}>✕</button>
            </div>
            <div className="business-layer-hint">
              在这里描述你的业务语义——术语、指标定义和报表规则。
              它由整个工作区共享，无论本体模式是否开启，都会随每个问题提供给分析智能体。
              已验证的数据库表结构始终优先。
            </div>
            <textarea
              className="business-layer-textarea"
              value={businessLayerDraft}
              onChange={(e) => setBusinessLayerDraft(e.target.value)}
              disabled={businessLayerBusy}
              placeholder={'# 术语\n- VIP 客户 = 年消费超过约定阈值的客户\n\n# 指标定义\n- 收入 = 订单总金额之和'}
            />
            <div className="business-layer-footer">
              <span className="business-layer-status">{businessLayerStatus}</span>
              <button className="icon-btn" onClick={persistBusinessLayer} disabled={businessLayerBusy}>
                保存
              </button>
            </div>
          </div>
        </div>
      )}
      {mysqlSettingsOpen && <MySQLSettingsModal onClose={() => setMysqlSettingsOpen(false)} />}
    </div>
  );
}

export default App;
