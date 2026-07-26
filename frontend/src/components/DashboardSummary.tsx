import type { UploadStats } from "../types/upload";
import { formatOpeningStatName } from "../utils/openings";
import { StatCard } from "./StatCard";

interface DashboardSummaryProps {
  stats: UploadStats;
}

export function DashboardSummary({ stats }: DashboardSummaryProps) {
  return (
    <div className="dashboard-summary">
      <div className="stat-grid">
        <StatCard label="Games" value={stats.total_games} />
        <StatCard label="Wins" value={stats.wins} />
        <StatCard label="Draws" value={stats.draws} />
        <StatCard label="Losses" value={stats.losses} />
        <StatCard label="Win %" value={`${stats.win_percentage}%`} />
        <StatCard label="Score %" value={`${stats.score_percentage}%`} />
        <StatCard label="As White" value={stats.games_as_white} />
        <StatCard label="As Black" value={stats.games_as_black} />
      </div>

      {stats.openings.length > 0 && (
        <table className="opening-table">
          <thead>
            <tr>
              <th>Opening</th>
              <th>Games</th>
              <th>Score %</th>
            </tr>
          </thead>
          <tbody>
            {stats.openings.map((opening) => (
              <tr key={opening.name}>
                <td>{formatOpeningStatName(opening.name)}</td>
                <td>{opening.games}</td>
                <td>{opening.score_percentage}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
