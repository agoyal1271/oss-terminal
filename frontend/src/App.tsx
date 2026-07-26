import { Link, Route, Routes, useLocation } from "react-router-dom";
import { SearchBar } from "./components/SearchBar";
import { HomePage } from "./pages/HomePage";
import { CompanyPage } from "./pages/CompanyPage";
import "./App.css";

function App() {
  const location = useLocation();
  const isHome = location.pathname === "/";

  return (
    <div className="app-shell">
      {!isHome && (
        <header className="top-nav">
          <Link to="/" className="brand">OSS Terminal</Link>
          <div className="top-search">
            <SearchBar />
          </div>
        </header>
      )}
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/c/:ticker" element={<CompanyPage />} />
      </Routes>
    </div>
  );
}

export default App;
