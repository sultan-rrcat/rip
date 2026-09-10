export default function Header() {
  return (
    <div className="m-1">
      <header className="h-11 px-8 font-inter shrink-0 bg-white border border-gray-400 flex items-center justify-between shadow-sm rounded-xl">
        <span className="text-md font-semibold text-gray-900 tracking-tight">
          Deep Research Assistant
        </span>

        <div className="flex items-center gap-4">
          {/* <div className="flex items-center gap-2 px-4 py-1.5 bg-gray-200 rounded-full">
                        <SearchIcon/>
                        <input type="text" placeholder="Search insights..." className="bg-transparent border-none outline-none text-xs text-gray placeholder:text-gray-400 w-40" />

                    </div> */}
        </div>
      </header>
    </div>
  )
}
