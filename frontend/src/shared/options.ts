import type { FeedbackReason, ReadingState } from '../types/papers'

export const READING_STATE_OPTIONS: Array<[ReadingState, string]> = [
  ['unread', '未读'], ['queued', '待读队列'], ['reading', '阅读中'], ['read', '已读'], ['reproduce', '准备复现'], ['cited', '已引用'], ['archived', '已归档'],
]

export const PRIORITY_LABELS: Record<number, string> = { 1: '低', 2: '稍后', 3: '普通', 4: '优先', 5: '最高' }

export const READING_QUEUE_LABELS: Record<string, string> = { must_read: '必读', read: '精读', skim: '速览', skip: '暂存' }

export const FEEDBACK_REASON_OPTIONS: Array<[FeedbackReason, string]> = [
  ['not_relevant', '主题不相关'], ['incremental', '方法增量有限'], ['weak_evidence', '证据不足'], ['no_real_robot', '缺少真实机器人'],
  ['no_code', '未开放代码'], ['duplicate', '内容重复'], ['poor_writing', '表达不清'], ['topic_pause', '暂缓关注此主题'],
]
