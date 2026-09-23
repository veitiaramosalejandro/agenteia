export type Instance = {
  ID: string
  Code: string
  Name: string
  BaseUrl: string
  NotificationUrl?: string | null
  SourceIP?: string | null
  CountryCode: string
  Locale: string
  TimeZone: string
  active: boolean
  DataAPI?: {
    BaseUrl: string
    TimeoutSeconds: number
    MaxRows: number
    VerifyTLS: boolean
    active: boolean
    APIKeyConfigured: boolean
  } | null
}

export type Provider = {
  ID: string
  Code: string
  Name: string
  Provider: string
  Model: string
  BaseUrl?: string | null
  HasAPIKey: boolean
  Temperature: number
  MaxOutputTokens: number
  TimeoutSeconds: number
  IsDefault: boolean
  active: boolean
}

export type AgentModel = {
  ID: string
  ProviderCode: string
  Provider: string
  Model: string
  Role: string
  Capabilities: string[]
  Priority: number
  IsDefault: boolean
  TrainingMode: string
  LearnFromOwner: boolean
  LearnFromSystem: boolean
  LearnFromReactions: boolean
}

export type Agent = {
  IDResource: string
  IDAgentResource?: string | null
  Name: string
  FullName?: string | null
  OrganizationName?: string | null
  ScopeCount: number
  prompt?: { ID: string; Version: number; Name: string; Status: string; PublishedAt?: string } | null
  models: AgentModel[]
}

export type Health = {
  status: string
  version: string
  services: Record<string, unknown>
  runtime: Record<string, unknown>
}

export type PromptDraft = {
  ID: string
  IDResource: string
  Version: number
  Name: string
  SystemPrompt: string
  BehaviorConfig: Record<string, unknown>
  Status: string
}

export type KnowledgeRecord = {
  ID: string
  IDSolidSETInstance: string
  IDResource: string
  IDWorkRoom?: string | null
  Title?: string | null
  KnowledgeText: string
  Source: string
  Stamp: string
  active: boolean
}

export type KnowledgeSearchResult = {
  IDSolidSETInstance: string
  IDResource: string
  Query: string
  privateContext: string
  systemContext: string
  privateMatchCount: number
  systemMatchCount: number
}

export type IngestionRun = {
  ID?: string
  Status?: string
  ExecutionState?: string
  ProgressPercentage?: number
  TablesTotal?: number
  TablesCompleted?: number
  RowsProcessed?: number
  PointsIndexed?: number
  Error?: string | null
  Alive?: boolean
  Complete?: boolean
  UpdatedAt?: string
}
