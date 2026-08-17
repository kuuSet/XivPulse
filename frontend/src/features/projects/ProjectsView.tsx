import { Check, ChevronRight, FileText, NotebookTabs, Plus, Save, Search, Settings as SettingsIcon, Trash2, X } from 'lucide-react'
import { FormEvent, useEffect, useState } from 'react'
import { api } from '../../lib/api'
import { isDemo } from '../../lib/runtime'
import { sampleDashboard } from '../../sample'
import type { Paper, ProjectDraft, ProjectMembership, ReadingState, ResearchProject } from '../../types'
import type { LoadState, Toast } from '../../app/types'
import { Modal, TagEditor, WorkspaceSkeleton, PageError } from '../../shared/ui'
import { getReading, projectPaperSummary } from '../../shared/format'
import { READING_STATE_OPTIONS, PRIORITY_LABELS } from '../../shared/options'

const EMPTY_PROJECT: ProjectDraft = { name: '', description: '', goal: '', status: 'active', color: '#1f7a5a', due_date: null }

export function ProjectsView({ projects, onProjectsChange, onPapersReload, onOpenPaper, onToast }: {
  projects: ResearchProject[]
  onProjectsChange: (projects: ResearchProject[]) => void
  onPapersReload: () => void
  onOpenPaper: (paperId: Paper['id']) => void
  onToast: (kind: Toast['kind'], message: string) => void
}) {
  const [selectedId, setSelectedId] = useState<string>(() => String(projects[0]?.id ?? ''))
  const [draft, setDraft] = useState<ProjectDraft>(EMPTY_PROJECT)
  const [creating, setCreating] = useState(false)
  const [editing, setEditing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [paperQuery, setPaperQuery] = useState('')
  const [paperResults, setPaperResults] = useState<Paper[]>([])
  const [addPaperId, setAddPaperId] = useState('')
  const [membershipEdit, setMembershipEdit] = useState<ProjectMembership | null>(null)
  const [loadState, setLoadState] = useState<LoadState>(projects.length || isDemo ? 'ready' : 'loading')
  const [error, setError] = useState('')

  const selected = projects.find((project) => String(project.id) === selectedId) ?? projects[0] ?? null

  useEffect(() => {
    if (!selectedId && projects[0]) setSelectedId(String(projects[0].id))
    if (selectedId && !projects.some((project) => String(project.id) === selectedId)) setSelectedId(String(projects[0]?.id ?? ''))
  }, [projects, selectedId])

  useEffect(() => {
    if (projects.length || isDemo) { setLoadState('ready'); return }
    let active = true
    void api.projects().then(async (listed) => {
      const detailed = await Promise.all(listed.map(async (project) => {
        try { return await api.project(project.id) } catch { return project }
      }))
      if (active) { onProjectsChange(detailed); setLoadState('ready'); setError('') }
    }).catch((loadError) => { if (active) { setLoadState('error'); setError(loadError instanceof Error ? loadError.message : '分类库加载失败') } })
    return () => { active = false }
  }, [onProjectsChange, projects.length])

  useEffect(() => {
    let active = true
    const timer = window.setTimeout(() => {
      if (isDemo) { setPaperResults(sampleDashboard.items.slice(0, 30)); return }
      void api.papers({ q: paperQuery.trim() || undefined, limit: 30 })
        .then((result) => { if (active) setPaperResults(result.items) })
        .catch(() => { if (active) setPaperResults([]) })
    }, paperQuery.trim() ? 250 : 0)
    return () => { active = false; window.clearTimeout(timer) }
  }, [paperQuery])

  const openCreate = () => { setDraft(EMPTY_PROJECT); setCreating(true); setEditing(false) }
  const openEdit = () => {
    if (!selected || selected.is_system) return
    setDraft({ name: selected.name, description: selected.description, goal: selected.goal, status: selected.status, color: selected.color, due_date: selected.due_date ?? null })
    setEditing(true); setCreating(false)
  }

  const saveProject = async (event: FormEvent) => {
    event.preventDefault()
    if (!draft.name.trim()) return
    setSaving(true)
    try {
      const saved = isDemo
        ? editing && selected
          ? { ...selected, ...draft, name: draft.name.trim(), updated_at: new Date().toISOString() }
          : { ...draft, kind: 'custom', is_system: false, id: Date.now(), name: draft.name.trim(), paper_count: 0, papers: [], created_at: new Date().toISOString(), updated_at: new Date().toISOString() } as ResearchProject
        : editing && selected ? await api.updateProject(selected.id, draft) : await api.createProject(draft)
      const next = editing && selected ? projects.map((item) => String(item.id) === String(selected.id) ? saved : item) : [...projects, saved]
      onProjectsChange(next)
      setSelectedId(String(saved.id))
      setCreating(false); setEditing(false)
      onToast('success', editing ? '分类库已更新' : '分类库已创建')
    } catch (saveError) {
      onToast('error', saveError instanceof Error ? saveError.message : '分类库保存失败')
    } finally { setSaving(false) }
  }

  const deleteProject = async () => {
    if (!selected || selected.is_system || !window.confirm(`删除分类库“${selected.name}”？论文不会从论文库删除。`)) return
    try {
      if (!isDemo) await api.deleteProject(selected.id)
      onProjectsChange(projects.filter((item) => String(item.id) !== String(selected.id)))
      onToast('success', '分类库已删除，论文仍保留在论文库')
    } catch (deleteError) { onToast('error', deleteError instanceof Error ? deleteError.message : '分类库删除失败') }
  }

  const availablePapers = paperResults.filter((paper) => !selected?.papers?.some((membership) => String(membership.paper_id) === String(paper.id)))

  const addPaper = async () => {
    if (!selected || !addPaperId) return
    const paper = paperResults.find((item) => String(item.id) === addPaperId)
    if (!paper) return
    try {
      const membership = isDemo
        ? { id: Date.now(), project_id: selected.id, paper_id: paper.id, reading_state: 'queued' as ReadingState, priority: getReading(paper).priority, notes: '', tags: getReading(paper).tags, paper: projectPaperSummary(paper) }
        : await api.addProjectPaper(selected.id, paper.id, { reading_state: 'queued', priority: getReading(paper).priority, tags: getReading(paper).tags })
      onProjectsChange(projects.map((project) => String(project.id) === String(selected.id) ? { ...project, paper_count: project.paper_count + 1, papers: [...(project.papers ?? []), { ...membership, paper: membership.paper ?? projectPaperSummary(paper) }] } : project))
      setAddPaperId(''); setPaperQuery('')
      if (selected.kind === 'favorites') onPapersReload()
      onToast('success', `论文已加入“${selected.name}”`)
    } catch (addError) { onToast('error', addError instanceof Error ? addError.message : '加入分类库失败') }
  }

  const removePaper = async (membership: ProjectMembership) => {
    if (!selected) return
    try {
      if (!isDemo) await api.removeProjectPaper(selected.id, membership.paper_id)
      onProjectsChange(projects.map((project) => String(project.id) === String(selected.id) ? { ...project, paper_count: Math.max(0, project.paper_count - 1), papers: (project.papers ?? []).filter((item) => String(item.paper_id) !== String(membership.paper_id)) } : project))
      if (selected.kind === 'favorites') onPapersReload()
      onToast('success', '论文已从分类库移除')
    } catch (removeError) { onToast('error', removeError instanceof Error ? removeError.message : '移除论文失败') }
  }

  const saveMembership = async () => {
    if (!selected || !membershipEdit) return
    try {
      const payload = { reading_state: membershipEdit.reading_state, priority: membershipEdit.priority, notes: membershipEdit.notes, tags: membershipEdit.tags }
      const saved = isDemo ? membershipEdit : await api.updateProjectPaper(selected.id, membershipEdit.paper_id, payload)
      onProjectsChange(projects.map((project) => String(project.id) === String(selected.id) ? { ...project, papers: (project.papers ?? []).map((item) => String(item.paper_id) === String(saved.paper_id) ? { ...item, ...saved, paper: item.paper ?? saved.paper } : item) } : project))
      setMembershipEdit(null)
      onToast('success', '分类库内阅读信息已保存')
    } catch (editError) { onToast('error', editError instanceof Error ? editError.message : '保存失败') }
  }

  if (loadState === 'loading') return <WorkspaceSkeleton />
  if (loadState === 'error' && !projects.length) return <PageError message={error} onRetry={() => window.location.reload()} />

  return (
    <div className="standard-page projects-page">
      <section className="page-heading"><div><div className="eyebrow-row"><NotebookTabs size={14} /> 论文归档</div><h1>分类库</h1><p>收藏论文自动进入收藏库，也可以按课题、方法或用途建立自己的分类。</p></div><button className="button primary" onClick={openCreate}><Plus size={16} /> 新建分类</button></section>
      <div className="project-workbench">
        <aside className="project-list-pane">
          <div className="pane-title"><span>{projects.length} 个分类库</span></div>
          <div className="project-list">{projects.map((project) => <button key={project.id} className={String(project.id) === String(selected?.id) ? 'active' : ''} onClick={() => setSelectedId(String(project.id))}><i style={{ background: project.color }} /><div><strong>{project.name}</strong><span>{project.kind === 'favorites' ? '系统分类' : '自定义分类'} · {project.paper_count} 篇</span></div><ChevronRight size={16} /></button>)}</div>
        </aside>
        <section className="project-detail-pane">
          {selected ? <>
            <header className="project-header"><div className="project-title"><i style={{ background: selected.color }} /><div><span>{selected.kind === 'favorites' ? '系统分类' : '自定义分类'}</span><h2>{selected.name}</h2><p>{selected.description || '用于整理一组相关论文。'}</p></div></div>{!selected.is_system && <div className="project-header-actions"><button className="icon-button" title="编辑分类库" onClick={openEdit}><SettingsIcon size={16} /></button><button className="icon-button danger" title="删除分类库" onClick={() => void deleteProject()}><Trash2 size={16} /></button></div>}</header>
            <div className="project-facts"><div><span>收录论文</span><strong>{selected.paper_count} 篇</strong></div><div><span>正在阅读</span><strong>{(selected.papers ?? []).filter((item) => item.reading_state === 'reading').length} 篇</strong></div><div><span>已处理</span><strong>{(selected.papers ?? []).filter((item) => ['read', 'reproduce', 'cited', 'archived'].includes(item.reading_state)).length} 篇</strong></div></div>
            <div className="project-paper-tools"><label className="search-box"><Search size={16} /><input value={paperQuery} onChange={(event) => { setPaperQuery(event.target.value); setAddPaperId('') }} placeholder="搜索全部已初筛论文" /></label><select value={addPaperId} onChange={(event) => setAddPaperId(event.target.value)}><option value="">{availablePapers.length ? '选择一篇论文' : '没有可加入的论文'}</option>{availablePapers.map((paper) => <option value={String(paper.id)} key={paper.id}>{paper.arxiv_id} · {paper.title}</option>)}</select><button className="button secondary compact" disabled={!addPaperId} onClick={() => void addPaper()}><Plus size={14} /> 加入</button></div>
            <div className="project-paper-table">
              {(selected.papers ?? []).map((membership) => <article key={String(membership.id ?? membership.paper_id)} className="project-paper-link" tabIndex={0} onClick={() => onOpenPaper(membership.paper_id)} onKeyDown={(event) => { if (event.key === 'Enter') onOpenPaper(membership.paper_id) }}><div className="project-paper-main"><span>{membership.paper?.arxiv_id ?? membership.paper_id}</span><h3>{membership.paper?.title ?? '论文'}</h3><div>{membership.tags.map((tag) => <em key={tag}>{tag}</em>)}</div></div><div className="project-paper-state"><span className={`state-badge ${membership.reading_state}`}>{READING_STATE_OPTIONS.find(([value]) => value === membership.reading_state)?.[1]}</span><strong>P{membership.priority}</strong></div><div className="project-paper-actions"><button className="icon-button" title="编辑分类库内阅读信息" onClick={(event) => { event.stopPropagation(); setMembershipEdit({ ...membership }) }}><SettingsIcon size={15} /></button><button className="icon-button danger" title="从分类库移除" onClick={(event) => { event.stopPropagation(); void removePaper(membership) }}><X size={16} /></button></div></article>)}
              {!selected.papers?.length && <div className="empty-project-papers"><FileText size={23} /><strong>分类库中还没有论文</strong><p>{selected.kind === 'favorites' ? '在论文列表点击收藏后会自动出现在这里。' : '从上方搜索全部已初筛论文并加入。'}</p></div>}
            </div>
          </> : <div className="detail-placeholder"><NotebookTabs size={28} /><p>创建或选择一个分类库</p></div>}
        </section>
      </div>
      {(creating || editing) && <Modal title={editing ? '编辑分类库' : '新建分类库'} onClose={() => { setCreating(false); setEditing(false) }}><form className="modal-form" onSubmit={saveProject}><label className="field"><span>分类名称</span><input autoFocus required value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="例如：世界模型、待复现、综述素材" /></label><label className="field"><span>分类说明</span><textarea rows={3} value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} placeholder="说明这个分类收录什么论文" /></label><label className="field"><span>标识色</span><div className="color-options">{PROJECT_COLORS.map((color) => <button type="button" className={draft.color === color ? 'active' : ''} key={color} style={{ background: color }} onClick={() => setDraft({ ...draft, color })} aria-label={`选择颜色 ${color}`}>{draft.color === color && <Check size={14} />}</button>)}</div></label><div className="modal-actions"><button type="button" className="button secondary" onClick={() => { setCreating(false); setEditing(false) }}>取消</button><button className="button primary" disabled={saving || !draft.name.trim()}><Save size={15} /> {saving ? '保存中' : '保存分类'}</button></div></form></Modal>}
      {membershipEdit && <Modal title="编辑分类库内阅读信息" onClose={() => setMembershipEdit(null)}><div className="modal-form"><label className="field"><span>阅读状态</span><select value={membershipEdit.reading_state} onChange={(event) => setMembershipEdit({ ...membershipEdit, reading_state: event.target.value as ReadingState })}>{READING_STATE_OPTIONS.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><label className="field"><span>分类内优先级</span><select value={membershipEdit.priority} onChange={(event) => setMembershipEdit({ ...membershipEdit, priority: Number(event.target.value) })}>{[5, 4, 3, 2, 1].map((value) => <option value={value} key={value}>{value} · {PRIORITY_LABELS[value]}</option>)}</select></label><TagEditor label="分类标签" tags={membershipEdit.tags} placeholder="输入标签" onChange={(tags) => setMembershipEdit({ ...membershipEdit, tags })} /><label className="field"><span>分类笔记</span><textarea rows={4} value={membershipEdit.notes} onChange={(event) => setMembershipEdit({ ...membershipEdit, notes: event.target.value })} /></label><div className="modal-actions"><button className="button secondary" onClick={() => setMembershipEdit(null)}>取消</button><button className="button primary" onClick={() => void saveMembership()}><Save size={15} /> 保存</button></div></div></Modal>}
    </div>
  )
}

const PROJECT_COLORS = ['#1f7a5a', '#4267a9', '#b5662d', '#8a4d78', '#80621f', '#4f6d67']
