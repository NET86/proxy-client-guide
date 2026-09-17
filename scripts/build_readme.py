#!/usr/bin/env python3
"""Audit authoritative client sources and deterministically render README.

The directory deliberately separates static, human-reviewed catalog facts from
remote observations. Transient network failures never overwrite a last known
good business fact. Confirmed identity failures suppress unsafe download links.
No active client can automatically fail over to a different fork/product.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "data" / "clients.json"
DEFAULT_OBSERVATIONS = ROOT / "data" / "observations.json"
DEFAULT_OUTPUT = ROOT / "README.md"
ACTIVE_DAYS = 180
RECENT_DAYS = 365
FRESH_DAYS = 7
MIN_COVERAGE = 0.95
MAX_AUDIT_WORKERS = 6
PLATFORMS = ("macos", "ios", "tvos", "windows", "android", "linux")
STATUS_RANK = {"🟢": 0, "🟡": 1, "🕒": 2, "❓": 3, "🔴": 4}
STATUS_LABELS = {
    "🟢": "🟢 活跃",
    "🟡": "🟡 半年至一年未更新",
    "🕒": "🕒 一年以上未更新",
    "❓": "❓ 待确认",
    "🔴": "🔴 历史项目",
}
CATEGORY_ORDER = {"mihomo": 0, "sing_box": 1, "multi_core": 2, "proprietary": 3, "legacy": 4}
CATEGORY_TITLES = {
    "mihomo": "Mihomo / Clash 内核客户端",
    "sing_box": "sing-box 内核客户端",
    "multi_core": "多内核客户端",
    "proprietary": "闭源客户端",
    "legacy": "历史项目",
}
BLOCKED_TEXT = ("orymi.net", "starlinkboost.com", "高速机场推荐")
FORBIDDEN_AUTOMATIC_REPLACEMENTS = ("flclashx", "slothclash", "clashfest")
UNSAFE_SOURCE_STATES = {"missing", "disabled", "identity_mismatch", "unknown"}
TRANSIENT_HTTP_CODES = {403, 408, 425, 429, 500, 502, 503, 504}
REQUEST_ATTEMPTS = 2
OBSERVATION_VERSION = 2
EVIDENCE_SCOPE_VERSION = 1
FUTURE_SKEW = dt.timedelta(minutes=5)


class ObservationError(RuntimeError):
    pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: object) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        if len(value) == 10:
            parsed = dt.datetime.combine(dt.date.fromisoformat(value), dt.time.min, tzinfo=dt.timezone.utc)
        else:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def require_not_future(value: object, now: dt.datetime, label: str) -> dt.datetime:
    parsed = parse_time(value)
    if parsed is None:
        raise ObservationError(f"{label} timestamp schema changed")
    if parsed > now + FUTURE_SKEW:
        raise ObservationError(f"{label} timestamp is implausibly in the future")
    return parsed


def _scope_hash(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def source_scope(client: dict[str, Any]) -> str:
    source_type = client["source_type"]
    payload: dict[str, Any] = {"v": EVIDENCE_SCOPE_VERSION, "component": "source", "source_type": source_type}
    if source_type == "github":
        payload.update(
            github_repo=client["github_repo"],
            official_repo_id=client["official_repo_id"],
            official_owner_id=client["official_owner_id"],
        )
    elif source_type == "app_store":
        payload.update(app_store_id=str(client["app_store_id"]), app_store_seller=client["app_store_seller"])
    else:
        payload.update(repository_url=client.get("repository_url", ""), lifecycle=client.get("source_lifecycle", "active"))
    return _scope_hash(payload)


def release_scope(client: dict[str, Any]) -> str:
    release_source = client.get("release_source", client["source_type"])
    payload: dict[str, Any] = {
        "v": EVIDENCE_SCOPE_VERSION,
        "component": "release",
        "release_source": release_source,
        "source_scope": source_scope(client),
        "download_url": client.get("download_url", ""),
    }
    if release_source == "app_store":
        payload.update(app_store_id=str(client["app_store_id"]), app_store_seller=client["app_store_seller"])
    return _scope_hash(payload)


def historical_release_scope(client: dict[str, Any]) -> str:
    return _scope_hash(
        {
            "v": EVIDENCE_SCOPE_VERSION,
            "component": "historical_release",
            "source_scope": source_scope(client),
            "download_url": client.get("download_url", ""),
            "history": client["historical_release"],
        }
    )


def core_evidence_scope(client: dict[str, Any]) -> str:
    return _scope_hash(
        {
            "v": EVIDENCE_SCOPE_VERSION,
            "component": "core_evidence",
            "source_scope": source_scope(client),
            "core": client.get("core", ""),
            "evidence": client.get("core_evidence", []),
        }
    )


def component_scope(client: dict[str, Any], component: str) -> str:
    if component == "source":
        return source_scope(client)
    if component == "release":
        return release_scope(client)
    if component == "historical_release":
        return historical_release_scope(client)
    if component == "core_evidence":
        return core_evidence_scope(client)
    raise ValueError(f"unknown evidence component: {component}")


def scoped_lkg(old: dict[str, Any] | None, scope: str) -> dict[str, Any]:
    if not isinstance(old, dict) or old.get("scope") != scope:
        return {}
    return copy.deepcopy(old)


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def write_json(path: Path, value: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n")


def load_clients(path: Path = DEFAULT_CATALOG) -> list[dict[str, Any]]:
    payload = read_json(path)
    clients = payload.get("clients")
    if payload.get("version") != 3 or not isinstance(clients, list) or not clients:
        raise ValueError("clients.json must be version 3 with a non-empty clients array")
    ids: set[str] = set()
    names: set[str] = set()
    for client in clients:
        if not isinstance(client, dict):
            raise ValueError("Every client must be an object")
        cid, name = client.get("id"), client.get("name")
        if not isinstance(cid, str) or not cid or cid in ids:
            raise ValueError("Every client must have a unique non-empty id")
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Every client must have a unique non-empty name")
        if "\n" in name or "\r" in name or "|" in name:
            raise ValueError(f"{name}: client name contains Markdown table control characters")
        ids.add(cid)
        names.add(name)
        if client.get("category") not in CATEGORY_ORDER:
            raise ValueError(f"{name}: invalid category")
        if client.get("source_type") not in {"github", "app_store", "manual"}:
            raise ValueError(f"{name}: invalid source_type")
        lifecycle = client.get("source_lifecycle", "active")
        if lifecycle not in {"active", "discontinued", "merged"}:
            raise ValueError(f"{name}: invalid lifecycle")
        platforms = client.get("platforms")
        if not isinstance(platforms, dict) or any(type(platforms.get(key)) is not bool for key in PLATFORMS):
            raise ValueError(f"{name}: every platform must be declared as boolean")
        if not any(platforms[key] for key in PLATFORMS):
            raise ValueError(f"{name}: at least one supported platform is required")
        source_type = client["source_type"]
        if source_type == "github":
            repository = client.get("github_repo")
            if not isinstance(repository, str) or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]+", repository) is None:
                raise ValueError(f"{name}: github_repo must be a canonical owner/repository path")
            if type(client.get("official_repo_id")) is not int or client["official_repo_id"] <= 0:
                raise ValueError(f"{name}: GitHub source requires official_repo_id")
            if type(client.get("official_owner_id")) is not int or client["official_owner_id"] <= 0:
                raise ValueError(f"{name}: GitHub source requires official_owner_id")
        if source_type == "app_store":
            if not str(client.get("app_store_id", "")).isdigit():
                raise ValueError(f"{name}: app_store_id required")
            if not isinstance(client.get("app_store_seller"), str) or not client["app_store_seller"].strip():
                raise ValueError(f"{name}: app_store_seller required")
        release_source = client.get("release_source", source_type)
        if release_source not in {"github", "app_store", "manual"}:
            raise ValueError(f"{name}: invalid release_source")
        if release_source == "app_store":
            if not str(client.get("app_store_id", "")).isdigit() or not isinstance(client.get("app_store_seller"), str):
                raise ValueError(f"{name}: App Store release source requires app id and seller pin")
        download = client.get("download_url", "")
        if download and (not isinstance(download, str) or not download.startswith("https://")):
            raise ValueError(f"{name}: download_url must be empty or HTTPS")
        if source_type == "manual" and download:
            raise ValueError(f"{name}: manual sources cannot authorize a main download URL")
        if client["category"] == "legacy":
            if lifecycle == "active":
                raise ValueError(f"{name}: legacy entry must be discontinued or merged")
        else:
            if lifecycle != "active":
                raise ValueError(f"{name}: active directory entry cannot be discontinued or merged")
            if source_type == "manual":
                raise ValueError(f"{name}: active directory entry requires an official source")
            if not download:
                raise ValueError(f"{name}: active directory entry requires an official download/store URL")
        if "/releases/download/" in str(download).lower():
            raise ValueError(f"{name}: direct binary download links are forbidden")
        parsed_download = urllib.parse.urlparse(download) if download else None
        if download and release_source == "github" and source_type == "github":
            expected_prefix = f"/{client['github_repo']}/releases".casefold()
            download_path = parsed_download.path.rstrip("/").casefold()
            if parsed_download.hostname != "github.com" or not (
                download_path == expected_prefix or download_path.startswith(expected_prefix + "/")
            ):
                raise ValueError(f"{name}: GitHub download_url must stay under the pinned official repository releases path")
        if download and release_source == "app_store":
            expected_app_id = f"id{client['app_store_id']}".casefold()
            app_path_segments = {segment.casefold() for segment in parsed_download.path.split("/") if segment}
            if parsed_download.hostname != "apps.apple.com" or expected_app_id not in app_path_segments:
                raise ValueError(f"{name}: App Store download_url must match the pinned app_store_id")
        for url_field in ("website_url", "repository_url"):
            value = client.get(url_field)
            if value is not None:
                parsed_value = urllib.parse.urlparse(str(value))
                if parsed_value.scheme != "https" or not parsed_value.hostname:
                    raise ValueError(f"{name}: {url_field} must be an absolute HTTPS URL")
        if "backups" in client:
            raise ValueError(f"{name}: automatic backup/fork failover is forbidden")
        history = client.get("historical_release")
        if history is not None:
            if source_type != "github" or type(history.get("release_id")) is not int or not history.get("tag"):
                raise ValueError(f"{name}: invalid historical release identity")
            assets = history.get("assets")
            if not isinstance(assets, list) or not assets:
                raise ValueError(f"{name}: historical release requires pinned assets")
            for asset in assets:
                if (
                    not isinstance(asset, dict)
                    or not isinstance(asset.get("name"), str)
                    or type(asset.get("id")) is not int
                    or type(asset.get("size")) is not int
                ):
                    raise ValueError(f"{name}: invalid historical asset pin")
        core_evidence = client.get("core_evidence", [])
        if source_type == "github" and lifecycle == "active" and "core" in client:
            if not isinstance(core_evidence, list) or not core_evidence:
                raise ValueError(f"{name}: active GitHub core claim requires non-empty core_evidence")
        for evidence in core_evidence:
            if not isinstance(evidence, dict):
                raise ValueError(f"{name}: invalid core evidence")
            url, patterns = evidence.get("url"), evidence.get("patterns")
            parsed = urllib.parse.urlparse(str(url))
            if parsed.scheme != "https" or parsed.hostname != "raw.githubusercontent.com":
                raise ValueError(f"{name}: core evidence must be a raw.githubusercontent.com HTTPS URL")
            if not isinstance(patterns, list) or not patterns or any(not isinstance(p, str) or not p for p in patterns):
                raise ValueError(f"{name}: invalid core evidence patterns")
            for pattern in patterns:
                re.compile(pattern)
        for archive in client.get("third_party_archives", []):
            if not isinstance(archive, dict) or not str(archive.get("url", "")).startswith("https://"):
                raise ValueError(f"{name}: invalid third-party archive")
            if "/releases/download/" in archive["url"].lower():
                raise ValueError(f"{name}: third-party archives must link release pages, not binaries")
    return clients


def empty_observations() -> dict[str, Any]:
    return {
        "version": OBSERVATION_VERSION,
        "last_run_at": None,
        "clients": {},
        "health": {
            "attempted": 0,
            "succeeded": 0,
            "coverage": 0.0,
            "anomalies": ["scoped evidence has not been established"],
        },
    }


def load_observations(path: Path = DEFAULT_OBSERVATIONS) -> dict[str, Any]:
    if not path.exists():
        return empty_observations()
    payload = read_json(path)
    if payload.get("version") == 1:
        # V1 predates evidence-scope binding. Reusing it would transfer trust
        # from a different static catalog configuration, so migration is fail-closed.
        return empty_observations()
    if payload.get("version") != OBSERVATION_VERSION or not isinstance(payload.get("clients"), dict):
        raise ValueError(f"observations.json must be version {OBSERVATION_VERSION} with a clients object")
    return payload


def request_json(url: str, token: str | None = None, attempts: int = REQUEST_ATTEMPTS) -> dict[str, Any] | None:
    headers = {"Accept": "application/json", "User-Agent": "NET86-clash-directory/3"}
    if token and urllib.parse.urlparse(url).hostname == "api.github.com":
        headers["Authorization"] = f"Bearer {token}"
        headers["X-GitHub-Api-Version"] = "2022-11-28"
    request = urllib.request.Request(url, headers=headers)
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.load(response)
            if not isinstance(payload, dict):
                raise ObservationError(f"Expected JSON object from {url}")
            return payload
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code not in TRANSIENT_HTTP_CODES:
                raise
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(0.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def request_text(url: str, attempts: int = REQUEST_ATTEMPTS) -> str | None:
    request = urllib.request.Request(url, headers={"Accept": "text/plain", "User-Agent": "NET86-clash-directory/3"})
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read().decode(response.headers.get_content_charset() or "utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code not in TRANSIENT_HTTP_CODES:
                raise
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(0.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def require_repo_schema(value: dict[str, Any]) -> None:
    owner = value.get("owner")
    if type(value.get("id")) is not int or not isinstance(value.get("full_name"), str):
        raise ObservationError("GitHub repository schema changed")
    if not isinstance(owner, dict) or type(owner.get("id")) is not int:
        raise ObservationError("GitHub repository owner schema changed")
    if type(value.get("archived")) is not bool or type(value.get("disabled")) is not bool:
        raise ObservationError("GitHub repository lifecycle schema changed")
    pushed = value.get("pushed_at")
    if pushed is not None and parse_time(pushed) is None:
        raise ObservationError("GitHub repository pushed_at schema changed")


def require_release_schema(value: dict[str, Any]) -> None:
    if type(value.get("id")) is not int or not isinstance(value.get("tag_name"), str):
        raise ObservationError("GitHub release identity schema changed")
    if parse_time(value.get("published_at")) is None:
        raise ObservationError("GitHub release published_at schema changed")
    if type(value.get("draft")) is not bool or type(value.get("prerelease")) is not bool or not isinstance(value.get("assets"), list):
        raise ObservationError("GitHub release schema incomplete")
    for asset in value["assets"]:
        if (
            not isinstance(asset, dict)
            or type(asset.get("id")) is not int
            or not isinstance(asset.get("name"), str)
            or type(asset.get("size")) is not int
            or not isinstance(asset.get("state"), str)
        ):
            raise ObservationError("GitHub release asset schema changed")


def usable_release_asset_count(value: dict[str, Any]) -> int:
    return sum(1 for asset in value["assets"] if asset["state"] == "uploaded" and asset["size"] > 0)


RECORD_META_KEYS = {
    "observation_state",
    "observed_at",
    "last_success_at",
    "consecutive_failures",
    "last_error_at",
    "error",
    "unverified_reason",
}


def _business_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in record.items()
        if key not in RECORD_META_KEYS and not key.startswith("observed_")
    }


def _clear_observation_diagnostics(record: dict[str, Any]) -> None:
    for key in list(record):
        if key.startswith("observed_") and key != "observed_at":
            record.pop(key, None)
    for key in ("last_error_at", "error", "unverified_reason"):
        record.pop(key, None)


def failure_record(old: dict[str, Any] | None, stamp: str, error: str, scope: str) -> dict[str, Any]:
    record = scoped_lkg(old, scope)
    record.setdefault("state", "unknown")
    record["scope"] = scope
    record["observation_state"] = "error"
    record["last_error_at"] = stamp
    record["error"] = error
    record["consecutive_failures"] = int(record.get("consecutive_failures", 0)) + 1
    return record


def positive_record(old: dict[str, Any] | None, stamp: str, scope: str, **fields: Any) -> dict[str, Any]:
    record = scoped_lkg(old, scope)
    previous_business = _business_snapshot(record)
    previous_observation_state = record.get("observation_state")
    previous_success = parse_time(record.get("last_success_at"))
    current_stamp = parse_time(stamp)

    _clear_observation_diagnostics(record)
    record.update(fields)
    record["scope"] = scope
    current_business = _business_snapshot(record)
    same_business = previous_business == current_business
    same_day = bool(previous_success and current_stamp and previous_success <= current_stamp and previous_success.date() == current_stamp.date())
    recovered = previous_observation_state in {"error", "unverified", "verified_negative"}

    record["observation_state"] = "fresh"
    record["consecutive_failures"] = 0
    if not (same_business and same_day and not recovered):
        record["observed_at"] = stamp
        record["last_success_at"] = stamp
    return record


def negative_record(old: dict[str, Any] | None, stamp: str, scope: str, state: str, **observed: Any) -> dict[str, Any]:
    record = scoped_lkg(old, scope)
    _clear_observation_diagnostics(record)
    record["scope"] = scope
    record["state"] = state
    record["observation_state"] = "verified_negative"
    record["observed_at"] = stamp
    record["consecutive_failures"] = 0
    record.update(observed)
    return record


def unverified_record(old: dict[str, Any] | None, stamp: str, scope: str, reason: str) -> dict[str, Any]:
    record = scoped_lkg(old, scope)
    record.setdefault("state", "unknown")
    record["scope"] = scope
    record["observation_state"] = "unverified"
    record["observed_at"] = stamp
    record["unverified_reason"] = reason
    record["consecutive_failures"] = int(record.get("consecutive_failures", 0))
    return record


def app_store_lookup_url(app_id: str, now: dt.datetime) -> str:
    nonce = now.strftime("%Y%m%d%H%M%S%f")
    return f"https://itunes.apple.com/lookup?id={app_id}&country=us&cache_bust={nonce}"


def validate_app_store(value: dict[str, Any]) -> dict[str, Any] | None:
    count, entries = value.get("resultCount"), value.get("results")
    if type(count) is not int or not isinstance(entries, list):
        raise ObservationError("App Store response schema changed")
    if count < 1 or not entries:
        return None
    first = entries[0]
    if not isinstance(first, dict):
        raise ObservationError("App Store result schema changed")
    if type(first.get("trackId")) is not int or not isinstance(first.get("sellerName"), str):
        raise ObservationError("App Store identity schema changed")
    if not isinstance(first.get("version"), str) or parse_time(first.get("currentVersionReleaseDate")) is None:
        raise ObservationError("App Store release schema changed")
    return first


def audit_app_store_release(
    client: dict[str, Any],
    old: dict[str, Any],
    now: dt.datetime,
    request: Callable[..., dict[str, Any] | None],
) -> tuple[dict[str, Any], list[str], bool]:
    stamp = iso(now)
    previous = old.get("release") if isinstance(old, dict) else None
    scope = release_scope(client)
    try:
        payload = request(app_store_lookup_url(client["app_store_id"], now), None)
        if payload is None:
            raise ObservationError("App Store lookup returned no JSON")
        entry = validate_app_store(payload)
        if entry is None:
            record = unverified_record(previous, stamp, scope, "region_missing")
            return record, [f"{client['id']}: US App Store lookup returned no app"], False
        if entry["trackId"] != int(client["app_store_id"]) or entry["sellerName"] != client["app_store_seller"]:
            record = negative_record(
                previous,
                stamp,
                scope,
                "identity_mismatch",
                observed_track_id=entry["trackId"],
                observed_seller=entry["sellerName"],
            )
            return record, [f"{client['id']}: App Store publisher identity changed"], False
        published = require_not_future(entry["currentVersionReleaseDate"], now, "App Store release")
        previous_scoped = scoped_lkg(previous, scope)
        old_published = parse_time(previous_scoped.get("published_at"))
        if old_published and published < old_published:
            record = negative_record(
                previous,
                stamp,
                scope,
                "rollback",
                observed_version=entry["version"],
                observed_published_at=iso(published),
            )
            return record, [f"{client['id']}: App Store release timestamp moved backwards"], False
        return (
            positive_record(
                previous,
                stamp,
                scope,
                state="ok",
                version=entry["version"],
                published_at=iso(published),
                seller=entry["sellerName"],
                track_id=entry["trackId"],
            ),
            [],
            True,
        )
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, ObservationError, ValueError) as exc:
        return failure_record(previous, stamp, str(exc), scope), [], False


def audit_core_evidence(
    client: dict[str, Any],
    old: dict[str, Any],
    now: dt.datetime,
    fetch_text: Callable[[str], str | None],
) -> tuple[dict[str, Any] | None, list[str], bool]:
    evidence = client.get("core_evidence", [])
    if not evidence:
        return None, [], True
    stamp = iso(now)
    previous = old.get("core_evidence") if isinstance(old, dict) else None
    scope = core_evidence_scope(client)
    try:
        for item in evidence:
            text = fetch_text(item["url"])
            if text is None:
                record = negative_record(previous, stamp, scope, "missing")
                return record, [f"{client['id']}: core evidence URL returned 404"], False
            missing = [pattern for pattern in item["patterns"] if re.search(pattern, text, re.IGNORECASE) is None]
            if missing:
                record = negative_record(previous, stamp, scope, "mismatch", observed_missing_patterns=missing)
                return record, [f"{client['id']}: core evidence no longer matches"], False
        return positive_record(previous, stamp, scope, state="ok"), [], True
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, UnicodeError, re.error) as exc:
        return failure_record(previous, stamp, str(exc), scope), [], False


def audit_github(
    client: dict[str, Any],
    old: dict[str, Any],
    token: str | None,
    now: dt.datetime,
    request: Callable[..., dict[str, Any] | None],
    fetch_text: Callable[[str], str | None] = request_text,
) -> tuple[dict[str, Any], list[str], bool]:
    stamp = iso(now)
    repo = client["github_repo"]
    previous_source = old.get("source") if isinstance(old, dict) else None
    source_scope_value = source_scope(client)
    previous_source_scoped = scoped_lkg(previous_source, source_scope_value)
    result = copy.deepcopy(old or {})
    anomalies: list[str] = []
    ok = True
    try:
        metadata = request(f"https://api.github.com/repos/{repo}", token)
        if metadata is None:
            result["source"] = negative_record(previous_source, stamp, source_scope_value, "missing")
            return result, [f"{client['id']}: official repository returned 404"], False
        require_repo_schema(metadata)
        expected_repo = client.get("official_repo_id")
        expected_owner = client.get("official_owner_id")
        if expected_repo is not None and (
            metadata["id"] != expected_repo
            or expected_owner is not None and metadata["owner"]["id"] != expected_owner
            or metadata["full_name"].casefold() != repo.casefold()
        ):
            result["source"] = negative_record(
                previous_source,
                stamp,
                source_scope_value,
                "identity_mismatch",
                observed_repo_id=metadata["id"],
                observed_owner_id=metadata["owner"]["id"],
                observed_full_name=metadata["full_name"],
            )
            return result, [f"{client['id']}: repository identity changed"], False
        pushed_at = metadata.get("pushed_at")
        pushed = require_not_future(pushed_at, now, "GitHub repository pushed_at") if pushed_at is not None else None
        state = "disabled" if metadata["disabled"] else "archived" if metadata["archived"] else "ok"
        if state == "disabled":
            result["source"] = negative_record(
                previous_source,
                stamp,
                source_scope_value,
                "disabled",
                observed_repo_id=metadata["id"],
                observed_owner_id=metadata["owner"]["id"],
                observed_full_name=metadata["full_name"],
            )
            anomalies.append(f"{client['id']}: official repository disabled")
            ok = False
        else:
            source_record = positive_record(
                previous_source,
                stamp,
                source_scope_value,
                state=state,
                repo_id=metadata["id"],
                owner_id=metadata["owner"]["id"],
                full_name=metadata["full_name"],
                last_activity_at=iso(pushed) if pushed is not None else None,
            )
            lifecycle = client.get("source_lifecycle", "active")
            if lifecycle in {"discontinued", "merged"} and state == "ok" and previous_source_scoped.get("state") == "archived":
                source_record["lifecycle_review_required"] = True
            if lifecycle == "active":
                source_record.pop("lifecycle_review_required", None)
            result["source"] = source_record
            if state == "archived" and lifecycle == "active":
                anomalies.append(f"{client['id']}: active catalog entry is now archived")
                ok = False
            if source_record.get("lifecycle_review_required"):
                anomalies.append(f"{client['id']}: official source changed from archived to active; lifecycle review required")
                ok = False
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, ObservationError, ValueError) as exc:
        result["source"] = failure_record(previous_source, stamp, str(exc), source_scope_value)
        return result, anomalies, False

    release_source = client.get("release_source", client["source_type"])
    if release_source == "github" and not client.get("historical_release"):
        previous_release = old.get("release") if isinstance(old, dict) else None
        release_scope_value = release_scope(client)
        previous_release_scoped = scoped_lkg(previous_release, release_scope_value)
        try:
            release = request(f"https://api.github.com/repos/{repo}/releases/latest", token)
            if release is None:
                result["release"] = negative_record(previous_release, stamp, release_scope_value, "missing")
                if previous_release_scoped.get("version"):
                    anomalies.append(f"{client['id']}: previously observed latest release disappeared")
                else:
                    anomalies.append(f"{client['id']}: no latest release is available")
                ok = False
            else:
                require_release_schema(release)
                if release["draft"] or release["prerelease"]:
                    raise ObservationError("latest release unexpectedly draft/prerelease")
                published = require_not_future(release["published_at"], now, "GitHub release published_at")
                version = release["tag_name"]
                asset_count = usable_release_asset_count(release)
                old_published = parse_time(previous_release_scoped.get("published_at"))
                old_version = previous_release_scoped.get("version")
                old_release_id = previous_release_scoped.get("release_id")
                if old_published and published < old_published:
                    result["release"] = negative_record(
                        previous_release,
                        stamp,
                        release_scope_value,
                        "rollback",
                        observed_version=version,
                        observed_published_at=iso(published),
                        observed_release_id=release["id"],
                        observed_asset_count=asset_count,
                    )
                    anomalies.append(f"{client['id']}: latest release timestamp moved backwards")
                    ok = False
                elif old_version == version and type(old_release_id) is int and release["id"] != old_release_id:
                    result["release"] = negative_record(
                        previous_release,
                        stamp,
                        release_scope_value,
                        "identity_mismatch",
                        observed_release_id=release["id"],
                        observed_asset_count=asset_count,
                    )
                    anomalies.append(f"{client['id']}: latest release was recreated under the same tag")
                    ok = False
                elif asset_count == 0:
                    result["release"] = negative_record(
                        previous_release,
                        stamp,
                        release_scope_value,
                        "assets_missing",
                        observed_version=version,
                        observed_published_at=iso(published),
                        observed_release_id=release["id"],
                        observed_asset_count=0,
                    )
                    anomalies.append(f"{client['id']}: latest release has no usable uploaded assets")
                    ok = False
                else:
                    result["release"] = positive_record(
                        previous_release,
                        stamp,
                        release_scope_value,
                        state="ok",
                        version=version,
                        published_at=iso(published),
                        release_id=release["id"],
                        asset_count=asset_count,
                    )
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, ObservationError, ValueError) as exc:
            result["release"] = failure_record(previous_release, stamp, str(exc), release_scope_value)
            ok = False
    elif release_source == "app_store":
        release, issues, release_ok = audit_app_store_release(client, old, now, request)
        result["release"] = release
        anomalies.extend(issues)
        ok = ok and release_ok

    history = client.get("historical_release")
    if history:
        previous_history = old.get("historical_release") if isinstance(old, dict) else None
        history_scope_value = historical_release_scope(client)
        try:
            tag = urllib.parse.quote(history["tag"], safe="")
            release = request(f"https://api.github.com/repos/{repo}/releases/tags/{tag}", token)
            if release is None:
                result["historical_release"] = negative_record(previous_history, stamp, history_scope_value, "missing")
                anomalies.append(f"{client['id']}: official historical release disappeared")
                ok = False
            else:
                require_release_schema(release)
                require_not_future(release["published_at"], now, "historical release published_at")
                if release["id"] != history["release_id"]:
                    result["historical_release"] = negative_record(
                        previous_history,
                        stamp,
                        history_scope_value,
                        "identity_mismatch",
                        observed_release_id=release["id"],
                    )
                    anomalies.append(f"{client['id']}: historical release was recreated")
                    ok = False
                else:
                    actual_assets = {asset.get("name"): asset for asset in release["assets"] if isinstance(asset, dict)}
                    bad: list[str] = []
                    for pin in history["assets"]:
                        asset = actual_assets.get(pin["name"])
                        if not asset or asset.get("id") != pin["id"] or asset.get("size") != pin["size"] or asset.get("state") != "uploaded":
                            bad.append(pin["name"])
                    if bad:
                        result["historical_release"] = negative_record(
                            previous_history,
                            stamp,
                            history_scope_value,
                            "asset_mismatch",
                            observed_bad_assets=bad,
                        )
                        anomalies.append(f"{client['id']}: historical release assets changed")
                        ok = False
                    else:
                        result["historical_release"] = positive_record(
                            previous_history,
                            stamp,
                            history_scope_value,
                            state="ok",
                            tag=history["tag"],
                            release_id=release["id"],
                        )
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, ObservationError, ValueError) as exc:
            result["historical_release"] = failure_record(previous_history, stamp, str(exc), history_scope_value)
            ok = False

    core, issues, core_ok = audit_core_evidence(client, old, now, fetch_text)
    if core is not None:
        result["core_evidence"] = core
    anomalies.extend(issues)
    ok = ok and core_ok
    return result, anomalies, ok


def audit_app_store_source(
    client: dict[str, Any],
    old: dict[str, Any],
    now: dt.datetime,
    request: Callable[..., dict[str, Any] | None],
) -> tuple[dict[str, Any], list[str], bool]:
    stamp = iso(now)
    previous_source = old.get("source") if isinstance(old, dict) else None
    previous_release = old.get("release") if isinstance(old, dict) else None
    source_scope_value = source_scope(client)
    release_scope_value = release_scope(client)
    try:
        payload = request(app_store_lookup_url(client["app_store_id"], now), None)
        if payload is None:
            raise ObservationError("App Store lookup returned no JSON")
        entry = validate_app_store(payload)
        if entry is None:
            return {
                "source": unverified_record(previous_source, stamp, source_scope_value, "region_missing"),
                "release": unverified_record(previous_release, stamp, release_scope_value, "region_missing"),
            }, [f"{client['id']}: US App Store lookup returned no app"], False
        if entry["trackId"] != int(client["app_store_id"]) or entry["sellerName"] != client["app_store_seller"]:
            return {
                "source": negative_record(
                    previous_source,
                    stamp,
                    source_scope_value,
                    "identity_mismatch",
                    observed_seller=entry["sellerName"],
                    observed_track_id=entry["trackId"],
                ),
                "release": negative_record(
                    previous_release,
                    stamp,
                    release_scope_value,
                    "identity_mismatch",
                    observed_seller=entry["sellerName"],
                    observed_track_id=entry["trackId"],
                ),
            }, [f"{client['id']}: App Store publisher identity changed"], False
        published = require_not_future(entry["currentVersionReleaseDate"], now, "App Store release")
        previous_release_scoped = scoped_lkg(previous_release, release_scope_value)
        old_published = parse_time(previous_release_scoped.get("published_at"))
        release_ok = True
        issues: list[str] = []
        if old_published and published < old_published:
            release = negative_record(
                previous_release,
                stamp,
                release_scope_value,
                "rollback",
                observed_version=entry["version"],
                observed_published_at=iso(published),
            )
            release_ok = False
            issues.append(f"{client['id']}: App Store release timestamp moved backwards")
        else:
            release = positive_record(
                previous_release,
                stamp,
                release_scope_value,
                state="ok",
                version=entry["version"],
                published_at=iso(published),
                seller=entry["sellerName"],
                track_id=entry["trackId"],
            )
        source = positive_record(
            previous_source,
            stamp,
            source_scope_value,
            state="ok",
            seller=entry["sellerName"],
            track_id=entry["trackId"],
            last_activity_at=iso(published),
        )
        return {"source": source, "release": release}, issues, release_ok
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, ObservationError, ValueError) as exc:
        return {
            "source": failure_record(previous_source, stamp, str(exc), source_scope_value),
            "release": failure_record(previous_release, stamp, str(exc), release_scope_value),
        }, [], False


def audit(
    clients: list[dict[str, Any]],
    observations: dict[str, Any],
    request: Callable[..., dict[str, Any] | None] = request_json,
    fetch_text: Callable[[str], str | None] = request_text,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    current = now or utc_now()
    stamp = iso(current)
    output = copy.deepcopy(observations)
    output["version"] = OBSERVATION_VERSION
    previous_run = parse_time(output.get("last_run_at"))
    if previous_run is None or previous_run > current or previous_run.date() != current.date():
        output["last_run_at"] = stamp
    output.setdefault("clients", {})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    attempted = 0
    succeeded = 0
    anomalies: list[str] = []
    valid_ids = {client["id"] for client in clients}
    output["clients"] = {cid: value for cid, value in output["clients"].items() if cid in valid_ids}

    remote_clients: list[dict[str, Any]] = []
    previous_by_id: dict[str, dict[str, Any]] = {}
    for client in clients:
        cid = client["id"]
        old = output["clients"].get(cid, {})
        if client["source_type"] == "manual":
            output["clients"].pop(cid, None)
            continue
        remote_clients.append(client)
        previous_by_id[cid] = copy.deepcopy(old)

    attempted = len(remote_clients)

    def audit_one(client: dict[str, Any]) -> tuple[dict[str, Any], list[str], bool]:
        old = previous_by_id[client["id"]]
        if client["source_type"] == "github":
            return audit_github(client, old, token, current, request, fetch_text)
        return audit_app_store_source(client, old, current, request)

    future_by_id: dict[str, concurrent.futures.Future[tuple[dict[str, Any], list[str], bool]]] = {}
    if remote_clients:
        worker_count = min(MAX_AUDIT_WORKERS, len(remote_clients))
        with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="client-audit") as executor:
            for client in remote_clients:
                future_by_id[client["id"]] = executor.submit(audit_one, client)
            # Apply completed work in catalog order, not completion order, so the
            # persisted snapshot is deterministic even though requests run concurrently.
            for client in remote_clients:
                result, issues, ok = future_by_id[client["id"]].result()
                output["clients"][client["id"]] = result
                anomalies.extend(issues)
                succeeded += int(ok)

    output["clients"] = {
        client["id"]: output["clients"][client["id"]]
        for client in clients
        if client["id"] in output["clients"]
    }
    output["health"] = derive_health(clients, output, current)
    output["health"]["attempted_last_run"] = attempted
    output["health"]["succeeded_last_run"] = succeeded
    return output


def component_is_scoped(client: dict[str, Any], record: dict[str, Any], component_name: str) -> bool:
    component = record.get(component_name, {})
    return isinstance(component, dict) and component.get("scope") == component_scope(client, component_name)


def component_fresh(client: dict[str, Any], record: dict[str, Any], component_name: str, now: dt.datetime) -> bool:
    if not component_is_scoped(client, record, component_name):
        return False
    component = record[component_name]
    last_success = parse_time(component.get("last_success_at"))
    if last_success is None or last_success > now:
        return False
    return now - last_success < dt.timedelta(days=FRESH_DAYS)


def component_positive(client: dict[str, Any], record: dict[str, Any], component_name: str) -> bool:
    if not component_is_scoped(client, record, component_name):
        return False
    state = record[component_name].get("state")
    allowed = {
        "source": {"ok", "archived"},
        "release": {"ok"},
        "historical_release": {"ok"},
        "core_evidence": {"ok"},
    }
    return state in allowed[component_name]


def component_trusted(client: dict[str, Any], record: dict[str, Any], component_name: str, now: dt.datetime) -> bool:
    if not component_positive(client, record, component_name) or not component_fresh(client, record, component_name, now):
        return False
    component = record.get(component_name, {})
    return component.get("observation_state") != "unverified"


def derive_health(clients: list[dict[str, Any]], observations: dict[str, Any], now: dt.datetime | None = None) -> dict[str, Any]:
    current = now or utc_now()
    records = observations.get("clients", {}) if isinstance(observations.get("clients"), dict) else {}
    remote_clients = [client for client in clients if client["source_type"] != "manual"]
    succeeded = 0
    anomalies: list[str] = []
    for client in remote_clients:
        cid = client["id"]
        record = records.get(cid, {}) if isinstance(records.get(cid, {}), dict) else {}
        all_current = True
        for component_name in expected_observation_components(client):
            component = record.get(component_name, {}) if isinstance(record, dict) else {}
            if not component_is_scoped(client, record, component_name):
                anomalies.append(f"{cid}: {component_name} evidence missing or scope mismatch")
                all_current = False
                continue
            state = component.get("state")
            # LKG retention is not proof this run observed the upstream.
            if component.get("observation_state") == "error":
                all_current = False
            if not component_positive(client, record, component_name):
                anomalies.append(f"{cid}: unresolved {component_name} state {state}")
                all_current = False
            if component.get("observation_state") == "unverified":
                anomalies.append(f"{cid}: {component_name} observation unverified: {component.get('unverified_reason', 'unknown')}")
                all_current = False
            last_success = parse_time(component.get("last_success_at"))
            if last_success is not None and last_success > current:
                anomalies.append(f"{cid}: {component_name} last_success_at is in the future")
                all_current = False
            elif not component_fresh(client, record, component_name, current):
                anomalies.append(f"{cid}: {component_name} evidence stale or never positively verified")
                all_current = False
        source = record.get("source", {}) if isinstance(record, dict) else {}
        if source.get("state") == "archived" and client.get("source_lifecycle", "active") == "active":
            anomalies.append(f"{cid}: active catalog entry remains archived")
            all_current = False
        if source.get("lifecycle_review_required"):
            anomalies.append(f"{cid}: lifecycle review required after archived-to-active transition")
            all_current = False
        succeeded += int(all_current)
    attempted = len(remote_clients)
    coverage = succeeded / attempted if attempted else 1.0
    if coverage < MIN_COVERAGE:
        anomalies.append(f"audit coverage {succeeded}/{attempted} below {MIN_COVERAGE:.0%}")
    return {
        "attempted": attempted,
        "succeeded": succeeded,
        "coverage": round(coverage, 4),
        "anomalies": sorted(set(anomalies)),
    }


def source_trusted(client: dict[str, Any], record: dict[str, Any], now: dt.datetime | None = None) -> bool:
    if client["source_type"] == "manual":
        return False
    return component_trusted(client, record, "source", now or utc_now())


def activity_status(client: dict[str, Any], record: dict[str, Any], now: dt.datetime | None = None) -> tuple[str, str]:
    if client.get("source_lifecycle") in {"discontinued", "merged"} or client["category"] == "legacy":
        return "🔴", "历史项目"
    current = now or utc_now()
    for component_name in expected_observation_components(client):
        component = record.get(component_name, {}) if isinstance(record, dict) else {}
        if not component_is_scoped(client, record, component_name):
            return "❓", "配置已变更，待重新核验"
        if component.get("observation_state") == "unverified":
            return "❓", "核验结果不明确"
        if component.get("observation_state") == "error":
            return "❓", "核验暂时失败"
        if not component_positive(client, record, component_name):
            return "❓", "来源或版本异常"
        if not component_fresh(client, record, component_name, current):
            return "❓", "超过 7 天未成功核验"
    source = record.get("source", {})
    if source.get("lifecycle_review_required"):
        return "❓", "项目状态变化，待确认"
    if source.get("state") == "archived":
        return "🔴", "官方仓库已归档"
    activity_candidates = [
        parse_time(source.get("last_activity_at")),
        parse_time(record.get("release", {}).get("published_at")),
    ]
    activities = [value for value in activity_candidates if value is not None and value <= current]
    if not activities:
        return "❓", "缺少可靠更新时间"
    age = current - max(activities)
    if age <= dt.timedelta(days=ACTIVE_DAYS):
        return "🟢", "近半年有官方更新"
    if age <= dt.timedelta(days=RECENT_DAYS):
        return "🟡", "半年至一年未更新"
    return "🕒", "一年以上未更新"


def links_for(client: dict[str, Any], record: dict[str, Any], now: dt.datetime | None = None) -> tuple[str, str]:
    current = now or utc_now()
    source_type = client["source_type"]
    source_state = record.get("source", {}).get("state")
    repository = ""
    if source_type == "github" and component_is_scoped(client, record, "source") and source_state not in UNSAFE_SOURCE_STATES:
        repository = f"https://github.com/{client['github_repo']}"
    elif source_type == "app_store":
        repository = client.get("website_url", "")
    elif source_type == "manual":
        repository = client.get("repository_url", "")
    if source_type == "manual":
        return repository, ""
    download = client.get("download_url", "")
    if not source_trusted(client, record, current):
        download = ""
    if client.get("historical_release"):
        if not component_trusted(client, record, "historical_release", current):
            download = ""
    elif client.get("release_source", source_type) in {"github", "app_store"}:
        if not component_trusted(client, record, "release", current):
            download = ""
    if download and client.get("download_page_url"):
        download = client["download_page_url"]
    return repository, download


def icon(value: bool) -> str:
    return "✅" if value else "❌"


def markdown_link(label: str, url: str) -> str:
    return f"[{label}]({url})" if url else "—"


def status_display(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def status_detail(status: str, reason: str) -> str:
    display = status_display(status)
    label = display.split(" ", 1)[1] if " " in display else display
    return display if reason == label else f"{display}｜{reason}"


def platform_summary(client: dict[str, Any]) -> str:
    labels = {
        "macos": "macOS",
        "ios": "iOS",
        "tvos": "tvOS",
        "windows": "Windows",
        "android": "Android",
        "linux": "Linux",
    }
    return " / ".join(labels[key] for key in PLATFORMS if client["platforms"][key])


def expected_observation_components(client: dict[str, Any]) -> tuple[str, ...]:
    if client["source_type"] == "manual":
        return ()
    components = ["source"]
    if client.get("historical_release"):
        components.append("historical_release")
    elif client.get("release_source", client["source_type"]) in {"github", "app_store"}:
        components.append("release")
    if client.get("core_evidence"):
        components.append("core_evidence")
    return tuple(components)


def evidence_summary(
    clients: list[dict[str, Any]],
    observations: dict[str, Any],
    now: dt.datetime | None = None,
) -> str:
    current = now or utc_now()
    times: list[dt.datetime] = []
    missing = 0
    records = observations.get("clients", {})
    for client in clients:
        record = records.get(client["id"], {})
        for component_name in expected_observation_components(client):
            component = record.get(component_name, {})
            if not component_is_scoped(client, record, component_name):
                missing += 1
                continue
            timestamp = parse_time(component.get("last_success_at")) if isinstance(component, dict) else None
            if timestamp is None or timestamp > current:
                missing += 1
            else:
                times.append(timestamp)
    if not times:
        return "核验：暂无成功记录。"
    earliest = min(times).date().isoformat()
    latest = max(times).date().isoformat()
    suffix = f"；{missing} 项待成功核验" if missing else ""
    if earliest == latest:
        return f"核验：最近成功日期 {latest}{suffix}。"
    return f"核验：成功记录 {earliest} 至 {latest}{suffix}。"


def component_warning(component_name: str, component: dict[str, Any]) -> str | None:
    state = component.get("state", "unknown")
    unresolved = state not in {"ok", "archived", "manual"}
    known_problem = state not in {"ok", "archived", "manual", "unknown"}
    pending = " 未确认恢复。" if unresolved else ""
    blocks_download = component_name in {"项目来源", "版本", "历史版本"} and unresolved
    if component.get("observation_state") == "error":
        last_success = parse_time(component.get("last_success_at"))
        if blocks_download and known_problem:
            last_label = f"最近成功核验 {last_success.date().isoformat()}；" if last_success else ""
            return (
                f"{component_name}核验失败；{last_label}"
                "既有异常未恢复，下载入口保持隐藏。"
            )
        if blocks_download:
            return f"{component_name}核验失败；下载入口保持隐藏。"
        if last_success:
            return (
                f"{component_name}核验失败；"
                f"最近成功核验 {last_success.date().isoformat()}。"
                f"保留已确认记录。{pending}"
            )
        return f"{component_name}核验失败。{pending}"
    if component.get("observation_state") == "unverified":
        reason = component.get("unverified_reason", "unknown")
        if reason == "region_missing":
            return f"{component_name}在指定 App Store 区域无结果；下载入口暂时隐藏。{pending}"
        return f"{component_name}核验结果不明确；相关入口暂时隐藏。{pending}"
    state = component.get("state")
    messages = {
        ("项目来源", "missing"): "项目来源不可用；下载入口已隐藏。",
        ("项目来源", "disabled"): "项目来源已关闭；下载入口已隐藏。",
        ("项目来源", "identity_mismatch"): "项目身份与已确认记录不一致；下载入口已隐藏。",
        ("项目来源", "region_missing"): "指定 App Store 区域无结果。",
        ("版本", "missing"): "已确认版本不可用；保留原记录。",
        ("版本", "rollback"): "版本时间早于已确认记录；保留原记录。",
        ("版本", "identity_mismatch"): "版本身份与已确认记录不一致；下载入口已隐藏。",
        ("版本", "assets_missing"): "版本页可用但缺少安装文件；待确认。",
        ("版本", "region_missing"): "指定 App Store 区域无版本结果。",
        ("历史版本", "missing"): "原官方下载页不可用；下载入口已隐藏。",
        ("历史版本", "identity_mismatch"): "历史版本身份不一致；下载入口已隐藏。",
        ("历史版本", "asset_mismatch"): "历史版本安装文件发生变化；下载入口已隐藏。",
        ("内核说明", "missing"): "内核说明不可用；保留原记录。",
        ("内核说明", "mismatch"): "内核说明发生变化；保留原记录。",
    }
    return messages.get((component_name, state))


def sort_key(client: dict[str, Any], record: dict[str, Any], now: dt.datetime) -> tuple[Any, ...]:
    status = activity_status(client, record, now)[0]
    return (
        CATEGORY_ORDER[client["category"]],
        STATUS_RANK[status],
        *(-int(client["platforms"][key]) for key in PLATFORMS),
        client["name"].casefold(),
    )


def render_readme(clients: list[dict[str, Any]], observations: dict[str, Any], now: dt.datetime | None = None) -> str:
    current = now or utc_now()
    records = observations.get("clients", {})
    lines = [
        "# 代理客户端导航",
        "",
        "<!-- 本 README 由 scripts/build_readme.py 从 data/clients.json 与 data/observations.json 生成。 -->",
        "",
        "收录常见代理客户端，并按主要内核或实现方式分类。",
        "状态基于官方来源与维护时间，仅用于导航参考，不代表安全背书。",
        evidence_summary(clients, observations, current),
        "> 页面仅在自动核验或人工更新后变化；核验时间超过 7 天时，请重新确认项目状态和下载链接。",
        "",
        "> 🟢 活跃；🟡 半年至一年未更新；🕒 一年以上未更新；❓ 待确认；🔴 历史项目。",
        "> “官方来源”中的“官方仓库”表示项目提供公开代码仓库；“官网”表示主要官方入口。来源类型不等同于开源许可证。",
        "> “闭源客户端”仅表示未提供公开客户端源码，不代表一定收费。",
        "> 第三方历史资料不作为官方下载来源。",
        "",
    ]
    lines.extend([
        "## 收录原则",
        "",
        "- 在用项目须具备明确定位、官方来源、支持平台和可核验的官方安装或下载入口。",
        "- 仅有第三方镜像或无法确认项目身份的，不列入在用项目。",
        "- 单次核验失败仅标记为 ❓ 待确认；确认停更或合并后转入历史项目。",
        "- 仅在收录错误、项目无关或记录重复时删除条目。",
        "",
    ])
    for category in ("mihomo", "sing_box", "multi_core", "proprietary", "legacy"):
        group = [client for client in clients if client["category"] == category]
        group.sort(key=lambda client: sort_key(client, records.get(client["id"], {}), current))
        if not group:
            continue
        lines.extend([
            f"## {CATEGORY_TITLES[category]}",
            "",
            "| 客户端 | 状态 | macOS | iOS | tvOS | Windows | Android | Linux | 官方来源 | 下载 |",
            "| --- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | --- | --- |",
        ])
        for client in group:
            record = records.get(client["id"], {})
            status, _ = activity_status(client, record, current)
            repository, download = links_for(client, record, current)
            if client["category"] == "legacy":
                source_label = "原官方仓库" if client["source_type"] == "github" else "原项目"
            else:
                source_label = "官方仓库" if client["source_type"] == "github" else "官网"
            if client.get("historical_release"):
                download_label = "原官方下载页"
            elif download and "apps.apple.com" in download:
                download_label = "App Store"
            else:
                download_label = "下载页"
            platform = client["platforms"]
            lines.append(
                f"| {client['name']} | {status} | {icon(platform['macos'])} | {icon(platform['ios'])} | {icon(platform['tvos'])} | "
                f"{icon(platform['windows'])} | {icon(platform['android'])} | {icon(platform['linux'])} | "
                f"{markdown_link(source_label, repository)} | {markdown_link(download_label, download)} |"
            )
        lines.append("")
    lines.extend([
        "## 项目详情",
        "",
        "在用项目按主要内核或实现方式分类；历史项目单独归档，不作为新安装推荐。",
        "内核字段使用统一项目名；多内核统一写为“多内核（…）”。",
        "",
    ])
    for client in sorted(clients, key=lambda item: (CATEGORY_ORDER[item["category"]], item["name"].casefold())):
        record = records.get(client["id"], {})
        status, reason = activity_status(client, record, current)
        repository, download = links_for(client, record, current)
        notes = [
            str(value).rstrip("。；")
            for value in (client.get("compatibility_note"), client.get("source_note"))
            if value
        ]
        note_text = "；".join(notes) + "。" if notes else "无特殊说明。"
        warnings: list[str] = []
        for component_key, component_label in (
            ("source", "项目来源"),
            ("release", "版本"),
            ("historical_release", "历史版本"),
            ("core_evidence", "内核说明"),
        ):
            component = record.get(component_key, {})
            if isinstance(component, dict):
                warning = component_warning(component_label, component)
                if warning:
                    warnings.append(warning.rstrip("。；"))
        if warnings:
            verification = f"{'；'.join(warnings)}。"
        elif expected_observation_components(client):
            verification = "无异常。"
        else:
            verification = "不适用。"

        lines.extend([
            f"### {client['name']}",
            "",
            f"- 分类：{CATEGORY_TITLES[client['category']]}",
            f"- 状态：{status_detail(status, reason)}",
            f"- 平台：{platform_summary(client)}",
            f"- 内核：{client.get('core') or '未确认'}",
        ])
        if client["category"] == "legacy":
            historical = record.get("historical_release", {})
            historical_version = "未确认"
            if historical.get("tag") and component_is_scoped(client, record, "historical_release"):
                historical_version = str(historical["tag"])
            archives = client.get("third_party_archives", [])
            archive_text = "无"
            if archives:
                archive_text = "；".join(markdown_link(archive["url"], archive["url"]) for archive in archives)
                archive_text += "；只用于查找历史资料，不作为官方下载地址。"
            lines.extend([
                f"- 原官方来源：{markdown_link(repository, repository) if repository else '不可用'}",
                f"- 原官方下载：{markdown_link(download, download) if download else '不可用'}",
                f"- 最后版本：{historical_version}",
                f"- 说明：{note_text}",
                f"- 第三方历史资料：{archive_text}",
                f"- 核验：{verification}",
            ])
        else:
            release = record.get("release", {})
            version_text = "待确认"
            if release.get("version") and release.get("published_at") and component_is_scoped(client, record, "release"):
                published = parse_time(release.get("published_at"))
                published_label = published.date().isoformat() if published else str(release["published_at"])
                version_text = f"{release['version']}（{published_label}）"
            lines.extend([
                f"- 官方来源：{markdown_link(repository, repository) if repository else '待确认'}",
                f"- 下载：{markdown_link(download, download) if download else '待确认'}",
                f"- 版本：{version_text}",
                f"- 说明：{note_text}",
                f"- 核验：{verification}",
            ])
        lines.append("")
    lines.extend([
        "## 核验规则",
        "",
        "- 来源校验：项目地址、仓库归属或 App Store 发布者发生变化时，隐藏下载入口并标记为待确认。",
        "- 配置变更：项目地址、发布者或下载入口变更后，旧核验结果不直接沿用。",
        "- 临时失败：单次网络错误仅标记为待确认；连续 7 天未完成成功核验后隐藏下载入口。",
        "- App Store：指定区域无结果不等于下架；下载入口暂时隐藏，待后续核验。",
        "- 历史项目：仅保留可核验的原官方页面；第三方镜像仅作为历史资料。",
        "- 来源身份校验不等同于安装包安全认证。",
        "- 不自动替换为同名分支、继任项目或第三方镜像。",
        "- 版本回退：发布时间早于已确认版本时，保留已确认版本并标记为待确认。",
        "",
    ])
    return "\n".join(lines).rstrip() + "\n"


def validate_readme(text: str, clients: list[dict[str, Any]]) -> None:
    lower = text.lower()
    leaked = [value for value in BLOCKED_TEXT if value.lower() in lower]
    if leaked:
        raise ValueError(f"Blocked advertising text leaked: {', '.join(leaked)}")
    if "/releases/download/" in lower:
        raise ValueError("Direct binary links are forbidden")
    for replacement in FORBIDDEN_AUTOMATIC_REPLACEMENTS:
        if replacement in lower:
            raise ValueError(f"Derivative fork leaked into directory: {replacement}")
    for jargon in ("`LKG`", "`scope`", "`pin`", "canonical `", " unverified ", " failover", "lookup", "报警"):
        if jargon in text:
            raise ValueError(f"Maintenance jargon leaked into README: {jargon}")
    for client in clients:
        if text.count(f"### {client['name']}\n") != 1:
            raise ValueError(f"README must contain exactly one detail section for {client['name']}")


def health_check(clients: list[dict[str, Any]], observations: dict[str, Any], now: dt.datetime | None = None) -> None:
    health = derive_health(clients, observations, now)
    anomalies = health.get("anomalies", [])
    if anomalies:
        raise ValueError("Health anomalies: " + "; ".join(str(item) for item in anomalies))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--observations", type=Path, default=DEFAULT_OBSERVATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--audit", action="store_true", help="Refresh authoritative observations before rendering")
    parser.add_argument("--check", action="store_true", help="Fail if the generated README differs from --output")
    parser.add_argument("--health-check", action="store_true", help="Fail if the latest audit contains anomalies")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    clients = load_clients(args.catalog)
    observations = load_observations(args.observations)
    if args.audit:
        observations = audit(clients, observations)
        write_json(args.observations, observations)
    rendered = render_readme(clients, observations)
    validate_readme(rendered, clients)
    if args.check:
        existing = args.output.read_text(encoding="utf-8") if args.output.exists() else None
        if existing != rendered:
            print(f"{args.output} is not up to date.", file=sys.stderr)
            return 1
    else:
        atomic_write_text(args.output, rendered)
    if args.health_check:
        health_check(clients, observations)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
