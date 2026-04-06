import SendIcon from '@mui/icons-material/Send';

export default function Footer() {
    return (
        <footer className="m-2 px-8 py-6 bg-red shrink-0 flex items-center justify-center bg-white rounded-xl shadow-sm">
            <div className="w-full max-w-3xl mx-auto">
                {/* input bar */}
                <div className="flex items-center bg-gray-200 rounded-2xl p-1 focus-within:ring-2 focus-within:ring-gray-200 transition-all">
                    {/* input text  */}
                    <input type="text" placeholder="Ask me anything..." className="flex-1 bg-transparent border-none outline-none text-sm text-gray-600 placeholder:text-gray-400 p-4   " />

                    {/* send button  */}
                    <button className="w-11 h-11 bg-gray-500 text-white rounded-xl flex items-center justify-center ">
                        <SendIcon/>
                    </button>
                </div>
            </div>  
        </footer>
    )
}