import { Clock3, Copy, Map, Network, TrendingUp } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../lib/api'
import type { ResearchRoadmap, TopicTrendItem, TopicTrends } from '../../types/trends'
import type { LoadState, Toast } from '../../app/types'
import { WorkspaceSkeleton, PageError } from '../../shared/ui'

export function TrendsView({ onToast }: { onToast: (kind: Toast['kind'], message: string) => void }) {
  const [trends, setTrends] = useState<TopicTrends | null>(null)
  const [roadmap, setRoadmap] = useState<ResearchRoadmap | null>(null)
  const [state, setState] = useState<LoadState>('loading')
  const [error, setError] = useState('')
  const [tab, setTab] = useState<'trends' | 'roadmap'>('trends')
  const [selectedNode, setSelectedNode] = useState('')
  const [trendPeriod, setTrendPeriod] = useState<'daily' | 'weekly'>('daily')

  const load = useCallback(async () => {
    setState('loading'); setError('')

    try {
      const [trendData, roadmapData] = await Promise.all([api.topicTrends(), api.roadmap()])
      setTrends(trendData); setRoadmap(roadmapData); setSelectedNode(roadmapData.nodes[0]?.id ?? ''); setState('ready')
    } catch (loadError) { setState('error'); setError(loadError instanceof Error ? loadError.message : '研究方向数据加载失败') }
  }, [])
  useEffect(() => { void load() }, [load])
  if (state === 'loading') return <WorkspaceSkeleton />
  if (state === 'error' || !trends || !roadmap) return <PageError message={error} onRetry={load} />
  const trendData = trendPeriod === 'daily' ? trends.daily : trends.weekly
  const topicItems = trendData?.topics ?? trends.topics ?? []
  const selected = roadmap.nodes.find((node) => node.id === selectedNode) ?? roadmap.nodes[0]
  return (
    <div className="standard-page trends-page">
      <section className="page-heading">
        <div>
          <div className="eyebrow-row"><TrendingUp size={14} /> 本地研究情报</div>
          <h1>研究方向趋势与路线</h1>
          <p>研究方向只来自“关注设置”中启用的正式研究方向；arXiv 分类、关键词、AI 主题、我的标签和分类标签都不会成为趋势或路线节点。</p>
        </div>
        <div className="segmented-control view-tabs">
          <button className={tab === 'trends' ? 'active' : ''} onClick={() => setTab('trends')}><TrendingUp size={14} /> 研究方向趋势</button>
          <button className={tab === 'roadmap' ? 'active' : ''} onClick={() => setTab('roadmap')}><Map size={14} /> 研究方向路线</button>
        </div>
      </section>
      {tab === 'trends'
        ? <TopicTrendPanel trends={trends} period={trendPeriod} topics={topicItems} onPeriodChange={setTrendPeriod} />
        : <RoadmapPanel roadmap={roadmap} selected={selected} onSelect={setSelectedNode} onToast={onToast} />}
    </div>
  )
}

function TopicTrendPanel({ trends, period, topics, onPeriodChange }: { trends: TopicTrends; period: 'daily' | 'weekly'; topics: TopicTrendItem[]; onPeriodChange: (period: 'daily' | 'weekly') => void }) {
  const periodData = period === 'daily' ? trends.daily : trends.weekly
  const periodLabel = period === 'daily' ? '今日' : '近 7 日'
  const previousLabel = period === 'daily' ? '昨日' : '前 7 日'
  const maxCount = Math.max(1, ...topics.flatMap((topic) => (topic.series ?? []).map((item) => item.count)), ...topics.flatMap((topic) => [topic.count, topic.previous_count]))
  const fastestRising = [...topics].filter((topic) => topic.change > 0).sort((a, b) => b.change - a.change)[0]
  const formalCount = periodData?.paper_count ?? 0
  const candidateCount = periodData?.candidate_paper_count
  return (
    <div className="trends-layout">
      <section className="trend-table">
        {trends.awaiting_daily_update && <div className="trend-pending"><Clock3 size={15} /><div><strong>今日数据尚未更新</strong><span>当前继续展示最近完整研究日 {trends.latest_complete_date ?? trends.as_of}；每日任务完成后会自动切换到 {trends.requested_as_of}。</span></div></div>}
        <header>
          <div><h2>{periodLabel}研究方向变化</h2><p>{periodData?.start_date ?? trends.as_of}{periodData?.end_date && periodData.end_date !== periodData.start_date ? ` 至 ${periodData.end_date}` : ''}</p></div>
          <div className="trend-period-control"><button className={period === 'daily' ? 'active' : ''} onClick={() => onPeriodChange('daily')}>日变化</button><button className={period === 'weekly' ? 'active' : ''} onClick={() => onPeriodChange('weekly')}>周变化</button></div>
          <span>{formalCount} 篇正式方向论文{candidateCount !== undefined ? ` · ${candidateCount} 篇候选` : ''}</span>
        </header>
        <div className="trend-rows">
          {topics.map((topic) => <article key={topic.id}>
            <div className="trend-topic"><strong>{topic.name}</strong><span>{topic.count} 篇 · 均分 {Math.round(topic.average_score ?? 0)}</span></div>
            <MiniSeries values={(topic.series?.length ? topic.series : [{ period: previousLabel, count: topic.previous_count }, { period: periodLabel, count: topic.count }])} max={maxCount} />
            <div className={`momentum ${topic.direction}`}><span>{topic.direction === 'new' ? '新出现' : topic.direction === 'rising' ? '上升' : topic.direction === 'falling' ? '回落' : '稳定'}</span><strong>{topic.change > 0 ? '+' : ''}{topic.change}</strong></div>
          </article>)}
        </div>
      </section>
      <aside className="trend-aside">
        <h2>{periodLabel}观察</h2>
        <div className="trend-callout rising"><TrendingUp size={17} /><div><strong>上升最快的研究方向</strong><span>{fastestRising?.name ?? '暂无'} · 增加 {fastestRising?.change ?? 0} 篇</span></div></div>
        <div className="trend-callout"><Network size={17} /><div><strong>注意力集中度</strong><span>前 3 个研究方向覆盖 {Math.round(topics.slice(0, 3).reduce((sum, topic) => sum + (topic.share_percent ?? 0), 0))}% 的正式方向论文</span></div></div>
        <p className="method-note">{period === 'daily' ? '今日与昨日' : '近 7 日与此前 7 日'}按唯一论文数对比，只统计能归入当前正式研究方向的论文；抓取候选仍保留在“今日论文”，但不进入趋势。结果只反映本地收录。</p>
      </aside>
    </div>
  )
}

function MiniSeries({ values, max }: { values: Array<{ period: string; count: number }>; max: number }) {
  return <div className="mini-series" aria-label={values.map((item) => `${item.period} ${item.count}篇`).join('，')}>{values.map((item) => <span className={item.count === 0 ? 'zero' : ''} key={item.period} title={`${item.period} · ${item.count} 篇`} style={{ height: `${item.count === 0 ? 2 : Math.min(100, Math.max(6, item.count / Math.max(1, max) * 100))}%` }} />)}</div>
}

function RoadmapPanel({ roadmap, selected, onSelect, onToast }: { roadmap: ResearchRoadmap; selected?: ResearchRoadmap['nodes'][number]; onSelect: (id: string) => void; onToast: (kind: Toast['kind'], message: string) => void }) {
  const clusters = roadmap.clusters?.length ? roadmap.clusters : inferClusters(roadmap)
  const relationBasis = (roadmap.relation_basis ?? '连线只表示两个关注研究方向在同一篇论文中共现，不代表引用、因果或方法继承关系。').replace(/主题/g, '研究方向')
  const candidateCount = roadmap.coverage?.candidate_paper_count
  return (
    <div className="roadmap-layout">
      <section className="roadmap-canvas">
        <header>
          <div><h2>研究方向共现图</h2><p>{roadmap.window?.days ?? 90} 天观察窗 · {roadmap.coverage?.paper_count ?? 0} 篇正式方向论文{candidateCount !== undefined ? ` / ${candidateCount} 篇候选` : ''}</p></div>
          <span><Network size={14} /> {roadmap.relations.length} 条连接</span>
        </header>
        <div className="roadmap-clusters">
          {clusters.map((cluster) => <div className="roadmap-cluster" key={cluster.id} style={{ '--cluster-color': cluster.color ?? '#4267a9' } as React.CSSProperties}>
            <div className="cluster-head"><span>{cluster.label}</span><small>{cluster.description}</small></div>
            <div>{cluster.node_ids.map((id) => {
              const node = roadmap.nodes.find((item) => item.id === id)
              if (!node) return null
              const links = roadmap.relations.filter((edge) => edge.source === id || edge.target === id).length
              return <button className={selected?.id === id ? 'active' : ''} key={id} onClick={() => onSelect(id)}><i className={node.status} /><strong>{node.label}</strong><span>{node.paper_count} 篇 · {links} 连接</span></button>
            })}</div>
          </div>)}
        </div>
        <p className="relation-basis">{relationBasis}</p>
      </section>
      <aside className="roadmap-inspector">
        {selected ? <>
          <div className="node-status"><span className={selected.status}>{selected.status === 'emerging' ? '新兴研究方向' : selected.status === 'active' ? '活跃研究方向' : '稳定研究方向'}</span><strong>{Math.round(selected.average_score ?? 0)} 分</strong></div>
          <h2>{selected.label}</h2>
          <p>{selected.paper_count} 篇论文，最早出现于 {selected.first_seen ? new Date(`${selected.first_seen}T12:00:00`).toLocaleDateString('zh-CN') : '本地观察期内'}。</p>
          <InfoTags label="归因关键词" values={selected.keywords ?? []} />
          <InfoTags label="常见方法" values={selected.methods ?? []} />
          <div className="connected-topics">
            <span>高关联研究方向</span>
            {roadmap.relations.filter((edge) => edge.source === selected.id || edge.target === selected.id).sort((a, b) => b.weight - a.weight).slice(0, 4).map((edge) => {
              const id = edge.source === selected.id ? edge.target : edge.source
              const node = roadmap.nodes.find((item) => item.id === id)
              return <button key={id} onClick={() => onSelect(id)}><span>{node?.label ?? id}</span><strong>{edge.weight} 篇共现</strong></button>
            })}
          </div>
          <button className="button secondary" onClick={() => { void navigator.clipboard.writeText(`${selected.label}: ${(selected.keywords ?? []).join('、')}`); onToast('success', '研究方向线索已复制') }}><Copy size={15} /> 复制研究方向线索</button>
        </> : <div className="detail-placeholder"><Map size={25} /><p>选择一个研究方向节点</p></div>}
      </aside>
    </div>
  )
}

function InfoTags({ label, values }: { label: string; values: string[] }) { return <div className="info-tags"><span>{label}</span><div>{values.length ? values.map((value) => <em key={value}>{value}</em>) : <small>暂无稳定信号</small>}</div></div> }

function inferClusters(roadmap: ResearchRoadmap) {
  const statusMeta = [{ id: 'emerging', label: '新兴研究方向', color: '#b5662d' }, { id: 'active', label: '活跃研究方向', color: '#1f7a5a' }, { id: 'established', label: '稳定研究方向', color: '#4267a9' }]
  return statusMeta.map((meta) => ({ ...meta, description: '', node_ids: roadmap.nodes.filter((node) => node.status === meta.id).map((node) => node.id) })).filter((cluster) => cluster.node_ids.length)
}
