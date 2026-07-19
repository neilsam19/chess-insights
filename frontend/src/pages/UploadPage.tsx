import { useState } from "react";
import { FileUpload } from "../components/FileUpload";
import { PlayerNameSelect } from "../components/PlayerNameSelect";
import { DashboardSummary } from "../components/DashboardSummary";
import { GameList } from "../components/GameList";
import { ChessGameViewer } from "../components/ChessGameViewer";
import { postUploadPreview, UploadError } from "../api/uploads";
import { getMostFrequentPlayerName, getUniquePlayerNames } from "../utils/players";
import type { UploadPreviewResponse } from "../types/upload";

type Phase = "idle" | "choosing-name" | "ready";

export function UploadPage() {
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<UploadPreviewResponse | null>(null);
  const [uploadedFile, setUploadedFile] = useState<File | null>(null);
  const [selectedName, setSelectedName] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [selectedGameIndex, setSelectedGameIndex] = useState<number | null>(null);

  async function handleFileSubmit(file: File) {
    setIsLoading(true);
    setError(null);

    try {
      // player_name is intentionally omitted here - we just want the parsed
      // games so the user can pick which name in the file is theirs.
      const response = await postUploadPreview(file, "");
      setUploadedFile(file);
      setResult(response);
      setSelectedName(getMostFrequentPlayerName(response.games));
      setPhase("choosing-name");
    } catch (err) {
      setError(
        err instanceof UploadError ? err.message : "Something went wrong. Please try again.",
      );
      setResult(null);
      setPhase("idle");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleNameConfirm() {
    if (!uploadedFile) return;

    setIsLoading(true);
    setError(null);

    try {
      const response = await postUploadPreview(uploadedFile, selectedName);
      setResult(response);
      setSelectedGameIndex(response.games.length > 0 ? 0 : null);
      setPhase("ready");
    } catch (err) {
      setError(
        err instanceof UploadError ? err.message : "Something went wrong. Please try again.",
      );
    } finally {
      setIsLoading(false);
    }
  }

  const selectedGame =
    result && selectedGameIndex !== null ? result.games[selectedGameIndex] : null;

  return (
    <main className="upload-page">
      <h1>Chess Insights</h1>
      <FileUpload onSubmit={handleFileSubmit} isLoading={isLoading} />

      {error && (
        <p className="upload-error" role="alert">
          {error}
        </p>
      )}

      {phase === "choosing-name" && result && (
        <PlayerNameSelect
          names={getUniquePlayerNames(result.games)}
          selectedName={selectedName}
          onSelectedNameChange={setSelectedName}
          onConfirm={handleNameConfirm}
          isLoading={isLoading}
        />
      )}

      {phase === "ready" && result && (
        <>
          {result.warnings.length > 0 && (
            <ul className="upload-warnings">
              {result.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}
          <DashboardSummary stats={result.stats} />

          <div className="games-section">
            <GameList
              games={result.games}
              selectedIndex={selectedGameIndex}
              onSelect={setSelectedGameIndex}
            />
            {selectedGame && <ChessGameViewer key={selectedGame.index} game={selectedGame} />}
          </div>
        </>
      )}
    </main>
  );
}
