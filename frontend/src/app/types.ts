export type View = 'today' | 'library' | 'digest' | 'projects' | 'interests' | 'settings'
export type LoadState = 'idle' | 'loading' | 'ready' | 'error'
export type Toast = { id: number; kind: 'success' | 'error'; message: string }
