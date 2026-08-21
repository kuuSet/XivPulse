import { Building2, Check, CheckCircle2, CircleAlert, LoaderCircle, Pencil, Plus, Search, SlidersHorizontal, UserRound, X } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../../lib/api'
import type { BackendTopic, DirectionCatalog, DirectionCatalogItem, Interests } from '../../types/interests'
import type { LoadState, Toast } from '../../app/types'
import { Modal, FormSection, WorkspaceSkeleton, PageError } from '../../shared/ui'

type WatchKind = 'topic' | 'author' | 'organization'

export function InterestsView({ onToast }: { onToast: (kind: Toast['kind'], message: string) => void }) {
  const [interests, setInterests] = useState<Interests | null>(null)
  const [catalog, setCatalog] = useState<DirectionCatalog | null>(null)
  const [catalogQuery, setCatalogQuery] = useState('')
  const [descriptionDraft, setDescriptionDraft] = useState('')
  const [editingTopicIndex, setEditingTopicIndex] = useState<number | null>(null)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickerKind, setPickerKind] = useState<WatchKind>('topic')
  const [state, setState] = useState<LoadState>('loading')
  const [error, setError] = useState('')
  const [saveState, setSaveState] = useState<'saved' | 'pending' | 'saving' | 'error'>('saved')
  const [saveRetry, setSaveRetry] = useState(0)
  const savedInterestsRef = useRef('')
  const saveVersionRef = useRef(0)
  const pendingSavesRef = useRef(0)
  const saveQueueRef = useRef(Promise.resolve())

  const load = useCallback(async () => {
    setState('loading')
    try {
      const [nextInterests, nextCatalog] = await Promise.all([api.interests(), api.directionCatalog()])
      savedInterestsRef.current = interestsFingerprint(nextInterests)
      setInterests(nextInterests)
      setCatalog(nextCatalog)
      setSaveState('saved')
      setState('ready')
      setError('')
    } catch (loadError) {
      setState('error')
      setError(loadError instanceof Error ? loadError.message : '关注设置加载失败')
    }
  }, [])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!interests || state !== 'ready') return
    const fingerprint = interestsFingerprint(interests)
    if (fingerprint === savedInterestsRef.current && pendingSavesRef.current === 0) return
    if (!interests.topics.some((topic) => topic.enabled !== false)) {
      setSaveState('error')
      return
    }
    const version = ++saveVersionRef.current
    setSaveState('pending')
    const timer = window.setTimeout(() => {
      const queuedInterests = interests
      setSaveState('saving')
      pendingSavesRef.current += 1
      saveQueueRef.current = saveQueueRef.current.then(async () => {
        try {
          const saved = await api.saveInterests(queuedInterests)
          savedInterestsRef.current = interestsFingerprint(saved)
          if (version !== saveVersionRef.current) return
          setInterests(saved)
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
  }, [interests, onToast, saveRetry, state])

  const topicIdentities = useMemo(
    () => new Set((interests?.topics ?? []).filter((topic) => topic.enabled !== false).flatMap((topic) => [topic.name, ...(topic.aliases ?? [])]).map(directionIdentity)),
    [interests],
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
    if (!interests) return
    const identities = new Set([item.name, ...item.aliases].map(directionIdentity))
    const existingIndex = interests.topics.findIndex((topic) =>
      [topic.name, ...(topic.aliases ?? [])].some((label) => identities.has(directionIdentity(label))),
    )
    if (existingIndex >= 0 && interests.topics[existingIndex].enabled !== false) {
      onToast('error', '该关注标签已经存在')
      return
    }
    if (existingIndex >= 0) {
      setInterests({ ...interests, topics: interests.topics.map((topic, index) => index === existingIndex ? { ...topic, enabled: true } : topic) })
      setCatalogQuery('')
      return
    }
    const { slug: _slug, group: _group, level: _level, parent_slug: _parentSlug, parent_name: _parentName, ...topic } = item
    setInterests({ ...interests, topics: [...interests.topics, { ...topic, description: catalogDescription(item), query: '' }] })
    setCatalogQuery('')
    setDescriptionDraft('')
  }

  const removeTopic = (index: number) => {
    if (!interests) return
    if (interests.topics.filter((topic) => topic.enabled !== false).length <= 1) {
      onToast('error', '至少保留一个关注标签')
      return
    }
    setInterests({ ...interests, topics: interests.topics.filter((_, topicIndex) => topicIndex !== index) })
  }

  const addCustomTopic = () => {
    if (!interests) return
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
    const description = descriptionDraft.trim()
    if (!isEnglishDescription(description)) {
      onToast('error', '请用英文写清这个标签关注的问题、方法或应用边界')
      return
    }
    const existingIndex = interests.topics.findIndex((topic) =>
      [topic.name, ...(topic.aliases ?? [])].some((label) => directionIdentity(label) === directionIdentity(name)),
    )
    if (existingIndex >= 0 && interests.topics[existingIndex].enabled !== false) {
      onToast('error', '该关注标签已经存在')
      return
    }
    if (existingIndex >= 0) {
      setInterests({ ...interests, topics: interests.topics.map((topic, index) => index === existingIndex ? { ...topic, enabled: true } : topic) })
      setCatalogQuery('')
      return
    }
    const categories = Array.from(new Set(interests.topics.filter((topic) => topic.enabled !== false).flatMap((topic) => topic.arxiv_categories)))
    setInterests({
      ...interests,
      topics: [...interests.topics, {
        name,
        description,
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
    setDescriptionDraft('')
  }

  const saveTopicDescription = () => {
    if (!interests || editingTopicIndex === null) return
    const description = descriptionDraft.trim()
    if (!isEnglishDescription(description)) {
      onToast('error', '请用英文写清这个标签关注的问题、方法或应用边界')
      return
    }
    setInterests({
      ...interests,
      topics: interests.topics.map((topic, index) => index === editingTopicIndex ? { ...topic, description } : topic),
    })
    setEditingTopicIndex(null)
    setDescriptionDraft('')
  }

  const addNamedWatch = () => {
    if (!interests) return
    const value = catalogQuery.normalize('NFKC').trim()
    if (value.length < 2) {
      onToast('error', pickerKind === 'author' ? '请输入作者姓名' : '请输入机构名称')
      return
    }
    const field = pickerKind === 'author' ? 'watched_authors' : 'watched_organizations'
    if (interests[field].some((item) => item.normalize('NFKC').trim().toLocaleLowerCase() === value.toLocaleLowerCase())) {
      onToast('error', '该关注项已经存在')
      return
    }
    setInterests({ ...interests, [field]: [...interests[field], value] })
    setCatalogQuery('')
  }

  const removeNamedWatch = (field: 'watched_authors' | 'watched_organizations', index: number) => {
    if (!interests) return
    setInterests({ ...interests, [field]: interests[field].filter((_, itemIndex) => itemIndex !== index) })
  }

  if (state === 'loading' || !interests) return state === 'error' ? <PageError message={error} onRetry={load} /> : <WorkspaceSkeleton />

  return (
    <div className="standard-page settings-page">
      <section className="page-heading">
        <div><div className="eyebrow-row"><SlidersHorizontal size={14} /> 个性化筛选</div><h1>关注设置</h1><p>统一管理研究方向、作者和机构。</p></div>
        <div className={`auto-save-status ${saveState}`} aria-live="polite">
          {saveState === 'saving' || saveState === 'pending' ? <LoaderCircle className={saveState === 'saving' ? 'spin' : ''} size={15} /> : saveState === 'error' ? <CircleAlert size={15} /> : <CheckCircle2 size={15} />}
          <span>{saveState === 'pending' ? '等待自动保存' : saveState === 'saving' ? '正在保存' : saveState === 'error' ? '保存失败' : '已自动保存'}</span>
          {saveState === 'error' && <button type="button" onClick={() => setSaveRetry((value) => value + 1)}>重试</button>}
        </div>
      </section>
      <div className="form-layout interests-form-layout">
        <div className="form-main">
          <FormSection title="关注标签" description="标签名称和英文描述共同决定 LLM 的筛选范围。">
            <div className="interest-tag-editor">
              <div className="interest-tags">
                {interests.topics.map((topic, index) => topic.enabled === false ? null : <span className="interest-tag topic" title={topic.description || '尚未填写标签描述'} key={topic.id ?? `new-${index}`}>
                  {englishDirectionLabel(topic)}
                  <button type="button" onClick={() => { setEditingTopicIndex(index); setDescriptionDraft(topic.description) }} aria-label={`编辑 ${englishDirectionLabel(topic)}`} title="编辑描述"><Pencil size={12} /></button>
                  <button type="button" onClick={() => removeTopic(index)} aria-label={`删除 ${englishDirectionLabel(topic)}`} title="删除标签"><X size={13} /></button>
                </span>)}
                <button className="interest-add-tag" type="button" onClick={() => { setPickerKind('topic'); setPickerOpen(true) }} aria-label="添加关注标签" title="添加关注标签"><Plus size={16} /></button>
              </div>
              <p>建议保留 3–12 个边界清晰的研究方向。</p>
            </div>
          </FormSection>
          <FormSection title="关注作者" description="用于在“今日论文”中将这些作者的论文优先显示。">
            <div className="interest-tag-editor">
              <div className="interest-tags">
                {interests.watched_authors.map((author, index) => <span className="interest-tag author" key={`author-${author}`}><UserRound size={12} />{author}<button type="button" onClick={() => removeNamedWatch('watched_authors', index)} aria-label={`删除作者 ${author}`} title="删除作者"><X size={13} /></button></span>)}
                <button className="interest-add-tag" type="button" onClick={() => { setPickerKind('author'); setPickerOpen(true) }} aria-label="添加关注作者" title="添加关注作者"><Plus size={16} /></button>
              </div>
              <p>使用论文中的完整作者姓名进行精确匹配。</p>
            </div>
          </FormSection>
          <FormSection title="关注机构" description="用于在“今日论文”中将这些机构的论文优先显示。">
            <div className="interest-tag-editor">
              <div className="interest-tags">
                {interests.watched_organizations.map((organization, index) => <span className="interest-tag organization" key={`organization-${organization}`}><Building2 size={12} />{organization}<button type="button" onClick={() => removeNamedWatch('watched_organizations', index)} aria-label={`删除机构 ${organization}`} title="删除机构"><X size={13} /></button></span>)}
                <button className="interest-add-tag" type="button" onClick={() => { setPickerKind('organization'); setPickerOpen(true) }} aria-label="添加关注机构" title="添加关注机构"><Plus size={16} /></button>
              </div>
              <p>使用论文 affiliation 中的完整机构词组进行匹配。</p>
            </div>
          </FormSection>
        </div>
      </div>
      {pickerOpen && <Modal title={pickerKind === 'topic' ? '添加关注标签' : pickerKind === 'author' ? '添加关注作者' : '添加关注机构'} onClose={() => { setPickerOpen(false); setCatalogQuery(''); setDescriptionDraft('') }}>
        <div className="interest-picker">
          <label className="search-box"><Search size={15} /><input autoFocus value={catalogQuery} onChange={(event) => setCatalogQuery(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); if (pickerKind !== 'topic') addNamedWatch() } }} placeholder={pickerKind === 'topic' ? 'Search presets or type a new interest' : pickerKind === 'author' ? 'Type an author name' : 'Type an organization name'} />{catalogQuery && <button type="button" onClick={() => setCatalogQuery('')} title="清空搜索"><X size={14} /></button>}</label>
          {pickerKind === 'topic' && catalogQuery.trim() && !catalog?.items.some((item) => [item.name, ...item.aliases].some((label) => directionIdentity(label) === directionIdentity(catalogQuery))) && <label className="field interest-description-field"><span>Tag description (English)</span><textarea value={descriptionDraft} onChange={(event) => setDescriptionDraft(event.target.value)} placeholder="Describe the research problems, methods, systems, and application boundary covered by this tag." maxLength={2000} /><small>The LLM reads this text during title prefiltering and abstract scoring. Be specific enough to exclude adjacent fields.</small></label>}
          {catalogQuery.trim() && <button className={`interest-custom-option ${pickerKind === 'topic' && !isEnglishDirectionLabel(catalogQuery) ? 'invalid' : ''}`} type="button" onClick={pickerKind === 'topic' ? addCustomTopic : addNamedWatch}>
            <Plus size={15} />
            <span><strong>Add “{catalogQuery.trim()}”</strong><small>{pickerKind === 'topic' ? isEnglishDirectionLabel(catalogQuery) ? '匹配预设时保留完整规则，否则新建自定义标签' : '自定义标签需要使用英文短语' : pickerKind === 'author' ? '使用论文中的完整作者姓名进行精确匹配' : '使用完整机构词组进行匹配'}</small></span>
          </button>}
          {pickerKind === 'topic' && <><div className="interest-picker-head"><strong>Preset interests</strong><span>{visibleCatalogItems.length} tags</span></div>
          <div className="interest-options">
            {visibleCatalogItems.map((item) => { const exists = catalogItemExists(item); return <button className={exists ? 'selected' : ''} type="button" disabled={exists} onClick={() => addCatalogTopic(item)} title={`${item.name}：${item.description}`} key={item.slug}>{exists ? <Check size={13} /> : <Plus size={13} />}<span>{englishDirectionLabel(item)}</span></button> })}
            {!visibleCatalogItems.length && <p className="interest-empty">没有匹配的预设标签，可以使用上方输入创建自定义标签。</p>}
          </div></>}
        </div>
      </Modal>}
      {editingTopicIndex !== null && <Modal title={`编辑 ${englishDirectionLabel(interests.topics[editingTopicIndex])}`} onClose={() => { setEditingTopicIndex(null); setDescriptionDraft('') }}>
        <div className="interest-picker">
          <label className="field interest-description-field"><span>Tag description (English)</span><textarea autoFocus value={descriptionDraft} onChange={(event) => setDescriptionDraft(event.target.value)} placeholder="Describe the research problems, methods, systems, and application boundary covered by this tag." maxLength={2000} /><small>标题预筛和摘要粗排都会读取这段描述。</small></label>
          <button className="button primary" type="button" onClick={saveTopicDescription}>保存描述</button>
        </div>
      </Modal>}
    </div>
  )
}

function directionIdentity(value: string) {
  return value.normalize('NFKC').trim().replace(/[\s_-]+/g, '').toLocaleLowerCase()
}

function interestsFingerprint(interests: Interests) {
  return JSON.stringify(interests)
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

function isEnglishDescription(value: string) {
  return value.trim().length >= 12 && /[A-Za-z]/.test(value) && !/[\u3400-\u9fff]/.test(value)
}

function catalogDescription(item: DirectionCatalogItem) {
  const terms = Array.from(new Set([...item.keywords, ...item.context_keywords])).slice(0, 12)
  return `Research on ${englishDirectionLabel(item)}${terms.length ? `, including ${terms.join(', ')}` : ''}.`
}
