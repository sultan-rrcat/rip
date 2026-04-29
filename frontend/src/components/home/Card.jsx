import AddIcon from '@mui/icons-material/Add'
import MoreVertIcon from '@mui/icons-material/MoreVert'
import Menu from '@mui/material/Menu'
import MenuItem from '@mui/material/MenuItem'
import EditIcon from '@mui/icons-material/Edit'
import DeleteIcon from '@mui/icons-material/Delete'

import Dialog from '@mui/material/Dialog'
import DialogTitle from '@mui/material/DialogTitle'
import DialogContent from '@mui/material/DialogContent'
import DialogActions from '@mui/material/DialogActions'
import TextField from '@mui/material/TextField'
import Button from '@mui/material/Button'

import Note from '@mui/icons-material/Note'
import { useNavigate } from 'react-router-dom'

import { createNotebookAPI, renameNotebookAPI, deleteNotebookAPI } from "../../services/notebooks";

import { useState, useEffect } from 'react'

export default function Card({ isNew, title, id, setNotebooks, onDelete }) {
    // console.log(`Notebook-Properties: id: ${id} - title: ${title} - isNew: ${isNew}`)

    const [openEdit, setOpenEdit] = useState(false)
    const [openDelete, setOpenDelete] = useState(false)
    const [newTitle, setNewTitle] = useState(title)
    const [anchorEl, setAnchorEl] = useState(null)
    const open = Boolean(anchorEl)

    const navigate = useNavigate();

    async function createNotebook() {
        const data = await createNotebookAPI("Untitled Vault")
        navigate(`/notebook/${data.notebook_id}`)
    }

    function handleMenuOpen(event) {
        event.stopPropagation()
        setAnchorEl(event.currentTarget)
        console.log("clicked 3 dot")
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

    async function handleEditSave() {
        await renameNotebookAPI(id, newTitle)
        setNotebooks(prev =>
            prev.map(n => n.notebook_id === id ? { ...n, notebook_name: newTitle } : n)
        )
        setOpenEdit(false)
    }

    async function handleDeleteConfirm() {
        setOpenDelete(false)
        try {
            await deleteNotebookAPI(id)
            onDelete(id)
        } catch (err) {
            console.error(err)
        } finally {
            setOpenDelete(false)
        }
    }

    function handleSaveKeyDown(e) {
        if (e.key === "Enter") {
            e.preventDefault()
            handleEditSave()
        }
    }

    useEffect(() => {
        setNewTitle(title)
    }, [title])


    if (!isNew) {
        return (
            <div onClick={() => navigate(`/notebook/${id}`)} className='m-2 bg-white border rounded-2xl h-48 w-48 relative flex items-center justify-center flex-col cursor-pointer'>
                <div onClick={handleMenuOpen} className='absolute top-2 right-2 h-10 w-10 hover:bg-gray-200 rounded-xl flex items-center justify-center'><MoreVertIcon fontSize='small' /></div>
                <div className='m-2 p-3 bg-gray-100 border rounded-xl border-gray-200 flex'><Note fontSize='large' /></div>
                <p className="m-2 text-md truncate overflow-hidden whitespace-nowrap max-w-28" title={title}>{title}</p>
                <div>
                    <Menu anchorEl={anchorEl} open={open} onClose={handleMenuClose} onClick={(e) => e.stopPropagation()}>
                        <MenuItem onClick={() => {
                            handleMenuClose();
                            console.log("Edit Clicked");
                            setOpenEdit(true);
                        }}>
                            <EditIcon fontSize='small' style={{ marginRight: 8 }} />
                            Edit Title
                        </MenuItem>

                        <MenuItem onClick={() => {
                            handleMenuClose();
                            console.log("Delete Clicked");
                            setOpenDelete(true);
                        }}>
                            <DeleteIcon fontSize='small' style={{ marginRight: 8 }} />
                            Delete
                        </MenuItem>
                    </Menu>
                </div>
                <div>
                    <Dialog open={openEdit} onClose={handleEditClose} onClick={(e) => e.stopPropagation()}>
                        <DialogTitle>Edit Notebook Title</DialogTitle>
                        <DialogContent>
                            <TextField autoFocus fullWidth value={newTitle} onChange={(e) => setNewTitle(e.target.value)} onKeyDown={handleSaveKeyDown} variant='outlined' size='small' />
                        </DialogContent>
                        <DialogActions>
                            <Button onClick={handleEditClose}>Cancel</Button>
                            <Button onClick={handleEditSave} variant='contained'>Save</Button>
                        </DialogActions>
                    </Dialog>
                </div>

                <div>
                    <Dialog open={openDelete} onClose={handleDeleteClose} onClick={(e) => e.stopPropagation()}>
                        <DialogTitle>Delete Notebook</DialogTitle>
                        <DialogContent>
                            Are you sure you want to delete <b>{title}</b>
                            <br />
                            This action cannot be undone.
                        </DialogContent>
                        <DialogActions>
                            <Button onClick={() => handleDeleteClose()} >Cancel</Button>
                            <Button onClick={() => handleDeleteConfirm()} color='error' variant='contained' >Delete</Button>
                        </DialogActions>
                    </Dialog>
                </div>
            </div>
        )

    } else {
        return (
            <div onClick={createNotebook} className='m-2 bg-white border rounded-2xl h-48 w-48 flex items-center justify-center flex-col cursor-pointer'>
                <div className='m-2 p-3 bg-gray-300 border rounded-full border-gray-200 flex'><AddIcon fontSize='large' /></div>
                <div className='m-2 text-xs flex'>{title}</div>
            </div>
        )
    }
}