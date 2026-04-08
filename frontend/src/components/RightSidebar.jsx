import AddIcon from '@mui/icons-material/Add'
import LocalLibraryIcon from '@mui/icons-material/LocalLibrary'
import BuildIcon from '@mui/icons-material/Build'
import QuizIcon from '@mui/icons-material/Quiz'
import ImageIcon from '@mui/icons-material/Image'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'

export default function RightSidebar() {

    const tools = [
        {
            title: "Generate Quiz",
            desc: "Test your understanding instantly",
            icon: <QuizIcon />,
            color: "bg-green-100 text-green-600"
        },
        {
            title: "Flash Cards",
            desc: "Revise key concepts quickly",
            icon: <LocalLibraryIcon />,
            color: "bg-blue-100 text-blue-600"
        },
        {
            title: "Summarize Notes",
            desc: "AI-powered summaries",
            icon: <AutoAwesomeIcon />,
            color: "bg-yellow-100 text-yellow-600"
        }
    ]

    return (
        <div className="flex m-1 bg-gray-50 overflow-hidden border border-gray-400 rounded-2xl">
            <aside className="w-72 shrink-0 flex flex-col p-4 gap-4">

                {/* HEADER */}
                <div className="flex items-center justify-between p-3 bg-gray-100 border border-gray-200 rounded-xl">
                    <div>
                        <h1 className="text-lg font-semibold text-gray-900 tracking-tight">
                            Tools
                        </h1>
                    </div>
                    <div className="w-10 h-10 rounded-xl bg-gray-900 text-white flex items-center justify-center shadow">
                        <BuildIcon fontSize="small" />
                    </div>
                </div>

                {/* TOOL LIST */}
                <div className="flex flex-col gap-3 mt-2">
                    {tools.map((tool, i) => (
                        <div
                            key={i}
                            className="flex items-center gap-3 p-3 rounded-xl bg-white border border-gray-200 hover:shadow-md hover:-translate-y-0.5 transition cursor-pointer"
                        >
                            <div className={`w-10 h-10 flex items-center justify-center rounded-lg ${tool.color}`}>
                                {tool.icon}
                            </div>

                            <div className="flex flex-col">
                                <span className="text-sm font-medium text-gray-900">
                                    {tool.title}
                                </span>
                                <span className="text-xs text-gray-500">
                                    {tool.desc}
                                </span>
                            </div>
                        </div>
                    ))}
                </div>

                {/* FOOTER / CONTEXT */}
                <div className="mt-auto p-3 bg-gray-100 rounded-xl text-xs text-gray-600">
                    💡 Tip: Use quizzes after reading to retain 2x more
                </div>

            </aside>
        </div>
    )
}