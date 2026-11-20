"""Read trusted same-workflow artifacts and verify a published release."""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import time
from zipfile import BadZipFile, ZipFile

from .pipeline import fetch_public, valid_state, write


def github(*args, binary=False):
    proc = subprocess.run(["gh", "api", *args], capture_output=True, check=True)
    return proc.stdout if binary else json.loads(proc.stdout)


def list_artifacts(name, pages=1):
    repo = os.environ["GITHUB_REPOSITORY"]
    for page in range(1, pages + 1):
        response = github(f"repos/{repo}/actions/artifacts?name={name}&per_page=100&page={page}")
        for artifact in response["artifacts"]:
            if not artifact["expired"]:
                yield artifact
        if len(response["artifacts"]) < 100:
            break


def download(artifact, filename):
    repo = os.environ["GITHUB_REPOSITORY"]
    buffer = github(f"repos/{repo}/actions/artifacts/{artifact['id']}/zip", binary=True)
    with ZipFile(io.BytesIO(buffer)) as archive:
        return json.loads(archive.read(filename))


def trusted(artifact):
    repo = os.environ["GITHUB_REPOSITORY"]
    context = artifact["workflow_run"]
    if str(context["id"]) == os.environ.get("GITHUB_RUN_ID"):
        return False
    if context.get("head_branch") != os.environ.get("DEFAULT_BRANCH","main"):
        return False
    run = github(f"repos/{repo}/actions/runs/{context['id']}")
    return (run["path"] == ".github/workflows/publish.yml" and run["event"] in {"schedule", "workflow_dispatch"}
            and run["head_repository"]["full_name"].lower() == repo.lower() and run["run_attempt"] == 1)


def restore():
    for artifact in list_artifacts("checkpoint-v1"):
        if not trusted(artifact):
            continue
        try:
            state = download(artifact, "state.json")
            if valid_state(state):
                write(".runtime/checkpoint/state.json", state)
                print("Recovered checkpoint from run", artifact["workflow_run"]["id"])
                return
        except (ValueError, OSError, KeyError, BadZipFile, subprocess.CalledProcessError) as exc:
            print("Invalid checkpoint", artifact["id"], type(exc).__name__)
    print("Cold start; pipeline will check the public snapshot.")


def verify(base, release):
    for attempt in range(12):
        try:
            current = fetch_public(base, "manifest.json") or {}
            if str(current.get("release_id")) == release:
                print("Public release confirmed", release)
                return
        except (OSError, ValueError):
            pass
        if attempt < 11:
            time.sleep(5)
    raise SystemExit("Public release has not yet been confirmed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["restore", "verify"])
    parser.add_argument("--page", default="")
    args = parser.parse_args()
    if args.operation == "restore":
        restore()
    else:
        verify(args.page, os.environ["GITHUB_RUN_ID"])
