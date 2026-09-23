import type { AdminUser, Agent, Approval, AutomationEvaluation, AutomationRule, ChangeRecord, Health, IngestionRun, Instance, KnowledgeRecord, KnowledgeSearchResult, ObservabilitySnapshot, PromptDraft, Provider, WorkRoom } from './types'

const API_BASE = (import.meta.env.VITE_AGENT_API_URL || '').replace(/\/$/, '')

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = sessionStorage.getItem('acc-token')
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(init?.headers || {}),
    },
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = typeof payload?.detail === 'string'
      ? payload.detail
      : payload?.detail?.message || `Error HTTP ${response.status}`
    throw new Error(detail)
  }
  return payload as T
}

export const api = {
  authStatus: () => request<{ enabled: boolean; configured: boolean }>('/api/v1/control-center/auth/status'),
  login: (username: string, password: string) => request<{ token: string; expiresAt: string; user: AdminUser }>('/api/v1/control-center/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  me: () => request<AdminUser>('/api/v1/control-center/auth/me'),
  logout: () => request<void>('/api/v1/control-center/auth/logout', { method: 'POST' }),
  health: () => request<Health>('/api/v1/agent/health'),
  instances: () => request<{ total: number; items: Instance[] }>('/api/v1/agent/solidset/instances'),
  testInstance: (code: string) => request<Record<string, unknown>>(`/api/v1/agent/solidset/instances/${encodeURIComponent(code)}/test-connection`, { method: 'POST' }),
  solidsetCatalog: (dataset: 'workrooms' | 'resources', code: string, limit = 20) => request<{ instanceCode: string; rows: Array<Record<string, unknown>>; rowCount: number; hasMore: boolean }>(`/api/v1/agent/solidset/${dataset}?instanceCode=${encodeURIComponent(code)}&offset=0&limit=${limit}`),
  syncSolidset: (operation: 'workrooms' | 'logins' | 'resources' | 'chat-workroom' | 'agent-scopes' | 'agent-models', code: string) => request<Record<string, unknown>>(`/api/v1/agent/solidset/${operation}/sync?instanceCode=${encodeURIComponent(code)}`, { method: 'POST' }),
  agents: (code: string) => request<{ total: number; items: Agent[] }>(`/api/v1/agent/solidset/instances/${encodeURIComponent(code)}/agents`),
  providers: () => request<Provider[]>('/api/v1/agent/llm/providers'),
  saveModel: (resourceId: string, body: Record<string, unknown>) => request(`/api/v1/agent/solidset/agents/${resourceId}/model`, { method: 'PUT', body: JSON.stringify(body) }),
  generatePrompt: (resourceId: string, instanceCode: string, body: Record<string, unknown>) => request<PromptDraft>(`/api/v1/agent/solidset/agents/${resourceId}/prompt/generate?instanceCode=${encodeURIComponent(instanceCode)}`, { method: 'POST', body: JSON.stringify(body) }),
  publishedPrompt: (resourceId: string, instanceCode: string) => request<PromptDraft>(`/api/v1/agent/solidset/agents/${resourceId}/prompt/published?instanceCode=${encodeURIComponent(instanceCode)}`),
  publishPrompt: (resourceId: string, promptId: string, instanceCode: string) => request<PromptDraft>(`/api/v1/agent/solidset/agents/${resourceId}/prompt/${promptId}/publish?instanceCode=${encodeURIComponent(instanceCode)}`, { method: 'POST' }),
  dialogue: (body: Record<string, unknown>) => request<{ IDSession: string; responses: Array<{ AgentName: string; response: string; sent: boolean }> }>('/api/v1/agent/solidset/multi-agent/dialogue', { method: 'POST', body: JSON.stringify(body) }),
  knowledge: (resourceId: string, instanceCode: string, activeOnly = true) => request<{ total: number; items: KnowledgeRecord[] }>(`/api/v1/agent/solidset/agents/${resourceId}/knowledge?instanceCode=${encodeURIComponent(instanceCode)}&activeOnly=${activeOnly}`),
  createKnowledge: (resourceId: string, body: Record<string, unknown>) => request<KnowledgeRecord & { indexed: boolean }>(`/api/v1/agent/solidset/agents/${resourceId}/knowledge`, { method: 'POST', body: JSON.stringify(body) }),
  deactivateKnowledge: (resourceId: string, knowledgeId: string, instanceCode: string) => request(`/api/v1/agent/solidset/agents/${resourceId}/knowledge/${knowledgeId}?instanceCode=${encodeURIComponent(instanceCode)}`, { method: 'DELETE' }),
  reindexKnowledge: (resourceId: string, knowledgeId: string, instanceCode: string) => request(`/api/v1/agent/solidset/agents/${resourceId}/knowledge/${knowledgeId}/index?instanceCode=${encodeURIComponent(instanceCode)}`, { method: 'POST' }),
  searchKnowledge: (resourceId: string, body: Record<string, unknown>) => request<KnowledgeSearchResult>(`/api/v1/agent/solidset/agents/${resourceId}/knowledge/search`, { method: 'POST', body: JSON.stringify(body) }),
  startSystemIngestion: (instanceCode: string, tables: string[], adminKey: string) => request<{ status: string; runId: string; statusUrl: string }>('/api/v1/agent/system-knowledge-ingestion/start', { method: 'POST', headers: { 'X-Agent-Admin-Key': adminKey }, body: JSON.stringify({ instanceCode, tables: tables.length ? tables : null }) }),
  ingestionStatus: (instanceCode: string, adminKey: string) => request<IngestionRun>(`/api/v1/agent/system-knowledge-ingestion/status?instanceCode=${encodeURIComponent(instanceCode)}`, { headers: { 'X-Agent-Admin-Key': adminKey } }),
  workrooms: (instanceCode: string) => request<{ total: number; items: WorkRoom[] }>(`/api/v1/agent/solidset/instances/${encodeURIComponent(instanceCode)}/workrooms`),
  configureWorkroomAgent: (instanceCode: string, workroomId: string, resourceId: string, body: { active: boolean; response_order: number }) => request(`/api/v1/agent/solidset/instances/${encodeURIComponent(instanceCode)}/workrooms/${workroomId}/agents/${resourceId}`, { method: 'PUT', body: JSON.stringify(body) }),
  automationRules: (instanceCode: string) => request<{ total: number; items: AutomationRule[] }>(`/api/v1/agent/solidset/instances/${encodeURIComponent(instanceCode)}/automation-rules`),
  createAutomationRule: (instanceCode: string, body: Record<string, unknown>) => request<{ status: string; rule: AutomationRule }>(`/api/v1/agent/solidset/instances/${encodeURIComponent(instanceCode)}/automation-rules`, { method: 'POST', body: JSON.stringify(body) }),
  deactivateAutomationRule: (instanceCode: string, ruleId: string) => request(`/api/v1/agent/solidset/instances/${encodeURIComponent(instanceCode)}/automation-rules/${ruleId}`, { method: 'DELETE' }),
  evaluateAutomationRule: (instanceCode: string, ruleId: string, message: string, approved: boolean) => request<AutomationEvaluation>(`/api/v1/agent/solidset/instances/${encodeURIComponent(instanceCode)}/automation-rules/${ruleId}/evaluate`, { method: 'POST', body: JSON.stringify({ Message: message, Approved: approved }) }),
  executeAutomationRule: (instanceCode: string, ruleId: string, body: Record<string, unknown>) => request<{ status: string; evaluation: AutomationEvaluation; dialogue?: { responses: Array<{ AgentName: string; response: string; sent: boolean }> } }>(`/api/v1/agent/solidset/instances/${encodeURIComponent(instanceCode)}/automation-rules/${ruleId}/execute`, { method: 'POST', body: JSON.stringify(body) }),
  observability: (instanceCode: string, params: URLSearchParams) => request<ObservabilitySnapshot>(`/api/v1/agent/solidset/instances/${encodeURIComponent(instanceCode)}/observability?${params}`),
  exportObservability: async (instanceCode: string, params: URLSearchParams) => {
    const token = sessionStorage.getItem('acc-token')
    const response = await fetch(`${API_BASE}/api/v1/agent/solidset/instances/${encodeURIComponent(instanceCode)}/observability/export.csv?${params}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
    if (!response.ok) throw new Error(`Error HTTP ${response.status}`)
    const blob = await response.blob(); const url = URL.createObjectURL(blob); const anchor = document.createElement('a')
    anchor.href = url; anchor.download = `agent-observability-${instanceCode}.csv`; anchor.click(); URL.revokeObjectURL(url)
  },
  adminUsers: () => request<{ items: AdminUser[] }>('/api/v1/control-center/users'),
  createAdminUser: (body: Record<string, unknown>) => request<AdminUser>('/api/v1/control-center/users', { method: 'POST', body: JSON.stringify(body) }),
  setAdminUserActive: (id: string, active: boolean) => request<AdminUser>(`/api/v1/control-center/users/${id}`, { method: 'PATCH', body: JSON.stringify({ active }) }),
  approvals: (instanceCode?: string) => request<{ items: Approval[] }>(`/api/v1/control-center/approvals${instanceCode ? `?instanceCode=${encodeURIComponent(instanceCode)}` : ''}`),
  requestApproval: (body: Record<string, unknown>) => request<Approval>('/api/v1/control-center/approvals', { method: 'POST', body: JSON.stringify(body) }),
  decideApproval: (id: string, decision: 'approved' | 'rejected', note = '') => request<Approval>(`/api/v1/control-center/approvals/${id}/decision`, { method: 'POST', body: JSON.stringify({ decision, note }) }),
  changes: (instanceCode?: string) => request<{ items: ChangeRecord[] }>(`/api/v1/control-center/changes${instanceCode ? `?instanceCode=${encodeURIComponent(instanceCode)}` : ''}`),
  restoreChange: (id: string, approvalId: string) => request(`/api/v1/control-center/changes/${id}/restore`, { method: 'POST', body: JSON.stringify({ approvalId }) }),
}
