import { useEffect, useState } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import { highlight } from '../lib/highlight'
import s from './FileViewer.module.css'

// Module-level so the array identity is stable across renders.
const gfmPlugins = [remarkGfm]

const IMAGE = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'ico', 'avif'])
const VIDEO = new Set(['mp4', 'webm', 'mov', 'mkv'])
const AUDIO = new Set(['mp3', 'wav', 'ogg', 'm4a', 'flac'])
const MARKDOWN = new Set(['md', 'markdown'])

function extOf(path: string): string {
  const base = path.slice(path.lastIndexOf('/') + 1)
  const dot = base.lastIndexOf('.')
  return dot > 0 ? base.slice(dot + 1).toLowerCase() : ''
}

function baseName(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1) || path
}

type TextState =
  | { status: 'loading' }
  | { status: 'text'; content: string }
  | { status: 'fallback' }

function TextRenderer({ sid, path, markdown }: { sid: string; path: string; markdown: boolean }) {
  const [state, setState] = useState<TextState>({ status: 'loading' })
  const [html, setHtml] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setState({ status: 'loading' })
    setHtml(null)
    api.fsReadText(sid, path)
      .then(content => {
        if (!alive) return
        if (content.includes('\0')) { setState({ status: 'fallback' }); return }
        setState({ status: 'text', content })
        if (!markdown) {
          highlight(content, extOf(path))
            .then(h => { if (alive && h) setHtml(h) })
            .catch(() => {})
        }
      })
      .catch(() => { if (alive) setState({ status: 'fallback' }) })
    return () => { alive = false }
  }, [sid, path, markdown])

  if (state.status === 'loading') return <div className={s.loading}>Loading…</div>
  if (state.status === 'fallback') return <Fallback sid={sid} path={path} />

  if (markdown) {
    return <div className={s.markdown}><Markdown remarkPlugins={gfmPlugins}>{state.content}</Markdown></div>
  }

  const lines = state.content.replace(/\n$/, '').split('\n')
  return (
    <div className={s.code}>
      <pre className={s.gutter} aria-hidden="true">
        {lines.map((_, i) => `${i + 1}\n`).join('')}
      </pre>
      {html
        ? <div className={s.text} dangerouslySetInnerHTML={{ __html: html }} />
        : <pre className={s.text}>{state.content}</pre>}
    </div>
  )
}

function Fallback({ sid, path }: { sid: string; path: string }) {
  return (
    <div className={s.fallback}>
      <div className={s.fileName}>{baseName(path)}</div>
      <div className={s.fallbackMsg}>Cannot preview this file</div>
      <a className={s.download} href={api.fsFileUrl(sid, path)} download>Download</a>
    </div>
  )
}

export default function FileViewer({ sid, path }: { sid: string; path: string }) {
  const ext = extOf(path)
  const url = api.fsFileUrl(sid, path)

  if (IMAGE.has(ext)) {
    return <div className={s.media}><img className={s.image} src={url} alt={baseName(path)} /></div>
  }
  if (VIDEO.has(ext)) {
    return <div className={s.media}><video className={s.video} controls src={url} /></div>
  }
  if (AUDIO.has(ext)) {
    return <div className={s.media}><audio controls src={url} /></div>
  }
  if (ext === 'pdf') {
    return <iframe className={s.pdf} src={url} title={baseName(path)} />
  }
  return <TextRenderer sid={sid} path={path} markdown={MARKDOWN.has(ext)} />
}
