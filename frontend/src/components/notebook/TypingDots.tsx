export default function TypingDots() {
  return (
    <div
      role="status"
      aria-label="Assistant is thinking"
      className="flex h-5 items-center gap-1.5 motion-reduce:animate-none"
    >
      <span className="sr-only">Assistant is thinking</span>
      <span
        aria-hidden="true"
        className="h-2 w-2 animate-bounce rounded-full bg-brass motion-reduce:animate-none"
        style={{ animationDelay: '0ms' }}
      />
      <span
        aria-hidden="true"
        className="h-2 w-2 animate-bounce rounded-full bg-brass motion-reduce:animate-none"
        style={{ animationDelay: '150ms' }}
      />
      <span
        aria-hidden="true"
        className="h-2 w-2 animate-bounce rounded-full bg-brass motion-reduce:animate-none"
        style={{ animationDelay: '300ms' }}
      />
    </div>
  )
}
