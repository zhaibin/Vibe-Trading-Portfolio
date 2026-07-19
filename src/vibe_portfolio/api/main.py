import uvicorn

from vibe_portfolio.api.app import create_app
from vibe_portfolio.config import Settings
from vibe_portfolio.web import web_dist_path


def main() -> None:
    settings = Settings()
    if not (web_dist_path() / "index.html").is_file():
        raise RuntimeError("frontend build is missing; build the portfolio web application first")
    uvicorn.run(
        create_app(),
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
