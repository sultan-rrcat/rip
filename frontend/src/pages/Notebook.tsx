import { useParams, Link } from 'react-router-dom'
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
    <div className="flex h-screen min-h-0 gap-2 overflow-hidden bg-bench p-2">
      <LeftSidebar
        files={files}
        onUpload={handleUpload}
        onDelete={handleDelete}
        notebookName={notebookName}
        onRenameNotebook={renameNotebook}
      />
      <main className="flex min-h-0 flex-1 flex-col gap-2 overflow-hidden">
        <div className="flex shrink-0 items-center justify-between px-1 pt-0.5">
          <Link
            to="/"
            className="font-ledger text-[11px] font-semibold tracking-[0.18em] text-ink-soft/70 transition-colors hover:text-ink"
          >
            ← STACK
          </Link>
          <p className="font-ledger hidden text-[10px] tracking-[0.2em] text-ink-soft/50 sm:block">
            OFFLINE · NOTHING LEAVES THIS MACHINE
          </p>
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <ChatArea messages={messages} activeRun={activeRun} pastRuns={pastRuns} />
        </div>
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
