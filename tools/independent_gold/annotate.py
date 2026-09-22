from __future__ import annotations

import argparse
import concurrent.futures
import gzip
import hashlib
import json
import pathlib
import random
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from typing import Any, Iterable


ROOT = pathlib.Path(__file__).resolve().parents[2]
RUBRIC_PATH = pathlib.Path(__file__).with_name("rubric_v1.md")
ABSENCE_ITEMS = {"v10", "v11", "v16", "v18", "v20"}
ITEMS = tuple(f"v{i}" for i in range(1, 25))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_jsonl_gz(path: pathlib.Path) -> Iterable[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def canonical_sources(record: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    docs: list[dict[str, Any]] = []
    rendered: list[str] = []
    for index, doc in enumerate(record.get("docs") or []):
        text = unicodedata.normalize("NFC", str(doc.get("text") or ""))
        info = {
            "index": index,
            "doc_id": str(doc.get("doc_id") or f"D{index}"),
            "type": str(doc.get("type") or "unknown"),
            "text": text,
            "sha256": sha256_text(text),
        }
        docs.append(info)
        rendered.append(
            f"\n<<<DOC index={index} id={info['doc_id']} type={info['type']} "
            f"sha256={info['sha256']}>>>\n{text}\n<<<END DOC>>>"
        )
    return "".join(rendered), docs


def build_messages(record: dict[str, Any], rubric: str) -> list[dict[str, str]]:
    sources, _ = canonical_sources(record)
    meta = json.dumps(record.get("meta") or {}, ensure_ascii=False, sort_keys=True)
    completeness = json.dumps(
        {
            "dropped_doc_counts": record.get("dropped_doc_counts") or {},
            "input_completeness": record.get("input_completeness") or {},
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    user = (
        f"공고ID: {record['id']}\n"
        f"META: {meta}\n"
        f"COMPLETENESS: {completeness}\n"
        "아래 제공 문서만 판정 근거로 사용하라. 운영 모델의 예측은 제공되지 않았다."
        f"\n{sources}\n"
        "v1부터 v24까지 빠짐없이 JSON으로 판정하라."
    )
    return [
        {"role": "system", "content": rubric},
        {"role": "user", "content": user},
    ]


def request_payload(model: str, messages: list[dict[str, str]], seed: int) -> dict[str, Any]:
    compact_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["labels", "confidence", "evidence"],
        "properties": {
            "labels": {"type": "string", "pattern": "^[01U]{24}$"},
            "confidence": {"type": "string", "pattern": "^[HML]{24}$"},
            "evidence": {
                "type": "object",
                "additionalProperties": {"type": "string", "maxLength": 500},
            },
        },
    }
    return {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "top_p": 1,
        "seed": seed,
        "max_tokens": 384,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "compact_annotation", "strict": True, "schema": compact_schema},
        },
    }


def post_json(endpoint: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start : end + 1])


def normalize_decision(raw: dict[str, Any]) -> dict[str, Any]:
    compact_labels = raw.get("labels")
    compact_confidence = raw.get("confidence")
    if isinstance(compact_labels, str):
        if len(compact_labels) != len(ITEMS) or any(ch not in "01U" for ch in compact_labels):
            raise ValueError("compact labels must be exactly 24 characters from 0/1/U")
        if not isinstance(compact_confidence, str) or len(compact_confidence) != len(ITEMS):
            raise ValueError("compact confidence must be exactly 24 characters")
        if any(ch not in "HML" for ch in compact_confidence):
            raise ValueError("compact confidence must use H/M/L")
        evidence_map = raw.get("evidence") or {}
        reason_map = raw.get("reasons") or {}
        if not isinstance(evidence_map, dict) or not isinstance(reason_map, dict):
            raise ValueError("compact evidence and reasons must be objects")
        result: dict[str, Any] = {}
        confidence_names = {"H": "high", "M": "medium", "L": "low"}
        for index, item in enumerate(ITEMS):
            label_char = compact_labels[index]
            label: int | str = int(label_char) if label_char in "01" else "U"
            evidence = evidence_map.get(item, "") or ""
            if not isinstance(evidence, str):
                raise ValueError(f"invalid compact evidence for {item}")
            evidence = unicodedata.normalize("NFC", evidence.strip())
            if item in ABSENCE_ITEMS:
                evidence = ""
            result[item] = {
                "label": label,
                "confidence": confidence_names[compact_confidence[index]],
                "evidence": evidence,
                "reason": str(reason_map.get(item, ""))[:500],
            }
        return result
    container = raw.get("items") or raw.get("판정") or raw
    if not isinstance(container, dict):
        raise ValueError("model output has no item object")
    result: dict[str, Any] = {}
    for item in ITEMS:
        cell = container.get(item)
        if not isinstance(cell, dict):
            raise ValueError(f"missing object for {item}")
        label = cell.get("label", cell.get("위반여부"))
        if label not in (0, 1, "U"):
            raise ValueError(f"invalid label for {item}: {label!r}")
        confidence = str(cell.get("confidence", "low")).lower()
        if confidence not in {"high", "medium", "low"}:
            confidence = "low"
        evidence = cell.get("evidence", cell.get("근거문구", "")) or ""
        if not isinstance(evidence, str):
            raise ValueError(f"invalid evidence for {item}")
        evidence = unicodedata.normalize("NFC", evidence.strip())
        reason = str(cell.get("reason", cell.get("근거", ""))).strip()
        if item in ABSENCE_ITEMS:
            evidence = ""
        result[item] = {
            "label": label,
            "confidence": confidence,
            "evidence": evidence,
            "reason": reason[:500],
        }
    return result


def locate_evidence(decisions: dict[str, Any], docs: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    for item, cell in decisions.items():
        evidence = cell["evidence"]
        locations: list[dict[str, Any]] = []
        if evidence:
            for doc in docs:
                start = doc["text"].find(evidence)
                if start >= 0:
                    locations.append(
                        {
                            "doc_index": doc["index"],
                            "doc_id": doc["doc_id"],
                            "doc_sha256": doc["sha256"],
                            "start": start,
                            "end": start + len(evidence),
                        }
                    )
            if not locations:
                errors.append(f"{item}: evidence_not_verbatim")
        if cell["label"] == 1 and item not in ABSENCE_ITEMS and not evidence:
            errors.append(f"{item}: positive_without_evidence")
        cell["evidence_locations"] = locations
    return errors


def completed_ids(path: pathlib.Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("status") == "ok" and row.get("id"):
                ids.add(str(row["id"]))
    return ids


def annotate_one(
    record: dict[str, Any],
    *,
    rubric: str,
    endpoint: str,
    model: str,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    messages = build_messages(record, rubric)
    payload = request_payload(model, messages, seed=17)
    request_hash = sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    started = time.time()
    last_error = ""
    last_content = ""
    for attempt in range(1, retries + 1):
        try:
            response = post_json(endpoint, payload, timeout)
            content = response["choices"][0]["message"]["content"]
            last_content = content
            parsed = parse_json_object(content)
            decisions = normalize_decision(parsed)
            _, docs = canonical_sources(record)
            evidence_errors = locate_evidence(decisions, docs)
            return {
                "schema_version": "dacon.independent.annotation.v1",
                "id": record["id"],
                "status": "ok",
                "model": model,
                "endpoint_kind": "openai_compatible",
                "rubric_sha256": sha256_text(rubric),
                "request_sha256": request_hash,
                "source_sha256": sha256_text(
                    json.dumps(record, ensure_ascii=False, sort_keys=True)
                ),
                "attempt": attempt,
                "elapsed_seconds": round(time.time() - started, 3),
                "usage": response.get("usage") or {},
                "decisions": decisions,
                "evidence_errors": evidence_errors,
                "raw_response_sha256": sha256_text(content),
                "raw_response": content,
            }
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                time.sleep(min(2**attempt + random.random(), 15))
    return {
        "schema_version": "dacon.independent.annotation.v1",
        "id": record["id"],
        "status": "error",
        "model": model,
        "rubric_sha256": sha256_text(rubric),
        "request_sha256": request_hash,
        "elapsed_seconds": round(time.time() - started, 3),
        "error": last_error,
        "raw_response_sha256": sha256_text(last_content) if last_content else None,
        "raw_response": last_content,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=pathlib.Path, required=True)
    parser.add_argument("--output", type=pathlib.Path, required=True)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--id", action="append", dest="ids")
    args = parser.parse_args()

    rubric = RUBRIC_PATH.read_text(encoding="utf-8")
    done = completed_ids(args.output)
    wanted = set(args.ids or [])
    records = [
        row
        for row in read_jsonl_gz(args.input)
        if row["id"] not in done and (not wanted or row["id"] in wanted)
    ]
    if args.limit is not None:
        records = records[: args.limit]
    args.output.parent.mkdir(parents=True, exist_ok=True)

    kwargs = {
        "rubric": rubric,
        "endpoint": args.endpoint,
        "model": args.model,
        "timeout": args.timeout,
        "retries": args.retries,
    }
    with args.output.open("a", encoding="utf-8", newline="\n") as handle:
        if args.workers == 1:
            iterator = (annotate_one(record, **kwargs) for record in records)
        else:
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=args.workers)
            iterator = executor.map(lambda record: annotate_one(record, **kwargs), records)
        for index, result in enumerate(iterator, 1):
            handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            print(
                f"[{index}/{len(records)}] {result['id']} {result['status']} "
                f"{result.get('elapsed_seconds', 0)}s",
                flush=True,
            )
        if args.workers != 1:
            executor.shutdown(wait=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
