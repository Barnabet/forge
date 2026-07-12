// Compact relative/absolute label for a session's last activity.
// ts is epoch seconds; 0 means unknown.
export function formatLastTs(ts: number, now: number = Date.now() / 1000): string {
  if (!ts) return ''
  const diff = now - ts
  if (diff < 60) return 'now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m`
  const d = new Date(ts * 1000)
  const today = new Date(now * 1000)
  const sameDay = d.toDateString() === today.toDateString()
  if (sameDay)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  if (diff < 7 * 86400) return `${Math.floor(diff / 86400) || 1}d`
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
}
