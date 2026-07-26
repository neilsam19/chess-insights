import "./App.css";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { HomePage } from "./pages/HomePage";
import { SavedDashboardPage } from "./pages/SavedDashboardPage";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/uploads/:uploadId" element={<SavedDashboardPage />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
