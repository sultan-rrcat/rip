import AddIcon from '@mui/icons-material/Add'
import MoreVertIcon from '@mui/icons-material/MoreVert'
import Menu from '@mui/material/Menu'
import MenuItem from '@mui/material/MenuItem'
import EditIcon from '@mui/icons-material/Edit'
import DeleteIcon from '@mui/icons-material/Delete'
import ArrowForwardIcon from '@mui/icons-material/ArrowForward'

import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import TextField from '@mui/material/TextField'
import Button from '@mui/material/Button'

import { useNavigate } from 'react-router-dom'

import {
  createNotebookAPI,
  renameNotebookAPI,
  deleteNotebookAPI,
} from '@/services/notebooks'
import type { Notebook } from '@/types'

import { useState } from 'react'
import type {
  Dispatch,
  KeyboardEvent,
  MouseEvent,
  SetStateAction,
} from 'react'

interface NewCardProps {
  isNew: true
  title: string
}

interface ExistingCardProps {
  isNew: false
  title: string
  id: string
  shelfmark: string
  ledger: string
  index: number
  setNotebooks: Dispatch<SetStateAction<Notebook[]>>
  onDelete: (id: string) => void
}

type CardProps = NewCardProps | ExistingCardProps

export default function Card(props: CardProps) {
  const { isNew, title } = props

  const [openEdit, setOpenEdit] = useState(false)
  const [openDelete, setOpenDelete] = useState(false)
  const [newTitle, setNewTitle] = useState(title)
  const [anchorEl, setAnchorEl] = useState<HTMLElement | null>(null)
  const open = Boolean(anchorEl)

  const navigate = useNavigate()

  async function createNotebook(): Promise<void> {
    const data = await createNotebookAPI('Untitled Vault')
    navigate(`/notebook/${data.notebook_id}`)
  }

  function handleMenuOpen(event: MouseEvent<HTMLButtonElement>) {
    event.stopPropagation()
    setAnchorEl(event.currentTarget)
  }

  function handleMenuClose() {
    setAnchorEl(null)
  }

  function handleEditClose() {
    setOpenEdit(false)
  }

  function handleDeleteClose() {
    setOpenDelete(false)
  }

  async function handleEditSave(): Promise<void> {
    if (isNew) return
    const next = newTitle.trim()
    if (!next) return
    await renameNotebookAPI(props.id, next)
    props.setNotebooks((prev) =>
      prev.map((nb) =>
        nb.notebook_id === props.id ? { ...nb, notebook_name: next } : nb,
      ),
    )
    setOpenEdit(false)
  }

  async function handleDeleteConfirm(): Promise<void> {
    if (isNew) return
    setOpenDelete(false)
    try {
      await deleteNotebookAPI(props.id)
      props.onDelete(props.id)
    } catch (err) {
      console.error(err)
    } finally {
      setOpenDelete(false)
    }
  }

  function handleSaveKeyDown(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === 'Enter') {
      e.preventDefault()
      handleEditSave()
    }
  }

  function handleEditOpen() {
    setNewTitle(title)
    setOpenEdit(true)
  }

  if (!isNew) {
    const { id, shelfmark, ledger, index } = props
    return (
      <div
        className="drawer-row drawer-enter relative rounded-lg border border-line bg-card shadow-[0_1px_0_rgba(21,39,54,0.12),0_8px_24px_-16px_rgba(21,39,54,0.4)]"
        style={{ animationDelay: `${Math.min(index, 8) * 60}ms` }}
      >
        {/* signature: brass drawer pull */}
        <div className="flex justify-center pt-2.5" aria-hidden="true">
          <span className="brass-pull block h-1.5 w-12 rounded-full bg-brass" />
        </div>
        <div className="flex items-center gap-3 px-4 pb-3 pt-2 sm:gap-4 sm:px-5">
          <span className="font-ledger hidden shrink-0 rounded border border-line bg-paper px-2 py-1 text-[11px] font-semibold tracking-widest text-ink-soft sm:inline-block">
            {shelfmark}
          </span>
          <button
            type="button"
            onClick={() => navigate(`/notebook/${id}`)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') navigate(`/notebook/${id}`)
            }}
            className="min-w-0 flex-1 cursor-pointer rounded text-left"
            aria-label={`Open ${title}`}
          >
            <span className="font-display block truncate text-[17px] font-semibold leading-tight text-ink">
              {title}
            </span>
            <span className="font-ledger mt-1 block truncate text-[11px] tracking-wide text-ink-soft/70">
              {ledger}
            </span>
          </button>
          <span className="hidden h-8 w-px bg-line/70 sm:block" aria-hidden="true" />
          <span className="flex shrink-0 items-center gap-1">
            <button
              type="button"
              onClick={() => navigate(`/notebook/${id}`)}
              className="flex h-9 w-9 items-center justify-center rounded-full text-ink-soft transition-colors hover:bg-paper"
              aria-label={`Open ${title}`}
            >
              <ArrowForwardIcon fontSize="small" />
            </button>
            <button
              type="button"
              onClick={handleMenuOpen}
              className="flex h-9 w-9 items-center justify-center rounded-full text-ink-soft transition-colors hover:bg-paper"
              aria-label={`Options for ${title}`}
              aria-haspopup="menu"
            >
              <MoreVertIcon fontSize="small" />
            </button>
          </span>
        </div>
        <div>
          <Menu
            anchorEl={anchorEl}
            open={open}
            onClose={handleMenuClose}
            onClick={(e) => e.stopPropagation()}
          >
            <MenuItem
              onClick={() => {
                handleMenuClose()
                handleEditOpen()
              }}
            >
              <EditIcon fontSize="small" style={{ marginRight: 8 }} />
              Edit Title
            </MenuItem>

            <MenuItem
              onClick={() => {
                handleMenuClose()
                setOpenDelete(true)
              }}
            >
              <DeleteIcon fontSize="small" style={{ marginRight: 8 }} />
              Delete
            </MenuItem>
          </Menu>
        </div>
        <div>
          <Dialog
            open={openEdit}
            onClose={handleEditClose}
            onClick={(e) => e.stopPropagation()}
          >
            <DialogTitle>Edit Notebook Title</DialogTitle>
            <DialogContent>
              <TextField
                autoFocus
                fullWidth
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                onKeyDown={handleSaveKeyDown}
                variant="outlined"
                size="small"
              />
            </DialogContent>
            <DialogActions>
              <Button onClick={handleEditClose}>Cancel</Button>
              <Button onClick={handleEditSave} variant="contained">
                Save
              </Button>
            </DialogActions>
          </Dialog>
        </div>

        <div>
          <Dialog
            open={openDelete}
            onClose={handleDeleteClose}
            onClick={(e) => e.stopPropagation()}
          >
            <DialogTitle>Delete Notebook</DialogTitle>
            <DialogContent>
              Are you sure you want to delete <b>{title}</b>
              <br />
              This action cannot be undone.
            </DialogContent>
            <DialogActions>
              <Button onClick={() => handleDeleteClose()}>Cancel</Button>
              <Button
                onClick={() => handleDeleteConfirm()}
                color="error"
                variant="contained"
              >
                Delete
              </Button>
            </DialogActions>
          </Dialog>
        </div>
      </div>
    )
  } else {
    return (
      <button
        type="button"
        onClick={createNotebook}
        className="drawer-enter group flex w-full items-center gap-4 rounded-lg border-2 border-dashed border-ink-soft/30 bg-transparent px-5 py-5 text-left transition-colors hover:border-ledger hover:bg-card/60"
        style={{ animationDelay: '0ms' }}
      >
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-ink text-paper transition-colors group-hover:bg-ledger">
          <AddIcon fontSize="small" />
        </span>
        <span>
          <span className="font-display block text-[15px] font-semibold text-ink">
            New line of inquiry
          </span>
          <span className="mt-0.5 block text-[13px] text-ink-soft/75">
            Name it after the question, not the files.
          </span>
        </span>
      </button>
    )
  }
}
