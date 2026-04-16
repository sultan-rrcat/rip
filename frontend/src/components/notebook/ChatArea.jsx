import SmartToyIcon from '@mui/icons-material/SmartToy';
import { useRef, useEffect } from "react";
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { oneLight } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';


export default function ChatArea({ messages }) {
    const bottomRef = useRef(null)

    useEffect(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth" })
    }, [messages])

    return (
        <div className="m-1 space-y-6 flex-1 overflow-y-auto px-8 py-10 bg-white border border-gray-400 rounded-xl shadow-sm">
            {messages.map((message) => {
                if (message.role === "assistant") {
                    return (
                        <AssistantMessage key={message.id} text={message.text} sources={message.sources} />
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
                if (message.role === "error") {
                    return (
                        <ErrorMessage key={message.id} text={message.text}></ErrorMessage>
                    )
                }
            })}
            <div ref={bottomRef} />
        </div>
    )

}

function AssistantMessage({ text, children, sources = [] }) {
    return (
        <div className="flex gap-4 items-start">
            {/* avatar  */}
            <div className="w-8 h-8 rounded-lg bg-blue-100 flex items-center justify-center shrink-0">
                <SmartToyIcon />
            </div>
            {/* content */}
            <div className="flex-1 space-y-1">
                <p className="text-xs font-small text-gray-300">Assistant</p>
                <div className="text-xs text-gray-600 leading-relaxed max-w-2xl border border-gray-200 p-4 rounded-xl rounded-tl-none bg-gray-100">
                    {children ? children : (
                        <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                                // Custom renderer for code blocks
                                code({ node, inline, className, children, ...props }) {
                                    const match = /language-(\w+)/.exec(className || '')
                                    return !inline && match ? (
                                        <SyntaxHighlighter
                                            style={oneLight}
                                            language={match}
                                            PreTag="div"
                                            {...props}
                                        >
                                            {String(children).replace(/\n$/, '')}
                                        </SyntaxHighlighter>
                                    ) : (
                                        <code className={className} {...props}>
                                            {children}
                                        </code>
                                    )
                                },
                                // Ensure tables look good with Tailwind
                                table: ({ node, ...props }) => (
                                    <div className="overflow-x-auto my-2">
                                        <table className="border-collapse border border-gray-300 min-w-full" {...props} />
                                    </div>
                                ),
                                th: ({ node, ...props }) => <th className="border border-gray-300 px-4 py-2 bg-gray-200" {...props} />,
                                td: ({ node, ...props }) => <td className="border border-gray-300 px-4 py-2" {...props} />,
                            }}
                        >
                            {text}
                        </ReactMarkdown>
                    )}

                    {sources && sources.length > 0 && (
                        <div className='mt-4 pt-3 border-t border-gray-300'>
                            <p className='text-[11px] font-semibold text-gray-500 mb-1'>Sources</p>
                            <ul>
                                {
                                    sources.length > 0 && (
                                        sources.map((s, i) => (
                                            <li key={i} className='text-[11px] text-gray-500'>
                                                [{i + 1}] {s.source} - {s.section}
                                            </li>
                                        ))
                                    )
                                }
                            </ul>
                        </div>
                    )}
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
                <div className="inline-block text-gray-600 bg-blue-100 px-5 py-2 rounded-2xl rounded-tr-none text-xs leading-relaxed shadow-sm max-w-2xl">{text}</div>
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