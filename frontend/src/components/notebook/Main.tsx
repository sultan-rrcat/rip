import Header from './Header'
import ChatArea from './ChatArea'
import Footer from './Footer'
import type { Message } from '@/types'

interface MainProps {
  messages: Message[]
  onSendMessage: (text: string) => void
}

export default function Main({ messages, onSendMessage }: MainProps) {
  return (
    <main className="flex-1 flex flex-col">
      <Header />
      <ChatArea messages={messages} />
      <Footer onSendMessage={onSendMessage} />
    </main>
  )
}
