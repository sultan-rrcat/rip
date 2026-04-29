import {BrowserRouter, Routes, Route} from "react-router-dom"
import Home from "./pages/Home"
import Notebook from "./pages/Notebook"

export default function App(){
  return(
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home/>}/>
        <Route path="/notebook/:notebook_id" element={<Notebook/>}/>
      </Routes>
    </BrowserRouter>
  )
}