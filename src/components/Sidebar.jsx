import AddIcon from '@mui/icons-material/Add'
import LocalLibraryIcon from '@mui/icons-material/LocalLibrary';
import FolderOpenIcon from '@mui/icons-material/FolderOpen';
import PictureAsPdfIcon from '@mui/icons-material/PictureAsPdf';
import DeleteIcon from '@mui/icons-material/Delete';


export default function Sidebar({ files, onUpload, onDelete, activeFileIds = [], onFileSelect, onSelectAll }) {
    return (
        <div className="flex m-2 bg-white overflow-hidden border-none rounded-xl">
            <aside className="w-80 shrink-0 flex flex-col border-r border-gray-200 rounded-xl shadow-sm">

                {/* header  */}
                <div className="px-8 py-6 flex items-center justify-between">
                    <div>
                        <h1 className="text-lg font-bold text-gray-900">Knowledge Base</h1>
                        <p className="text-xs text-gray-400 mt-0.5">{activeFileIds.length} Active Source{activeFileIds.length !== 1 ? 's' : ''}</p>
                    </div>
                    <div className="w-10 h-10 rounded-full bg-indigo-100 flex items-center justify-center">
                        {/* <span className="text-indigo-600 text-sm">✅</span> */}
                        {/* <span className="material-symbols-outlined" style={{fontSize:'28px'}}>local_library</span> */}
                        <LocalLibraryIcon />
                    </div>
                </div>

                {/* upload button  */}
                <div className="px-6">
                    <input id="file-upload" type="file" accept=".pdf" multiple className="hidden" onChange={onUpload} />

                    <label htmlFor="file-upload" className="w-full font-inter text-sm font-semibold bg-blue-100 text-gray-500 py-3 border border-gray-400 rounded-xl  flex items-center justify-center gap-2 cursor-pointer hover:bg-gray-100 hover:shadow-sm transition-all duration-200">
                        <AddIcon />
                        Add sources
                    </label>
                </div>

                {/* file list  */}
                <nav className="px-6">

                    {/* {files.length !== 0 && (
                        <p className="m-3 text-[10px] uppercase tracking-widest font-bold text-gray-500 flex items-center justify-center">
                            Recent Files
                        </p>
                    )} */}

                    {files.length === 0 && (
                        <div className="px-4 py-8 text-center">
                            <FolderOpenIcon />
                            <p className="text-xs text-gray-400 mt-2">No files uploaded yet</p>
                        </div>
                    )}

                    {files.length > 0 && (
                        <div className='p-2 border-none flex'>
                            <label className='flex items-center gap-2 cursor-pointer'>
                                <input
                                    type="checkbox"
                                    checked={activeFileIds.length === files.length && files.length > 0}
                                    onChange={onSelectAll}
                                    className="w-4 h-4 cursor-pointer accent-indigo-600"
                                />
                                <span className="text-[12px] font-semibold text-gray-600 hover:text-gray-800 ">
                                    Select All
                                </span>
                            </label>
                        </div>
                    )}
                    <div className='space-y-1'>
                        {files.map(file => (
                            <FileItem
                                key={file.id}
                                file={file}
                                onDelete={onDelete}
                                isActive={activeFileIds.includes(file.id)}
                                onSelect={() => onFileSelect(file.id)}
                            />
                        ))}

                    </div>
                </nav>
            </aside>
        </div>
    )
}

function FileItem({ file, onDelete, isActive, onSelect }) {
    return (
        <div onClick={onSelect} className={`group flex items-center justify-between p-1 text-gray-300 cursor-pointer mb-1 ${isActive ? 'bg-indigo-50 border border-indigo-200' : 'hover:bg-gray-100'}`}>
            <div className="flex items-center gap-2 overflow-hidden flex-1">
                <input
                    type='checkbox'
                    checked={isActive}
                    onChange={(e) => {
                        e.stopPropagation()
                        onSelect()
                    }}
                    className='w-4 h-4 shrink-0 cursor-pointer accent-indigo-300'
                />
                <PictureAsPdfIcon className='text-red-500 shrink-0' />
                <div className='min-w-0'>
                    <p className='text-xs font-medium text-gray-800 truncate' title={file.name}>{file.name}</p>
                    <p className='text-xs text-gray-400'>{file.size}</p>
                    <p className='text-xs text-gray-400 flex items-center gap-2'>
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
                            <span className="text-red-500 font-medium">Error</span>
                        )}
                    </p>
                </div>
            </div>

            <button onClick={(e) => { e.stopPropagation(); onDelete(file.id) }}
                className="opacity-0 group-hover:opacity-100 transition-opacity duration-200 p-1 rounded hover:bg-red-100" aria-label='Delete File'>
                <DeleteIcon className='text-gray-400 hover:text-red-500' />
            </button>

        </div>
    )
}