import Card from '@/components/home/Card'
import LogoutButton from '@/components/LogoutButton'
import { useState, useEffect } from 'react'
import { getNotebooksAPI } from '@/services/notebooks'
import type { Notebook } from '@/types'

export default function Home() {
  const [notebooks, setNotebooks] = useState<Notebook[]>([])

  useEffect(() => {
    let cancelled = false
    getNotebooksAPI()
      .then((data) => {
        if (!cancelled && Array.isArray(data)) setNotebooks(data)
      })
      .catch((err) => {
        console.error('Failed to load notebooks:', err)
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="relative flex items-center justify-center h-screen">
      <div className="absolute top-4 right-4">
        <LogoutButton />
      </div>
      <div className="m-2 h-3/4 w-3/4 border rounded-2xl bg-gray-100 flex items-center justify-center overflow-y-auto ">
        <div className="flex flex-wrap items-center justify-center">
          <Card isNew={true} title={'Create New'} />
          {notebooks.map((n) => (
            <Card
              key={n.notebook_id}
              isNew={false}
              title={n.notebook_name}
              id={n.notebook_id}
              setNotebooks={setNotebooks}
              onDelete={(id) => {
                setNotebooks((prev) =>
                  prev.filter((nb) => nb.notebook_id !== id),
                )
              }}
            />
          ))}
        </div>
      </div>
    </div>
  )
}
