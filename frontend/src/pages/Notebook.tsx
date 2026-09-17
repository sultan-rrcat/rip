import { useParams } from 'react-router-dom'
import ChatArea from '@/components/notebook/ChatArea'
import Footer from '@/components/notebook/Footer'
import LeftSidebar from '@/components/notebook/LeftSidebar'
import { useNotebook } from '@/hooks/notebooks/useNotebook'
import { useFiles } from '@/hooks/notebooks/useFiles'
import { useMessages } from '@/hooks/notebooks/useMessages'

export default function Notebook() {
  const { notebook_id } = useParams()
  const { notebookName, renameNotebook } = useNotebook(notebook_id)
  const { files, handleUpload, handleDelete } = useFiles(notebook_id)
  const { messages, activeRun, pastRuns, isRunning, handleSendMessage, handleCancelRun } =
    useMessages(notebook_id)

  return (
    <div className="flex h-screen bg-gray-200 overflow-hidden">
      <LeftSidebar
        files={files}
        onUpload={handleUpload}
        onDelete={handleDelete}
        notebookName={notebookName}
        onRenameNotebook={renameNotebook}
      />
      <main className="flex-1 flex flex-col overflow-hidden">
        <ChatArea messages={messages} activeRun={activeRun} pastRuns={pastRuns} />
        <Footer
          onSendMessage={handleSendMessage}
          isLoading={isRunning}
          isRunning={isRunning}
          onCancel={handleCancelRun}
        />
      </main>
    </div>
  )
}
