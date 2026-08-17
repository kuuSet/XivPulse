import { Check, CheckCircle2, ChevronDown, CircleAlert, LoaderCircle, Plus, Search, SlidersHorizontal, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../lib/api'
import { isDemo } from '../../lib/runtime'
import { sampleProfile } from '../../sample'
import type { BackendTopic, DirectionCatalog, DirectionCatalogItem, Profile } from '../../types/profile'
import type { LoadState, Toast } from '../../app/types'
import { Modal, FormSection, TagEditor, WorkspaceSkeleton, PageError } from '../../shared/ui'

export function ProfileView({ onToast }: { onToast: (kind: Toast['kind'], message: string) => void }) {
  const [profile, setProfile] = useState<Profile | null>(null)
  const [catalog, setCatalog] = useState<DirectionCatalog | null>(null)
  const [catalogQuery, setCatalogQuery] = useState('')
  const [pickerOpen, setPickerOpen] = useState(false)
  const [state, setState] = useState<LoadState>('loading')
  const [error, setError] = useState('')
  const [saveState, setSaveState] = useState<'saved' | 'pending' | 'saving' | 'error'>('saved')
  const [saveRetry, setSaveRetry] = useState(0)
  const savedProfileRef = useRef('')
  const saveVersionRef = useRef(0)
  const pendingSavesRef = useRef(0)
  const saveQueueRef = useRef(Promise.resolve())

  const load = useCallback(async () => {
    setState('loading')
    try {
      const [nextProfile, nextCatalog] = isDemo
        ? [sampleProfile, demoDirectionCatalog(sampleProfile)]
        : await Promise.all([api.profile(), api.directionCatalog()])
      savedProfileRef.current = profileFingerprint(nextProfile)
      setProfile(nextProfile)
      setCatalog(nextCatalog)
      setSaveState('saved')
      setState('ready')
      setError('')
    } catch (loadError) {
      setState('error')
      setError(loadError instanceof Error ? loadError.message : '研究画像加载失败')
    }
  }, [])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!profile || state !== 'ready') return
    const fingerprint = profileFingerprint(profile)
    if (fingerprint === savedProfileRef.current && pendingSavesRef.current === 0) return
    if (!profile.topics.some((topic) => topic.enabled !== false)) {
      setSaveState('error')
      return
    }
    const version = ++saveVersionRef.current
    setSaveState('pending')
    const timer = window.setTimeout(() => {
      const queuedProfile = profile
      setSaveState('saving')
      pendingSavesRef.current += 1
      saveQueueRef.current = saveQueueRef.current.then(async () => {
        try {
          const saved = isDemo ? queuedProfile : await api.saveProfile(queuedProfile)
          savedProfileRef.current = profileFingerprint(saved)
          if (version !== saveVersionRef.current) return
          setProfile(saved)
          setSaveState('saved')
        } catch (saveError) {
          if (version !== saveVersionRef.current) return
          setSaveState('error')
          onToast('error', saveError instanceof Error ? saveError.message : '自动保存失败')
        } finally {
          pendingSavesRef.current -= 1
        }
      })
    }, 600)
    return () => window.clearTimeout(timer)
  }, [onToast, profile, saveRetry, state])

  const topicIdentities = useMemo(
    () => new Set((profile?.topics ?? []).filter((topic) => topic.enabled !== false).flatMap((topic) => [topic.name, ...(topic.aliases ?? [])]).map(directionIdentity)),
    [profile],
  )
  const visibleCatalogItems = useMemo(() => {
    if (!catalog) return []
    const query = catalogQuery.normalize('NFKC').trim().toLocaleLowerCase()
    return catalog.items.filter((item) => {
      if (!query) return true
      return [englishDirectionLabel(item), item.name, item.group, item.description, ...item.aliases, ...item.keywords]
        .join('\n').toLocaleLowerCase().includes(query)
    })
  }, [catalog, catalogQuery])

  const catalogItemExists = (item: DirectionCatalogItem) =>
    [item.name, ...item.aliases].some((label) => topicIdentities.has(directionIdentity(label)))

  const addCatalogTopic = (item: DirectionCatalogItem) => {
    if (!profile) return
    const identities = new Set([item.name, ...item.aliases].map(directionIdentity))
    const existingIndex = profile.topics.findIndex((topic) =>
      [topic.name, ...(topic.aliases ?? [])].some((label) => identities.has(directionIdentity(label))),
    )
    if (existingIndex >= 0 && profile.topics[existingIndex].enabled !== false) {
      onToast('error', '该关注标签已经存在')
      return
    }
    if (existingIndex >= 0) {
      setProfile({ ...profile, topics: profile.topics.map((topic, index) => index === existingIndex ? { ...topic, enabled: true } : topic) })
      setCatalogQuery('')
      return
    }
    const { slug: _slug, group: _group, description: _description, level: _level, parent_slug: _parentSlug, parent_name: _parentName, ...topic } = item
    setProfile({ ...profile, topics: [...profile.topics, { ...topic, query: '' }] })
    setCatalogQuery('')
  }

  const removeTopic = (index: number) => {
    if (!profile) return
    if (profile.topics.filter((topic) => topic.enabled !== false).length <= 1) {
      onToast('error', '至少保留一个关注标签')
      return
    }
    setProfile({ ...profile, topics: profile.topics.filter((_, topicIndex) => topicIndex !== index) })
  }

  const addCustomTopic = () => {
    if (!profile) return
    const name = catalogQuery.normalize('NFKC').trim()
    if (!isEnglishDirectionLabel(name)) {

      onToast('error', '请使用清晰的英文短语，例如 Multimodal robot learning')
      return
    }
    const preset = catalog?.items.find((item) =>
      [item.name, ...item.aliases].some((label) => directionIdentity(label) === directionIdentity(name)),
    )
    if (preset) {
      addCatalogTopic(preset)
      return
    }
    const existingIndex = profile.topics.findIndex((topic) =>
      [topic.name, ...(topic.aliases ?? [])].some((label) => directionIdentity(label) === directionIdentity(name)),
    )
    if (existingIndex >= 0 && profile.topics[existingIndex].enabled !== false) {
      onToast('error', '该关注标签已经存在')
      return
    }
    if (existingIndex >= 0) {
      setProfile({ ...profile, topics: profile.topics.map((topic, index) => index === existingIndex ? { ...topic, enabled: true } : topic) })
      setCatalogQuery('')
      return
    }
    const categories = Array.from(new Set(profile.topics.filter((topic) => topic.enabled !== false).flatMap((topic) => topic.arxiv_categories)))
    setProfile({
      ...profile,
      topics: [...profile.topics, {
        name,
        query: '',
        arxiv_categories: categories.length ? categories : ['cs.AI'],
        keywords: [name],
        context_keywords: [],
        aliases: [],
        negative_keywords: [],
        weight: 1,
        enabled: true,
      }],
    })
    setCatalogQuery('')
  }

  if (state === 'loading' || !profile) return state === 'error' ? <PageError message={error} onRetry={load} /> : <WorkspaceSkeleton />

  return (
    <div className="standard-page settings-page">
      <section className="page-heading">
        <div><div className="eyebrow-row"><SlidersHorizontal size={14} /> 个性化筛选</div><h1>关注设置</h1><p>用少量英文标签描述长期关注的研究方向。</p></div>
        <div className={`auto-save-status ${saveState}`} aria-live="polite">
          {saveState === 'saving' || saveState === 'pending' ? <LoaderCircle className={saveState === 'saving' ? 'spin' : ''} size={15} /> : saveState === 'error' ? <CircleAlert size={15} /> : <CheckCircle2 size={15} />}
          <span>{saveState === 'pending' ? '等待自动保存' : saveState === 'saving' ? '正在保存' : saveState === 'error' ? '保存失败' : '已自动保存'}</span>
          {saveState === 'error' && <button type="button" onClick={() => setSaveRetry((value) => value + 1)}>重试</button>}
        </div>
      </section>
      <div className="form-layout profile-form-layout">
        <div className="form-main">
          <FormSection title="关注标签" description="使用简短、自然的英文研究方向；预设标签已经包含合适的语义词和 arXiv 抓取分类。">
            <div className="interest-tag-editor">
              <div className="interest-tags">
                {profile.topics.map((topic, index) => topic.enabled === false ? null : <span className="interest-tag" key={topic.id ?? `new-${index}`}>
                  {englishDirectionLabel(topic)}
                  <button type="button" onClick={() => removeTopic(index)} aria-label={`删除 ${englishDirectionLabel(topic)}`} title="删除标签"><X size={13} /></button>
                </span>)}
                <button className="interest-add-tag" type="button" onClick={() => setPickerOpen(true)} aria-label="添加关注标签" title="添加关注标签"><Plus size={16} /></button>
              </div>
              <p>建议保留 3–12 个边界清晰的方向；删除和添加后会自动保存。</p>
            </div>
          </FormSection>
          <details className="form-section optional-section">
            <summary><div><h2>个性化描述与作者关注</h2><p>可选；用于给 AI 更多研究背景，并提高关注作者的论文优先级。</p></div><ChevronDown size={16} /></summary>
            <div className="form-section-body">
              <label className="field"><span>画像名称</span><input value={profile.name} onChange={(event) => setProfile({ ...profile, name: event.target.value })} /></label>
              <label className="field"><span>研究兴趣描述</span><textarea rows={5} value={profile.description} onChange={(event) => setProfile({ ...profile, description: event.target.value })} placeholder="例如：关注具身智能中的世界模型与接触丰富操作……" /><small>这段描述会作为论文评估上下文提供给模型。</small></label>
              <TagEditor label="关注作者" tags={profile.watched_authors} placeholder="输入作者英文名" onChange={(tags) => setProfile({ ...profile, watched_authors: tags })} />
            </div>
          </details>
        </div>
      </div>
      {pickerOpen && <Modal title="添加关注标签" onClose={() => { setPickerOpen(false); setCatalogQuery('') }}>
        <div className="interest-picker">
          <label className="search-box"><Search size={15} /><input autoFocus value={catalogQuery} onChange={(event) => setCatalogQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') event.preventDefault() }} placeholder="Search presets or type a new interest" />{catalogQuery && <button type="button" onClick={() => setCatalogQuery('')} title="清空搜索"><X size={14} /></button>}</label>
          {catalogQuery.trim() && <button className={`interest-custom-option ${isEnglishDirectionLabel(catalogQuery) ? '' : 'invalid'}`} type="button" onClick={addCustomTopic}>
            <Plus size={15} />
            <span><strong>Add “{catalogQuery.trim()}”</strong><small>{isEnglishDirectionLabel(catalogQuery) ? '匹配预设时保留完整规则，否则新建自定义标签' : '自定义标签需要使用英文短语'}</small></span>
          </button>}
          <div className="interest-picker-head"><strong>Preset interests</strong><span>{visibleCatalogItems.length} tags</span></div>
          <div className="interest-options">
            {visibleCatalogItems.map((item) => { const exists = catalogItemExists(item); return <button className={exists ? 'selected' : ''} type="button" disabled={exists} onClick={() => addCatalogTopic(item)} title={`${item.name}：${item.description}`} key={item.slug}>{exists ? <Check size={13} /> : <Plus size={13} />}<span>{englishDirectionLabel(item)}</span></button> })}
            {!visibleCatalogItems.length && <p className="interest-empty">没有匹配的预设标签，可以使用上方输入创建自定义标签。</p>}
          </div>
        </div>
      </Modal>}
    </div>
  )
}

function directionIdentity(value: string) {
  return value.normalize('NFKC').trim().replace(/[\s_-]+/g, '').toLocaleLowerCase()
}

function profileFingerprint(profile: Profile) {
  return JSON.stringify(profile)
}

function englishDirectionLabel(topic: Pick<BackendTopic, 'name' | 'aliases' | 'keywords'>) {
  const candidates = [...(topic.aliases ?? []), ...(topic.keywords ?? []), topic.name]
  const english = candidates.filter((value) => /[A-Za-z]/.test(value) && !/[\u3400-\u9fff]/.test(value))
  return english.find((value) => !/^[A-Z0-9-]{2,10}$/.test(value)) ?? english[0] ?? topic.name
}

function isEnglishDirectionLabel(value: string) {
  const normalized = value.normalize('NFKC').trim()
  return normalized.length >= 3 && /[A-Za-z]/.test(normalized) && !/[\u3400-\u9fff]/.test(normalized)
}

function demoDirectionCatalog(profile: Profile): DirectionCatalog {
  const groups = ['示例方向']
  const items: DirectionCatalogItem[] = profile.topics.map((topic, index) => ({
    ...topic,
    slug: `demo-${index}`,
    group: groups[0],
    level: 'main',
    description: '演示模式中的现有研究方向。连接本地服务后可浏览完整 AI 方向库。',
  }))
  return { version: 'demo', updated_at: '', groups, count: items.length, items, sources: [], scope_note: '演示模式仅展示当前画像中的方向。' }
}
