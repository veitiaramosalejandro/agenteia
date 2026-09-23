import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Activity, Bot, BrainCircuit, CheckCircle2, ChevronDown, CircleAlert,
  Database, Download, FileSearch, FlaskConical, Gauge, Layers3, LoaderCircle, Menu, Network, PanelLeftClose,
  History, LogOut, Play, RefreshCw, Save, Search, Send, ServerCog, ShieldCheck,
  Sparkles, Trash2, UploadCloud, UserCog, Waypoints, Workflow, X,
} from 'lucide-react'
import { api } from './api'
import type { AdminUser, Agent, Approval, AutomationEvaluation, AutomationRule, ChangeRecord, Health, IngestionRun, Instance, KnowledgeRecord, KnowledgeSearchResult, ObservabilitySnapshot, PromptDraft, Provider, WorkRoom } from './types'

type View = 'overview' | 'instances' | 'agents' | 'prompts' | 'models' | 'knowledge' | 'automation' | 'observability' | 'governance' | 'lab'
type Notice = { type: 'success' | 'error'; text: string } | null

const nav: Array<{ id: View; label: string; icon: typeof Gauge }> = [
  { id: 'overview', label: 'Visión general', icon: Gauge },
  { id: 'instances', label: 'Instancias', icon: Layers3 },
  { id: 'agents', label: 'Agentes', icon: Bot },
  { id: 'prompts', label: 'Prompts', icon: BrainCircuit },
  { id: 'models', label: 'Modelos', icon: Network },
  { id: 'knowledge', label: 'Conocimiento', icon: Database },
  { id: 'automation', label: 'Automatización', icon: Workflow },
  { id: 'observability', label: 'Observabilidad', icon: Activity },
  { id: 'governance', label: 'Gobierno', icon: UserCog },
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
  const [authReady, setAuthReady] = useState(false)
  const [authEnabled, setAuthEnabled] = useState(true)
  const [authConfigured, setAuthConfigured] = useState(true)
  const [currentUser, setCurrentUser] = useState<AdminUser | null>(null)

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

  useEffect(() => {
    void (async () => {
      try {
        const status = await api.authStatus(); setAuthEnabled(status.enabled); setAuthConfigured(status.configured)
        if (!status.enabled) setCurrentUser({ ID: 'disabled', username: 'local', displayName: 'Modo local', role: 'administrator', active: true, permissions: ['read', 'configure', 'operate', 'approve', 'restore', 'manage_users'] })
        else if (sessionStorage.getItem('acc-token')) setCurrentUser(await api.me())
      } catch { sessionStorage.removeItem('acc-token') }
      finally { setAuthReady(true) }
    })()
  }, [])
  useEffect(() => { if (authReady && currentUser) void loadBase() }, [authReady, currentUser]) // eslint-disable-line react-hooks/exhaustive-deps
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
    if (view === 'knowledge') return <Knowledge {...shared} />
    if (view === 'automation') return <Automation {...shared} />
    if (view === 'observability') return <Observability {...shared} />
    if (view === 'governance') return <Governance selectedInstance={selectedInstance} currentUser={currentUser!} notify={notify} authEnabled={authEnabled} />
    if (view === 'lab') return <Lab {...shared} />
    return <Overview health={health} instances={instances} agents={agents} providers={providers} selected={selectedInstance} />
  }, [view, health, instances, agents, providers, selectedInstance, instanceCode, notify, loadAgents, currentUser, authEnabled])

  if (!authReady) return <Loading />
  if (authEnabled && !authConfigured) return <AuthSetupRequired />
  if (authEnabled && !currentUser) return <Login onLogin={(user) => { setCurrentUser(user); void loadBase() }} />

  const logout = async () => { try { await api.logout() } catch { /* revoke locally even if the API is unavailable */ } sessionStorage.removeItem('acc-token'); setCurrentUser(null) }

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
          <div className="current-user"><strong>{currentUser?.displayName}</strong><small>{currentUser?.role}</small></div>
          {authEnabled && <button className="icon-button" onClick={() => void logout()} title="Cerrar sesión"><LogOut size={17} /></button>}
          <button className="icon-button" onClick={() => void loadBase()} title="Actualizar"><RefreshCw size={18} /></button>
        </header>
        <section className="workspace">{loading ? <Loading /> : content}</section>
      </main>
      {notice && <div className={`toast ${notice.type}`}>{notice.type === 'success' ? <CheckCircle2 size={19} /> : <CircleAlert size={19} />}{notice.text}<button onClick={() => setNotice(null)}><X size={16} /></button></div>}
    </div>
  )
}

function AuthSetupRequired() {
  return <div className="login-shell"><div className="login-card"><div className="brand-mark"><ShieldCheck size={25} /></div><span className="eyebrow">Configuración requerida</span><h1>Crea el administrador inicial</h1><p>Define las variables siguientes en el archivo .env y recrea el servicio agent-service.</p><pre>CONTROL_CENTER_BOOTSTRAP_USERNAME=admin{`\n`}CONTROL_CENTER_BOOTSTRAP_PASSWORD=tu-clave-de-al-menos-12-caracteres</pre><div className="safe-note"><CircleAlert size={15} /> No se incluye una contraseña predeterminada.</div></div></div>
}

function Login({ onLogin }: { onLogin: (user: AdminUser) => void }) {
  const [username, setUsername] = useState(''); const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false); const [error, setError] = useState('')
  const submit = async (event: React.FormEvent) => { event.preventDefault(); setBusy(true); setError(''); try { const result = await api.login(username, password); sessionStorage.setItem('acc-token', result.token); onLogin(result.user) } catch (e) { setError(e instanceof Error ? e.message : 'No fue posible iniciar sesión.') } finally { setBusy(false) } }
  return <div className="login-shell"><form className="login-card" onSubmit={submit}><div className="brand-mark"><Waypoints size={25} /></div><span className="eyebrow">Agent Control Center</span><h1>Acceso administrativo</h1><p>Inicia sesión para administrar agentes y configuraciones.</p><label>Usuario<input autoFocus autoComplete="username" value={username} onChange={e => setUsername(e.target.value)} /></label><label>Contraseña<input type="password" autoComplete="current-password" value={password} onChange={e => setPassword(e.target.value)} /></label>{error && <div className="error-box">{error}</div>}<button className="primary" disabled={busy || !username || !password}>{busy ? <LoaderCircle className="spin" size={16} /> : <ShieldCheck size={16} />} Entrar</button></form></div>
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

function Knowledge({ selectedInstance, agents, notify }: Shared) {
  const [tab, setTab] = useState<'sources' | 'ingestion' | 'test'>('sources')
  const [agentId, setAgentId] = useState('')
  const [records, setRecords] = useState<KnowledgeRecord[]>([])
  const [showInactive, setShowInactive] = useState(false)
  const [busy, setBusy] = useState(false)
  const [title, setTitle] = useState('')
  const [source, setSource] = useState('manual')
  const [workRoom, setWorkRoom] = useState('')
  const [knowledgeText, setKnowledgeText] = useState('')
  const [adminKey, setAdminKey] = useState('')
  const [tables, setTables] = useState('')
  const [run, setRun] = useState<IngestionRun | null>(null)
  const [query, setQuery] = useState('')
  const [ragWorkRoom, setRagWorkRoom] = useState('')
  const [minScore, setMinScore] = useState(0.6)
  const [includeSystem, setIncludeSystem] = useState(true)
  const [result, setResult] = useState<KnowledgeSearchResult | null>(null)

  useEffect(() => {
    if (!agents.some(a => a.IDResource === agentId)) setAgentId(agents[0]?.IDResource || '')
  }, [agents, agentId])

  const loadRecords = useCallback(async () => {
    if (!selectedInstance || !agentId) return setRecords([])
    try {
      const data = await api.knowledge(agentId, selectedInstance.Code, !showInactive)
      setRecords(data.items)
    } catch (error) {
      setRecords([])
      notify('error', error instanceof Error ? error.message : 'No fue posible cargar el conocimiento.')
    }
  }, [selectedInstance, agentId, showInactive, notify])

  useEffect(() => { void loadRecords() }, [loadRecords])

  const create = async () => {
    if (!selectedInstance || !agentId || !knowledgeText.trim()) return
    setBusy(true)
    try {
      const body: Record<string, unknown> = {
        SolidSETInstanceCode: selectedInstance.Code,
        Title: title.trim() || null,
        KnowledgeText: knowledgeText.trim(),
        Source: source,
        active: true,
      }
      if (workRoom.trim()) body.IDWorkRoom = workRoom.trim()
      const saved = await api.createKnowledge(agentId, body)
      setTitle(''); setWorkRoom(''); setKnowledgeText('')
      await loadRecords()
      notify(saved.indexed ? 'success' : 'error', saved.indexed ? 'Fuente guardada e indexada.' : 'Fuente guardada, pero falta indexarla.')
    } catch (error) {
      notify('error', error instanceof Error ? error.message : 'No fue posible guardar la fuente.')
    } finally { setBusy(false) }
  }

  const deactivate = async (record: KnowledgeRecord) => {
    if (!selectedInstance || !window.confirm(`¿Desactivar “${record.Title || record.Source}”?`)) return
    setBusy(true)
    try {
      await api.deactivateKnowledge(agentId, record.ID, selectedInstance.Code)
      await loadRecords()
      notify('success', 'Fuente desactivada y retirada del índice.')
    } catch (error) { notify('error', error instanceof Error ? error.message : 'No fue posible desactivar la fuente.') }
    finally { setBusy(false) }
  }

  const reindex = async (record: KnowledgeRecord) => {
    if (!selectedInstance) return
    setBusy(true)
    try {
      await api.reindexKnowledge(agentId, record.ID, selectedInstance.Code)
      notify('success', 'Fuente reindexada en Qdrant.')
    } catch (error) { notify('error', error instanceof Error ? error.message : 'No fue posible reindexar.') }
    finally { setBusy(false) }
  }

  const refreshRun = async () => {
    if (!selectedInstance || !adminKey) return notify('error', 'Introduce la clave administrativa de ingestión.')
    setBusy(true)
    try { setRun(await api.ingestionStatus(selectedInstance.Code, adminKey)) }
    catch (error) { notify('error', error instanceof Error ? error.message : 'No fue posible consultar la ingestión.') }
    finally { setBusy(false) }
  }

  const startIngestion = async () => {
    if (!selectedInstance || !adminKey) return notify('error', 'Introduce la clave administrativa de ingestión.')
    setBusy(true)
    try {
      await api.startSystemIngestion(selectedInstance.Code, lines(tables), adminKey)
      setRun(await api.ingestionStatus(selectedInstance.Code, adminKey))
      notify('success', 'Ingestión del conocimiento del sistema encolada.')
    } catch (error) { notify('error', error instanceof Error ? error.message : 'No fue posible iniciar la ingestión.') }
    finally { setBusy(false) }
  }

  const searchRag = async () => {
    if (!selectedInstance || !agentId || !query.trim()) return
    setBusy(true); setResult(null)
    try {
      const body: Record<string, unknown> = {
        SolidSETInstanceCode: selectedInstance.Code,
        Query: query.trim(), Limit: 5, MinScore: minScore,
        IncludeSystemKnowledge: includeSystem,
      }
      if (ragWorkRoom.trim()) body.IDWorkRoom = ragWorkRoom.trim()
      setResult(await api.searchKnowledge(agentId, body))
    } catch (error) { notify('error', error instanceof Error ? error.message : 'La prueba RAG falló.') }
    finally { setBusy(false) }
  }

  const agentPicker = <label>Agente<select value={agentId} onChange={e => { setAgentId(e.target.value); setResult(null) }}>{agents.map(a => <option key={a.IDResource} value={a.IDResource}>{a.Name}</option>)}</select></label>
  if (!selectedInstance || agents.length === 0) return <><PageHeader eyebrow="RAG" title="Conocimiento" copy="Fuentes, ingestión y recuperación aisladas por instancia y agente." /><Empty title="No hay un agente disponible" copy="Selecciona una instancia con agentes sincronizados." /></>

  return <>
    <PageHeader eyebrow="RAG" title="Conocimiento" copy={`Administra fuentes y valida la recuperación en ${selectedInstance.Name}.`} />
    <div className="knowledge-tabs">
      <button className={tab === 'sources' ? 'active' : ''} onClick={() => setTab('sources')}><Database size={16} /> Fuentes</button>
      <button className={tab === 'ingestion' ? 'active' : ''} onClick={() => setTab('ingestion')}><UploadCloud size={16} /> Ingestión</button>
      <button className={tab === 'test' ? 'active' : ''} onClick={() => setTab('test')}><FileSearch size={16} /> Prueba RAG</button>
    </div>

    {tab === 'sources' && <div className="knowledge-layout">
      <section className="panel form-panel">
        <span className="eyebrow">Nueva fuente</span><h2>Texto verificado</h2>
        {agentPicker}
        <Field label="Título" value={title} onChange={setTitle} />
        <label>Procedencia<select value={source} onChange={e => setSource(e.target.value)}><option value="manual">Manual</option><option value="manual_policy">Política interna</option><option value="documentation">Documentación verificada</option></select></label>
        <Field label="ID del canal · opcional" value={workRoom} onChange={setWorkRoom} />
        <Field label="Contenido" value={knowledgeText} onChange={setKnowledgeText} area />
        <button className="primary" onClick={() => void create()} disabled={busy || !knowledgeText.trim()}>{busy ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />} Guardar e indexar</button>
        <small className="safe-note"><ShieldCheck size={14} /> Solo se aceptan fuentes explícitas; una respuesta generada no se registra aquí automáticamente.</small>
      </section>
      <section className="panel knowledge-list-panel">
        <div className="panel-title"><div><span className="eyebrow">Inventario</span><h2>{records.length} fuentes</h2></div><label className="inline-check"><input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} /> Incluir inactivas</label></div>
        <div className="knowledge-list">{records.length === 0 ? <div className="preview-placeholder"><Database size={30} /><p>Este agente todavía no tiene fuentes registradas.</p></div> : records.map(record => <article key={record.ID} className={!record.active ? 'inactive' : ''}><div><span className={`status-pill ${record.active ? 'success' : ''}`}>{record.active ? 'Activa' : 'Inactiva'}</span><span className="source-tag">{record.Source}</span></div><h3>{record.Title || 'Sin título'}</h3><p>{record.KnowledgeText}</p><footer><span>{new Date(record.Stamp).toLocaleString()}</span>{record.IDWorkRoom && <code>Canal {shortId(record.IDWorkRoom)}</code>}<div><button className="icon-button" title="Reindexar" disabled={!record.active || busy} onClick={() => void reindex(record)}><RefreshCw size={15} /></button><button className="icon-button danger-button" title="Desactivar" disabled={!record.active || busy} onClick={() => void deactivate(record)}><Trash2 size={15} /></button></div></footer></article>)}</div>
      </section>
    </div>}

    {tab === 'ingestion' && <div className="split-grid ingestion-layout">
      <section className="panel form-panel"><span className="eyebrow">Catálogo SolidSET</span><h2>Nueva ejecución</h2><p className="section-copy">Materializa tablas autorizadas de la Data API como conocimiento del sistema de esta instancia.</p><label>Clave administrativa<input type="password" value={adminKey} onChange={e => setAdminKey(e.target.value)} autoComplete="off" /></label><Field label="Tablas · una por línea; vacío usa la selección predeterminada" value={tables} onChange={setTables} area /><button className="primary" disabled={busy || !adminKey} onClick={() => void startIngestion()}><UploadCloud size={17} /> Iniciar ingestión</button><small className="safe-note"><ShieldCheck size={14} /> La clave permanece únicamente en memoria durante esta sesión de la página.</small></section>
      <section className="panel"><div className="panel-title"><div><span className="eyebrow">Última ejecución</span><h2>{run?.ExecutionState || run?.Status || 'Sin consultar'}</h2></div><button className="secondary" disabled={busy || !adminKey} onClick={() => void refreshRun()}><RefreshCw size={15} /> Actualizar</button></div><div className="progress-track"><span style={{ width: `${run?.ProgressPercentage || 0}%` }} /></div><strong className="progress-value">{run?.ProgressPercentage || 0}%</strong><dl className="detail-list"><div><dt>Tablas</dt><dd>{run ? `${run.TablesCompleted || 0} / ${run.TablesTotal || 0}` : '—'}</dd></div><div><dt>Filas procesadas</dt><dd>{run?.RowsProcessed ?? '—'}</dd></div><div><dt>Puntos indexados</dt><dd>{run?.PointsIndexed ?? '—'}</dd></div><div><dt>Worker activo</dt><dd>{run ? (run.Alive ? 'Sí' : 'No') : '—'}</dd></div></dl>{run?.Error && <div className="error-box">{run.Error}</div>}</section>
    </div>}

    {tab === 'test' && <div className="lab-grid">
      <section className="panel form-panel"><span className="eyebrow">Recuperación aislada</span><h2>Consulta semántica</h2>{agentPicker}<Field label="ID del canal · opcional" value={ragWorkRoom} onChange={setRagWorkRoom} /><Field label="Consulta" value={query} onChange={setQuery} area /><label>Puntuación mínima · {minScore.toFixed(2)}<input type="range" min="0" max="1" step="0.05" value={minScore} onChange={e => setMinScore(Number(e.target.value))} /></label><label className="inline-check"><input type="checkbox" checked={includeSystem} onChange={e => setIncludeSystem(e.target.checked)} /> Incluir conocimiento del sistema</label><button className="primary" disabled={busy || !query.trim()} onClick={() => void searchRag()}>{busy ? <LoaderCircle className="spin" size={17} /> : <Search size={17} />} Probar recuperación</button><small className="safe-note"><ShieldCheck size={14} /> Esta prueba no invoca al LLM y no envía mensajes a SolidSET.</small></section>
      <section className="panel rag-results"><div className="panel-title"><div><span className="eyebrow">Contexto recuperado</span><h2>Resultados RAG</h2></div>{result && <span className="status-pill">{result.privateMatchCount + result.systemMatchCount} coincidencias</span>}</div>{!result ? <div className="preview-placeholder"><FileSearch size={32} /><p>Ejecuta una consulta para inspeccionar exactamente el contexto disponible para el agente.</p></div> : <><ResultContext title="Fuentes privadas" count={result.privateMatchCount} text={result.privateContext} /><ResultContext title="Conocimiento del sistema" count={result.systemMatchCount} text={result.systemContext} /></>}</section>
    </div>}
  </>
}

function ResultContext({ title, count, text }: { title: string; count: number; text: string }) {
  return <div className="result-context"><div><strong>{title}</strong><span>{count}</span></div>{text ? <pre>{text}</pre> : <p>Sin coincidencias por encima del umbral.</p>}</div>
}

function Automation({ selectedInstance, agents, notify }: Shared) {
  const [tab, setTab] = useState<'channels' | 'rules' | 'execute'>('channels')
  const [rooms, setRooms] = useState<WorkRoom[]>([])
  const [rules, setRules] = useState<AutomationRule[]>([])
  const [roomId, setRoomId] = useState('')
  const [agentId, setAgentId] = useState('')
  const [ruleId, setRuleId] = useState('')
  const [busy, setBusy] = useState(false)
  const [name, setName] = useState('Revisión técnica controlada')
  const [trigger, setTrigger] = useState<'manual' | 'selected_message'>('manual')
  const [instruction, setInstruction] = useState('Responde dentro de la especialidad publicada del agente.')
  const [requiredCaps, setRequiredCaps] = useState<string[]>(['general'])
  const [maxRuns, setMaxRuns] = useState(10)
  const [requireApproval, setRequireApproval] = useState(true)
  const [message, setMessage] = useState('Revisa este pedido y prepara una respuesta técnica.')
  const [sender, setSender] = useState('')
  const [approved, setApproved] = useState(false)
  const [sendToSolidSET, setSendToSolidSET] = useState(false)
  const [evaluation, setEvaluation] = useState<AutomationEvaluation | null>(null)
  const [output, setOutput] = useState('')

  const load = useCallback(async () => {
    if (!selectedInstance) { setRooms([]); setRules([]); return }
    try {
      const [roomData, ruleData] = await Promise.all([
        api.workrooms(selectedInstance.Code), api.automationRules(selectedInstance.Code),
      ])
      setRooms(roomData.items); setRules(ruleData.items)
      setRoomId(current => roomData.items.some(r => r.IDWorkRoom === current) ? current : roomData.items[0]?.IDWorkRoom || '')
      setRuleId(current => ruleData.items.some(r => r.ID === current && r.active) ? current : ruleData.items.find(r => r.active)?.ID || '')
    } catch (error) { notify('error', error instanceof Error ? error.message : 'No fue posible cargar la automatización.') }
  }, [selectedInstance, notify])

  useEffect(() => { void load() }, [load])
  useEffect(() => { if (!agents.some(a => a.IDResource === agentId)) setAgentId(agents[0]?.IDResource || '') }, [agents, agentId])

  const selectedRoom = rooms.find(room => room.IDWorkRoom === roomId)
  const selectedRule = rules.find(rule => rule.ID === ruleId)
  const activeAssignment = selectedRoom?.agents.find(a => a.IDResource === agentId)
  const agentCapabilities = Array.from(new Set(agents.find(a => a.IDResource === agentId)?.models.flatMap(m => m.Capabilities) || [])).sort()

  const saveAssignment = async (resourceId: string, active: boolean, order: number) => {
    if (!selectedInstance || !roomId) return
    setBusy(true)
    try {
      await api.configureWorkroomAgent(selectedInstance.Code, roomId, resourceId, { active, response_order: order })
      await load(); notify('success', 'Asignación del canal actualizada.')
    } catch (error) { notify('error', error instanceof Error ? error.message : 'No fue posible configurar el canal.') }
    finally { setBusy(false) }
  }

  const toggleCap = (cap: string) => setRequiredCaps(old => old.includes(cap) ? old.filter(v => v !== cap) : [...old, cap])
  const createRule = async () => {
    if (!selectedInstance || !roomId || !agentId || !name.trim()) return
    setBusy(true)
    try {
      const result = await api.createAutomationRule(selectedInstance.Code, {
        IDResource: agentId, IDWorkRoom: roomId, Name: name.trim(), TriggerType: trigger,
        Instruction: instruction.trim(), RequiredCapabilities: requiredCaps,
        MaxRunsPerHour: maxRuns, RequireApproval: requireApproval, active: true,
      })
      await load(); setRuleId(result.rule.ID); notify('success', 'Regla controlada creada.')
    } catch (error) { notify('error', error instanceof Error ? error.message : 'No fue posible crear la regla.') }
    finally { setBusy(false) }
  }

  const removeRule = async (rule: AutomationRule) => {
    if (!selectedInstance || !window.confirm(`¿Desactivar “${rule.Name}”?`)) return
    setBusy(true)
    try { await api.deactivateAutomationRule(selectedInstance.Code, rule.ID); await load(); notify('success', 'Regla desactivada.') }
    catch (error) { notify('error', error instanceof Error ? error.message : 'No fue posible desactivar la regla.') }
    finally { setBusy(false) }
  }

  const evaluate = async () => {
    if (!selectedInstance || !ruleId || !message.trim()) return
    setBusy(true); setOutput('')
    try { setEvaluation(await api.evaluateAutomationRule(selectedInstance.Code, ruleId, message.trim(), approved)) }
    catch (error) { notify('error', error instanceof Error ? error.message : 'No fue posible evaluar la regla.') }
    finally { setBusy(false) }
  }

  const execute = async () => {
    if (!selectedInstance || !ruleId || !message.trim()) return
    setBusy(true); setOutput('')
    try {
      const body: Record<string, unknown> = { Message: message.trim(), Approved: approved, SendToSolidSET: sendToSolidSET }
      if (sender.trim()) body.SenderResourceId = sender.trim()
      const response = await api.executeAutomationRule(selectedInstance.Code, ruleId, body)
      setEvaluation(response.evaluation)
      setOutput(response.dialogue?.responses.map(item => `${item.AgentName}\n${item.response}`).join('\n\n') || '')
      notify(response.status === 'completed' ? 'success' : 'error', response.status === 'completed' ? 'Tarea controlada completada.' : 'La política bloqueó la ejecución.')
    } catch (error) { notify('error', error instanceof Error ? error.message : 'La ejecución controlada falló.') }
    finally { setBusy(false) }
  }

  if (!selectedInstance) return <><PageHeader eyebrow="Control" title="Automatización" copy="Canales, reglas y tareas con permisos explícitos." /><Empty title="Selecciona una instancia" copy="Toda automatización pertenece a una instancia SolidSET." /></>
  return <>
    <PageHeader eyebrow="Control" title="Automatización" copy="Configura agentes por canal y ejecuta tareas con capacidades, límites y aprobación verificables." />
    <div className="knowledge-tabs"><button className={tab === 'channels' ? 'active' : ''} onClick={() => setTab('channels')}><Layers3 size={16} /> Canales</button><button className={tab === 'rules' ? 'active' : ''} onClick={() => setTab('rules')}><Workflow size={16} /> Reglas</button><button className={tab === 'execute' ? 'active' : ''} onClick={() => setTab('execute')}><Play size={16} /> Tareas controladas</button></div>

    {tab === 'channels' && <div className="channel-layout"><section className="panel form-panel"><span className="eyebrow">Ámbito</span><h2>Canal seleccionado</h2><label>Canal<select value={roomId} onChange={e => setRoomId(e.target.value)}>{rooms.map(room => <option key={room.IDWorkRoom} value={room.IDWorkRoom}>{room.Name || room.Code || shortId(room.IDWorkRoom)}</option>)}</select></label>{selectedRoom && <><dl className="detail-list"><div><dt>Código</dt><dd>{selectedRoom.Code || '—'}</dd></div><div><dt>ID</dt><dd><code>{selectedRoom.IDWorkRoom}</code></dd></div><div><dt>Estado</dt><dd>{selectedRoom.active ? 'Activo' : 'Inactivo'}</dd></div></dl><p className="section-copy">{selectedRoom.Description || 'Sin descripción sincronizada.'}</p></>}</section><section className="panel"><span className="eyebrow">Asignaciones</span><h2>Agentes del canal</h2><div className="assignment-list">{agents.map(agent => { const assignment = selectedRoom?.agents.find(item => item.IDResource === agent.IDResource); return <article key={agent.IDResource}><div className="agent-name"><span>{initials(agent.Name)}</span><div><strong>{agent.Name}</strong><small>{agent.models.flatMap(m => m.Capabilities).join(', ') || 'Sin capacidades'}</small></div></div><label className="inline-check"><input type="checkbox" checked={Boolean(assignment?.active)} disabled={busy || !selectedRoom?.active} onChange={e => void saveAssignment(agent.IDResource, e.target.checked, assignment?.response_order || 0)} /> Activo</label><label>Orden<input type="number" min="0" max="1000" value={assignment?.response_order || 0} disabled={busy || !assignment?.active} onChange={e => void saveAssignment(agent.IDResource, true, Number(e.target.value))} /></label></article>})}</div></section></div>}

    {tab === 'rules' && <div className="automation-layout"><section className="panel form-panel"><span className="eyebrow">Nueva política</span><h2>Regla de respuesta</h2><label>Canal<select value={roomId} onChange={e => setRoomId(e.target.value)}>{rooms.filter(r => r.active).map(room => <option key={room.IDWorkRoom} value={room.IDWorkRoom}>{room.Name || room.Code}</option>)}</select></label><label>Agente<select value={agentId} onChange={e => setAgentId(e.target.value)}>{agents.map(agent => <option key={agent.IDResource} value={agent.IDResource}>{agent.Name}</option>)}</select></label>{!activeAssignment?.active && <div className="error-box">Activa primero este agente en el canal seleccionado.</div>}<Field label="Nombre" value={name} onChange={setName} /><label>Disparador<select value={trigger} onChange={e => setTrigger(e.target.value as 'manual' | 'selected_message')}><option value="manual">Solo ejecución manual</option><option value="selected_message">Mensaje dirigido al agente</option></select></label><Field label="Instrucción operativa" value={instruction} onChange={setInstruction} area /><label>Capacidades requeridas</label><div className="chips">{['general', 'coding', 'reasoning', 'sql', 'external_web'].map(cap => <button key={cap} className={requiredCaps.includes(cap) ? 'selected' : ''} onClick={() => toggleCap(cap)}>{cap}</button>)}</div><div className="form-row"><label>Máximo por hora<input type="number" min="1" max="1000" value={maxRuns} onChange={e => setMaxRuns(Number(e.target.value))} /></label><label className="inline-check approval-check"><input type="checkbox" checked={requireApproval} onChange={e => setRequireApproval(e.target.checked)} /> Requiere aprobación</label></div><button className="primary" disabled={busy || !activeAssignment?.active} onClick={() => void createRule()}><Save size={17} /> Crear regla</button><small className="safe-note"><ShieldCheck size={14} /> Las capacidades se validan nuevamente antes de cada ejecución.</small></section><section className="panel"><span className="eyebrow">Políticas registradas</span><h2>{rules.filter(r => r.active).length} reglas activas</h2><div className="rule-list">{rules.length === 0 ? <div className="preview-placeholder"><Workflow size={30} /><p>No existen reglas para esta instancia.</p></div> : rules.map(rule => <article key={rule.ID} className={!rule.active ? 'inactive' : ''}><div><span className={`status-pill ${rule.active ? 'success' : ''}`}>{rule.active ? 'Activa' : 'Inactiva'}</span><span className="source-tag">{rule.TriggerType}</span></div><h3>{rule.Name}</h3><p>{rule.Instruction || 'Sin instrucción adicional.'}</p><div className="chips">{rule.RequiredCapabilities.map(cap => <span key={cap}>{cap}</span>)}</div><footer><span>{rule.MaxRunsPerHour}/hora</span><span>{rule.RequireApproval ? 'Con aprobación' : 'Sin aprobación previa'}</span><button className="icon-button danger-button" disabled={!rule.active || busy} onClick={() => void removeRule(rule)}><Trash2 size={15} /></button></footer></article>)}</div></section></div>}

    {tab === 'execute' && <div className="lab-grid"><section className="panel form-panel"><span className="eyebrow">Ejecución</span><h2>Tarea controlada</h2><label>Regla<select value={ruleId} onChange={e => { setRuleId(e.target.value); setEvaluation(null); setOutput('') }}>{rules.filter(r => r.active).map(rule => <option key={rule.ID} value={rule.ID}>{rule.Name}</option>)}</select></label>{selectedRule && <div className="rule-summary"><strong>{selectedRule.TriggerType}</strong><span>{selectedRule.MaxRunsPerHour} ejecuciones/hora</span></div>}<Field label="ID del remitente · opcional" value={sender} onChange={setSender} /><Field label="Pedido" value={message} onChange={setMessage} area /><label className="inline-check"><input type="checkbox" checked={approved} onChange={e => setApproved(e.target.checked)} /> Aprobación concedida para esta ejecución</label><label className="inline-check"><input type="checkbox" checked={sendToSolidSET} onChange={e => setSendToSolidSET(e.target.checked)} /> Enviar resultado a SolidSET</label><div className="button-row"><button className="secondary" disabled={busy || !ruleId} onClick={() => void evaluate()}><ShieldCheck size={16} /> Evaluar</button><button className="primary" disabled={busy || !ruleId} onClick={() => void execute()}><Play size={16} /> Ejecutar</button></div><small className="safe-note"><ShieldCheck size={14} /> El envío siempre exige aprobación explícita, aunque la regla permita vistas previas automáticas.</small></section><section className="panel automation-result"><div className="panel-title"><div><span className="eyebrow">Decisión</span><h2>{evaluation ? (evaluation.eligible ? 'Ejecución permitida' : 'Ejecución bloqueada') : 'Sin evaluar'}</h2></div>{evaluation && <span className={`status-pill ${evaluation.eligible ? 'success' : 'danger'}`}>{evaluation.runsLastHour}/{evaluation.maxRunsPerHour}</span>}</div>{evaluation ? <><div className="capability-compare"><div><strong>Requeridas</strong><p>{evaluation.requiredCapabilities.join(', ') || 'Ninguna'}</p></div><div><strong>Disponibles</strong><p>{evaluation.configuredCapabilities.join(', ') || 'Ninguna'}</p></div></div>{evaluation.reasons.length > 0 && <div className="error-box">{evaluation.reasons.join('\n')}</div>}{output && <div className="answer">{output}</div>}</> : <div className="preview-placeholder"><ShieldCheck size={32} /><p>Evalúa la regla antes de ejecutar para conocer permisos, aprobación y límites.</p></div>}</section></div>}
  </>
}

function Observability({ selectedInstance, agents, notify }: Shared) {
  const [snapshot, setSnapshot] = useState<ObservabilitySnapshot | null>(null)
  const [hours, setHours] = useState(24)
  const [eventType, setEventType] = useState('')
  const [status, setStatus] = useState('')
  const [resourceId, setResourceId] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(false)
  const [busy, setBusy] = useState(false)
  const [expanded, setExpanded] = useState('')

  const params = useCallback((exporting = false) => {
    const value = new URLSearchParams({ hours: String(hours), limit: exporting ? '1000' : '250' })
    if (eventType) value.set('eventType', eventType)
    if (status) value.set('status', status)
    if (resourceId) value.set('resourceId', resourceId)
    return value
  }, [hours, eventType, status, resourceId])

  const load = useCallback(async (silent = false) => {
    if (!selectedInstance) return setSnapshot(null)
    if (!silent) setBusy(true)
    try { setSnapshot(await api.observability(selectedInstance.Code, params())) }
    catch (error) { notify('error', error instanceof Error ? error.message : 'No fue posible cargar las métricas.') }
    finally { if (!silent) setBusy(false) }
  }, [selectedInstance, params, notify])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!autoRefresh) return
    const timer = window.setInterval(() => void load(true), 15000)
    return () => window.clearInterval(timer)
  }, [autoRefresh, load])

  const exportCsv = async () => {
    if (!selectedInstance) return
    try { await api.exportObservability(selectedInstance.Code, params(true)) }
    catch (error) { notify('error', error instanceof Error ? error.message : 'No fue posible exportar los eventos.') }
  }

  if (!selectedInstance) return <><PageHeader eyebrow="Operación" title="Observabilidad" copy="Métricas y auditorías aisladas por instancia." /><Empty title="Selecciona una instancia" copy="El panel nunca mezcla eventos de instancias diferentes." /></>
  const metrics = snapshot?.metrics
  const runtime = snapshot?.runtime.dialogue || {}
  const eventTypes = Object.entries(metrics?.byType || {})
  const maxType = Math.max(1, ...eventTypes.map(([, count]) => count))
  return <>
    <PageHeader eyebrow="Operación" title="Observabilidad" copy="Rendimiento, fallos y trazabilidad sin exponer contenido sensible." action={<div className="button-row"><button className="secondary" disabled={busy} onClick={() => void load()}><RefreshCw className={busy ? 'spin' : ''} size={16} /> Actualizar</button><button className="primary" onClick={() => void exportCsv()}><Download size={16} /> Exportar CSV</button></div>} />
    <section className="observability-filters panel"><label>Ventana<select value={hours} onChange={e => setHours(Number(e.target.value))}><option value="1">Última hora</option><option value="24">24 horas</option><option value="168">7 días</option><option value="720">30 días</option></select></label><label>Tipo<select value={eventType} onChange={e => setEventType(e.target.value)}><option value="">Todos</option><option value="response">Respuestas</option><option value="automation">Automatización</option><option value="ingestion">Ingestión</option><option value="tool">Herramientas</option></select></label><label>Estado<select value={status} onChange={e => setStatus(e.target.value)}><option value="">Todos</option><option value="completed">Completado</option><option value="failed">Fallido</option><option value="blocked">Bloqueado</option><option value="queued">En cola</option><option value="running">En ejecución</option></select></label><label>Agente<select value={resourceId} onChange={e => setResourceId(e.target.value)}><option value="">Todos</option>{agents.map(agent => <option key={agent.IDResource} value={agent.IDResource}>{agent.Name}</option>)}</select></label><label className="inline-check"><input type="checkbox" checked={autoRefresh} onChange={e => setAutoRefresh(e.target.checked)} /> Actualizar cada 15 s</label></section>

    <div className="metric-grid observability-metrics"><article className="metric"><div className="metric-icon"><Activity size={21} /></div><span>Eventos</span><strong>{metrics?.total ?? '—'}</strong><small>{snapshot?.windowHours || hours} horas</small></article><article className="metric"><div className="metric-icon"><CheckCircle2 size={21} /></div><span>Tasa de éxito</span><strong>{metrics ? `${metrics.successRate}%` : '—'}</strong><small>{metrics?.successful || 0} completados</small></article><article className="metric"><div className="metric-icon"><Gauge size={21} /></div><span>Duración media</span><strong>{metrics ? formatDuration(metrics.averageDurationMs) : '—'}</strong><small>P95 {metrics ? formatDuration(metrics.p95DurationMs) : '—'}</small></article><article className="metric"><div className="metric-icon"><CircleAlert size={21} /></div><span>Fallos y bloqueos</span><strong>{metrics?.failed ?? '—'}</strong><small>{Number(runtime.count || 0)} diálogos en runtime</small></article></div>

    <div className="split-grid observability-summary"><section className="panel"><span className="eyebrow">Distribución</span><h2>Eventos por tipo</h2><div className="type-bars">{eventTypes.length === 0 ? <p className="section-copy">No hay eventos para estos filtros.</p> : eventTypes.map(([type, count]) => <div key={type}><span>{eventLabel(type)}</span><div><i style={{ width: `${count * 100 / maxType}%` }} /></div><strong>{count}</strong></div>)}</div></section><section className="panel"><span className="eyebrow">Runtime</span><h2>Diálogo interactivo</h2><dl className="detail-list"><div><dt>Ejecuciones</dt><dd>{runtime.count ?? '—'}</dd></div><div><dt>Última duración</dt><dd>{runtime.last_seconds != null ? `${runtime.last_seconds}s` : '—'}</dd></div><div><dt>Máxima duración</dt><dd>{runtime.max_seconds != null ? `${runtime.max_seconds}s` : '—'}</dd></div><div><dt>Cache hits</dt><dd>{runtime.cache_hits ?? '—'}</dd></div></dl></section></div>

    <section className="panel events-panel"><div className="panel-title"><div><span className="eyebrow">Auditoría</span><h2>Eventos recientes</h2></div><span className="status-pill">Metadatos seguros</span></div>{!snapshot?.events.length ? <div className="preview-placeholder"><Activity size={32} /><p>No existen eventos dentro de la ventana y filtros seleccionados.</p></div> : <div className="events-table"><table><thead><tr><th>Fecha</th><th>Tipo</th><th>Estado</th><th>Duración</th><th>Agente</th><th>Referencia</th><th>Detalle</th></tr></thead><tbody>{snapshot.events.map(event => <tr key={`${event.type}-${event.reference}`}><td>{new Date(event.timestamp).toLocaleString()}</td><td><span className="event-type">{eventLabel(event.type)}</span>{event.operation && <small>{event.operation}</small>}</td><td><span className={`status-pill ${statusClass(event.status)}`}>{event.status}</span></td><td>{formatDuration(event.duration_ms)}</td><td><code>{shortId(event.resourceId)}</code></td><td><code title={event.reference}>{shortId(event.reference)}</code></td><td>{event.error ? <button className="secondary compact" onClick={() => setExpanded(expanded === event.reference ? '' : event.reference)}><CircleAlert size={13} /> Ver error</button> : '—'}{expanded === event.reference && event.error && <div className="event-error">{event.error}</div>}</td></tr>)}</tbody></table></div>}</section>
    <p className="privacy-note"><ShieldCheck size={14} /> La vista y el CSV omiten mensajes, prompts, argumentos de herramientas, credenciales y respuestas generadas.</p>
  </>
}

function Governance({ selectedInstance, currentUser, notify, authEnabled }: { selectedInstance: Instance | null; currentUser: AdminUser; notify: Shared['notify']; authEnabled: boolean }) {
  const [tab, setTab] = useState<'approvals' | 'history' | 'users'>('approvals')
  const [approvals, setApprovals] = useState<Approval[]>([]); const [changes, setChanges] = useState<ChangeRecord[]>([]); const [users, setUsers] = useState<AdminUser[]>([])
  const [busy, setBusy] = useState(false); const [form, setForm] = useState({ username: '', displayName: '', password: '', role: 'operator' })
  const canApprove = currentUser.permissions.includes('approve'); const canRestore = currentUser.permissions.includes('restore'); const canUsers = currentUser.permissions.includes('manage_users')
  const load = useCallback(async () => { setBusy(true); try { const [approvalData, changeData] = await Promise.all([api.approvals(selectedInstance?.Code), api.changes(selectedInstance?.Code)]); setApprovals(approvalData.items); setChanges(changeData.items); if (canUsers) setUsers((await api.adminUsers()).items) } catch (e) { notify('error', e instanceof Error ? e.message : 'No fue posible cargar el gobierno administrativo.') } finally { setBusy(false) } }, [selectedInstance?.Code, canUsers, notify])
  useEffect(() => { if (authEnabled) void load() }, [load, authEnabled])
  if (!authEnabled) return <><PageHeader eyebrow="Seguridad" title="Gobierno administrativo" copy="Activa CONTROL_CENTER_AUTH_ENABLED y configura el usuario inicial para habilitar este módulo." /><Empty title="Autenticación deshabilitada" copy="El modo local conserva compatibilidad, pero no proporciona control de acceso ni aprobaciones." /></>
  const decide = async (id: string, decision: 'approved' | 'rejected') => { try { await api.decideApproval(id, decision); notify('success', decision === 'approved' ? 'Aprobación concedida.' : 'Solicitud rechazada.'); await load() } catch (e) { notify('error', e instanceof Error ? e.message : 'No fue posible decidir la solicitud.') } }
  const requestRestore = async (change: ChangeRecord) => { try { await api.requestApproval({ instanceCode: selectedInstance?.Code, operation: 'restore', resourceType: change.ResourceType, resourceId: change.ID, reason: `Restaurar el estado anterior del cambio ${change.ID}.` }); notify('success', 'Solicitud de restauración creada.'); await load() } catch (e) { notify('error', e instanceof Error ? e.message : 'No fue posible solicitar la restauración.') } }
  const restore = async (change: ChangeRecord) => { const approval = approvals.find(item => item.Operation === 'restore' && item.ResourceID === change.ID && item.Status === 'approved'); if (!approval) return notify('error', 'Este cambio no tiene una aprobación vigente.'); try { await api.restoreChange(change.ID, approval.ID); notify('success', 'Configuración restaurada.'); await load() } catch (e) { notify('error', e instanceof Error ? e.message : 'No fue posible restaurar el cambio.') } }
  const createUser = async (event: React.FormEvent) => { event.preventDefault(); try { await api.createAdminUser(form); setForm({ username: '', displayName: '', password: '', role: 'operator' }); notify('success', 'Usuario administrativo creado.'); await load() } catch (e) { notify('error', e instanceof Error ? e.message : 'No fue posible crear el usuario.') } }
  return <><PageHeader eyebrow="Seguridad" title="Gobierno administrativo" copy="Accesos, decisiones durables e historial de cambios de configuración." action={<button className="secondary" onClick={() => void load()} disabled={busy}><RefreshCw className={busy ? 'spin' : ''} size={16} /> Actualizar</button>} />
    <div className="knowledge-tabs"><button className={tab === 'approvals' ? 'active' : ''} onClick={() => setTab('approvals')}><ShieldCheck size={15} /> Aprobaciones</button><button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}><History size={15} /> Historial</button>{canUsers && <button className={tab === 'users' ? 'active' : ''} onClick={() => setTab('users')}><UserCog size={15} /> Usuarios</button>}</div>
    {tab === 'approvals' && <section className="panel governance-panel"><div className="panel-title"><div><span className="eyebrow">Decisiones</span><h2>Aprobaciones persistentes</h2></div></div><div className="table-wrap"><table><thead><tr><th>Operación</th><th>Recurso</th><th>Solicitud</th><th>Estado</th><th>Acción</th></tr></thead><tbody>{approvals.map(item => <tr key={item.ID}><td><strong>{item.Operation}</strong><small>{new Date(item.CreatedAt).toLocaleString()}</small></td><td>{item.ResourceType}<code>{item.ResourceID}</code></td><td>{item.Reason}<small>{item.RequestedByName}</small></td><td><span className={`status-pill ${item.Status === 'approved' || item.Status === 'consumed' ? 'success' : item.Status === 'rejected' ? 'danger' : 'warning'}`}>{item.Status}</span></td><td>{canApprove && item.Status === 'pending' ? <div className="button-row"><button className="secondary compact" onClick={() => void decide(item.ID, 'approved')}>Aprobar</button><button className="secondary compact danger-button" onClick={() => void decide(item.ID, 'rejected')}>Rechazar</button></div> : '—'}</td></tr>)}</tbody></table></div></section>}
    {tab === 'history' && <section className="panel governance-panel"><span className="eyebrow">Trazabilidad</span><h2>Historial de cambios</h2><div className="table-wrap"><table><thead><tr><th>Fecha</th><th>Actor</th><th>Cambio</th><th>Resultado</th><th>Restauración</th></tr></thead><tbody>{changes.map(change => { const approved = approvals.some(item => item.Operation === 'restore' && item.ResourceID === change.ID && item.Status === 'approved'); return <tr key={change.ID}><td>{new Date(change.CreatedAt).toLocaleString()}</td><td>{change.UserName || 'Sistema'}</td><td><strong>{change.Action} · {change.ResourceType}</strong><code>{change.Path}</code></td><td><span className={`status-pill ${change.StatusCode < 400 ? 'success' : 'danger'}`}>HTTP {change.StatusCode}</span></td><td>{change.BeforeState ? approved && canRestore ? <button className="primary compact" onClick={() => void restore(change)}>Restaurar</button> : <button className="secondary compact" onClick={() => void requestRestore(change)}>Solicitar</button> : <span className="status-pill">Solo auditoría</span>}</td></tr> })}</tbody></table></div></section>}
    {tab === 'users' && canUsers && <div className="governance-users"><form className="panel form-panel" onSubmit={createUser}><span className="eyebrow">Acceso</span><h2>Nuevo usuario</h2><label>Usuario<input value={form.username} onChange={e => setForm({ ...form, username: e.target.value })} /></label><label>Nombre visible<input value={form.displayName} onChange={e => setForm({ ...form, displayName: e.target.value })} /></label><label>Contraseña temporal<input type="password" minLength={12} value={form.password} onChange={e => setForm({ ...form, password: e.target.value })} /></label><label>Rol<select value={form.role} onChange={e => setForm({ ...form, role: e.target.value })}><option value="operator">Operador</option><option value="auditor">Auditor</option><option value="administrator">Administrador</option></select></label><button className="primary" disabled={!form.username || !form.displayName || form.password.length < 12}>Crear usuario</button></form><section className="panel"><span className="eyebrow">RBAC</span><h2>Usuarios administrativos</h2><div className="user-list">{users.map(user => <article key={user.ID}><div><strong>{user.displayName}</strong><small>{user.username} · {user.role}</small></div><span className={`status-pill ${user.active ? 'success' : 'danger'}`}>{user.active ? 'Activo' : 'Inactivo'}</span><button className="secondary compact" disabled={user.ID === currentUser.ID} onClick={() => void api.setAdminUserActive(user.ID, !user.active).then(load)}>{user.active ? 'Desactivar' : 'Activar'}</button></article>)}</div></section></div>}
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
function formatDuration(ms: number) { return ms >= 1000 ? `${(ms / 1000).toFixed(2)} s` : `${Math.round(ms)} ms` }
function eventLabel(value: string) { return ({ response: 'Respuesta', automation: 'Automatización', ingestion: 'Ingestión', tool: 'Herramienta' } as Record<string, string>)[value] || value }
function statusClass(value: string) { return value === 'completed' ? 'success' : ['failed', 'blocked', 'cancelled'].includes(value) ? 'danger' : value === 'queued' ? 'warning' : '' }

export default App
