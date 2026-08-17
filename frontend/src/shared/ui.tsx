import { Building2, CircleAlert, Inbox, Plus, RefreshCw, X } from 'lucide-react'
import { ReactNode, useState } from 'react'

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return <div className="modal-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}><section className="modal" role="dialog" aria-modal="true" aria-label={title}><header><h2>{title}</h2><button className="icon-button" onClick={onClose} aria-label="关闭"><X size={18} /></button></header>{children}</section></div>
}

export function FormSection({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <section className="form-section"><div className="form-section-head"><h2>{title}</h2><p>{description}</p></div><div className="form-section-body">{children}</div></section>
}

export function TagEditor({ label, tags, onChange, placeholder, tone = 'neutral' }: { label: string; tags: string[]; onChange: (tags: string[]) => void; placeholder: string; tone?: 'neutral' | 'positive' | 'negative' }) {
  const [draft, setDraft] = useState('')
  const add = () => {
    const next = draft.trim().replace(/,$/, '')
    const key = next.toLocaleLowerCase().replace(/[\s_-]+/g, '')
    if (next && !tags.some((tag) => tag.toLocaleLowerCase().replace(/[\s_-]+/g, '') === key)) onChange([...tags, next])
    setDraft('')
  }
  return (
    <div className="field tag-field"><span>{label}</span><div className={`tag-editor ${tone}`}>
      {tags.map((tag) => <span className="editable-tag" key={tag}>{tag}<button type="button" onClick={() => onChange(tags.filter((item) => item !== tag))} aria-label={`移除 ${tag}`}><X size={13} /></button></span>)}
      <input value={draft} onChange={(event) => setDraft(event.target.value)} onBlur={add} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ',') { event.preventDefault(); add() } if (event.key === 'Backspace' && !draft && tags.length) onChange(tags.slice(0, -1)) }} placeholder={tags.length ? '' : placeholder} />
      <button className="tag-add" type="button" onMouseDown={(event) => event.preventDefault()} onClick={add} aria-label="添加"><Plus size={15} /></button>
    </div></div>
  )
}

export function WorkspaceSkeleton() {
  return <div className="workspace-page loading-page"><div className="skeleton heading-skeleton" /><div className="skeleton metric-skeleton" /><div className="skeleton content-skeleton" /></div>
}

export function PageError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return <div className="page-state"><div className="state-icon error"><CircleAlert size={24} /></div><h1>暂时无法加载数据</h1><p>{message}</p><button className="button primary" onClick={onRetry}><RefreshCw size={16} /> 重新连接</button><small>请确认后端服务已在本机启动，并检查 `/api` 代理配置。</small></div>
}

export function EmptyPapers({ filtered }: { filtered: boolean }) {
  return <div className="empty-state"><Inbox size={25} /><h3>{filtered ? '没有匹配的论文' : '今天还没有候选论文'}</h3><p>{filtered ? '调整关键词或筛选条件后再试。' : '可手动抓取最新论文，或等待下次定时任务。'}</p></div>
}

export function OrganizationBadge({ name }: { name: string }) {
  return <span className="organization-badge" title="重点机构，依据 arXiv 明确提供的作者单位识别"><Building2 size={11} /> {name}</span>
}
