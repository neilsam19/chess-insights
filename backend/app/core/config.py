from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    frontend_origin: str = "http://localhost:5173"
    max_upload_size_bytes: int = 5 * 1024 * 1024  # 5 MB
    database_url: str = "postgresql+psycopg://chess_user:chess_password@localhost:5433/chess_insights"

    # Stockfish is a binary dependency, installed manually and pointed at by
    # configuration - never downloaded at runtime and never supplied by a
    # client. Unset means engine analysis is unavailable, not that it should
    # be searched for on PATH.
    stockfish_path: str | None = None
    stockfish_depth: int = 12
    stockfish_threads: int = 1
    stockfish_hash_mb: int = 64

    # Analysis worker. Only the worker process reads these - the API never
    # starts an engine, so none of them affect request handling.
    #
    # Unset worker id means "derive one from host and pid", which is what makes
    # lease ownership meaningful when more than one worker is running.
    analysis_worker_id: str | None = None
    # How many games the worker analyses at once. Bounded by configuration and
    # never derived from how much work is submitted - queueing 500 games must
    # start this many engines, not 500.
    #
    # Approximate CPU demand is `concurrency x STOCKFISH_THREADS`, and memory
    # roughly `concurrency x STOCKFISH_HASH_MB`. Default 1 is the safe local
    # choice: analysis stays responsive and nothing competes for cores.
    analysis_worker_concurrency: int = 1
    # How long a claim stays valid without a heartbeat. Comfortably longer than
    # the time one position takes, so a slow position never looks like a dead
    # worker. The sweep that acts on expiry arrives in Checkpoint 4.
    analysis_lease_seconds: int = 60
    # How often the lease is renewed while a job runs. Must be comfortably
    # smaller than the lease itself: renewal happens on a background thread, so
    # a single slow position cannot let its own lease expire.
    #
    # This also bounds how quickly a worker notices a cancellation, since it
    # learns about one from the same heartbeat - which is why it is 2s rather
    # than something lazier. One single-row update every two seconds per
    # running job is nothing; a cancel that takes five seconds to be noticed
    # reads to a user as a button that did not work.
    analysis_heartbeat_seconds: float = 2.0
    # How long to wait before polling again when the queue is empty.
    analysis_poll_seconds: float = 2.0
    # How often to look for jobs abandoned by a dead worker.
    analysis_stale_sweep_seconds: float = 30.0
    # Delay before a requeued job becomes claimable again. Keeps a job that
    # kills its worker from being retried in a tight loop.
    analysis_retry_delay_seconds: int = 5

    model_config = {"env_file": ".env"}


settings = Settings()
