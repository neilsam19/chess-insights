import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UploadAnalysisPanel } from "./UploadAnalysisPanel";
import * as analysisApi from "../api/analysis";
import type { BatchCreated, BatchStatus } from "../types/analysis";

vi.mock("../api/analysis", async () => {
  const actual = await vi.importActual<typeof analysisApi>("../api/analysis");
  return {
    ...actual,
    analyzeUpload: vi.fn(),
    getBatch: vi.fn(),
    cancelBatch: vi.fn(),
  };
});

const UPLOAD_ID = "11111111-0000-4000-8000-000000000001";
const BATCH_ID = "22222222-0000-4000-8000-000000000002";

function created(overrides: Partial<BatchCreated> = {}): BatchCreated {
  return {
    batch_id: BATCH_ID,
    upload_id: UPLOAD_ID,
    status: "queued",
    queued: 5,
    skipped_complete: 0,
    already_active: 0,
    ineligible: 0,
    total_games: 5,
    engine: { requested_depth: 12, options: { Threads: 1 } },
    ...overrides,
  };
}

function batch(overrides: Partial<BatchStatus> = {}): BatchStatus {
  return {
    batch_id: BATCH_ID,
    upload_id: UPLOAD_ID,
    status: "running",
    counts: { queued: 3, running: 1, complete: 1, failed: 0, cancelled: 0 },
    total_jobs: 5,
    completed_positions: 30,
    total_positions: 150,
    percent: 20,
    games_complete: 1,
    created_at: "2026-07-30T00:00:00Z",
    started_at: "2026-07-30T00:00:01Z",
    completed_at: null,
    cancel_requested: false,
    can_cancel: true,
    failed_game_ids: [],
    ...overrides,
  };
}

function renderPanel() {
  return render(<UploadAnalysisPanel uploadId={UPLOAD_ID} totalGames={5} />);
}

describe("bulk upload analysis", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(analysisApi.analyzeUpload).mockResolvedValue(created());
    vi.mocked(analysisApi.getBatch).mockResolvedValue(batch());
    vi.mocked(analysisApi.cancelBatch).mockResolvedValue(
      batch({ status: "cancelled", can_cancel: false, cancel_requested: true }),
    );
  });

  it("offers to analyse the whole upload with one action", async () => {
    renderPanel();

    expect(
      screen.getByRole("button", { name: "Analyze All Games" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Queues all 5 games/)).toBeInTheDocument();
    expect(analysisApi.analyzeUpload).not.toHaveBeenCalled();
  });

  it("queues every game and then shows aggregate progress", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Analyze All Games" }));

    await waitFor(() =>
      expect(analysisApi.analyzeUpload).toHaveBeenCalledWith(UPLOAD_ID),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "1 of 5 games analysed",
    );
    expect(
      screen.getByRole("progressbar", { name: "Upload analysis progress" }),
    ).toHaveValue(30);
  });

  it("reports what was skipped, not just what was queued", async () => {
    // The difference between "nothing happened" and "nothing needed to".
    const user = userEvent.setup();
    vi.mocked(analysisApi.analyzeUpload).mockResolvedValue(
      created({ batch_id: null, queued: 0, skipped_complete: 4, already_active: 1 }),
    );
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Analyze All Games" }));

    expect(await screen.findByText(/4 already analysed/)).toBeInTheDocument();
    expect(screen.getByText(/1 already queued/)).toBeInTheDocument();
  });

  it("says plainly when there is nothing to do", async () => {
    const user = userEvent.setup();
    vi.mocked(analysisApi.analyzeUpload).mockResolvedValue(
      created({ batch_id: null, queued: 0, skipped_complete: 5 }),
    );
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Analyze All Games" }));

    expect(await screen.findByText(/5 already analysed/)).toBeInTheDocument();
    expect(analysisApi.getBatch).not.toHaveBeenCalled();
  });

  it("shows a breakdown of job states", async () => {
    const user = userEvent.setup();
    vi.mocked(analysisApi.getBatch).mockResolvedValue(
      batch({
        counts: { queued: 2, running: 1, complete: 3, failed: 1, cancelled: 0 },
      }),
    );
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Analyze All Games" }));

    const counts = await screen.findByRole("list");
    expect(counts).toHaveTextContent("3 complete");
    expect(counts).toHaveTextContent("1 failed");
    // Zero-valued states are omitted rather than shown as noise.
    expect(counts).not.toHaveTextContent("cancelled");
  });

  it("cancels the whole batch", async () => {
    const user = userEvent.setup();
    renderPanel();
    await user.click(screen.getByRole("button", { name: "Analyze All Games" }));

    await user.click(await screen.findByRole("button", { name: "Cancel All" }));

    await waitFor(() => expect(analysisApi.cancelBatch).toHaveBeenCalledWith(BATCH_ID));
    expect(
      await screen.findByText(/Games already analysed were kept/),
    ).toBeInTheDocument();
  });

  it("does not offer to queue again while a batch is active", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Analyze All Games" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Analyze All Games" })).toBeDisabled(),
    );
  });

  it("distinguishes a partial failure from a total one", async () => {
    const user = userEvent.setup();
    vi.mocked(analysisApi.getBatch).mockResolvedValue(
      batch({
        status: "partial_failed",
        can_cancel: false,
        counts: { queued: 0, running: 0, complete: 4, failed: 1, cancelled: 0 },
      }),
    );
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Analyze All Games" }));

    expect(await screen.findByText(/some games failed/)).toBeInTheDocument();
  });

  it("surfaces a queueing failure without pretending work started", async () => {
    const user = userEvent.setup();
    vi.mocked(analysisApi.analyzeUpload).mockRejectedValue(new Error("boom"));
    renderPanel();

    await user.click(screen.getByRole("button", { name: "Analyze All Games" }));

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  });
});

describe("bulk polling", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    vi.mocked(analysisApi.analyzeUpload).mockResolvedValue(created());
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  async function tick(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  /**
   * fireEvent rather than userEvent: userEvent schedules its own delays, which
   * deadlock against fake timers here.
   */
  async function clickStart() {
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Analyze All Games" }));
    });
  }

  it("polls while running and stops when the batch finishes", async () => {
    vi.mocked(analysisApi.getBatch)
      .mockResolvedValueOnce(batch())
      .mockResolvedValueOnce(batch({ games_complete: 3 }))
      .mockResolvedValue(
        batch({
          status: "complete",
          can_cancel: false,
          percent: 100,
          games_complete: 5,
          counts: { queued: 0, running: 0, complete: 5, failed: 0, cancelled: 0 },
        }),
      );

    render(<UploadAnalysisPanel uploadId={UPLOAD_ID} totalGames={5} />);
    await clickStart();

    await tick(2100);
    await tick(2100);

    expect(screen.getByText("All games analysed.")).toBeInTheDocument();

    const callsWhenDone = vi.mocked(analysisApi.getBatch).mock.calls.length;
    await tick(20000);
    expect(vi.mocked(analysisApi.getBatch).mock.calls.length).toBe(callsWhenDone);
  });

  it("stops polling on unmount", async () => {
    vi.mocked(analysisApi.getBatch).mockResolvedValue(batch());

    const { unmount } = render(
      <UploadAnalysisPanel uploadId={UPLOAD_ID} totalGames={5} />,
    );
    await clickStart();
    await tick(2100);
    const beforeUnmount = vi.mocked(analysisApi.getBatch).mock.calls.length;

    unmount();
    await tick(20000);

    expect(vi.mocked(analysisApi.getBatch).mock.calls.length).toBe(beforeUnmount);
  });
});
