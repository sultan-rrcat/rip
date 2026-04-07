import SmartToyIcon from '@mui/icons-material/SmartToy';
import { useRef, useEffect } from "react"

export default function ChatArea({ activeFile, messages }) {
    const bottomRef = useRef(null)

    useEffect(() => {
        bottomRef.current?.scrollIntoView({behavior: "smooth"})
    }, [messages])

    return (
        <div className="m-2 space-y-6 flex-1 overflow-y-auto px-8 py-10 bg-white rounded-xl shadow-sm">
            {messages.map((message) => {
                if (message.role === "assistant") {
                    return (
                        <AssistantMessage key={message.id} text={message.text} />
                    )
                }
                if (message.role === "user") {
                    return (
                        <UserMessage key={message.id} text={message.text} />
                    )
                }
                if (message.role === "loading") {
                    return (
                        <AssistantMessage key={message.id}>
                            <TypingIndicator />
                        </AssistantMessage>
                    )
                }
                if(message.role==="error"){
                    return(
                        <ErrorMessage key={message.id} text={message.text}></ErrorMessage>
                    )
                }
            })}
            <div ref={bottomRef} />
        </div>
    )

}

function AssistantMessage({ text, children }) {
    return (
        <div className="flex gap-4 items-start">
            {/* avatar  */}
            <div className="w-8 h-8 rounded-lg bg-indigo-100 flex items-center justify-center shrink-0">
                <SmartToyIcon />
            </div>
            {/* content */}
            <div className="flex-1 space-y-1">
                <p className="text-xs font-medium text-gray-300">Assistant</p>
                <div className="text-sm text-gray-600 leading-relaxed max-w-2xl">
                    {text || children}
                </div>
            </div>
        </div>
    )
}


function UserMessage({ text }) {
    return (
        <div className="flex gap-4 items-start justify-end">
            <div className="flex-1 space-y-1 text-right">
                <p className="text-xs font-medium text-gray-400 ">You</p>
                <div className="inline-block text-gray-600 bg-indigo-200 px-5 py-3 rounded-2xl rounded-tr-none text-sm leading-relaxed shadow-sm max-w-2xl">{text}</div>
            </div>
        </div>
    )
}

function ErrorMessage({ text }) {
    return (
        <div className="flex gap-4 items-start">
            <div className="w-8 h-8 rounded-lg bg-red-100 flex items-center justify-center shrink-0">
                <SmartToyIcon className="text-red-400" />
            </div>
            <div className="flex-1 space-y-1">
                <p className="text-xs font-medium text-red-300">Assistant</p>
                <div className="text-sm text-red-400 leading-relaxed max-w-2xl">
                    {text}
                </div>
            </div>
        </div>
    )
}

function TypingIndicator() {
    return (
        <div className='flex gap-1 items-center h-5'>
            <span className='w-1 h-1 bg-gray-700 rounded-full animate-bounce' style={{ animationDelay: '0ms' }} />
            <span className='w-1 h-1 bg-gray-700 rounded-full animate-bounce' style={{ animationDelay: '150ms' }} />
            <span className='w-1 h-1 bg-gray-700 rounded-full animate-bounce' style={{ animationDelay: '300ms' }} />
        </div>
    )
}