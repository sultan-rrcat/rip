import { useNavigate } from 'react-router-dom'
import IconButton from '@mui/material/IconButton'
import LogoutIcon from '@mui/icons-material/Logout'
import Tooltip from '@mui/material/Tooltip'

export default function LogoutButton() {
  const navigate = useNavigate()

  async function handleLogout() {
    try {
      await fetch('/api/auth/logout', {
        method: 'POST',
        credentials: 'include',
      })
    } finally {
      navigate('/login')
    }
  }

  return (
    <Tooltip title="Logout">
      <IconButton onClick={handleLogout} color="inherit" aria-label="Logout">
        <LogoutIcon />
      </IconButton>
    </Tooltip>
  )
}
