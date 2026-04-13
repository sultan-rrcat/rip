import Card from '../components/home/Card'
import { useState, useEffect } from 'react'

export default function Home() {
    const [notebooks, setNotebooks] = useState([])

    useEffect(() => {
        fetch("http://localhost:5000/api/notebooks")
            .then(res => res.json())
            .then(data => setNotebooks(data))
    }, [])

    return (
        <div className="flex items-center justify-center h-screen">
            <div className="m-2 h-3/4 w-3/4 border rounded-2xl bg-gray-100 flex items-center justify-center overflow-y-auto ">
                <div className='flex flex-wrap items-center justify-center'>
                    <Card isNew={true} title={"Create New"} />
                    {notebooks.map((n => (
                        <Card
                            key={n.notebook_id}
                            isNew={false}
                            title={n.notebook_name}
                            id={n.notebook_id}
                            setNotebooks={setNotebooks}
                        />
                    )))}
                </div>
            </div>
        </div>
    )
}

