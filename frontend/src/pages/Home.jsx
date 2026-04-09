import Card from '../components/home/Card'
import { useState, useEffect, useDebugValue } from 'react'


export default function Home() {
    const [notebooks, setNotebooks] = useState([])

    function getNotebooks() {
        return JSON.parse(localStorage.getItem("notebooks")) || []
    }

    function saveNotebooks(updated) {
        localStorage.setItem("notebooks", JSON.stringify(updated))
    }

    function handleRename(id) {
        const newName = prompt
    }

    useEffect(() => {
        const stored = JSON.parse(localStorage.getItem("notebooks")) || []
        setNotebooks(stored)
    }, [])

    return (
        <div className="flex items-center justify-center h-screen">
            <div className="m-2 h-3/4 w-3/4 border rounded-2xl bg-gray-100 flex items-center justify-center overflow-y-auto ">
                <div className='flex flex-wrap items-center justify-center'>
                    <Card isNew={true} title={"Create New"} />
                    {notebooks.map((n => (
                        <Card
                            key={n.id}
                            isNew={false}
                            title={n.name}
                            id={n.id}
                            setNotebooks={setNotebooks}
                        />
                    )))}
                </div>
            </div>
        </div>
    )
}

