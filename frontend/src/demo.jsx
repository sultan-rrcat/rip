<aside className="w-80 shrink-0 bg-gray-50 border-r border-gray-200 flex flex-col h-full">

  {/* Brand Header */}
  <div className="px-8 py-6 flex items-center justify-between">
    <div>
      <h1 className="text-lg font-bold text-gray-900">Knowledge Base</h1>
      <p className="text-xs text-gray-400 mt-0.5">3 Active Sources</p>
    </div>
    <div className="w-8 h-8 rounded-full bg-indigo-100 flex items-center justify-center">
      <span className="text-indigo-600 text-sm">📚</span>
    </div>
  </div>

  {/* Upload Button */}
  <div className="px-6">
    <button className="w-full bg-indigo-600 text-white py-3 rounded-xl font-medium flex items-center justify-center gap-2 hover:bg-indigo-700 transition-colors">
      ↑ Upload Document
    </button>
  </div>

  {/* File List */}
  <nav className="flex-1 px-4 mt-6 overflow-y-auto">
    <p className="px-4 mb-3 text-[10px] uppercase tracking-widest font-bold text-gray-400">
      Recent Files
    </p>

    {/* Active File */}
    <div className="flex items-center gap-3 p-3 bg-indigo-50 text-indigo-700 rounded-lg cursor-pointer mb-1">
      <span>📄</span>
      <span className="text-sm truncate">shift_log_nov.pdf</span>
    </div>

    {/* Normal File */}
    <div className="flex items-center gap-3 p-3 text-gray-600 hover:bg-gray-200 rounded-lg cursor-pointer mb-1 transition-colors">
      <span>📊</span>
      <span className="text-sm truncate">accelerator_parameters.csv</span>
    </div>

    {/* Normal File */}
    <div className="flex items-center gap-3 p-3 text-gray-600 hover:bg-gray-200 rounded-lg cursor-pointer transition-colors">
      <span>📘</span>
      <span className="text-sm truncate">control_system_manual.docx</span>
    </div>
  </nav>

  {/* User Profile — always at bottom */}
  <div className="p-6 mt-auto">
    <div className="flex items-center gap-3 p-3 rounded-xl bg-gray-100">
      <div className="w-8 h-8 rounded-lg bg-indigo-200 flex items-center justify-center text-sm font-bold text-indigo-700">
        A
      </div>
      <div>
        <p className="text-xs font-semibold text-gray-800">Dr. Aris Thorne</p>
        <p className="text-[10px] text-gray-400">Lead Investigator</p>
      </div>
    </div>
  </div>

</aside>
