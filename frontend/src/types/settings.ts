interface AnalysisPromptTemplate {
  id: string
  name: string
  description: string
  deep_analysis_prompt: string
}

export interface AnalysisPromptCatalog {
  version: string
  default_template_id: string
  max_prompt_chars: number
  templates: AnalysisPromptTemplate[]
  fixed_prompts: {
    screening: string
    deep: string
  }
}

export type CodexModel = 'gpt-5.6-sol' | 'gpt-5.6-terra' | 'gpt-5.6-luna'
export type CodexReasoningEffort = 'low' | 'medium' | 'high' | 'xhigh'
export type ModelStage = 'prefilter' | 'screening' | 'deep' | 'synthesis'

interface CodexOption<T extends string> {
  value: T
  label: string
  description: string
}

export interface StageProviderSettings {
  model_source: 'api' | 'codex'
  provider_name: string
  model_name: string
  api_base_url: string
  api_protocol: 'responses' | 'chat'
  api_key_configured: boolean
  codex_model: CodexModel
  codex_reasoning_effort: CodexReasoningEffort
}

export interface Settings {
  providers: Record<ModelStage, StageProviderSettings>
  codex_model_options: CodexOption<CodexModel>[]
  codex_reasoning_options: CodexOption<CodexReasoningEffort>[]
  ui_font_size_pt: number
  schedule_time: string
  schedule_timezone: string
  arxiv_fetch_limit: number
  prefilter_batch_size: number
  deep_analysis_limit: number
  deep_analysis_threshold?: number
  analysis_budget: 'economy' | 'balanced' | 'deep'
  daily_synthesis_enabled: boolean
  prompt_template_id: string
  deep_analysis_prompt: string
  prompt_version: string
  prompt_customized: boolean
  token_usage: TokenUsageSummary
  database_path?: string | null
  database_size_bytes?: number | null
  stored_paper_count: number
  stored_analysis_count: number
  stored_report_count: number
  email_enabled: boolean
  email_recipients: string[]
  smtp_host: string
  smtp_port: number
  smtp_security: 'starttls' | 'ssl'
  smtp_username: string
  smtp_password_configured: boolean
  smtp_sender: string
  public_url: string
}

interface TokenUsageTotals {
  record_count: number
  usage_available_count: number
  usage_missing_count: number
  input_tokens: number
  cached_input_tokens: number
  output_tokens: number
  reasoning_tokens: number
  total_tokens: number
}

interface TokenUsageSummary extends TokenUsageTotals {
  by_kind: Record<string, TokenUsageTotals>
  latest: null | {
    kind: string
    budget_mode: string
    input_tokens: number | null
    cached_input_tokens: number | null
    output_tokens: number | null
    reasoning_tokens: number | null
    total_tokens: number | null
    created_at: string
  }
}
