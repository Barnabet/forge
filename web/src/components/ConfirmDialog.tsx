import Modal from './Modal'
import s from './Dialogs.module.css'

export default function ConfirmDialog({
  title,
  body,
  confirmLabel,
  onConfirm,
  onCancel,
}: {
  title: string
  body: string
  confirmLabel: string
  onConfirm(): void
  onCancel(): void
}) {
  return (
    <Modal title={title} onClose={onCancel}>
      <div className={s.body}>{body}</div>
      <div className={s.actions}>
        <button className={s.ghost} onClick={onCancel}>Cancel</button>
        <button className={s.danger} onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </Modal>
  )
}
