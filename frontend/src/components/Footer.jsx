import SendIcon from '@mui/icons-material/Send';
import { useState } from 'react';

export default function Footer({ onSendMessage, isLoading }) {
    const [inputText, setInputText] = useState("")

    function handleSend() {
        if (!inputText.trim()) return
        onSendMessage(inputText)
        setInputText("")
    }

    function handleKeyDown(e) {
        if (e.key === "Enter") {
            handleSend()
        }
    }

    return (
        <footer className="m-1 py-4 shrink-0 bg-white rounded-xl shadow-sm">
            <div className="w-full max-w-3xl mx-auto">
                {/* input bar */}
                <div className="flex items-center justify-center bg-gray-200 rounded-xl p-1 focus-within:ring-2 focus-within:ring-gray-200 transition-all">
                    {/* input text  */}
                    <input
                        type="text"
                        value={inputText}
                        onChange={(e) => { setInputText(e.target.value) }}
                        onKeyDown={handleKeyDown}
                        placeholder="Ask me anything..."
                        className="flex-1 bg-transparent border-none outline-none text-xs text-gray-600 placeholder:text-gray-400 p-3   "
                        disabled={isLoading}
                    />

                    {/* send button  */}
                    <button
                        onClick={handleSend}
                        disabled={isLoading}
                        className="w-8 h-8 bg-gray-500 text-white rounded-md flex items-center justify-center cursor-pointer">
                        <SendIcon />
                    </button>
                </div>
            </div>
        </footer>
    )
}