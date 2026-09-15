export default function TypingDots() {
  return (
    <div
      role="status"
      aria-label="Assistant is thinking"
      className="flex gap-1.5 items-center h-5 motion-reduce:animate-none"
    >
      <span className="sr-only">Assistant is thinking</span>
      <span
        aria-hidden="true"
        className="w-2 h-2 bg-gray-400 rounded-full animate-bounce motion-reduce:animate-none"
        style={{ animationDelay: '0ms' }}
      />
      <span
        aria-hidden="true"
        className="w-2 h-2 bg-gray-400 rounded-full animate-bounce motion-reduce:animate-none"
        style={{ animationDelay: '150ms' }}
      />
      <span
        aria-hidden="true"
        className="w-2 h-2 bg-gray-400 rounded-full animate-bounce motion-reduce:animate-none"
        style={{ animationDelay: '300ms' }}
      />
    </div>
  )
}
