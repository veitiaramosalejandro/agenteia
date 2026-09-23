import type { Agent, Health, Instance, PromptDraft, Provider } from './types'

const API_BASE = (import.meta.env.VITE_AGENT_API_URL || '').replace(/\/$/, '')

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
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
  health: () => request<Health>('/api/v1/agent/health'),
  instances: () => request<{ total: number; items: Instance[] }>('/api/v1/agent/solidset/instances'),
  testInstance: (code: string) => request<Record<string, unknown>>(`/api/v1/agent/solidset/instances/${encodeURIComponent(code)}/test-connection`, { method: 'POST' }),
  agents: (code: string) => request<{ total: number; items: Agent[] }>(`/api/v1/agent/solidset/instances/${encodeURIComponent(code)}/agents`),
  providers: () => request<Provider[]>('/api/v1/agent/llm/providers'),
  saveModel: (resourceId: string, body: Record<string, unknown>) => request(`/api/v1/agent/solidset/agents/${resourceId}/model`, { method: 'PUT', body: JSON.stringify(body) }),
  generatePrompt: (resourceId: string, instanceCode: string, body: Record<string, unknown>) => request<PromptDraft>(`/api/v1/agent/solidset/agents/${resourceId}/prompt/generate?instanceCode=${encodeURIComponent(instanceCode)}`, { method: 'POST', body: JSON.stringify(body) }),
  publishPrompt: (resourceId: string, promptId: string, instanceCode: string) => request<PromptDraft>(`/api/v1/agent/solidset/agents/${resourceId}/prompt/${promptId}/publish?instanceCode=${encodeURIComponent(instanceCode)}`, { method: 'POST' }),
  dialogue: (body: Record<string, unknown>) => request<{ IDSession: string; responses: Array<{ AgentName: string; response: string; sent: boolean }> }>('/api/v1/agent/solidset/multi-agent/dialogue', { method: 'POST', body: JSON.stringify(body) }),
}
