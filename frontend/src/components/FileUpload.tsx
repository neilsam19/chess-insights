import { useState, type FormEvent } from "react";

interface FileUploadProps {
  onSubmit: (file: File) => void;
  isLoading: boolean;
}

export function FileUpload({ onSubmit, isLoading }: FileUploadProps) {
  const [file, setFile] = useState<File | null>(null);
  const [validationError, setValidationError] = useState<string | null>(null);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (!file) {
      setValidationError("Choose a .pgn file.");
      return;
    }

    setValidationError(null);
    onSubmit(file);
  }

  return (
    <form className="upload-form" onSubmit={handleSubmit}>
      <label className="upload-field">
        PGN file
        <input
          type="file"
          accept=".pgn"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>

      {validationError && <p className="upload-error">{validationError}</p>}

      <button type="submit" disabled={isLoading}>
        {isLoading ? "Uploading..." : "Upload"}
      </button>
    </form>
  );
}
