import io
import stat
import sys
import tarfile
import warnings
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

from verify_release_artifacts import verify_sdist, verify_wheel  # noqa: E402

ROOT = "vibe_trading_portfolio-0.1.0"
REQUIRED_SDIST_FILES = {
    "LICENSE": b"license\n",
    "README.md": b"readme\n",
    "alembic.ini": b"[alembic]\n",
    "frontend/index.html": b"<main></main>",
    "frontend/package-lock.json": b"{}\n",
    "frontend/package.json": b"{}\n",
    "frontend/playwright.config.ts": b"export default {};\n",
    "frontend/tooling/eslint.config.js": b"export default [];\n",
    "frontend/tooling/package.json": b"{}\n",
    "frontend/tsconfig.json": b"{}\n",
    "frontend/vite.config.ts": b"export default {};\n",
    "pyproject.toml": b"[project]\n",
    "uv.lock": b"version = 1\n",
}
REQUIRED_SDIST_PREFIX_FILES = {
    "docs/runbooks/portfolio-data.md": b"runbook\n",
    "frontend/e2e/portfolio.spec.ts": b"test('portfolio', () => {});\n",
    "frontend/src/main.tsx": b"export {};\n",
    "scripts/verify_release_artifacts.py": b"pass\n",
    "src/vibe_portfolio/__init__.py": b"",
    "src/vibe_portfolio/portfolio/migrations/env.py": b"pass\n",
    "tests/test_config.py": b"def test_config(): pass\n",
}
CURATED_SDIST_FILES = REQUIRED_SDIST_FILES | REQUIRED_SDIST_PREFIX_FILES


def _write_sdist(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, payload in files.items():
            info = tarfile.TarInfo(f"{ROOT}/{name}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def _write_wheel(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)


def test_verify_sdist_accepts_curated_source_archive(tmp_path: Path) -> None:
    archive = tmp_path / "package.tar.gz"
    _write_sdist(archive, CURATED_SDIST_FILES)

    report = verify_sdist(archive)

    assert report.member_count == len(CURATED_SDIST_FILES)
    assert report.payload_bytes == sum(len(payload) for payload in CURATED_SDIST_FILES.values())


@pytest.mark.parametrize("missing", tuple(REQUIRED_SDIST_FILES))
def test_verify_sdist_rejects_each_missing_required_file(tmp_path: Path, missing: str) -> None:
    archive = tmp_path / "package.tar.gz"
    files = CURATED_SDIST_FILES.copy()
    del files[missing]
    _write_sdist(archive, files)

    with pytest.raises(ValueError, match=f"required file: {missing}"):
        verify_sdist(archive)


@pytest.mark.parametrize(
    "missing_prefix",
    (
        "docs/runbooks/",
        "frontend/e2e/",
        "frontend/src/",
        "scripts/",
        "src/vibe_portfolio/",
        "src/vibe_portfolio/portfolio/migrations/",
        "tests/",
    ),
)
def test_verify_sdist_rejects_each_missing_required_prefix(tmp_path: Path, missing_prefix: str) -> None:
    archive = tmp_path / "package.tar.gz"
    files = {name: payload for name, payload in CURATED_SDIST_FILES.items() if not name.startswith(missing_prefix)}
    _write_sdist(archive, files)

    with pytest.raises(ValueError, match=f"required prefix: {missing_prefix}"):
        verify_sdist(archive)


@pytest.mark.parametrize(
    "member",
    (
        ".coverage",
        ".superpowers/sdd/progress.md",
        "dist/package.whl",
        "var/portfolio.db",
        "frontend/node_modules/package/index.js",
        "frontend/coverage/index.html",
        "frontend/test-results/result.json",
        "frontend/playwright-report/index.html",
        "src/package/__pycache__/module.pyc",
    ),
)
def test_verify_sdist_rejects_local_or_generated_members(tmp_path: Path, member: str) -> None:
    archive = tmp_path / "package.tar.gz"
    _write_sdist(archive, CURATED_SDIST_FILES | {member: b"artifact"})

    with pytest.raises(ValueError, match="forbidden archive member"):
        verify_sdist(archive)


def test_verify_sdist_rejects_personal_path_in_payload(tmp_path: Path) -> None:
    archive = tmp_path / "package.tar.gz"
    personal_path = b"built at /" + b"Users/zhaibin/project"
    _write_sdist(archive, CURATED_SDIST_FILES | {"docs/report.md": personal_path})

    with pytest.raises(ValueError, match="sensitive payload"):
        verify_sdist(archive)


def test_verify_wheel_requires_index_and_separate_hashed_css_and_js(tmp_path: Path) -> None:
    archive = tmp_path / "package.whl"
    _write_wheel(
        archive,
        {
            "vibe_portfolio/web/dist/index.html": b"<html></html>",
            "vibe_portfolio/web/dist/assets/index-0123456789abcdef.css": b"body{}",
            "vibe_portfolio/web/dist/assets/index-fedcba9876543210.js": b"export{}",
        },
    )

    report = verify_wheel(archive)

    assert report.member_count == 3
    assert report.payload_bytes == 27


@pytest.mark.parametrize(
    ("missing", "message"),
    (
        ("vibe_portfolio/web/dist/index.html", "exact SPA index.html"),
        ("vibe_portfolio/web/dist/assets/index-0123456789abcdef.css", "hashed SPA CSS"),
        ("vibe_portfolio/web/dist/assets/index-fedcba9876543210.js", "hashed SPA JavaScript"),
    ),
)
def test_verify_wheel_reports_each_missing_spa_artifact(tmp_path: Path, missing: str, message: str) -> None:
    archive = tmp_path / "package.whl"
    files = {
        "vibe_portfolio/web/dist/index.html": b"<html></html>",
        "vibe_portfolio/web/dist/assets/index-0123456789abcdef.css": b"body{}",
        "vibe_portfolio/web/dist/assets/index-fedcba9876543210.js": b"export{}",
    }
    del files[missing]
    _write_wheel(archive, files)

    with pytest.raises(ValueError, match=message):
        verify_wheel(archive)


def test_verify_wheel_rejects_duplicate_normalized_member_names(tmp_path: Path) -> None:
    archive = tmp_path / "package.whl"
    files = {
        "vibe_portfolio/web/dist/index.html": b"<html></html>",
        "vibe_portfolio/web/dist/assets/index-0123456789abcdef.css": b"body{}",
        "vibe_portfolio/web/dist/assets/index-fedcba9876543210.js": b"export{}",
    }
    with zipfile.ZipFile(archive, "w") as wheel:
        for name, payload in files.items():
            wheel.writestr(name, payload)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            wheel.writestr("vibe_portfolio/web/dist/index.html", b"duplicate")

    with pytest.raises(ValueError, match="duplicate normalized wheel member"):
        verify_wheel(archive)


@pytest.mark.parametrize(("file_type", "label"), ((stat.S_IFLNK, "symlink"), (stat.S_IFIFO, "special")))
def test_verify_wheel_rejects_unsafe_unix_member_modes(tmp_path: Path, file_type: int, label: str) -> None:
    archive = tmp_path / "package.whl"
    files = {
        "vibe_portfolio/web/dist/index.html": b"<html></html>",
        "vibe_portfolio/web/dist/assets/index-0123456789abcdef.css": b"body{}",
        "vibe_portfolio/web/dist/assets/index-fedcba9876543210.js": b"export{}",
    }
    with zipfile.ZipFile(archive, "w") as wheel:
        for name, payload in files.items():
            wheel.writestr(name, payload)
        malicious = zipfile.ZipInfo(f"vibe_portfolio/{label}")
        malicious.create_system = 3
        malicious.external_attr = (file_type | 0o777) << 16
        wheel.writestr(malicious, b"target")

    with pytest.raises(ValueError, match="unsupported wheel member type"):
        verify_wheel(archive)
