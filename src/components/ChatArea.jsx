import SmartToyIcon from '@mui/icons-material/SmartToy';

export default function ChatArea(){
    return(
        // <div className="m-2 border">
            <div className="m-2 flex-1 overflow-y-auto px-8 py-10 bg-white rounded-xl shadow-sm">
            {/* flex-1 : take up all the available space inside a flex container */}
            {/* overflow-y-auto : Controls vertical overflow, adds a scrollbar only when content exceed its limit and size. */}
                <div className="my-auto mx-auto space-y-1">
                    <AssistantMessage>
                        How can I assist you today?
                    </AssistantMessage>

                    <UserMessage>
                        What is synchrotron radiation?
                    </UserMessage>
                </div>
            </div>
        // </div>
    )
}

function AssistantMessage({children}){
    return(
        <div className="flex gap-4 items-start">
            {/* avatar  */}
            <div className="w-8 h-8 rounded-lg bg-indigo-100 flex items-center justify-center shrink-0">
                <SmartToyIcon/>
            </div>
            {/* content */}
            <div className="flex-1 space-y-1">
                <p className="text-xs font-medium text-gray-300">Assistant</p>
                <div className="text-sm text-gray-600 leading-relaxed max-w-2xl">
                    {children}
                </div>
            </div>
        </div>
    )
}


function UserMessage({children}){
    return(
        <div className="flex gap-4 items-start justify-end">
            <div className="flex-1 space-y-1 text-right">
                <p className="text-xs font-medium text-gray-400 ">You</p>
                <div className="inline-block text-gray-600 bg-indigo-200 px-5 py-3 rounded-2xl rounded-tr-none text-sm leading-relaxed shadow-sm max-w-2xl">{children}</div>
            </div>
        </div>
    )
}