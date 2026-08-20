import { Check, CheckCircle2, ChevronDown, CircleAlert, Database, KeyRound, Mail, RotateCcw, Save, Settings as SettingsIcon, ShieldAlert, Sparkles, Wifi, X } from 'lucide-react'
import { FormEvent, useCallback, useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { isDemo } from '../../lib/runtime'
import { samplePromptCatalog, sampleSettings } from '../../sample'
import type { MethodCandidate, MethodCatalog } from '../../types/methods'
import type { AnalysisPromptCatalog, Settings } from '../../types/settings'
import type { LoadState, Toast } from '../../app/types'
import { FormSection, WorkspaceSkeleton, PageError } from '../../shared/ui'
import { formatDateTime, formatBytes, formatInteger } from '../../shared/format'

export function SettingsView({ onToast, onFontSizeChange }: { onToast: (kind: Toast['kind'], message: string) => void; onFontSizeChange: (value: number) => void }) {
  const [settings, setSettings] = useState<Settings | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [smtpPassword, setSmtpPassword] = useState('')
  const [state, setState] = useState<LoadState>('loading')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [methodCatalog, setMethodCatalog] = useState<MethodCatalog | null>(null)
  const [promptCatalog, setPromptCatalog] = useState<AnalysisPromptCatalog | null>(null)
  const [methodCandidates, setMethodCandidates] = useState<MethodCandidate[]>([])
  const [candidateNames, setCandidateNames] = useState<Record<string, string>>({})
  const [methodBusy, setMethodBusy] = useState(false)
  const [emailTesting, setEmailTesting] = useState(false)

  const load = useCallback(async () => {
    setState('loading')
    try {
      if (isDemo) {
        setSettings(sampleSettings)
        onFontSizeChange(sampleSettings.ui_font_size_pt)
        setPromptCatalog(samplePromptCatalog)
      } else {
        const [loadedSettings, catalog, candidates, prompts] = await Promise.all([
          api.settings(), api.methodCatalog(), api.methodCandidates(), api.promptCatalog(),
        ])
        setSettings(loadedSettings)
        onFontSizeChange(loadedSettings.ui_font_size_pt)
        setMethodCatalog(catalog)
        setPromptCatalog(prompts)
        setMethodCandidates(candidates.items)
        setCandidateNames(Object.fromEntries(candidates.items.map((item) => [item.key, item.name])))
      }
      setState('ready')
      setError('')
    } catch (loadError) {
      setState('error')
      setError(loadError instanceof Error ? loadError.message : '系统设置加载失败')
    }
  }, [onFontSizeChange])

  useEffect(() => { void load() }, [load])

  const save = async (event: FormEvent) => {
    event.preventDefault()
    if (!settings) return
    setSaving(true)
    try {
      const saved = isDemo ? { ...settings, api_key_configured: Boolean(apiKey) || settings.api_key_configured, smtp_password_configured: Boolean(smtpPassword) || settings.smtp_password_configured } : await api.saveSettings({ ...settings, ...(apiKey ? { api_key: apiKey } : {}), ...(smtpPassword ? { smtp_password: smtpPassword } : {}) })
      setSettings(saved)
      onFontSizeChange(saved.ui_font_size_pt)
      setApiKey('')
      setSmtpPassword('')
      onToast('success', '系统设置已保存')
    } catch (saveError) {
      onToast('error', saveError instanceof Error ? saveError.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    if (!settings) return
    setTesting(true)
    setTestResult(null)
    try {
      const result = isDemo ? { ok: true, message: '连接成功，模型响应正常（演示）' } : await api.testConnection({ model_source: settings.model_source, provider_name: settings.provider_name, model_name: settings.model_name, api_base_url: settings.api_base_url, codex_model: settings.codex_model, ...(apiKey ? { api_key: apiKey } : {}) })
      setTestResult(result)
    } catch (testError) {
      setTestResult({ ok: false, message: testError instanceof Error ? testError.message : '连接测试失败' })
    } finally {
      setTesting(false)
    }
  }

  const testEmail = async () => {
    if (!settings) return
    setEmailTesting(true)
    try {
      if (!isDemo) {
        const saved = await api.saveSettings({ ...settings, ...(smtpPassword ? { smtp_password: smtpPassword } : {}) })
        setSettings(saved); setSmtpPassword('')
        const result = await api.testEmail()
        if (!result.ok) throw new Error(`测试邮件发送失败（${result.email_failed} 个收件人）`)
        onToast('success', `测试邮件已发送给 ${result.email_sent} 个收件人`)
      } else onToast('success', '演示模式不发送真实邮件')
    } catch (emailError) {
      onToast('error', emailError instanceof Error ? emailError.message : '测试邮件发送失败')
    } finally { setEmailTesting(false) }
  }

  if (state === 'loading' || !settings) return state === 'error' ? <PageError message={error} onRetry={load} /> : <WorkspaceSkeleton />
  const usingCodex = settings.model_source === 'codex'
  const insecure = !usingCodex && settings.api_base_url.trim().toLowerCase().startsWith('http://')
  const budgetOptions = [
    { id: 'economy' as const, name: '经济', detail: '正文 1.6 万字符 / PDF 12 页；标题与摘要 900、深读 2,000 输出 Token' },
    { id: 'balanced' as const, name: '平衡', detail: '正文 3 万字符 / PDF 25 页；标题与摘要 1,200、深读 3,500 输出 Token' },
    { id: 'deep' as const, name: '深度', detail: '正文 4.5 万字符 / PDF 40 页；标题与摘要 1,600、深读 5,000 输出 Token' },
  ]
  const selectedPromptTemplate = promptCatalog?.templates.find((item) => item.id === settings.prompt_template_id)
  const promptModified = Boolean(selectedPromptTemplate && (
    settings.screening_prompt !== selectedPromptTemplate.screening_prompt
    || settings.deep_analysis_prompt !== selectedPromptTemplate.deep_analysis_prompt
  ))
  const applyPromptTemplate = (templateId: string) => {
    const template = promptCatalog?.templates.find((item) => item.id === templateId)
    if (!template) return
    setSettings({
      ...settings,
      prompt_template_id: template.id,
      screening_prompt: template.screening_prompt,
      deep_analysis_prompt: template.deep_analysis_prompt,
      prompt_customized: false,
    })
  }
  const acceptCandidate = async (candidate: MethodCandidate) => {
    if (isDemo) return
    setMethodBusy(true)
    try {
      const catalog = await api.acceptMethodCandidate(candidate.key, candidateNames[candidate.key]?.trim() || candidate.name)
      setMethodCatalog(catalog)
      setMethodCandidates((items) => items.filter((item) => item.key !== candidate.key))
      onToast('success', `已接受方法：${candidate.name}`)
    } catch (acceptError) {
      onToast('error', acceptError instanceof Error ? acceptError.message : '接受候选失败')
    } finally { setMethodBusy(false) }
  }
  const dismissCandidate = async (candidate: MethodCandidate) => {
    if (isDemo) return
    setMethodBusy(true)
    try {
      await api.dismissMethodCandidate(candidate.key)
      setMethodCandidates((items) => items.filter((item) => item.key !== candidate.key))
    } catch (dismissError) {
      onToast('error', dismissError instanceof Error ? dismissError.message : '忽略候选失败')
    } finally { setMethodBusy(false) }
  }
  const removeCustomMethod = async (name: string) => {
    if (!methodCatalog || isDemo) return
    setMethodBusy(true)
    try {
      const retained = methodCatalog.items.filter((item) => item.source === 'custom' && item.name !== name).map((item) => ({ name: item.name, aliases: item.aliases.filter((alias) => alias !== item.name) }))
      setMethodCatalog(await api.saveCustomMethods(retained))
    } catch (removeError) {
      onToast('error', removeError instanceof Error ? removeError.message : '删除自定义方法失败')
    } finally { setMethodBusy(false) }
  }

  return (
    <div className="standard-page settings-page">
      <section className="page-heading">
        <div><div className="eyebrow-row"><SettingsIcon size={14} /> 本地服务配置</div><h1>系统设置</h1><p>配置界面字号、模型连接、报告时区和分析规模。</p></div>
        <button className="button primary" type="submit" form="settings-form" disabled={saving} aria-label={saving ? '正在保存系统设置' : '保存系统设置'} title={saving ? '正在保存' : '保存更改'}><Save size={16} /><span>{saving ? '保存中' : '保存更改'}</span></button>
      </section>

      <form id="settings-form" className="form-layout" onSubmit={save}>
        <div className="form-main">
          <FormSection title="界面显示" description="以 12pt 为当前视觉基准，调整时保留标题、正文和辅助文字的层级比例。">
            <label className="field font-size-field"><span>界面基准字号</span><div><input type="number" min="10" max="16" step="0.5" value={settings.ui_font_size_pt} onChange={(event) => { const value = Math.min(16, Math.max(10, Number(event.target.value) || 10)); setSettings({ ...settings, ui_font_size_pt: value }); onFontSizeChange(value) }} /><em>pt</em></div><small>可设置 10–16pt，修改时立即预览，保存后长期生效。</small></label>
          </FormSection>

          <FormSection title="模型连接" description="分析流水线可以使用 OpenAI 兼容 API，或调用运行 XivPulse 的机器上已登录的 Codex CLI。">
            <div className="budget-mode-list model-source-list" role="radiogroup" aria-label="模型连接来源">
              <label className={!usingCodex ? 'selected' : ''}><input type="radio" name="model-source" checked={!usingCodex} onChange={() => { setSettings({ ...settings, model_source: 'api' }); setTestResult(null) }} /><span><strong>API</strong><small>直接调用 OpenAI 或兼容接口，适合稳定的自动化任务。</small></span></label>
              <label className={usingCodex ? 'selected' : ''}><input type="radio" name="model-source" checked={usingCodex} onChange={() => { setSettings({ ...settings, model_source: 'codex' }); setTestResult(null) }} /><span><strong>本机 Codex</strong><small>复用 Codex 登录；每次分析会启动独立进程，速度更慢且上下文开销更高。</small></span></label>
            </div>
            {usingCodex ? <>
              <label className="field"><span>Codex 模型（可选）</span><input value={settings.codex_model} onChange={(event) => { setSettings({ ...settings, codex_model: event.target.value }); setTestResult(null) }} placeholder="留空使用 Codex 默认模型" /><small>需要先在本机执行 codex login；Docker 部署还需要挂载登录文件。</small></label>
              <div className="warning-banner"><ShieldAlert size={18} /><div><strong>本机 Codex 适合可信的个人环境</strong><span>论文内容会作为不可信数据交给只读沙箱中的 Codex。登录凭据不会保存到网页设置或数据库。</span></div></div>
            </> : <>
              <div className="field-grid two">
                <label className="field"><span>服务商</span><select value={settings.provider_name} onChange={(event) => setSettings({ ...settings, provider_name: event.target.value })}><option value="openai-compatible">OpenAI 兼容</option><option value="openai">OpenAI</option><option value="custom">自定义</option></select></label>
                <label className="field"><span>模型</span><input value={settings.model_name} onChange={(event) => setSettings({ ...settings, model_name: event.target.value })} placeholder="gpt-5-mini" /></label>
              </div>
              <label className="field"><span>Base URL</span><input value={settings.api_base_url} onChange={(event) => { setSettings({ ...settings, api_base_url: event.target.value }); setTestResult(null) }} placeholder="https://api.openai.com/v1" /></label>
              {insecure && <div className="warning-banner"><ShieldAlert size={18} /><div><strong>连接未加密</strong><span>这个地址使用 HTTP，API 密钥可能在网络中明文传输。除本机调试外请使用 HTTPS。</span></div></div>}
              <label className="field"><span>API Key</span><div className="secret-input"><KeyRound size={16} /><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} autoComplete="new-password" placeholder={settings.api_key_configured ? '已配置，留空则不修改' : '输入 API Key'} />{settings.api_key_configured && !apiKey && <span className="configured-label"><Check size={13} /> 已配置</span>}</div><small>不会返回到浏览器；保存后界面只显示配置状态。</small></label>
            </>}
            <div className="connection-row">
              <button type="button" className="button secondary" onClick={test} disabled={testing || (!usingCodex && (!settings.api_base_url || !settings.model_name))}><Wifi size={16} /> {testing ? '正在测试' : '测试连接'}</button>
              {testResult && <span className={`connection-result ${testResult.ok ? 'ok' : 'failed'}`}>{testResult.ok ? <CheckCircle2 size={16} /> : <CircleAlert size={16} />}{testResult.message}</span>}
            </div>
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
            <div className="field-grid two"><label className="field"><span>SMTP 用户名</span><input value={settings.smtp_username} onChange={(event) => setSettings({ ...settings, smtp_username: event.target.value })} autoComplete="username" /></label><label className="field"><span>授权码</span><div className="secret-input"><KeyRound size={16} /><input type="password" value={smtpPassword} onChange={(event) => setSmtpPassword(event.target.value)} autoComplete="new-password" placeholder={settings.smtp_password_configured ? '已配置，留空则不修改' : '输入 SMTP 授权码'} />{settings.smtp_password_configured && !smtpPassword && <span className="configured-label"><Check size={13} /> 已配置</span>}</div></label></div>
            <div className="field-grid two"><label className="field"><span>发件人</span><input type="email" value={settings.smtp_sender} onChange={(event) => setSettings({ ...settings, smtp_sender: event.target.value })} placeholder="radar@example.com" /></label><label className="field"><span>收件人</span><input value={settings.email_recipients.join(', ')} onChange={(event) => setSettings({ ...settings, email_recipients: event.target.value.split(',').map((value) => value.trim()).filter(Boolean) })} placeholder="you@example.com, team@example.com" /></label></div>
            <label className="field"><span>公网基础地址</span><input value={settings.public_url} onChange={(event) => setSettings({ ...settings, public_url: event.target.value })} placeholder="https://203.0.113.10" /><small>邮件中的完整日报和论文详情链接使用此同源地址。</small></label>
            <button type="button" className="button secondary" disabled={emailTesting || !settings.smtp_host || !settings.smtp_sender || !settings.email_recipients.length} onClick={() => void testEmail()}><Mail size={16} />{emailTesting ? '正在发送' : '保存并发送测试邮件'}</button>
          </FormSection>

          <FormSection title="处理规模与门槛" description="每日流水线依次执行批量标题预筛、逐篇摘要评分和高分论文全文深读；每一级完成后立即保存结果。">
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
                <header><span>第 2 级</span><h3>摘要评分</h3></header>
                <p>仅对标题预筛保留的论文逐篇阅读摘要，给出相关度、原因和匹配标签。</p>
              </article>
              <article>
                <header><span>第 3 级</span><h3>全文深读</h3></header>
                <p>摘要初筛完成后，AI 相关度达到 <strong>{settings.deep_analysis_threshold ?? 90}</strong> 分才进入全文队列。</p>
                <label className="field"><span>每轮最多深读</span><div><input type="number" min="0" max="100" value={settings.deep_analysis_limit} onChange={(event) => setSettings({ ...settings, deep_analysis_limit: Number(event.target.value) })} /><em>篇</em></div></label>
              </article>
            </div>
            <label className="field"><span>全文深读的 AI 相关度门槛</span><div className="range-readout"><span>越高越省 Token，但可能漏掉边缘相关论文</span><strong>{settings.deep_analysis_threshold ?? 90}</strong></div><input className="range" type="range" min="0" max="100" step="5" value={settings.deep_analysis_threshold ?? 90} onChange={(event) => setSettings({ ...settings, deep_analysis_threshold: Number(event.target.value) })} /></label>
            <div className="cost-note"><Sparkles size={16} /><span>三个筛选阶段都会消耗模型 Token。标题预筛最便宜，摘要评分只处理保留项，全文深读每次最多 {settings.deep_analysis_limit} 篇。</span></div>
          </FormSection>

          <FormSection title="论文分析提示词" description="模板与编辑内容从下一次摘要初筛和全文深读开始生效，不会重写已有分析。">
            {promptCatalog && <>
              <div className="prompt-template-toolbar">
                <label className="field"><span>推荐模板</span><select value={settings.prompt_template_id} onChange={(event) => applyPromptTemplate(event.target.value)}>{promptCatalog.templates.map((template) => <option value={template.id} key={template.id}>{template.name}</option>)}</select></label>
                <div className="prompt-template-summary"><span className={promptModified ? 'modified' : ''}>{promptModified ? '已修改' : '模板原文'}</span><strong>{selectedPromptTemplate?.name}</strong><p>{selectedPromptTemplate?.description}</p><small>目录版本 {promptCatalog.version}</small></div>
                <button type="button" className="button secondary compact" disabled={!promptModified} onClick={() => applyPromptTemplate(settings.prompt_template_id)}><RotateCcw size={14} /> 恢复模板</button>
              </div>
              <div className="prompt-editor-grid">
                <label className="field prompt-editor"><span className="prompt-field-head"><strong>摘要初筛侧重</strong><small>{settings.screening_prompt.length} / {promptCatalog.max_prompt_chars}</small></span><textarea rows={7} maxLength={promptCatalog.max_prompt_chars} value={settings.screening_prompt} onChange={(event) => setSettings({ ...settings, screening_prompt: event.target.value, prompt_customized: true })} /></label>
                <label className="field prompt-editor"><span className="prompt-field-head"><strong>全文深读侧重</strong><small>{settings.deep_analysis_prompt.length} / {promptCatalog.max_prompt_chars}</small></span><textarea rows={9} maxLength={promptCatalog.max_prompt_chars} value={settings.deep_analysis_prompt} onChange={(event) => setSettings({ ...settings, deep_analysis_prompt: event.target.value, prompt_customized: true })} /></label>
              </div>
              <details className="fixed-prompt-details"><summary><ShieldAlert size={15} /><span>查看不可编辑的固定系统规则</span><ChevronDown size={14} /></summary><div><section><strong>摘要初筛</strong><pre>{promptCatalog.fixed_prompts.screening}</pre></section><section><strong>全文深读</strong><pre>{promptCatalog.fixed_prompts.deep}</pre></section></div></details>
            </>}
          </FormSection>

          <FormSection title="Token 用量" description="只统计模型服务商在响应中返回的真实 usage；没有 usage 的旧记录或兼容服务不会被估算成 Token。">
            <div className="token-usage-grid">
              <div><span>累计总 Token</span><strong>{formatInteger(settings.token_usage.total_tokens)}</strong></div>
              <div><span>输入</span><strong>{formatInteger(settings.token_usage.input_tokens)}</strong></div>
              <div><span>缓存输入</span><strong>{formatInteger(settings.token_usage.cached_input_tokens)}</strong></div>
              <div><span>输出 / 推理</span><strong>{formatInteger(settings.token_usage.output_tokens)} / {formatInteger(settings.token_usage.reasoning_tokens)}</strong></div>
            </div>
            <div className="token-kind-breakdown">
              <span>摘要初筛 <strong>{formatInteger(settings.token_usage.by_kind.screening?.total_tokens ?? 0)}</strong></span>
              <span>全文深读 <strong>{formatInteger(settings.token_usage.by_kind.deep?.total_tokens ?? 0)}</strong></span>
              <span>日报综合研判 <strong>{formatInteger(settings.token_usage.by_kind.report_synthesis?.total_tokens ?? 0)}</strong></span>
            </div>
            {settings.token_usage.latest ? <div className="latest-token-row"><span>最近一次：{settings.token_usage.latest.kind === 'deep' ? '全文深读' : settings.token_usage.latest.kind === 'report_synthesis' ? '日报综合研判' : '摘要初筛'} · {settings.token_usage.latest.budget_mode}</span><strong>{formatInteger(settings.token_usage.latest.total_tokens ?? 0)} Token</strong><small>{formatDateTime(settings.token_usage.latest.created_at)}</small></div> : <div className="retention-note"><CircleAlert size={16} /><div><strong>还没有可计量的 AI 调用</strong><p>下一次真实分析且 Provider 返回 usage 后，这里会开始累计。</p></div></div>}
            {settings.token_usage.usage_missing_count > 0 && <small className="usage-missing">另有 {settings.token_usage.usage_missing_count} 份历史分析未提供 usage，不计为 0。</small>}
          </FormSection>

          <FormSection title="共同方法词表" description="内置词表覆盖主要 AI 方法族；候选从最近 30 天已保存的分析中本地发现，至少跨 2 篇论文重复，接受前不会出现在日报。">
            {methodCatalog && <div className="method-catalog-summary"><div><strong>{methodCatalog.built_in_count}</strong><span>内置方法</span></div><div><strong>{methodCatalog.groups.length}</strong><span>稳定方法族</span></div><div><strong>{methodCatalog.custom_count}</strong><span>已接受候选</span></div><small>词表版本 {methodCatalog.version}</small></div>}
            {methodCatalog && methodCatalog.items.some((item) => item.source === 'custom') && <div className="custom-methods"><span>自定义方法</span><div>{methodCatalog.items.filter((item) => item.source === 'custom').map((item) => <span key={item.slug}>{item.name}<button type="button" disabled={methodBusy} onClick={() => void removeCustomMethod(item.name)} aria-label={`删除 ${item.name}`} title="删除自定义方法"><X size={12} /></button></span>)}</div></div>}
            <div className="method-candidates-head"><div><strong>待审核候选</strong><small>候选发现不调用模型，Token 成本为 0</small></div><span>{methodCandidates.length}</span></div>
            {methodCandidates.length ? <div className="method-candidate-list">{methodCandidates.map((candidate) => <article key={candidate.key}><div><input className="candidate-name-input" value={candidateNames[candidate.key] ?? candidate.name} onChange={(event) => setCandidateNames((values) => ({ ...values, [candidate.key]: event.target.value }))} aria-label={`${candidate.name} 的规范名称`} /><small>{candidate.paper_count} 篇论文重复出现 · 原始短语：{candidate.name}</small><p>{candidate.representative_papers.map((paper) => paper.title).join('；')}</p></div><div><button type="button" className="icon-button" disabled={methodBusy} onClick={() => void dismissCandidate(candidate)} title="忽略候选" aria-label={`忽略 ${candidate.name}`}><X size={15} /></button><button type="button" className="button secondary" disabled={methodBusy || !(candidateNames[candidate.key] ?? candidate.name).trim()} onClick={() => void acceptCandidate(candidate)}><Check size={15} /> 接受</button></div></article>)}</div> : <div className="method-empty"><CheckCircle2 size={18} /><span>当前没有达到重复门槛的新候选</span></div>}
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
            {usingCodex
              ? <ul><li>Codex 登录文件不写入数据库</li><li>Docker 中只读挂载认证文件</li><li>每次调用使用临时目录</li><li>本地服务不要暴露到公网</li></ul>
              : <ul><li>API Key 不写入前端代码</li><li>浏览器无法读取已保存密钥</li><li>建议使用限额独立密钥</li><li>本地服务不要暴露到公网</li></ul>}
          </div>
        </aside>
      </form>
    </div>
  )
}
