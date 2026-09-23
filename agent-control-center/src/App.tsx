import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, Bot, BrainCircuit, CheckCircle2, ChevronDown, CircleAlert,
  FlaskConical, Gauge, Layers3, LoaderCircle, Menu, Network, PanelLeftClose,
  Play, RefreshCw, Save, Search, Send, ServerCog, Settings2, ShieldCheck,
  Sparkles, Waypoints, X,
} from 'lucide-react'
import { api } from './api'
import type { Agent, Health, Instance, PromptDraft, Provider } from './types'

type View = 'overview' | 'instances' | 'agents' | 'prompts' | 'models' | 'lab'
type Notice = { type: 'success' | 'error'; text: string } | null

const nav: Array<{ id: View; label: string; icon: typeof Gauge }> = [
  { id: 'overview', label: 'Visión general', icon: Gauge },
  { id: 'instances', label: 'Instancias', icon: Layers3 },
  { id: 'agents', label: 'Agentes', icon: Bot },
  { id: 'prompts', label: 'Prompts', icon: BrainCircuit },
  { id: 'models', label: 'Modelos', icon: Network },
  { id: 'lab', label: 'Laboratorio', icon: FlaskConical },
]

const defaultPrompt = {
  name: 'Developer Senior Full-Stack',
  role: 'ingeniero de software senior y revisor técnico',
  objective: 'Diseñar, revisar y optimizar código de alta calidad y proponer arquitecturas robustas.',
  specialties: 'Arquitectura de software\nDesarrollo Backend\nDesarrollo Frontend\nTesting y TDD',
  code_review_instructions: 'Identifica primero el lenguaje, framework y propósito.\nMantén el lenguaje y framework originales.\nSepara las suposiciones de los hechos observables.',
  response_format: 'Propósito del código\nProblemas encontrados\nRefactorización propuesta\nPruebas recomendadas',
  restrictions: 'No inventar APIs o funciones.\nNo generar código malicioso.',
  out_of_scope_action: 'Explica brevemente tu especialidad y declina solicitudes sustantivas fuera de ella.',
  tone: 'técnico, directo, profesional y colaborativo',
  response_style: 'detallado, con ejemplos cuando sean útiles y justificando cada decisión',
  default_language: 'es',
  created_by: 'agent-control-center',
}

function App() {
  const [view, setView] = useState<View>('overview')
  const [collapsed, setCollapsed] = useState(false)
  const [instances, setInstances] = useState<Instance[]>([])
  const [instanceCode, setInstanceCode] = useState(localStorage.getItem('acc-instance') || '')
  const [agents, setAgents] = useState<Agent[]>([])
  const [providers, setProviders] = useState<Provider[]>([])
  const [health, setHealth] = useState<Health | null>(null)
  const [loading, setLoading] = useState(true)
  const [notice, setNotice] = useState<Notice>(null)

  const selectedInstance = instances.find((item) => item.Code === instanceCode) || null

  const notify = useCallback((type: 'success' | 'error', text: string) => {
    setNotice({ type, text })
    window.setTimeout(() => setNotice(null), 4200)
  }, [])

  const loadBase = useCallback(async () => {
    setLoading(true)
    try {
      const [instanceData, providerData, healthData] = await Promise.all([
        api.instances(), api.providers(), api.health(),
      ])
      setInstances(instanceData.items)
      setProviders(providerData)
      setHealth(healthData)
      const valid = instanceData.items.some((item) => item.Code === instanceCode)
      if (!valid && instanceData.items[0]) setInstanceCode(instanceData.items[0].Code)
    } catch (error) {
      notify('error', error instanceof Error ? error.message : 'No fue posible cargar la consola.')
    } finally {
      setLoading(false)
    }
  }, [instanceCode, notify])

  const loadAgents = useCallback(async () => {
    if (!instanceCode) return setAgents([])
    try {
      const data = await api.agents(instanceCode)
      setAgents(data.items)
    } catch (error) {
      setAgents([])
      notify('error', error instanceof Error ? error.message : 'No fue posible cargar los agentes.')
    }
  }, [instanceCode, notify])

  useEffect(() => { void loadBase() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (instanceCode) {
      localStorage.setItem('acc-instance', instanceCode)
      void loadAgents()
    }
  }, [instanceCode, loadAgents])

  const content = useMemo(() => {
    const shared = { selectedInstance, agents, providers, notify, refreshAgents: loadAgents }
    if (view === 'instances') return <Instances instances={instances} selected={instanceCode} notify={notify} />
    if (view === 'agents') return <Agents {...shared} />
    if (view === 'prompts') return <Prompts {...shared} />
    if (view === 'models') return <Models {...shared} />
    if (view === 'lab') return <Lab {...shared} />
    return <Overview health={health} instances={instances} agents={agents} providers={providers} selected={selectedInstance} />
  }, [view, health, instances, agents, providers, selectedInstance, instanceCode, notify, loadAgents])

  return (
    <div className={`app-shell ${collapsed ? 'is-collapsed' : ''}`}>
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Waypoints size={23} /></div><div><strong>Agent</strong><span>Control Center</span></div></div>
        <button className="collapse" onClick={() => setCollapsed(!collapsed)} aria-label="Contraer navegación"><PanelLeftClose size={18} /></button>
        <nav>{nav.map(({ id, label, icon: Icon }) => <button key={id} className={view === id ? 'active' : ''} onClick={() => setView(id)}><Icon size={19} /><span>{label}</span></button>)}</nav>
        <div className="sidebar-foot"><ShieldCheck size={17} /><span>Administración local</span></div>
      </aside>
      <main>
        <header className="topbar">
          <button className="mobile-menu"><Menu size={20} /></button>
          <div className="instance-picker">
            <span>Instancia activa</span>
            <div className="select-wrap"><select value={instanceCode} onChange={(e) => setInstanceCode(e.target.value)}><option value="">Selecciona una instancia</option>{instances.map((item) => <option key={item.ID} value={item.Code}>{item.Name} · {item.Code}</option>)}</select><ChevronDown size={16} /></div>
          </div>
          <div className={`api-state ${health?.status === 'healthy' ? 'ok' : ''}`}><span /> API {health?.status === 'healthy' ? 'operativa' : 'sin conexión'}</div>
          <button className="icon-button" onClick={() => void loadBase()} title="Actualizar"><RefreshCw size={18} /></button>
        </header>
        <section className="workspace">{loading ? <Loading /> : content}</section>
      </main>
      {notice && <div className={`toast ${notice.type}`}>{notice.type === 'success' ? <CheckCircle2 size={19} /> : <CircleAlert size={19} />}{notice.text}<button onClick={() => setNotice(null)}><X size={16} /></button></div>}
    </div>
  )
}

function PageHeader({ eyebrow, title, copy, action }: { eyebrow: string; title: string; copy: string; action?: React.ReactNode }) {
  return <div className="page-header"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{copy}</p></div>{action}</div>
}

function Empty({ title, copy }: { title: string; copy: string }) {
  return <div className="empty"><Bot size={30} /><strong>{title}</strong><p>{copy}</p></div>
}

function Loading() { return <div className="loading"><LoaderCircle className="spin" /><span>Cargando configuración…</span></div> }

function Overview({ health, instances, agents, providers, selected }: { health: Health | null; instances: Instance[]; agents: Agent[]; providers: Provider[]; selected: Instance | null }) {
  const metrics = [
    ['Instancias', instances.length, `${instances.filter(i => i.active).length} activas`, Layers3],
    ['Agentes', agents.length, selected?.Name || 'Sin instancia', Bot],
    ['Proveedores', providers.length, `${providers.filter(p => p.active).length} disponibles`, ServerCog],
    ['Diálogos activos', Number(health?.runtime?.active_dialogues || 0), `Máximo ${health?.runtime?.dialogue_max_concurrent || '—'}`, Activity],
  ] as const
  return <>
    <PageHeader eyebrow="Operación" title="Visión general" copy="Estado actual de la plataforma, sus agentes y conexiones de IA." />
    <div className="metric-grid">{metrics.map(([label, value, hint, Icon]) => <article className="metric" key={label}><div className="metric-icon"><Icon size={21} /></div><span>{label}</span><strong>{value}</strong><small>{hint}</small></article>)}</div>
    <div className="split-grid">
      <article className="panel"><div className="panel-title"><div><span className="eyebrow">Runtime</span><h2>Estado del agente</h2></div><span className={`status-pill ${health?.status === 'healthy' ? 'success' : 'danger'}`}>{health?.status || 'offline'}</span></div><dl className="detail-list"><div><dt>Versión</dt><dd>{health?.version || '—'}</dd></div><div><dt>Modelo principal</dt><dd>{String((health?.services?.llm as Record<string, unknown>)?.model || '—')}</dd></div><div><dt>Proveedor</dt><dd>{String((health?.services?.llm as Record<string, unknown>)?.provider || '—')}</dd></div><div><dt>Respuestas automáticas</dt><dd>{health?.runtime?.auto_reply_enabled ? 'Activas' : 'Desactivadas'}</dd></div></dl></article>
      <article className="panel accent-panel"><span className="eyebrow">Instancia seleccionada</span><h2>{selected?.Name || 'Selecciona una instancia'}</h2><p>{selected?.BaseUrl || 'El contexto de instancia es obligatorio para administrar agentes y conocimiento.'}</p>{selected && <div className="instance-meta"><span>{selected.Locale}</span><span>{selected.TimeZone}</span><span>{selected.CountryCode}</span></div>}</article>
    </div>
  </>
}

function Instances({ instances, selected, notify }: { instances: Instance[]; selected: string; notify: (t: 'success' | 'error', m: string) => void }) {
  const [testing, setTesting] = useState('')
  const test = async (code: string) => { setTesting(code); try { await api.testInstance(code); notify('success', `Conexión de ${code} verificada.`) } catch (e) { notify('error', e instanceof Error ? e.message : 'Falló la conexión.') } finally { setTesting('') } }
  return <><PageHeader eyebrow="Infraestructura" title="Instancias SolidSET" copy="Rutas, localización y proveedor de datos de cada entorno registrado." />
    <div className="cards">{instances.map(i => <article key={i.ID} className={`instance-card ${selected === i.Code ? 'selected' : ''}`}><div className="card-top"><div className="cube"><Layers3 size={21} /></div><span className={`status-pill ${i.active ? 'success' : ''}`}>{i.active ? 'Activa' : 'Inactiva'}</span></div><h3>{i.Name}</h3><code>{i.Code}</code><dl className="mini-list"><div><dt>API</dt><dd>{i.BaseUrl}</dd></div><div><dt>Data API</dt><dd>{i.DataAPI?.BaseUrl || 'No configurada'}</dd></div><div><dt>Región</dt><dd>{i.Locale} · {i.TimeZone}</dd></div></dl><button className="secondary" onClick={() => void test(i.Code)} disabled={testing === i.Code}>{testing === i.Code ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />} Probar conexión</button></article>)}</div>
  </>
}

type Shared = { selectedInstance: Instance | null; agents: Agent[]; providers: Provider[]; notify: (t: 'success' | 'error', m: string) => void; refreshAgents: () => Promise<void> }

function Agents({ selectedInstance, agents }: Shared) {
  const [filter, setFilter] = useState('')
  const shown = agents.filter(a => `${a.Name} ${a.FullName} ${a.IDResource}`.toLowerCase().includes(filter.toLowerCase()))
  return <><PageHeader eyebrow="Identidades" title="Agentes especializados" copy={`Recursos activos y asignaciones de ${selectedInstance?.Name || 'la instancia seleccionada'}.`} action={<label className="search"><Search size={17} /><input value={filter} onChange={e => setFilter(e.target.value)} placeholder="Buscar agente" /></label>} />
    {!selectedInstance ? <Empty title="Selecciona una instancia" copy="La lista de agentes siempre está aislada por instancia." /> : shown.length === 0 ? <Empty title="No hay agentes sincronizados" copy="Sincroniza recursos y alcances de esta instancia desde la API." /> : <div className="table-wrap"><table><thead><tr><th>Agente</th><th>Identidades</th><th>Prompt</th><th>Modelos</th><th>Ámbitos</th></tr></thead><tbody>{shown.map(a => <tr key={a.IDResource}><td><div className="agent-name"><span>{initials(a.Name)}</span><div><strong>{a.Name}</strong><small>{a.OrganizationName || 'Sin organización'}</small></div></div></td><td><code title={a.IDResource}>Humano · {shortId(a.IDResource)}</code><code title={a.IDAgentResource || ''}>IA · {shortId(a.IDAgentResource)}</code></td><td>{a.prompt ? <><span className="status-pill success">Publicado</span><small>v{a.prompt.Version} · {a.prompt.Name}</small></> : <span className="status-pill warning">Sin publicar</span>}</td><td><strong>{a.models.length}</strong><small>{a.models.find(m => m.IsDefault)?.Model || 'Sin predeterminado'}</small></td><td><strong>{a.ScopeCount}</strong><small>canales sincronizados</small></td></tr>)}</tbody></table></div>}
  </>
}

function Prompts({ selectedInstance, agents, notify, refreshAgents }: Shared) {
  const [agentId, setAgentId] = useState('')
  const [form, setForm] = useState(defaultPrompt)
  const [draft, setDraft] = useState<PromptDraft | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (!agents.some(a => a.IDResource === agentId)) setAgentId(agents[0]?.IDResource || '') }, [agents, agentId])
  const generate = async () => { if (!selectedInstance || !agentId) return; setBusy(true); try { const payload = { ...form, specialties: lines(form.specialties), code_review_instructions: lines(form.code_review_instructions), response_format: lines(form.response_format), restrictions: lines(form.restrictions) }; const result = await api.generatePrompt(agentId, selectedInstance.Code, payload); setDraft(result); notify('success', `Borrador v${result.Version} generado.`) } catch (e) { notify('error', e instanceof Error ? e.message : 'No fue posible generar el prompt.') } finally { setBusy(false) } }
  const publish = async () => { if (!selectedInstance || !agentId || !draft) return; setBusy(true); try { const result = await api.publishPrompt(agentId, draft.ID, selectedInstance.Code); setDraft(result); await refreshAgents(); notify('success', `Prompt v${result.Version} publicado.`) } catch (e) { notify('error', e instanceof Error ? e.message : 'No fue posible publicar.') } finally { setBusy(false) } }
  return <><PageHeader eyebrow="Comportamiento" title="Editor de prompts" copy="Genera una versión revisable y publícala solo cuando el contenido sea correcto." />
    {!selectedInstance || agents.length === 0 ? <Empty title="No hay un agente disponible" copy="Selecciona una instancia con agentes y alcances sincronizados." /> : <div className="editor-grid"><section className="panel form-panel"><label>Agente<select value={agentId} onChange={e => { setAgentId(e.target.value); setDraft(null) }}>{agents.map(a => <option key={a.IDResource} value={a.IDResource}>{a.Name}</option>)}</select></label><div className="form-row"><Field label="Nombre" value={form.name} onChange={v => setForm({ ...form, name: v })} /><Field label="Idioma" value={form.default_language} onChange={v => setForm({ ...form, default_language: v })} /></div><Field label="Rol" value={form.role} onChange={v => setForm({ ...form, role: v })} /><Field label="Objetivo" value={form.objective} onChange={v => setForm({ ...form, objective: v })} area /><Field label="Especialidades · una por línea" value={form.specialties} onChange={v => setForm({ ...form, specialties: v })} area /><Field label="Instrucciones de revisión · una por línea" value={form.code_review_instructions} onChange={v => setForm({ ...form, code_review_instructions: v })} area /><Field label="Formato de respuesta · uno por línea" value={form.response_format} onChange={v => setForm({ ...form, response_format: v })} area /><Field label="Restricciones · una por línea" value={form.restrictions} onChange={v => setForm({ ...form, restrictions: v })} area /><Field label="Acción fuera de alcance" value={form.out_of_scope_action} onChange={v => setForm({ ...form, out_of_scope_action: v })} area /><div className="form-row"><Field label="Tono" value={form.tone} onChange={v => setForm({ ...form, tone: v })} /><Field label="Estilo" value={form.response_style} onChange={v => setForm({ ...form, response_style: v })} /></div><button className="primary" onClick={() => void generate()} disabled={busy}>{busy ? <LoaderCircle className="spin" size={17} /> : <Sparkles size={17} />} Generar borrador</button></section><section className="panel preview"><div className="panel-title"><div><span className="eyebrow">Previsualización</span><h2>{draft?.Name || 'Sin borrador'}</h2></div>{draft && <span className={`status-pill ${draft.Status === 'active' ? 'success' : 'warning'}`}>{draft.Status}</span>}</div>{draft ? <><div className="prompt-meta"><span>Versión {draft.Version}</span><span>{draft.IDResource === agentId ? 'Agente correcto' : 'Revisar recurso'}</span></div><pre>{draft.SystemPrompt}</pre><button className="primary" onClick={() => void publish()} disabled={busy || draft.Status === 'active'}><Save size={17} /> Publicar esta versión</button></> : <div className="preview-placeholder"><BrainCircuit size={32} /><p>Completa el formulario para generar una versión persistida y revisable.</p></div>}</section></div>}
  </>
}

function Models({ selectedInstance, agents, providers, notify, refreshAgents }: Shared) {
  const [agentId, setAgentId] = useState('')
  const [providerCode, setProviderCode] = useState('')
  const [capabilities, setCapabilities] = useState(['general'])
  const [role, setRole] = useState('general')
  const [priority, setPriority] = useState(100)
  const [busy, setBusy] = useState(false)
  useEffect(() => { setAgentId(current => agents.some(a => a.IDResource === current) ? current : agents[0]?.IDResource || ''); setProviderCode(current => providers.some(p => p.Code === current) ? current : providers.find(p => p.active)?.Code || '') }, [agents, providers])
  const toggle = (cap: string) => setCapabilities(old => old.includes(cap) ? old.filter(v => v !== cap) : [...old, cap])
  const save = async () => { if (!selectedInstance || !agentId || !providerCode || !capabilities.length) return; setBusy(true); try { await api.saveModel(agentId, { IDSolidSETInstance: selectedInstance.ID, ProviderCode: providerCode, Role: role, LocalExecution: providers.find(p => p.Code === providerCode)?.Provider === 'ollama', TrainingMode: 'rag_reinforcement', LearnFromOwner: true, LearnFromSystem: true, LearnFromReactions: true, Capabilities: capabilities, Priority: priority, IsDefault: true, active: true }); await refreshAgents(); notify('success', 'Asignación de modelo guardada.') } catch (e) { notify('error', e instanceof Error ? e.message : 'No fue posible asignar el modelo.') } finally { setBusy(false) } }
  return <><PageHeader eyebrow="Enrutamiento" title="Modelos y capacidades" copy="Asigna conexiones reutilizables a un agente dentro de la instancia seleccionada." />
    <div className="split-grid model-layout"><section className="panel"><span className="eyebrow">Conexiones disponibles</span><h2>Proveedores LLM</h2><div className="provider-list">{providers.map(p => <div key={p.ID}><div className={`provider-dot ${p.active ? 'on' : ''}`} /><div><strong>{p.Name}</strong><small>{p.Provider} · {p.Model}</small></div>{p.IsDefault && <span className="status-pill">Global</span>}</div>)}</div></section><section className="panel form-panel"><span className="eyebrow">Nueva asignación</span><h2>Configurar agente</h2><label>Agente<select value={agentId} onChange={e => setAgentId(e.target.value)}>{agents.map(a => <option key={a.IDResource} value={a.IDResource}>{a.Name}</option>)}</select></label><label>Conexión<select value={providerCode} onChange={e => setProviderCode(e.target.value)}>{providers.filter(p => p.active).map(p => <option key={p.ID} value={p.Code}>{p.Name} · {p.Model}</option>)}</select></label><div className="form-row"><Field label="Rol" value={role} onChange={setRole} /><label>Prioridad<input type="number" value={priority} onChange={e => setPriority(Number(e.target.value))} min="0" max="10000" /></label></div><label>Capacidades</label><div className="chips">{['general', 'coding', 'reasoning', 'sql', 'external_web'].map(cap => <button key={cap} className={capabilities.includes(cap) ? 'selected' : ''} onClick={() => toggle(cap)}>{cap}</button>)}</div><button className="primary" onClick={() => void save()} disabled={busy || !selectedInstance}><Save size={17} /> Guardar asignación</button></section></div>
  </>
}

function Lab({ selectedInstance, agents, notify }: Shared) {
  const [agentId, setAgentId] = useState('')
  const [workRoom, setWorkRoom] = useState('')
  const [sender, setSender] = useState('')
  const [message, setMessage] = useState('Explica la complejidad temporal del algoritmo de Floyd-Warshall.')
  const [session, setSession] = useState('')
  const [answer, setAnswer] = useState('')
  const [elapsed, setElapsed] = useState(0)
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (!agents.some(a => a.IDResource === agentId)) setAgentId(agents[0]?.IDResource || '') }, [agents, agentId])
  const run = async () => { if (!selectedInstance || !agentId || !workRoom || !message.trim()) return notify('error', 'Completa agente, canal y pregunta.'); setBusy(true); setAnswer(''); const start = performance.now(); try { const body: Record<string, unknown> = { IDWorkRoom: workRoom, RawMessage: message, SelectedAgentResourceIds: [agentId], SendToSolidSET: false, SolidSETInstanceCode: selectedInstance.Code }; if (sender) body.SenderResourceId = sender; if (session) body.IDSession = session; const result = await api.dialogue(body); setSession(result.IDSession); setAnswer(result.responses.map(r => `${r.AgentName}\n${r.response}`).join('\n\n')); setElapsed((performance.now() - start) / 1000) } catch (e) { notify('error', e instanceof Error ? e.message : 'La prueba falló.') } finally { setBusy(false) } }
  return <><PageHeader eyebrow="Validación" title="Laboratorio de agentes" copy="Ejecuta una conversación aislada y revisa la respuesta antes de publicarla en SolidSET." />
    <div className="lab-grid"><section className="panel form-panel"><label>Agente<select value={agentId} onChange={e => setAgentId(e.target.value)}>{agents.map(a => <option key={a.IDResource} value={a.IDResource}>{a.Name}</option>)}</select></label><Field label="ID del canal" value={workRoom} onChange={setWorkRoom} /><Field label="ID del remitente · opcional" value={sender} onChange={setSender} /><Field label="Pregunta" value={message} onChange={setMessage} area /><button className="primary" onClick={() => void run()} disabled={busy}>{busy ? <LoaderCircle className="spin" size={17} /> : <Send size={17} />} Ejecutar prueba</button><small className="safe-note"><ShieldCheck size={14} /> La respuesta no será enviada a SolidSET.</small></section><section className="panel lab-result"><div className="panel-title"><div><span className="eyebrow">Resultado</span><h2>Respuesta del agente</h2></div>{elapsed > 0 && <span className="status-pill">{elapsed.toFixed(2)} s</span>}</div>{answer ? <div className="answer">{answer}</div> : <div className="preview-placeholder"><FlaskConical size={32} /><p>La respuesta y la sesión aparecerán aquí.</p></div>}{session && <div className="session"><span>Sesión</span><code>{session}</code></div>}</section></div>
  </>
}

function Field({ label, value, onChange, area = false }: { label: string; value: string; onChange: (value: string) => void; area?: boolean }) { return <label>{label}{area ? <textarea value={value} onChange={e => onChange(e.target.value)} rows={3} /> : <input value={value} onChange={e => onChange(e.target.value)} />}</label> }
function lines(value: string) { return value.split('\n').map(v => v.trim()).filter(Boolean) }
function shortId(value?: string | null) { return value ? `${value.slice(0, 8)}…${value.slice(-4)}` : 'No asignado' }
function initials(value: string) { return value.split(/\s+/).slice(0, 2).map(v => v[0]).join('').toUpperCase() }

export default App
