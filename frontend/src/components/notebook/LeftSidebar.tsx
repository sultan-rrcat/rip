import AddIcon from '@mui/icons-material/Add'
import LocalLibraryIcon from '@mui/icons-material/LocalLibrary'
import FolderOpenIcon from '@mui/icons-material/FolderOpen'
import PictureAsPdfIcon from '@mui/icons-material/PictureAsPdf'
import DeleteIcon from '@mui/icons-material/Delete'
import { useEffect, useState, memo } from 'react'
import type { ChangeEvent, KeyboardEvent } from 'react'
import type { NotebookFile } from '@/types'

interface LeftSidebarProps {
  files: NotebookFile[]
  onUpload: (event: ChangeEvent<HTMLInputElement>) => void
  onDelete: (fileId: string) => void
  onRenameNotebook: (name: string) => void
  notebookName: string
}

const LeftSidebar = memo(function LeftSidebar({
  files,
  onUpload,
  onDelete,
  onRenameNotebook,
  notebookName,
}: LeftSidebarProps) {
  const readyCount = files.filter((f) => f.status === 'ready').length
  const [isRenaming, setIsRenaming] = useState(false)
  const [tempName, setTempName] = useState(notebookName)

  function handleRenameSave() {
    if (!tempName.trim()) return
    onRenameNotebook(tempName)
    setIsRenaming(false)
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') handleRenameSave()
    if (e.key === 'Escape') {
      onRenameNotebook(notebookName)
      setIsRenaming(false)
    }
  }

  useEffect(() => {
    setTempName(notebookName)
  }, [notebookName])

  return (
    <div className="flex flex-col h-full">
      {/* name field  */}
      <div className="flex p-3 m-1 bg-white overflow-hidden border border-gray-400 rounded-xl items-center">
        {isRenaming ? (
          <input
            autoFocus
            value={tempName}
            onChange={(e) => setTempName(e.target.value)}
            onBlur={handleRenameSave}
            onKeyDown={handleKeyDown}
            className="w-full px-2 py-1 border-none text-md rounded"
          />
        ) : (
          <div
            onClick={() => setIsRenaming(true)}
            className="cursor-pointer text-md font-medium hover:bg-gray-200 px-2 py-1 rounded"
          >
            {notebookName}
          </div>
        )}
      </div>
      {/* sidebar  */}
      <div className="flex m-1 h-full bg-white overflow-hidden border border-gray-400 rounded-xl">
        <aside className="w-70 shrink-0 flex flex-col border-r border-gray-200 rounded-xl shadow-sm">
          {/* header  */}
          <div className="m-5 p-4 bg-gray-100 border border-gray-200 rounded-md flex items-center justify-between">
            <div>
              <h1 className="text-lg font-bold text-gray-900">
                Knowledge Base
              </h1>
              <p className="text-xs text-gray-400 mt-0.5">
                {readyCount} Active Source{readyCount !== 1 ? 's' : ''}
              </p>
            </div>
            <div className="w-9 h-9 rounded-full bg-blue-100 flex items-center justify-center">
              {/* <span className="text-indigo-600 text-sm">✅</span> */}
              {/* <span className="material-symbols-outlined" style={{fontSize:'28px'}}>local_library</span> */}
              <LocalLibraryIcon />
            </div>
          </div>

          {/* upload button  */}
          <div className="px-5">
            <input
              id="file-upload"
              type="file"
              accept=".pdf"
              multiple
              className="hidden"
              onChange={onUpload}
            />

            <label
              htmlFor="file-upload"
              className="w-full font-inter text-sm font-semibold bg-white text-black py-2 border border-gray-200 rounded-xl  flex items-center justify-center gap-2 cursor-pointer hover:bg-gray-100 hover:shadow-sm transition-all duration-200"
            >
              <AddIcon />
              Add sources
            </label>
          </div>

          {/* file list  */}
          <nav className="m-5 px-2 border border-gray-200 rounded-xl flex-1 overflow-y-auto">
            {files.length === 0 && (
              <div className="px-4 py-8 text-center">
                <FolderOpenIcon />
                <p className="text-xs text-gray-400 mt-2">
                  No files uploaded yet
                </p>
              </div>
            )}
            <div className="my-3">
              {files.map((file) => (
                <FileItem key={file.id} file={file} onDelete={onDelete} />
              ))}
            </div>
          </nav>
        </aside>
      </div>
    </div>
  )
})

export default LeftSidebar

function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

interface FileItemProps {
  file: NotebookFile
  onDelete: (fileId: string) => void
}

function FileItem({ file, onDelete }: FileItemProps) {
  const bgColor = {
    uploading: 'bg-blue-50',
    processing: 'bg-yellow-50',
    ready: 'bg-green-50',
    error: 'bg-red-100',
  }[file.status]
  return (
    <div className="border border-gray-100 rounded-sm m-1">
      <div
        className={`group flex items-center justify-between text-gray-300 cursor-pointer ${bgColor}`}
      >
        <div className="flex gap-3 items-center overflow-hidden flex-1">
          <PictureAsPdfIcon
            fontSize="small"
            className="text-red-500 shrink-0"
          />
          <div className="min-w-0">
            <p
              className="text-[10px] text-gray-800 truncate"
              title={file.name}
            >
              {file.name}
            </p>
            <p className="text-[9px] text-gray-400">
              {formatFileSize(file.size)}
            </p>
            <p className="text-[9px] text-gray-400 flex items-center gap-2">
              {file.status === 'uploading' && (
                <>
                  <span className="w-3 h-3 border-2 border-gray-300 border-t-indigo-500 rounded-full animate-spin"></span>
                  Uploading
                </>
              )}

              {file.status === 'processing' && (
                <>
                  <span className="w-3 h-3 border-2 border-gray-300 border-t-indigo-500 rounded-full animate-spin"></span>
                  Processing
                </>
              )}

              {file.status === 'ready' && (
                <span className="text-green-500 font-medium">Ready</span>
              )}

              {file.status === 'error' && (
                <span className="text-red-500 font-medium">
                  Error: Error adding file to Knowledge base.
                </span>
              )}
            </p>
          </div>
        </div>

        <button
          onClick={(e) => {
            e.stopPropagation()
            onDelete(file.id)
          }}
          className="opacity-0 group-hover:opacity-100 transition-opacity duration-200 p-1 rounded hover:bg-red-100"
          aria-label="Delete File"
        >
          <DeleteIcon
            fontSize="small"
            className="text-gray-400 hover:text-red-500"
          />
        </button>
      </div>
    </div>
  )
}
