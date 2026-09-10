import CloseIcon from '@mui/icons-material/Close'
import ErrorIcon from '@mui/icons-material/Error'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import InfoIcon from '@mui/icons-material/Info'

export type NotificationType = 'info' | 'error' | 'success'

export interface NotificationData {
  show: boolean
  type: NotificationType
  message: string
}

interface NotificationProps {
  notification: NotificationData
  onClose: () => void
}

export default function Notification({
  notification,
  onClose,
}: NotificationProps) {
  if (!notification.show) return null

  const bgColor = {
    info: 'bg-blue-50 border-blue-200',
    error: 'bg-red-50 border-red-200',
    success: 'bg-green-50 border-green-200',
  }

  const textColor = {
    info: 'text-blue-800',
    error: 'text-red-800',
    success: 'text-green-800',
  }

  const iconColor = {
    info: 'text-blue-500',
    error: 'text-red-500',
    success: 'text-green-500',
  }

  const getIcon = () => {
    switch (notification.type) {
      case 'error':
        return <ErrorIcon className={`${iconColor[notification.type]}`} />
      case 'success':
        return <CheckCircleIcon className={`${iconColor[notification.type]}`} />
      default:
        return <InfoIcon className={`${iconColor[notification.type]}`} />
    }
  }

  return (
    <div className="fixed top-4 right-4 z-50 animate-slideIn">
      <div
        className={`border rounded-lg p-4 shadow-lg ${bgColor[notification.type]}`}
      >
        <div className="flex items-start gap-3">
          {getIcon()}
          <div className="flex-1">
            <p className={`text-sm font-medium ${textColor[notification.type]}`}>
              {notification.message}
            </p>
          </div>
          <button
            onClick={onClose}
            className={`${textColor[notification.type]} hover:opacity-70 transition-opacity`}
          >
            <CloseIcon fontSize="small" />
          </button>
        </div>
      </div>
    </div>
  )
}
