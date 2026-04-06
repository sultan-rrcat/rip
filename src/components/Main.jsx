import Header from "./Header"
import ChatArea from "./ChatArea"
import Footer from "./Footer"

export default function Main() {
    return (
        <main className="flex-1 flex flex-col">
            <Header />
            <ChatArea />
            <Footer />
        </main>
    )
}