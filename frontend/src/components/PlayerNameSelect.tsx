interface PlayerNameSelectProps {
  names: string[];
  selectedName: string;
  onSelectedNameChange: (name: string) => void;
  onConfirm: () => void;
  isLoading: boolean;
}

export function PlayerNameSelect({
  names,
  selectedName,
  onSelectedNameChange,
  onConfirm,
  isLoading,
}: PlayerNameSelectProps) {
  return (
    <div className="player-name-select">
      <label className="upload-field">
        Which name is yours?
        <select value={selectedName} onChange={(event) => onSelectedNameChange(event.target.value)}>
          {names.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <button type="button" onClick={onConfirm} disabled={isLoading}>
        {isLoading ? "Loading..." : "Confirm"}
      </button>
    </div>
  );
}
