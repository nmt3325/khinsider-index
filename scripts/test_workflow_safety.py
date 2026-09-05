import os
from pathlib import Path
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

DEFAULT_ENV = {
    "GITHUB_REPOSITORY": "nmt3325/khinsider-index",
    "GH_REPO": "nmt3325/khinsider-index",
}

DEFAULT_TOUCH_FILES = (
    "recent-state.json",
    "recent-albums.ndjson",
    "recent-slugs.txt",
    "album-list.pages",
)


def load_step(workflow_name: str, step_name: str) -> dict:
    doc = yaml.load((WORKFLOWS / workflow_name).read_text(), Loader=yaml.BaseLoader)
    steps = next(iter(doc["jobs"].values()))["steps"]
    return next(step for step in steps if step.get("name") == step_name)


def run_shell_step(
    script: str,
    tmp_path: Path,
    gh_body: str,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/bin/sh\nset -eu\n" + gh_body)
    gh.chmod(0o755)
    output = tmp_path / "github_output"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
        **DEFAULT_ENV,
    }
    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    return result, output


def test_album_meta_restores_existing_snapshot(tmp_path: Path) -> None:
    restore = load_step("album-meta.yaml", "Restore crawl-data snapshot")
    result, output = run_shell_step(
        restore["run"],
        tmp_path,
        """
if [ "$1" = "release" ] && [ "$2" = "view" ] && [ "$3" = "crawl-data" ]; then
  exit 0
fi
if [ "$1" = "release" ] && [ "$2" = "download" ] && [ "$3" = "crawl-data" ]; then
  printf 'restored metadata\n' | gzip -c > album-meta.ndjson.gz
  printf '{"cursor": 1}\n' | gzip -c > recent-state.json.gz
  exit 0
fi
echo "unexpected gh invocation: $*" >&2
exit 99
""",
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text().strip() == "bootstrap=false"
    assert (tmp_path / "album-meta.ndjson").read_text() == "restored metadata\n"
    for name in DEFAULT_TOUCH_FILES:
        assert (tmp_path / name).exists()


def test_album_meta_bootstraps_only_on_404(tmp_path: Path) -> None:
    restore = load_step("album-meta.yaml", "Restore crawl-data snapshot")
    result, output = run_shell_step(
        restore["run"],
        tmp_path,
        """
if [ "$1" = "release" ] && [ "$2" = "view" ] && [ "$3" = "crawl-data" ]; then
  echo "gh: HTTP 404: Not Found" >&2
  exit 1
fi
echo "unexpected gh invocation: $*" >&2
exit 99
""",
    )
    assert result.returncode == 0, result.stderr
    assert output.read_text().strip() == "bootstrap=true"
    for name in DEFAULT_TOUCH_FILES:
        assert (tmp_path / name).exists()


@pytest.mark.parametrize(
    "message",
    [
        "gh: Service Unavailable (HTTP 503)",
        "gh: HTTP 401: Unauthorized",
        "gh: HTTP 403: Forbidden",
        "gh: rate limit exceeded (HTTP 429)",
        "gh: dial tcp 127.0.0.1:443: connect: connection refused",
    ],
)
def test_album_meta_fails_closed_on_non_404_lookup(tmp_path: Path, message: str) -> None:
    restore = load_step("album-meta.yaml", "Restore crawl-data snapshot")
    result, output = run_shell_step(
        restore["run"],
        tmp_path,
        f"""
echo {message!r} >&2
exit 1
""",
    )
    assert result.returncode != 0
    if output.exists():
        assert "bootstrap=true" not in output.read_text()


def test_album_meta_fails_on_download_error(tmp_path: Path) -> None:
    restore = load_step("album-meta.yaml", "Restore crawl-data snapshot")
    result, _ = run_shell_step(
        restore["run"],
        tmp_path,
        """
if [ "$1" = "release" ] && [ "$2" = "view" ] && [ "$3" = "crawl-data" ]; then
  exit 0
fi
if [ "$1" = "release" ] && [ "$2" = "download" ] && [ "$3" = "crawl-data" ]; then
  echo "gh: Service Unavailable (HTTP 503)" >&2
  exit 1
fi
echo "unexpected gh invocation: $*" >&2
exit 99
""",
    )
    assert result.returncode != 0


def test_album_meta_fails_on_corrupt_gzip(tmp_path: Path) -> None:
    restore = load_step("album-meta.yaml", "Restore crawl-data snapshot")
    result, _ = run_shell_step(
        restore["run"],
        tmp_path,
        """
if [ "$1" = "release" ] && [ "$2" = "view" ] && [ "$3" = "crawl-data" ]; then
  exit 0
fi
if [ "$1" = "release" ] && [ "$2" = "download" ] && [ "$3" = "crawl-data" ]; then
  printf 'not a gzip file' > broken.gz
  exit 0
fi
echo "unexpected gh invocation: $*" >&2
exit 99
""",
    )
    assert result.returncode != 0


def test_checkpoint_uploads_are_guarded_by_restore_success() -> None:
    refresh = load_step("album-meta.yaml", "Refresh the crawl-data snapshot")
    assert refresh["if"] == "${{ always() && steps.restore.outcome == 'success' }}"

    residual_save = load_step(
        "album-meta-residual.yaml",
        "Save the final checkpoint even after a crawl error",
    )
    assert residual_save["if"] == "${{ always() && steps.restore.outcome == 'success' }}"

    wayback_persist = load_step("wayback-archive.yaml", "Persist state to release")
    assert wayback_persist["if"] == "${{ always() && steps.restore.outcome == 'success' }}"
