import { Check, CheckCircle2, ChevronDown, CircleAlert, Database, KeyRound, Mail, RotateCcw, Settings as SettingsIcon, ShieldAlert, Sparkles, Wifi } from 'lucide-react'
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../lib/api'
import type { AnalysisPromptCatalog, ModelStage, Settings, StageProviderSettings } from '../../types/settings'
import type { LoadState, Toast } from '../../app/types'
import { FormSection, WorkspaceSkeleton, PageError } from '../../shared/ui'
import { formatDateTime, formatBytes, formatInteger } from '../../shared/format'

const MODEL_STAGES: Array<{ id: ModelStage; name: string; description: string }> = [
  { id: 'prefilter', name: '标题预筛', description: '批量读取标题，优先选择快速、便宜的模型。' },
  { id: 'screening', name: '摘要粗排', description: '逐篇读取摘要，只返回进入全文深读的优先级分数。' },
  { id: 'deep', name: '全文深读', description: '读取论文正文并生成完整研究分析。' },
  { id: 'synthesis', name: '日报综合', description: '跨论文生成每日研究态势与行动建议。' },
]

function StageProviderEditor({
  stage,
  name,
  description,
  provider,
  settings,
  apiKey,
  testing,
  testResult,
  onChange,
  onApiKeyChange,
  onSaveApiKey,
  onTest,
}: {
  stage: ModelStage
  name: string
  description: string
  provider: StageProviderSettings
  settings: Settings
  apiKey: string
  testing: boolean
  testResult?: { ok: boolean; message: string }
  onChange: (update: Partial<StageProviderSettings>) => void
  onApiKeyChange: (value: string) => void
  onSaveApiKey: () => void
  onTest: () => void
}) {
  const usingCodex = provider.model_source === 'codex'
  const insecure = !usingCodex && provider.api_base_url.trim().toLowerCase().startsWith('http://')
  const selectedModel = settings.codex_model_options.find((option) => option.value === provider.codex_model)
  const selectedReasoning = settings.codex_reasoning_options.find((option) => option.value === provider.codex_reasoning_effort)

  return <article className="stage-provider-card">
    <header><div><span>{name}</span><p>{description}</p></div></header>
    <div className="budget-mode-list model-source-list" role="radiogroup" aria-label={`${name}模型来源`}>
      <label className={!usingCodex ? 'selected' : ''}><input type="radio" name={`${stage}-model-source`} checked={!usingCodex} onChange={() => onChange({ model_source: 'api' })} /><span><strong>API</strong><small>独立 API 配置</small></span></label>
      <label className={usingCodex ? 'selected' : ''}><input type="radio" name={`${stage}-model-source`} checked={usingCodex} onChange={() => onChange({ model_source: 'codex' })} /><span><strong>本机 Codex</strong><small>复用本机登录</small></span></label>
    </div>
    {usingCodex ? <div className="field-grid two">
      <label className="field"><span>Codex 模型</span><select value={provider.codex_model} onChange={(event) => onChange({ codex_model: event.target.value as StageProviderSettings['codex_model'] })}>{settings.codex_model_options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select><small>{selectedModel?.description}</small></label>
      <label className="field"><span>推理强度</span><select value={provider.codex_reasoning_effort} onChange={(event) => onChange({ codex_reasoning_effort: event.target.value as StageProviderSettings['codex_reasoning_effort'] })}>{settings.codex_reasoning_options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select><small>{selectedReasoning?.description}</small></label>
    </div> : <>
      <div className="field-grid two">
        <label className="field"><span>服务商</span><select value={provider.provider_name} onChange={(event) => onChange({ provider_name: event.target.value })}><option value="openai-compatible">OpenAI 兼容</option><option value="openai">OpenAI</option><option value="custom">自定义</option></select></label>
        <label className="field"><span>模型</span><input value={provider.model_name} onChange={(event) => onChange({ model_name: event.target.value })} placeholder="gpt-5-mini" /></label>
      </div>
      <div className="field-grid two">
        <label className="field"><span>Base URL</span><input value={provider.api_base_url} onChange={(event) => onChange({ api_base_url: event.target.value })} placeholder="https://api.openai.com/v1" /></label>
        <label className="field"><span>协议</span><select value={provider.api_protocol} onChange={(event) => onChange({ api_protocol: event.target.value as StageProviderSettings['api_protocol'] })}><option value="responses">Responses</option><option value="chat">Chat Completions</option></select></label>
      </div>
      {insecure && <div className="warning-banner"><ShieldAlert size={18} /><div><strong>连接未加密</strong><span>除本机调试外请使用 HTTPS。</span></div></div>}
      <label className="field"><span>API Key</span><div className="secret-input"><KeyRound size={16} /><input type="password" value={apiKey} onChange={(event) => onApiKeyChange(event.target.value)} onBlur={onSaveApiKey} autoComplete="new-password" placeholder={provider.api_key_configured ? '已配置，留空则不修改' : '输入 API Key'} />{provider.api_key_configured && !apiKey && <span className="configured-label"><Check size={13} /> 已配置</span>}</div></label>
    </>}
    <div className="connection-row">
      <button type="button" className="button secondary" onClick={onTest} disabled={testing || (!usingCodex && (!provider.api_base_url || !provider.model_name))}><Wifi size={16} /> {testing ? '正在测试' : '测试此阶段'}</button>
      {testResult && <span className={`connection-result ${testResult.ok ? 'ok' : 'failed'}`}>{testResult.ok ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}{testResult.message}</span>}
    </div>
  </article>
}

export function SettingsView({ onToast, onFontSizeChange }: { onToast: (kind: Toast['kind'], message: string) => void; onFontSizeChange: (value: number) => void }) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [apiKeys, setApiKeys] = useState<Partial<Record<ModelStage, string>>>({})
  const [smtpPassword, setSmtpPassword] = useState('')
  const [state, setState] = useState<LoadState>('loading')
  const [error, setError] = useState('')
  const [saveState, setSaveState] = useState<'saved' | 'pending' | 'saving' | 'error'>('saved')
  const [testingStage, setTestingStage] = useState<ModelStage | null>(null)
  const [testResults, setTestResults] = useState<Partial<Record<ModelStage, { ok: boolean; message: string }>>>({})
  const [promptCatalog, setPromptCatalog] = useState<AnalysisPromptCatalog | null>(null)
  const [emailTesting, setEmailTesting] = useState(false)
  const savedSettings = useRef('')
  const autoSaveTimer = useRef<number>()

  const load = useCallback(async () => {
    setState('loading')
    try {
      const [loadedSettings, prompts] = await Promise.all([
        api.settings(), api.promptCatalog(),
      ])
      savedSettings.current = JSON.stringify(loadedSettings)
      setSettings(loadedSettings)
      onFontSizeChange(loadedSettings.ui_font_size_pt)
      setPromptCatalog(prompts)
      setState('ready')
      setError('')
    } catch (loadError) {
      setState('error')
      setError(loadError instanceof Error ? loadError.message : '系统设置加载失败')
    }
  }, [onFontSizeChange])

  useEffect(() => { void load() }, [load])

  const persistSettings = useCallback(async (next: Settings, secrets: { api_keys?: Partial<Record<ModelStage, string>>; smtp_password?: string } = {}) => {
    window.clearTimeout(autoSaveTimer.current)
    setSaveState('saving')
    try {
      const saved = await api.saveSettings({ ...next, ...secrets })
      savedSettings.current = JSON.stringify(saved)
      setSettings(saved)
      setSaveState('saved')
      return saved
    } catch (saveError) {
      setSaveState('error')
      onToast('error', saveError instanceof Error ? saveError.message : '自动保存失败')
      return null
    }
  }, [onToast])

  useEffect(() => {
    if (state !== 'ready' || !settings) return
    const signature = JSON.stringify(settings)
    if (signature === savedSettings.current) return
    setSaveState('pending')
    window.clearTimeout(autoSaveTimer.current)
    autoSaveTimer.current = window.setTimeout(() => {
      void persistSettings(settings)
    }, 600)
    return () => window.clearTimeout(autoSaveTimer.current)
  }, [persistSettings, settings, state])

  const saveApiKey = async (stage: ModelStage) => {
    if (!settings) return
    const value = apiKeys[stage]
    if (!value) return
    const saved = await persistSettings(settings, { api_keys: { [stage]: value } })
    if (!saved) return
    setApiKeys((current) => ({ ...current, [stage]: '' }))
  }

  const saveSmtpPassword = async () => {
    if (!settings || !smtpPassword) return
    const saved = await persistSettings(settings, { smtp_password: smtpPassword })
    if (saved) setSmtpPassword('')
  }

  const test = async (stage: ModelStage) => {
    if (!settings) return
    setTestingStage(stage)
    setTestResults((current) => ({ ...current, [stage]: undefined }))
    const apiKey = apiKeys[stage]
    const saved = await persistSettings(settings, apiKey ? { api_keys: { [stage]: apiKey } } : {})
    if (!saved) {
      setTestResults((current) => ({ ...current, [stage]: { ok: false, message: '设置自动保存失败，未执行连接测试' } }))
      setTestingStage(null)
      return
    }
    setApiKeys((current) => ({ ...current, [stage]: '' }))
    try {
      const result = await api.testConnection(stage)
      setTestResults((current) => ({ ...current, [stage]: result }))
    } catch (testError) {
      setTestResults((current) => ({ ...current, [stage]: { ok: false, message: testError instanceof Error ? testError.message : '连接测试失败' } }))
    } finally {
      setTestingStage(null)
    }
  }

  const testEmail = async () => {
    if (!settings) return
    setEmailTesting(true)
    const saved = await persistSettings(settings, smtpPassword ? { smtp_password: smtpPassword } : {})
    if (!saved) {
      setEmailTesting(false)
      return
    }
    setSmtpPassword('')
    try {
      const result = await api.testEmail()
      if (!result.ok) throw new Error(`测试邮件发送失败（${result.email_failed} 个收件人）`)
      onToast('success', `测试邮件已发送给 ${result.email_sent} 个收件人`)
    } catch (emailError) {
      onToast('error', emailError instanceof Error ? emailError.message : '测试邮件发送失败')
    } finally { setEmailTesting(false) }
  }

  if (state === 'loading' || !settings) return state === 'error' ? <PageError message={error} onRetry={load} /> : <WorkspaceSkeleton />
  const anyCodex = Object.values(settings.providers).some((provider) => provider.model_source === 'codex')
  const budgetOptions = [
    { id: 'economy' as const, name: '经济', detail: '正文 1.6 万字符 / PDF 12 页；深读最多输出 2,000 Token' },
    { id: 'balanced' as const, name: '平衡', detail: '正文 3 万字符 / PDF 25 页；深读最多输出 3,500 Token' },
    { id: 'deep' as const, name: '深度', detail: '正文 4.5 万字符 / PDF 40 页；深读最多输出 5,000 Token' },
  ]
  const selectedPromptTemplate = promptCatalog?.templates.find((item) => item.id === settings.prompt_template_id)
  const promptModified = Boolean(selectedPromptTemplate && settings.deep_analysis_prompt !== selectedPromptTemplate.deep_analysis_prompt)
  const applyPromptTemplate = (templateId: string) => {
    const template = promptCatalog?.templates.find((item) => item.id === templateId)
    if (!template) return
    setSettings({
      ...settings,
      prompt_template_id: template.id,
      deep_analysis_prompt: template.deep_analysis_prompt,
      prompt_customized: false,
    })
  }
  return (
    <div className="standard-page settings-page">
      <section className="page-heading">
        <div><div className="eyebrow-row"><SettingsIcon size={14} /> 本地服务配置</div><h1>系统设置</h1><p>配置界面字号、模型连接、报告时区和分析规模。</p></div>
        <span className={`auto-save-status ${saveState}`} aria-live="polite">{saveState === 'pending' ? '等待自动保存' : saveState === 'saving' ? '正在自动保存' : saveState === 'error' ? '自动保存失败' : '已自动保存'}</span>
      </section>

      <div className="form-layout">
        <div className="form-main">
          <FormSection title="界面显示" description="以 12pt 为当前视觉基准，调整时保留标题、正文和辅助文字的层级比例。">
            <label className="field font-size-field"><span>界面基准字号</span><div><input type="number" min="10" max="16" step="0.5" value={settings.ui_font_size_pt} onChange={(event) => { const value = Math.min(16, Math.max(10, Number(event.target.value) || 10)); setSettings({ ...settings, ui_font_size_pt: value }); onFontSizeChange(value) }} /><em>pt</em></div><small>可设置 10–16pt，修改时立即预览，保存后长期生效。</small></label>
          </FormSection>

          <FormSection title="分阶段模型连接" description="每个阶段独立选择 API 或本机 Codex；修改后自动保存，只影响下一次调用。">
            <div className="stage-provider-list">
              {MODEL_STAGES.map((stage) => <StageProviderEditor
                key={stage.id}
                stage={stage.id}
                name={stage.name}
                description={stage.description}
                provider={settings.providers[stage.id]}
                settings={settings}
                apiKey={apiKeys[stage.id] ?? ''}
                testing={testingStage === stage.id}
                testResult={testResults[stage.id]}
                onChange={(update) => {
                  setSettings({ ...settings, providers: { ...settings.providers, [stage.id]: { ...settings.providers[stage.id], ...update } } })
                  setTestResults((current) => ({ ...current, [stage.id]: undefined }))
                }}
                onApiKeyChange={(value) => setApiKeys((current) => ({ ...current, [stage.id]: value }))}
                onSaveApiKey={() => void saveApiKey(stage.id)}
                onTest={() => void test(stage.id)}
              />)}
            </div>
            {anyCodex && <div className="warning-banner"><ShieldAlert size={18} /><div><strong>本机 Codex 适合可信的个人环境</strong><span>需要先登录 Codex；Docker 中只读挂载认证文件。论文内容会在只读沙箱中处理。</span></div></div>}
          </FormSection>

          <FormSection title="报告时间偏好" description="这里保存应用内的日期与时间显示偏好，不会修改 Windows 计划任务。真实自动运行时间请运行 install_task.ps1 更新。">
            <div className="field-grid two">
              <label className="field"><span>记录的计划时间</span><input type="time" value={settings.schedule_time} onChange={(event) => setSettings({ ...settings, schedule_time: event.target.value })} /></label>
              <label className="field"><span>时区</span><select value={settings.schedule_timezone} onChange={(event) => setSettings({ ...settings, schedule_timezone: event.target.value })}><option value="Asia/Shanghai">Asia/Shanghai</option><option value="UTC">UTC</option><option value="America/New_York">America/New_York</option><option value="Europe/London">Europe/London</option></select></label>
            </div>
          </FormSection>

          <FormSection title="日报邮件" description="完整日更保存日报后自动发送态势摘要和前 10 篇重点论文；授权码只保存在后端。">
            <label className="toggle-field"><input type="checkbox" checked={settings.email_enabled} onChange={(event) => setSettings({ ...settings, email_enabled: event.target.checked })} /><span><strong>自动发送日报邮件</strong><small>初筛未完成时邮件会明确标记失败数量</small></span></label>
            <div className="field-grid two"><label className="field"><span>SMTP 主机</span><input value={settings.smtp_host} onChange={(event) => setSettings({ ...settings, smtp_host: event.target.value })} placeholder="smtp.example.com" /></label><label className="field"><span>端口与安全模式</span><div className="smtp-port-row"><input type="number" min="1" max="65535" value={settings.smtp_port} onChange={(event) => setSettings({ ...settings, smtp_port: Number(event.target.value) })} /><select value={settings.smtp_security} onChange={(event) => setSettings({ ...settings, smtp_security: event.target.value as Settings['smtp_security'] })}><option value="starttls">STARTTLS</option><option value="ssl">SSL</option></select></div></label></div>
            <div className="field-grid two"><label className="field"><span>SMTP 用户名</span><input value={settings.smtp_username} onChange={(event) => setSettings({ ...settings, smtp_username: event.target.value })} autoComplete="username" /></label><label className="field"><span>授权码</span><div className="secret-input"><KeyRound size={16} /><input type="password" value={smtpPassword} onChange={(event) => setSmtpPassword(event.target.value)} onBlur={() => void saveSmtpPassword()} autoComplete="new-password" placeholder={settings.smtp_password_configured ? '已配置，留空则不修改' : '输入 SMTP 授权码'} />{settings.smtp_password_configured && !smtpPassword && <span className="configured-label"><Check size={13} /> 已配置</span>}</div></label></div>
            <div className="field-grid two"><label className="field"><span>发件人</span><input type="email" value={settings.smtp_sender} onChange={(event) => setSettings({ ...settings, smtp_sender: event.target.value })} placeholder="radar@example.com" /></label><label className="field"><span>收件人</span><input value={settings.email_recipients.join(', ')} onChange={(event) => setSettings({ ...settings, email_recipients: event.target.value.split(',').map((value) => value.trim()).filter(Boolean) })} placeholder="you@example.com, team@example.com" /></label></div>
            <label className="field"><span>公网基础地址</span><input value={settings.public_url} onChange={(event) => setSettings({ ...settings, public_url: event.target.value })} placeholder="https://203.0.113.10" /><small>邮件中的完整日报和论文详情链接使用此同源地址。</small></label>
            <button type="button" className="button secondary" disabled={emailTesting || !settings.smtp_host || !settings.smtp_sender || !settings.email_recipients.length} onClick={() => void testEmail()}><Mail size={16} />{emailTesting ? '正在发送' : '发送测试邮件'}</button>
          </FormSection>

          <FormSection title="处理规模与门槛" description="每日流水线依次执行批量标题预筛、逐篇摘要粗排和高分论文全文深读；每一级完成后立即保存结果。">
            <div className="budget-mode-list" role="radiogroup" aria-label="单篇分析 Token 档位">
              {budgetOptions.map((option) => <label key={option.id} className={settings.analysis_budget === option.id ? 'selected' : ''}><input type="radio" name="analysis-budget" checked={settings.analysis_budget === option.id} onChange={() => setSettings({ ...settings, analysis_budget: option.id })} /><span><strong>{option.name}</strong><small>{option.detail}</small></span></label>)}
            </div>
            <label className="toggle-field"><input type="checkbox" checked={settings.daily_synthesis_enabled} onChange={(event) => setSettings({ ...settings, daily_synthesis_enabled: event.target.checked })} /><span><strong>日更后自动生成 AI 综合研判</strong><small>每天只对最终日报调用一次；经济 / 平衡 / 深度档的最大输出约为 2,000 / 3,500 / 5,000 Token，打开日报不会重复调用。</small></span></label>
            <label className="field compact-number-field"><span>本轮最多抓取的候选论文</span><input type="number" min="10" max="1000" value={settings.arxiv_fetch_limit} onChange={(event) => setSettings({ ...settings, arxiv_fetch_limit: Number(event.target.value) })} /></label>
            <div className="analysis-thresholds">
              <article>
                <header><span>第 1 级</span><h3>标题预筛</h3></header>
                <p>将标题、arXiv 分类和关注标签批量交给 LLM，只排除明显无关论文，不发送摘要。</p>
                <label className="field"><span>单批标题数量</span><div><input type="number" min="1" max={settings.arxiv_fetch_limit} value={settings.prefilter_batch_size} onChange={(event) => setSettings({ ...settings, prefilter_batch_size: Number(event.target.value) })} /><em>篇</em></div><small>批次越大，请求次数越少，但单次输入和结构化输出越长。</small></label>
              </article>
              <article>
                <header><span>第 2 级</span><h3>摘要粗排</h3></header>
                <p>仅对标题预筛保留的论文逐篇阅读摘要，只返回 0–100 的全文深读优先级。</p>
              </article>
              <article>
                <header><span>第 3 级</span><h3>全文深读</h3></header>
                <p>摘要粗排达到 <strong>{settings.deep_analysis_threshold ?? 90}</strong> 分才进入全文队列。</p>
                <label className="field"><span>每轮最多深读</span><div><input type="number" min="0" max="100" value={settings.deep_analysis_limit} onChange={(event) => setSettings({ ...settings, deep_analysis_limit: Number(event.target.value) })} /><em>篇</em></div></label>
              </article>
            </div>
            <label className="field"><span>全文深读的粗排门槛</span><div className="range-readout"><span>越高越省 Token，但可能漏掉边缘相关论文</span><strong>{settings.deep_analysis_threshold ?? 90}</strong></div><input className="range" type="range" min="0" max="100" step="5" value={settings.deep_analysis_threshold ?? 90} onChange={(event) => setSettings({ ...settings, deep_analysis_threshold: Number(event.target.value) })} /></label>
            <div className="cost-note"><Sparkles size={16} /><span>三个分析阶段都会消耗模型 Token。标题预筛最便宜，摘要粗排只输出一个分数，全文深读每次最多 {settings.deep_analysis_limit} 篇。</span></div>
          </FormSection>

          <FormSection title="全文分析提示词" description="模板与编辑内容只用于全文深读；摘要粗排始终使用固定分数标尺。">
            {promptCatalog && <>
              <div className="prompt-template-toolbar">
                <label className="field"><span>推荐模板</span><select value={settings.prompt_template_id} onChange={(event) => applyPromptTemplate(event.target.value)}>{promptCatalog.templates.map((template) => <option value={template.id} key={template.id}>{template.name}</option>)}</select></label>
                <div className="prompt-template-summary"><span className={promptModified ? 'modified' : ''}>{promptModified ? '已修改' : '模板原文'}</span><strong>{selectedPromptTemplate?.name}</strong><p>{selectedPromptTemplate?.description}</p><small>目录版本 {promptCatalog.version}</small></div>
                <button type="button" className="button secondary compact" disabled={!promptModified} onClick={() => applyPromptTemplate(settings.prompt_template_id)}><RotateCcw size={14} /> 恢复模板</button>
              </div>
              <label className="field prompt-editor"><span className="prompt-field-head"><strong>全文深读侧重</strong><small>{settings.deep_analysis_prompt.length} / {promptCatalog.max_prompt_chars}</small></span><textarea rows={9} maxLength={promptCatalog.max_prompt_chars} value={settings.deep_analysis_prompt} onChange={(event) => setSettings({ ...settings, deep_analysis_prompt: event.target.value, prompt_customized: true })} /></label>
              <details className="fixed-prompt-details"><summary><ShieldAlert size={15} /><span>查看不可编辑的固定系统规则</span><ChevronDown size={14} /></summary><div><section><strong>摘要粗排</strong><pre>{promptCatalog.fixed_prompts.screening}</pre></section><section><strong>全文深读</strong><pre>{promptCatalog.fixed_prompts.deep}</pre></section></div></details>
            </>}
          </FormSection>

          <FormSection title="Token 用量" description="只统计模型服务商在响应中返回的真实 usage；没有 usage 的调用不会被估算成 Token。">
            <div className="token-usage-grid">
              <div><span>累计总 Token</span><strong>{formatInteger(settings.token_usage.total_tokens)}</strong></div>
              <div><span>输入</span><strong>{formatInteger(settings.token_usage.input_tokens)}</strong></div>
              <div><span>缓存输入</span><strong>{formatInteger(settings.token_usage.cached_input_tokens)}</strong></div>
              <div><span>输出 / 推理</span><strong>{formatInteger(settings.token_usage.output_tokens)} / {formatInteger(settings.token_usage.reasoning_tokens)}</strong></div>
            </div>
            <div className="token-kind-breakdown">
              <span>摘要粗排 <strong>{formatInteger(settings.token_usage.by_kind.screening?.total_tokens ?? 0)}</strong></span>
              <span>全文深读 <strong>{formatInteger(settings.token_usage.by_kind.deep?.total_tokens ?? 0)}</strong></span>
              <span>日报综合研判 <strong>{formatInteger(settings.token_usage.by_kind.report_synthesis?.total_tokens ?? 0)}</strong></span>
            </div>
            {settings.token_usage.latest ? <div className="latest-token-row"><span>最近一次：{settings.token_usage.latest.kind === 'deep' ? '全文深读' : settings.token_usage.latest.kind === 'report_synthesis' ? '日报综合研判' : '摘要粗排'} · {settings.token_usage.latest.budget_mode}</span><strong>{formatInteger(settings.token_usage.latest.total_tokens ?? 0)} Token</strong><small>{formatDateTime(settings.token_usage.latest.created_at)}</small></div> : <div className="retention-note"><CircleAlert size={16} /><div><strong>还没有可计量的 AI 调用</strong><p>下一次真实分析且 Provider 返回 usage 后，这里会开始累计。</p></div></div>}
            {settings.token_usage.usage_missing_count > 0 && <small className="usage-missing">另有 {settings.token_usage.usage_missing_count} 份分析未提供 usage，不计为 0。</small>}
          </FormSection>

          <FormSection title="本地数据" description="论文、分析、反馈与每日日报都保存在这台电脑上。">
            <div className="database-status">
              <div><Database size={20} /><span><strong>{formatBytes(settings.database_size_bytes ?? 0)}</strong><small>当前数据库大小</small></span></div>
              <div><strong>{settings.stored_paper_count}</strong><small>篇论文</small></div>
              <div><strong>{settings.stored_analysis_count}</strong><small>份 AI 分析</small></div>
              <div><strong>{settings.stored_report_count}</strong><small>份日报快照</small></div>
            </div>
            <div className="retention-note"><CircleAlert size={16} /><div><strong>当前永久累积保存，不会自动清理</strong><p>数据库会持续累积；系统不长期保存 PDF、arXiv HTML 正文或模型读取的临时全文。</p></div></div>
            {settings.database_path && <label className="field"><span>数据库位置</span><input value={settings.database_path} readOnly /></label>}
          </FormSection>
        </div>
        <aside className="form-aside">
          <div className="sticky-panel security-panel">
            <h2><KeyRound size={17} /> 凭据安全</h2>
            {anyCodex
              ? <ul><li>Codex 登录文件不写入数据库</li><li>Docker 中只读挂载认证文件</li><li>每次调用使用临时目录</li><li>本地服务不要暴露到公网</li></ul>
              : <ul><li>API Key 不写入前端代码</li><li>浏览器无法读取已保存密钥</li><li>建议使用限额独立密钥</li><li>本地服务不要暴露到公网</li></ul>}
          </div>
        </aside>
      </div>
    </div>
  )
}
