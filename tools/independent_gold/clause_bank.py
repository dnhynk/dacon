"""Build a label-free, coordinate-verifiable clause occurrence bank.

This module is deliberately narrower than ``template_clusters``.  It indexes
each extracted ``(group, clause_fingerprint)`` independently so a reviewer can
read repeated source wording once and then audit *every* occurrence against its
own coordinates, source completeness, risk flags, and catalog scope.

Fingerprint equality is not semantic equivalence and never authorizes a label
or decision to be copied.  Only organizer records and the supplied catalog are
accepted; nested labels, predictions, and model outputs are rejected by the
same admissibility gate used by ``template_clusters``.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import pathlib
import sqlite3
import tempfile
from typing import Any, Iterable, Iterator, Mapping, Sequence

try:
    from tools.independent_gold import catalog_facts, template_clusters
except ModuleNotFoundError:  # Direct execution from this directory.
    import catalog_facts  # type: ignore[no-redef]
    import template_clusters  # type: ignore[no-redef]


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "dacon.independent.clause_bank.v1"
MANIFEST_SCHEMA_VERSION = "dacon.independent.clause_bank_manifest.v1"
GROUPS: dict[str, tuple[str, ...]] = dict(template_clusters.GROUPS)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_object(value: Any) -> str:
    return sha256_text(canonical_json(value))


def file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_or_absolute(path: pathlib.Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _catalog_provenance(index: catalog_facts.CatalogIndex) -> dict[str, Any]:
    return {
        "path": _relative_or_absolute(index.path),
        "sha256": index.sha256,
        "rows": index.total_rows,
    }


def _identity_from_clause(clause: Mapping[str, Any]) -> dict[str, Any]:
    """Return exactly the fields committed by the reused clause fingerprint."""

    return {
        "group": clause["group"] if "group" in clause else None,
        "doc_type": clause["doc_type"],
        "normalized_text": clause["normalized_text"],
        "variable_signature": clause["variable_signature"],
        "facets": clause["facets"],
        "anchors": clause["anchors"],
        "clipped": clause["clipped"],
    }


def _fingerprint_payload(group_name: str, clause: Mapping[str, Any]) -> dict[str, Any]:
    identity = _identity_from_clause(clause)
    identity["group"] = group_name
    return identity


def _connect(path: pathlib.Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA temp_store=FILE")
    connection.executescript(
        """
        CREATE TABLE source_records (
            record_id TEXT PRIMARY KEY,
            source_sha256 TEXT NOT NULL
        ) WITHOUT ROWID;
        CREATE TABLE clusters (
            group_name TEXT NOT NULL,
            clause_fingerprint TEXT NOT NULL,
            identity_json TEXT NOT NULL,
            identity_sha256 TEXT NOT NULL,
            PRIMARY KEY (group_name, clause_fingerprint)
        ) WITHOUT ROWID;
        CREATE TABLE quote_variants (
            group_name TEXT NOT NULL,
            clause_fingerprint TEXT NOT NULL,
            quote_sha256 TEXT NOT NULL,
            raw_quote TEXT NOT NULL,
            PRIMARY KEY (group_name, clause_fingerprint, quote_sha256)
        ) WITHOUT ROWID;
        CREATE TABLE catalog_variants (
            group_name TEXT NOT NULL,
            clause_fingerprint TEXT NOT NULL,
            scope_sha256 TEXT NOT NULL,
            scope_json TEXT NOT NULL,
            PRIMARY KEY (group_name, clause_fingerprint, scope_sha256)
        ) WITHOUT ROWID;
        CREATE TABLE occurrences (
            group_name TEXT NOT NULL,
            clause_fingerprint TEXT NOT NULL,
            occurrence_id TEXT NOT NULL,
            record_id TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            doc_index INTEGER NOT NULL,
            doc_id TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            source_doc_sha256 TEXT NOT NULL,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            text_sha256 TEXT NOT NULL,
            quote_sha256 TEXT NOT NULL,
            catalog_scope_sha256 TEXT,
            source_completeness_json TEXT NOT NULL,
            risk_flags_json TEXT NOT NULL,
            risk_tier TEXT NOT NULL,
            PRIMARY KEY (group_name, record_id, doc_index, start_offset, end_offset),
            UNIQUE (occurrence_id)
        ) WITHOUT ROWID;
        CREATE INDEX occurrences_by_cluster
            ON occurrences(group_name, clause_fingerprint, record_id, doc_index,
                           start_offset, end_offset);
        CREATE INDEX occurrences_by_quote
            ON occurrences(group_name, clause_fingerprint, quote_sha256);
        CREATE INDEX occurrences_by_catalog
            ON occurrences(group_name, clause_fingerprint, catalog_scope_sha256);
        """
    )
    return connection


def _ensure_cluster(
    connection: sqlite3.Connection,
    *,
    group_name: str,
    clause: Mapping[str, Any],
) -> None:
    fingerprint = str(clause["clause_fingerprint"])
    payload = _fingerprint_payload(group_name, clause)
    expected = sha256_object(payload)
    if expected != fingerprint:
        raise ValueError(
            f"clause fingerprint mismatch for {group_name}:{fingerprint}: {expected}"
        )
    identity_json = canonical_json(payload)
    identity_sha = sha256_text(identity_json)
    existing = connection.execute(
        "SELECT identity_json, identity_sha256 FROM clusters "
        "WHERE group_name=? AND clause_fingerprint=?",
        (group_name, fingerprint),
    ).fetchone()
    if existing is None:
        connection.execute(
            "INSERT INTO clusters VALUES (?, ?, ?, ?)",
            (group_name, fingerprint, identity_json, identity_sha),
        )
    elif existing != (identity_json, identity_sha):
        raise ValueError(
            f"clause fingerprint collision for {group_name}:{fingerprint}"
        )


def _ensure_quote_variant(
    connection: sqlite3.Connection,
    *,
    group_name: str,
    fingerprint: str,
    quote: str,
    quote_sha: str,
) -> None:
    if sha256_text(quote) != quote_sha:
        raise ValueError(
            f"raw quote hash mismatch for {group_name}:{fingerprint}:{quote_sha}"
        )
    existing = connection.execute(
        "SELECT raw_quote FROM quote_variants WHERE group_name=? "
        "AND clause_fingerprint=? AND quote_sha256=?",
        (group_name, fingerprint, quote_sha),
    ).fetchone()
    if existing is None:
        connection.execute(
            "INSERT INTO quote_variants VALUES (?, ?, ?, ?)",
            (group_name, fingerprint, quote_sha, quote),
        )
    elif existing[0] != quote:
        raise ValueError(
            f"raw quote SHA-256 collision for {group_name}:{fingerprint}:{quote_sha}"
        )


def _ensure_catalog_variant(
    connection: sqlite3.Connection,
    *,
    group_name: str,
    fingerprint: str,
    scope: Mapping[str, Any] | None,
    provenance_sha256: str,
) -> str | None:
    if scope is None:
        return None
    scope_payload = {
        "catalog_provenance_sha256": provenance_sha256,
        "scope": scope,
    }
    scope_json = canonical_json(scope_payload)
    scope_sha = sha256_text(scope_json)
    existing = connection.execute(
        "SELECT scope_json FROM catalog_variants WHERE group_name=? "
        "AND clause_fingerprint=? AND scope_sha256=?",
        (group_name, fingerprint, scope_sha),
    ).fetchone()
    if existing is None:
        connection.execute(
            "INSERT INTO catalog_variants VALUES (?, ?, ?, ?)",
            (group_name, fingerprint, scope_sha, scope_json),
        )
    elif existing[0] != scope_json:
        raise ValueError(
            f"catalog scope SHA-256 collision for {group_name}:{fingerprint}:{scope_sha}"
        )
    return scope_sha


def _insert_profile(
    connection: sqlite3.Connection,
    record: Mapping[str, Any],
    profile: Mapping[str, Any],
    *,
    catalog_provenance_sha256: str,
) -> None:
    errors = template_clusters.validate_profile(record, profile)
    if errors:
        raise ValueError(
            f"profile coordinate/hash validation failed for {record['id']}:"
            f"{profile.get('group')}: {errors[:10]}"
        )
    group_name = str(profile["group"])
    documents = record["docs"]
    scope = (profile.get("metadata_signature") or {}).get("catalog_scope")
    for clause in profile["clauses"]:
        _ensure_cluster(connection, group_name=group_name, clause=clause)
        fingerprint = str(clause["clause_fingerprint"])
        doc_index = int(clause["doc_index"])
        start = int(clause["start"])
        end = int(clause["end"])
        quote = str(documents[doc_index]["text"])[start:end]
        quote_sha = str(clause["text_sha256"])
        _ensure_quote_variant(
            connection,
            group_name=group_name,
            fingerprint=fingerprint,
            quote=quote,
            quote_sha=quote_sha,
        )
        scope_sha = _ensure_catalog_variant(
            connection,
            group_name=group_name,
            fingerprint=fingerprint,
            scope=scope,
            provenance_sha256=catalog_provenance_sha256,
        )
        coordinate_identity = {
            "group": group_name,
            "clause_fingerprint": fingerprint,
            "record_id": profile["record_id"],
            "source_sha256": profile["source_sha256"],
            "doc_index": doc_index,
            "doc_id": clause["doc_id"],
            "source_doc_sha256": clause["source_doc_sha256"],
            "start": start,
            "end": end,
            "text_sha256": quote_sha,
        }
        occurrence_id = sha256_object(coordinate_identity)
        values = (
            group_name,
            fingerprint,
            occurrence_id,
            str(profile["record_id"]),
            str(profile["source_sha256"]),
            doc_index,
            str(clause["doc_id"]),
            str(clause["doc_type"]),
            str(clause["source_doc_sha256"]),
            start,
            end,
            quote_sha,
            quote_sha,
            scope_sha,
            canonical_json(profile["source_completeness"]),
            canonical_json(profile["risk_flags"]),
            str(profile["routing"]["risk_tier"]),
        )
        try:
            connection.execute(
                "INSERT INTO occurrences VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                "duplicate occurrence coordinate or identity: "
                f"{group_name}:{profile['record_id']}:{doc_index}:{start}:{end}"
            ) from exc


def _grouped_cursor_rows(
    rows: Iterable[Sequence[Any]],
) -> Iterator[tuple[str, list[Sequence[Any]]]]:
    """Group an already-sorted cursor without materialising the full table."""

    current_key: str | None = None
    current_rows: list[Sequence[Any]] = []
    for row in rows:
        key = str(row[0])
        if current_key is None:
            current_key = key
        elif key != current_key:
            yield current_key, current_rows
            current_key = key
            current_rows = []
        current_rows.append(row)
    if current_key is not None:
        yield current_key, current_rows


def _next_group(
    iterator: Iterator[tuple[str, list[Sequence[Any]]]],
) -> tuple[str, list[Sequence[Any]]] | None:
    return next(iterator, None)


def _cluster_rows(
    connection: sqlite3.Connection,
    *,
    catalog_provenance: Mapping[str, Any],
) -> Iterator[dict[str, Any]]:
    """Stream deterministic cluster rows with a bounded number of SQL scans.

    The original implementation issued three aggregate queries for every
    fingerprint.  At the full 20,000-record scale that meant more than 1.5
    million SQLite queries.  This merge-walk reads the same primary-key/index
    order once per group and constructs byte-identical canonical rows.
    """

    provenance_sha = sha256_object(catalog_provenance)
    for group_name in GROUPS:
        clusters = connection.execute(
            "SELECT clause_fingerprint, identity_json, identity_sha256 FROM clusters "
            "WHERE group_name=? ORDER BY clause_fingerprint",
            (group_name,),
        )
        occurrence_groups = _grouped_cursor_rows(
            connection.execute(
                "SELECT clause_fingerprint, occurrence_id, record_id, source_sha256, "
                "doc_index, doc_id, doc_type, source_doc_sha256, start_offset, "
                "end_offset, text_sha256, quote_sha256, catalog_scope_sha256, "
                "source_completeness_json, risk_flags_json, risk_tier FROM occurrences "
                "WHERE group_name=? ORDER BY clause_fingerprint, record_id, doc_index, "
                "start_offset, end_offset, occurrence_id",
                (group_name,),
            )
        )
        quote_groups = _grouped_cursor_rows(
            connection.execute(
                "SELECT clause_fingerprint, quote_sha256, raw_quote FROM quote_variants "
                "WHERE group_name=? ORDER BY clause_fingerprint, quote_sha256",
                (group_name,),
            )
        )
        catalog_groups = _grouped_cursor_rows(
            connection.execute(
                "SELECT clause_fingerprint, scope_sha256, scope_json FROM "
                "catalog_variants WHERE group_name=? ORDER BY clause_fingerprint, "
                "scope_sha256",
                (group_name,),
            )
        )
        current_occurrences = _next_group(occurrence_groups)
        current_quotes = _next_group(quote_groups)
        current_catalog = _next_group(catalog_groups)
        for fingerprint, identity_json, identity_sha256 in clusters:
            fingerprint = str(fingerprint)
            if sha256_text(str(identity_json)) != str(identity_sha256):
                raise ValueError(
                    f"cluster identity hash mismatch: {group_name}:{fingerprint}"
                )
            identity = json.loads(identity_json)
            if identity.get("group") != group_name or sha256_object(identity) != fingerprint:
                raise ValueError(
                    f"cluster fingerprint mismatch: {group_name}:{fingerprint}"
                )
            if current_occurrences is not None and current_occurrences[0] < fingerprint:
                raise ValueError(
                    f"occurrence references missing cluster: "
                    f"{group_name}:{current_occurrences[0]}"
                )
            occurrence_rows: list[Sequence[Any]] = []
            if current_occurrences is not None and current_occurrences[0] == fingerprint:
                occurrence_rows = current_occurrences[1]
                current_occurrences = _next_group(occurrence_groups)

            if current_quotes is not None and current_quotes[0] < fingerprint:
                raise ValueError(
                    f"quote variant references missing cluster: "
                    f"{group_name}:{current_quotes[0]}"
                )
            quote_rows: list[Sequence[Any]] = []
            if current_quotes is not None and current_quotes[0] == fingerprint:
                quote_rows = current_quotes[1]
                current_quotes = _next_group(quote_groups)

            if current_catalog is not None and current_catalog[0] < fingerprint:
                raise ValueError(
                    f"catalog variant references missing cluster: "
                    f"{group_name}:{current_catalog[0]}"
                )
            catalog_rows: list[Sequence[Any]] = []
            if current_catalog is not None and current_catalog[0] == fingerprint:
                catalog_rows = current_catalog[1]
                current_catalog = _next_group(catalog_groups)

            occurrences: list[dict[str, Any]] = []
            tier_counts: collections.Counter[str] = collections.Counter()
            flag_counts: collections.Counter[str] = collections.Counter()
            completeness_counts: collections.Counter[str] = collections.Counter()
            quote_counts: collections.Counter[str] = collections.Counter()
            quote_members: dict[str, set[str]] = collections.defaultdict(set)
            catalog_counts: collections.Counter[str] = collections.Counter()
            catalog_members: dict[str, set[str]] = collections.defaultdict(set)
            record_ids: set[str] = set()
            for row in occurrence_rows:
                if int(row[8]) < 0 or int(row[9]) < int(row[8]):
                    raise ValueError(
                        f"invalid occurrence coordinates: "
                        f"{group_name}:{fingerprint}:{row[1]}"
                    )
                if str(row[10]) != str(row[11]):
                    raise ValueError(
                        f"occurrence text/quote hash mismatch: "
                        f"{group_name}:{fingerprint}:{row[1]}"
                    )
                occurrence_identity = {
                    "group": group_name,
                    "clause_fingerprint": fingerprint,
                    "record_id": row[2],
                    "source_sha256": row[3],
                    "doc_index": row[4],
                    "doc_id": row[5],
                    "source_doc_sha256": row[7],
                    "start": row[8],
                    "end": row[9],
                    "text_sha256": row[10],
                }
                if sha256_object(occurrence_identity) != str(row[1]):
                    raise ValueError(
                        f"occurrence identity hash mismatch: "
                        f"{group_name}:{fingerprint}:{row[1]}"
                    )
                completeness = json.loads(row[13])
                risk_flags = json.loads(row[14])
                if not isinstance(completeness, Mapping) or not isinstance(
                    risk_flags, list
                ):
                    raise ValueError(
                        f"invalid occurrence risk payload: "
                        f"{group_name}:{fingerprint}:{row[1]}"
                    )
                occurrence = {
                    "occurrence_id": row[1],
                    "record_id": row[2],
                    "source_sha256": row[3],
                    "coordinate": {
                        "doc_index": row[4],
                        "doc_id": row[5],
                        "doc_type": row[6],
                        "source_doc_sha256": row[7],
                        "start": row[8],
                        "end": row[9],
                        "text_sha256": row[10],
                    },
                    "raw_quote_variant_id": f"Q-{row[11]}",
                    "catalog_scope_variant_id": (
                        f"K-{row[12]}" if row[12] is not None else None
                    ),
                    "source_completeness": completeness,
                    "risk_flags": risk_flags,
                    "risk_tier": row[15],
                }
                occurrence["coordinate_sha256"] = sha256_object(occurrence["coordinate"])
                occurrences.append(occurrence)
                record_id = str(row[2])
                quote_sha = str(row[11])
                scope_sha = str(row[12]) if row[12] is not None else None
                record_ids.add(record_id)
                tier_counts[str(row[15])] += 1
                flag_counts.update(str(value) for value in risk_flags)
                completeness_counts[canonical_json(completeness)] += 1
                quote_counts[quote_sha] += 1
                quote_members[quote_sha].add(record_id)
                if scope_sha is not None:
                    catalog_counts[scope_sha] += 1
                    catalog_members[scope_sha].add(record_id)

            quote_variants = []
            for _, quote_sha, raw_quote in quote_rows:
                if str(quote_sha) not in quote_counts:
                    raise ValueError(
                        f"unreferenced quote variant: "
                        f"{group_name}:{fingerprint}:{quote_sha}"
                    )
                if sha256_text(str(raw_quote)) != str(quote_sha):
                    raise ValueError(
                        f"quote variant hash mismatch: "
                        f"{group_name}:{fingerprint}:{quote_sha}"
                    )
                quote_variants.append(
                    {
                        "variant_id": f"Q-{quote_sha}",
                        "text_sha256": quote_sha,
                        "chars": len(raw_quote),
                        "occurrence_count": quote_counts[str(quote_sha)],
                        "member_record_count": len(quote_members[str(quote_sha)]),
                        "raw_quote": raw_quote,
                    }
                )
            if set(quote_counts) != {str(row[1]) for row in quote_rows}:
                raise ValueError(
                    f"occurrence references missing quote variant: "
                    f"{group_name}:{fingerprint}"
                )

            catalog_variants = []
            for _, scope_sha, scope_json in catalog_rows:
                if str(scope_sha) not in catalog_counts:
                    raise ValueError(
                        f"unreferenced catalog variant: "
                        f"{group_name}:{fingerprint}:{scope_sha}"
                    )
                if sha256_text(str(scope_json)) != str(scope_sha):
                    raise ValueError(
                        f"catalog variant hash mismatch: "
                        f"{group_name}:{fingerprint}:{scope_sha}"
                    )
                decoded = json.loads(scope_json)
                if decoded.get("catalog_provenance_sha256") != provenance_sha:
                    raise ValueError(
                        f"catalog provenance mismatch: "
                        f"{group_name}:{fingerprint}:{scope_sha}"
                    )
                catalog_variants.append(
                    {
                        "variant_id": f"K-{scope_sha}",
                        "scope_sha256": scope_sha,
                        "occurrence_count": catalog_counts[str(scope_sha)],
                        "member_record_count": len(catalog_members[str(scope_sha)]),
                        "catalog_scope": decoded["scope"],
                    }
                )
            if set(catalog_counts) != {str(row[1]) for row in catalog_rows}:
                raise ValueError(
                    f"occurrence references missing catalog variant: "
                    f"{group_name}:{fingerprint}"
                )

            completeness_variants = [
                {"source_completeness": json.loads(value), "occurrence_count": count}
                for value, count in sorted(completeness_counts.items())
            ]
            yield {
                "schema_version": SCHEMA_VERSION,
                "row_kind": "group_clause_fingerprint_bank",
                "group": group_name,
                "target_items": list(GROUPS[group_name]),
                "cluster_id": f"C-{group_name}-{fingerprint[:16]}",
                "clause_fingerprint": fingerprint,
                "fingerprint_identity": identity,
                "occurrence_count": len(occurrences),
                "member_record_count": len(record_ids),
                "member_record_ids": sorted(record_ids),
                "raw_quote_variant_count": len(quote_variants),
                "raw_quote_variants": quote_variants,
                "risk_facets": {
                    "risk_tiers": dict(sorted(tier_counts.items())),
                    "risk_flags": dict(sorted(flag_counts.items())),
                    "source_completeness_variants": completeness_variants,
                    "clause_facets": identity["facets"],
                    "clipped": identity["clipped"],
                },
                "catalog_provenance": (
                    dict(catalog_provenance)
                    if group_name in {"v10-13", "v14-19"}
                    else None
                ),
                "catalog_provenance_sha256": (
                    provenance_sha if group_name in {"v10-13", "v14-19"} else None
                ),
                "catalog_scope_variants": catalog_variants,
                "occurrences": occurrences,
                "review_contract": {
                    "safe_for": "shared_clause_reading_and_occurrence_consistency_audit_only",
                    "automatic_label_or_decision_propagation_allowed": False,
                    "semantic_equivalence_claimed": False,
                    "each_occurrence_context_must_be_checked": True,
                },
            }
        for label, current in (
            ("occurrence", current_occurrences),
            ("quote variant", current_quotes),
            ("catalog variant", current_catalog),
        ):
            if current is not None:
                raise ValueError(
                    f"{label} references missing cluster: {group_name}:{current[0]}"
                )


def _group_statistics(
    connection: sqlite3.Connection,
    *,
    records_seen: int,
    profile_risk_counts: Mapping[str, collections.Counter[str]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for group_name in GROUPS:
        cluster_counts = [
            int(row[0])
            for row in connection.execute(
                "SELECT COUNT(*) FROM occurrences WHERE group_name=? "
                "GROUP BY clause_fingerprint ORDER BY COUNT(*) DESC",
                (group_name,),
            )
        ]
        unique_clusters = len(cluster_counts)
        occurrence_count = sum(cluster_counts)
        records_with_clause = int(
            connection.execute(
                "SELECT COUNT(DISTINCT record_id) FROM occurrences WHERE group_name=?",
                (group_name,),
            ).fetchone()[0]
        )
        raw_variants = int(
            connection.execute(
                "SELECT COUNT(*) FROM quote_variants WHERE group_name=?",
                (group_name,),
            ).fetchone()[0]
        )
        catalog_variants = int(
            connection.execute(
                "SELECT COUNT(*) FROM catalog_variants WHERE group_name=?",
                (group_name,),
            ).fetchone()[0]
        )
        top = [
            {
                "clause_fingerprint": row[0],
                "occurrence_count": int(row[1]),
                "member_record_count": int(row[2]),
                "raw_quote_variant_count": int(row[3]),
            }
            for row in connection.execute(
                "SELECT o.clause_fingerprint, COUNT(*), COUNT(DISTINCT o.record_id), "
                "COUNT(DISTINCT o.quote_sha256) FROM occurrences o WHERE o.group_name=? "
                "GROUP BY o.clause_fingerprint ORDER BY COUNT(*) DESC, "
                "o.clause_fingerprint LIMIT 20",
                (group_name,),
            )
        ]
        result[group_name] = {
            "records_profiled": records_seen,
            "records_with_retrieved_clause": records_with_clause,
            "records_without_retrieved_clause": records_seen - records_with_clause,
            "occurrences": occurrence_count,
            "unique_clause_fingerprints": unique_clusters,
            "repeated_clause_fingerprints": sum(value > 1 for value in cluster_counts),
            "occurrences_in_repeated_fingerprints": sum(
                value for value in cluster_counts if value > 1
            ),
            "singleton_clause_fingerprints": sum(value == 1 for value in cluster_counts),
            "largest_fingerprint_occurrences": max(cluster_counts, default=0),
            "raw_quote_variants": raw_variants,
            "catalog_scope_variants": catalog_variants,
            "profile_risk_tiers": dict(sorted(profile_risk_counts[group_name].items())),
            "top_fingerprints": top,
        }
    return result


def _temporary_path(destination: pathlib.Path, *, suffix: str = ".tmp") -> pathlib.Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{destination.name}.",
        suffix=suffix,
        dir=destination.parent,
        delete=False,
    )
    handle.close()
    return pathlib.Path(handle.name)


def _write_text_temp(destination: pathlib.Path, text: str) -> pathlib.Path:
    temporary = _temporary_path(destination)
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary


def render_summary_markdown(manifest: Mapping[str, Any]) -> str:
    stats = manifest["statistics"]
    lines = [
        "# 독립 clause bank census",
        "",
        "> 반복 조항의 표면 검수와 occurrence 일관성 감사 전용이다. 라벨 자동 전파와 의미 동치 주장은 금지된다.",
        "",
        f"- 공고: {stats['records']:,}건",
        f"- 조항 occurrence: {stats['occurrences']:,}개",
        f"- group별 clause fingerprint: {stats['unique_clause_fingerprints']:,}개",
        f"- 반복 fingerprint: {stats['repeated_clause_fingerprints']:,}개",
        f"- 반복 fingerprint 소속 occurrence: {stats['occurrences_in_repeated_fingerprints']:,}개",
        "",
        "| 그룹 | occurrence | 고유 fingerprint | 반복 fingerprint | 반복 occurrence | raw variants | 미검색 공고 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for group_name in GROUPS:
        row = manifest["groups"][group_name]
        lines.append(
            f"| {group_name} | {row['occurrences']:,} | "
            f"{row['unique_clause_fingerprints']:,} | "
            f"{row['repeated_clause_fingerprints']:,} | "
            f"{row['occurrences_in_repeated_fingerprints']:,} | "
            f"{row['raw_quote_variants']:,} | "
            f"{row['records_without_retrieved_clause']:,} |"
        )
    lines.extend(
        [
            "",
            "## 강제 안전 경계",
            "",
            "- fingerprint가 같아도 법적 결론이나 24개 항목의 정답이 같다는 뜻이 아니다.",
            "- 대표 조항을 읽은 뒤에도 모든 occurrence의 원문 좌표, metadata/context, source completeness, risk, catalog scope를 감사해야 한다.",
            "- JSONL은 각 occurrence의 record/document hash, 정확한 start/end, quote hash와 raw-quote variant를 보존한다.",
            "- 입력의 중첩 label/prediction/model-output 계열 필드는 수집 전에 거부된다.",
            "",
            f"Clause JSONL SHA-256: `{manifest['outputs']['clauses_jsonl']['sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def build_clause_bank(
    records: Iterable[Mapping[str, Any]],
    *,
    output_jsonl: pathlib.Path,
    manifest_output: pathlib.Path,
    summary_output: pathlib.Path | None = None,
    input_path: pathlib.Path | None = None,
    limit: int | None = None,
    catalog_index: catalog_facts.CatalogIndex | None = None,
) -> dict[str, Any]:
    """Build and atomically publish a disk-backed clause census.

    SQLite is only a temporary aggregation spool.  The durable large artifact
    is deterministic JSONL, allowing one cluster at a time to be consumed.
    The small manifest is replaced last and seals the JSONL and optional
    Markdown summary hashes.
    """

    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    destinations = [output_jsonl.resolve(), manifest_output.resolve()]
    if summary_output is not None:
        destinations.append(summary_output.resolve())
    if len(set(destinations)) != len(destinations):
        raise ValueError("output_jsonl, manifest_output, and summary_output must differ")

    catalog_index = catalog_index or catalog_facts.CatalogIndex.load()
    catalog_provenance = _catalog_provenance(catalog_index)
    catalog_provenance_sha = sha256_object(catalog_provenance)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    if summary_output is not None:
        summary_output.parent.mkdir(parents=True, exist_ok=True)

    database_path = _temporary_path(output_jsonl, suffix=".sqlite3")
    jsonl_temp: pathlib.Path | None = None
    summary_temp: pathlib.Path | None = None
    manifest_temp: pathlib.Path | None = None
    connection: sqlite3.Connection | None = None
    records_seen = 0
    profile_risk_counts = {name: collections.Counter() for name in GROUPS}
    try:
        connection = _connect(database_path)
        for record in records:
            if limit is not None and records_seen >= limit:
                break
            template_clusters.assert_admissible_record(
                record, location=f"record[{records_seen}]"
            )
            record_id = str(record["id"])
            source_sha = template_clusters.packetize.sha256_object(record)
            try:
                connection.execute(
                    "INSERT INTO source_records VALUES (?, ?)",
                    (record_id, source_sha),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"duplicate record id: {record_id}") from exc

            profiles = template_clusters.build_record_profiles(
                record, catalog_index=catalog_index
            )
            if set(profiles) != set(GROUPS):
                raise ValueError(
                    f"profile group mismatch for {record_id}: {sorted(profiles)}"
                )
            for group_name in GROUPS:
                profile = profiles[group_name]
                profile_risk_counts[group_name][profile["routing"]["risk_tier"]] += 1
                _insert_profile(
                    connection,
                    record,
                    profile,
                    catalog_provenance_sha256=catalog_provenance_sha,
                )
            records_seen += 1
            if records_seen % 100 == 0:
                connection.commit()
        connection.commit()

        groups = _group_statistics(
            connection,
            records_seen=records_seen,
            profile_risk_counts=profile_risk_counts,
        )
        jsonl_temp = _temporary_path(output_jsonl)
        digest = hashlib.sha256()
        jsonl_rows = 0
        with jsonl_temp.open("wb") as handle:
            for row in _cluster_rows(
                connection, catalog_provenance=catalog_provenance
            ):
                encoded = (canonical_json(row) + "\n").encode("utf-8")
                handle.write(encoded)
                digest.update(encoded)
                jsonl_rows += 1
            handle.flush()
            os.fsync(handle.fileno())

        total_occurrences = sum(row["occurrences"] for row in groups.values())
        manifest: dict[str, Any] = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "clause_row_schema_version": SCHEMA_VERSION,
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "semantic_role": "label_free_shared_clause_review_and_occurrence_consistency_audit",
            "review_only_contract": {
                "automatic_label_or_decision_propagation_allowed": False,
                "semantic_equivalence_claimed": False,
                "one_review_replaces_occurrence_context_audit": False,
                "every_occurrence_must_be_checked_against_context": True,
                "labels_predictions_or_model_outputs_consumed": False,
            },
            "input": {
                "path": str(input_path) if input_path is not None else None,
                "sha256": file_sha256(input_path) if input_path is not None else None,
                "records": records_seen,
                "limit": limit,
            },
            "catalog_provenance": catalog_provenance,
            "normalization_provenance": {
                "module": _relative_or_absolute(pathlib.Path(template_clusters.__file__)),
                "module_sha256": file_sha256(pathlib.Path(template_clusters.__file__)),
                "template_profile_schema_version": template_clusters.PROFILE_SCHEMA_VERSION,
                "coordinate_validation": "template_clusters.validate_profile",
            },
            "outputs": {
                "clauses_jsonl": {
                    "path": str(output_jsonl),
                    "sha256": digest.hexdigest(),
                    "bytes": jsonl_temp.stat().st_size,
                    "rows": jsonl_rows,
                },
                "summary_markdown": None,
            },
            "statistics": {
                "records": records_seen,
                "group_profiles": records_seen * len(GROUPS),
                "occurrences": total_occurrences,
                "unique_clause_fingerprints": sum(
                    row["unique_clause_fingerprints"] for row in groups.values()
                ),
                "repeated_clause_fingerprints": sum(
                    row["repeated_clause_fingerprints"] for row in groups.values()
                ),
                "occurrences_in_repeated_fingerprints": sum(
                    row["occurrences_in_repeated_fingerprints"]
                    for row in groups.values()
                ),
                "raw_quote_variants": sum(
                    row["raw_quote_variants"] for row in groups.values()
                ),
            },
            "groups": groups,
        }
        if manifest["statistics"]["unique_clause_fingerprints"] != jsonl_rows:
            raise ValueError("JSONL row count does not match unique fingerprint count")
        if summary_output is not None:
            summary = render_summary_markdown(manifest)
            summary_temp = _write_text_temp(summary_output, summary)
            manifest["outputs"]["summary_markdown"] = {
                "path": str(summary_output),
                "sha256": file_sha256(summary_temp),
                "bytes": summary_temp.stat().st_size,
            }
        manifest["content_sha256"] = sha256_object(
            {
                key: value
                for key, value in manifest.items()
                if key not in {"generated_at_utc", "content_sha256"}
            }
        )
        manifest_temp = _write_text_temp(
            manifest_output,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

        # The manifest is the publication seal, so replace it last.
        jsonl_temp.replace(output_jsonl)
        jsonl_temp = None
        if summary_output is not None and summary_temp is not None:
            summary_temp.replace(summary_output)
            summary_temp = None
        manifest_temp.replace(manifest_output)
        manifest_temp = None
        return manifest
    finally:
        if connection is not None:
            connection.close()
        for temporary in (jsonl_temp, summary_temp, manifest_temp, database_path):
            if temporary is not None and temporary.exists():
                temporary.unlink()


def _open_read_only_database(path: pathlib.Path) -> sqlite3.Connection:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"recovery database does not exist: {resolved}")
    connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _validate_recovery_spool(
    connection: sqlite3.Connection,
    records: Iterable[Mapping[str, Any]],
    *,
    expected_records: int | None,
) -> tuple[int, dict[str, collections.Counter[str]]]:
    """Fail closed unless an interrupted spool is complete and source-bound."""

    if expected_records is not None and expected_records <= 0:
        raise ValueError("expected_records must be positive")
    check = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
    if check != ["ok"]:
        raise ValueError(f"recovery database quick_check failed: {check[:10]}")

    required_tables = {
        "source_records",
        "clusters",
        "quote_variants",
        "catalog_variants",
        "occurrences",
    }
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing_tables = sorted(required_tables - tables)
    if missing_tables:
        raise ValueError(f"recovery database is missing tables: {missing_tables}")

    database_sources = {
        str(record_id): str(source_sha)
        for record_id, source_sha in connection.execute(
            "SELECT record_id, source_sha256 FROM source_records ORDER BY record_id"
        )
    }
    if expected_records is not None and len(database_sources) != expected_records:
        raise ValueError(
            "recovery database record count mismatch: "
            f"expected {expected_records}, found {len(database_sources)}"
        )

    input_sources: dict[str, str] = {}
    for index, record in enumerate(records):
        template_clusters.assert_admissible_record(record, location=f"record[{index}]")
        record_id = str(record["id"])
        if record_id in input_sources:
            raise ValueError(f"duplicate record id in recovery input: {record_id}")
        input_sources[record_id] = template_clusters.packetize.sha256_object(record)
    if expected_records is not None and len(input_sources) != expected_records:
        raise ValueError(
            "recovery input record count mismatch: "
            f"expected {expected_records}, found {len(input_sources)}"
        )
    if input_sources != database_sources:
        missing = sorted(set(input_sources) - set(database_sources))[:10]
        extra = sorted(set(database_sources) - set(input_sources))[:10]
        changed = sorted(
            record_id
            for record_id in set(input_sources) & set(database_sources)
            if input_sources[record_id] != database_sources[record_id]
        )[:10]
        raise ValueError(
            "recovery database/source identity mismatch: "
            f"missing={missing}, extra={extra}, changed={changed}"
        )

    for table in ("clusters", "quote_variants", "catalog_variants", "occurrences"):
        unknown = [
            str(row[0])
            for row in connection.execute(
                f"SELECT DISTINCT group_name FROM {table} "
                "WHERE group_name NOT IN "
                f"({','.join('?' for _ in GROUPS)}) ORDER BY group_name",
                tuple(GROUPS),
            )
        ]
        if unknown:
            raise ValueError(f"unknown groups in {table}: {unknown}")

    bad_source_links = int(
        connection.execute(
            "SELECT COUNT(*) FROM occurrences o LEFT JOIN source_records s "
            "ON s.record_id=o.record_id WHERE s.record_id IS NULL OR "
            "s.source_sha256<>o.source_sha256"
        ).fetchone()[0]
    )
    if bad_source_links:
        raise ValueError(
            f"occurrences with missing/mismatched source records: {bad_source_links}"
        )
    bad_cluster_links = int(
        connection.execute(
            "SELECT COUNT(*) FROM occurrences o LEFT JOIN clusters c ON "
            "c.group_name=o.group_name AND "
            "c.clause_fingerprint=o.clause_fingerprint "
            "WHERE c.clause_fingerprint IS NULL"
        ).fetchone()[0]
    )
    if bad_cluster_links:
        raise ValueError(f"occurrences with missing clusters: {bad_cluster_links}")
    empty_clusters = int(
        connection.execute(
            "SELECT COUNT(*) FROM clusters c WHERE NOT EXISTS "
            "(SELECT 1 FROM occurrences o WHERE o.group_name=c.group_name "
            "AND o.clause_fingerprint=c.clause_fingerprint)"
        ).fetchone()[0]
    )
    if empty_clusters:
        raise ValueError(f"clusters without occurrences: {empty_clusters}")

    conflicts = int(
        connection.execute(
            "SELECT COUNT(*) FROM (SELECT group_name, record_id FROM occurrences "
            "GROUP BY group_name, record_id HAVING COUNT(DISTINCT risk_tier)<>1)"
        ).fetchone()[0]
    )
    if conflicts:
        raise ValueError(f"record/group risk tier conflicts: {conflicts}")

    allowed_tiers = {"critical", "high", "standard"}
    risk_counts = {name: collections.Counter() for name in GROUPS}
    for group_name, risk_tier, count in connection.execute(
        "SELECT group_name, risk_tier, COUNT(*) FROM "
        "(SELECT group_name, record_id, MIN(risk_tier) AS risk_tier "
        "FROM occurrences GROUP BY group_name, record_id) "
        "GROUP BY group_name, risk_tier ORDER BY group_name, risk_tier"
    ):
        if str(risk_tier) not in allowed_tiers:
            raise ValueError(
                f"invalid recovery risk tier: {group_name}:{risk_tier}"
            )
        risk_counts[str(group_name)][str(risk_tier)] += int(count)
    records_seen = len(database_sources)
    for group_name in GROUPS:
        represented = sum(risk_counts[group_name].values())
        if represented > records_seen:
            raise ValueError(
                f"too many profiled records for {group_name}: {represented}"
            )
        # In the source builder an empty retrieval is unconditionally critical.
        risk_counts[group_name]["critical"] += records_seen - represented
        if sum(risk_counts[group_name].values()) != records_seen:
            raise ValueError(f"risk census mismatch for {group_name}")
    return records_seen, risk_counts


def recover_clause_bank(
    records: Iterable[Mapping[str, Any]],
    *,
    database_path: pathlib.Path,
    output_jsonl: pathlib.Path,
    manifest_output: pathlib.Path,
    summary_output: pathlib.Path | None = None,
    input_path: pathlib.Path | None = None,
    expected_records: int | None = None,
    catalog_index: catalog_facts.CatalogIndex | None = None,
) -> dict[str, Any]:
    """Validate and publish a completed spool left by interrupted serialization.

    Recovery never mutates or deletes ``database_path``.  It binds every source
    record hash to the organizer input, checks relational/hash invariants, and
    publishes the manifest last.  The manifest explicitly records that the
    original in-memory counters were reconstructed from occurrence risk tiers;
    no annotation labels or production outputs are consumed.
    """

    database_path = database_path.resolve()
    destinations = [output_jsonl.resolve(), manifest_output.resolve()]
    if summary_output is not None:
        destinations.append(summary_output.resolve())
    if len(set(destinations)) != len(destinations):
        raise ValueError("output_jsonl, manifest_output, and summary_output must differ")
    if database_path in destinations:
        raise ValueError("recovery database must not be an output destination")
    if input_path is not None and input_path.resolve() in destinations:
        raise ValueError("organizer input must not be an output destination")

    catalog_index = catalog_index or catalog_facts.CatalogIndex.load()
    catalog_provenance = _catalog_provenance(catalog_index)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    manifest_output.parent.mkdir(parents=True, exist_ok=True)
    if summary_output is not None:
        summary_output.parent.mkdir(parents=True, exist_ok=True)

    jsonl_temp: pathlib.Path | None = None
    summary_temp: pathlib.Path | None = None
    manifest_temp: pathlib.Path | None = None
    connection: sqlite3.Connection | None = None
    try:
        connection = _open_read_only_database(database_path)
        records_seen, profile_risk_counts = _validate_recovery_spool(
            connection,
            records,
            expected_records=expected_records,
        )
        groups = _group_statistics(
            connection,
            records_seen=records_seen,
            profile_risk_counts=profile_risk_counts,
        )

        jsonl_temp = _temporary_path(output_jsonl)
        digest = hashlib.sha256()
        jsonl_rows = 0
        with jsonl_temp.open("wb") as handle:
            for row in _cluster_rows(
                connection,
                catalog_provenance=catalog_provenance,
            ):
                encoded = (canonical_json(row) + "\n").encode("utf-8")
                handle.write(encoded)
                digest.update(encoded)
                jsonl_rows += 1
            handle.flush()
            os.fsync(handle.fileno())

        total_occurrences = sum(row["occurrences"] for row in groups.values())
        manifest: dict[str, Any] = {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "clause_row_schema_version": SCHEMA_VERSION,
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "semantic_role": "label_free_shared_clause_review_and_occurrence_consistency_audit",
            "review_only_contract": {
                "automatic_label_or_decision_propagation_allowed": False,
                "semantic_equivalence_claimed": False,
                "one_review_replaces_occurrence_context_audit": False,
                "every_occurrence_must_be_checked_against_context": True,
                "labels_predictions_or_model_outputs_consumed": False,
            },
            "input": {
                "path": str(input_path) if input_path is not None else None,
                "sha256": file_sha256(input_path) if input_path is not None else None,
                "records": records_seen,
                "limit": None,
            },
            "catalog_provenance": catalog_provenance,
            "normalization_provenance": {
                "module": _relative_or_absolute(pathlib.Path(template_clusters.__file__)),
                "module_sha256": file_sha256(pathlib.Path(template_clusters.__file__)),
                "template_profile_schema_version": template_clusters.PROFILE_SCHEMA_VERSION,
                "coordinate_validation": "validated_before_spool_insert_and_rechecked_by_recovery_hash_invariants",
            },
            "recovery_provenance": {
                "mode": "validated_read_only_sqlite_spool_republication",
                "database_path": str(database_path),
                "database_sha256": file_sha256(database_path),
                "database_bytes": database_path.stat().st_size,
                "sqlite_quick_check": "ok",
                "source_record_ids_and_hashes_replayed": True,
                "cluster_occurrence_variant_hashes_replayed": True,
                "profile_risk_tiers_reconstructed": True,
                "empty_retrieval_risk_rule": "critical",
                "original_builder_manifest_available": False,
                "database_mutated_or_deleted": False,
            },
            "outputs": {
                "clauses_jsonl": {
                    "path": str(output_jsonl),
                    "sha256": digest.hexdigest(),
                    "bytes": jsonl_temp.stat().st_size,
                    "rows": jsonl_rows,
                },
                "summary_markdown": None,
            },
            "statistics": {
                "records": records_seen,
                "group_profiles": records_seen * len(GROUPS),
                "occurrences": total_occurrences,
                "unique_clause_fingerprints": sum(
                    row["unique_clause_fingerprints"] for row in groups.values()
                ),
                "repeated_clause_fingerprints": sum(
                    row["repeated_clause_fingerprints"] for row in groups.values()
                ),
                "occurrences_in_repeated_fingerprints": sum(
                    row["occurrences_in_repeated_fingerprints"]
                    for row in groups.values()
                ),
                "raw_quote_variants": sum(
                    row["raw_quote_variants"] for row in groups.values()
                ),
            },
            "groups": groups,
        }
        if manifest["statistics"]["unique_clause_fingerprints"] != jsonl_rows:
            raise ValueError("JSONL row count does not match unique fingerprint count")
        if summary_output is not None:
            summary = render_summary_markdown(manifest)
            summary_temp = _write_text_temp(summary_output, summary)
            manifest["outputs"]["summary_markdown"] = {
                "path": str(summary_output),
                "sha256": file_sha256(summary_temp),
                "bytes": summary_temp.stat().st_size,
            }
        manifest["content_sha256"] = sha256_object(
            {
                key: value
                for key, value in manifest.items()
                if key not in {"generated_at_utc", "content_sha256"}
            }
        )
        manifest_temp = _write_text_temp(
            manifest_output,
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )

        jsonl_temp.replace(output_jsonl)
        jsonl_temp = None
        if summary_output is not None and summary_temp is not None:
            summary_temp.replace(summary_output)
            summary_temp = None
        manifest_temp.replace(manifest_output)
        manifest_temp = None
        return manifest
    finally:
        if connection is not None:
            connection.close()
        for temporary in (jsonl_temp, summary_temp, manifest_temp):
            if temporary is not None and temporary.exists():
                temporary.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a label-free group/clause-fingerprint occurrence bank."
    )
    parser.add_argument(
        "--input",
        type=pathlib.Path,
        default=ROOT / "data_open" / "train_unlabeled.jsonl.gz",
    )
    parser.add_argument("--output-jsonl", type=pathlib.Path, required=True)
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--summary-md", type=pathlib.Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument(
        "--recover-database",
        type=pathlib.Path,
        help="read-only interrupted SQLite spool to validate and republish",
    )
    parser.add_argument("--expected-records", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.recover_database is not None:
        if args.limit is not None:
            raise ValueError("--limit is incompatible with --recover-database")
        manifest = recover_clause_bank(
            template_clusters.read_jsonl_gz(args.input),
            database_path=args.recover_database,
            output_jsonl=args.output_jsonl,
            manifest_output=args.manifest,
            summary_output=args.summary_md,
            input_path=args.input,
            expected_records=args.expected_records,
        )
    else:
        if args.expected_records is not None:
            raise ValueError("--expected-records requires --recover-database")
        manifest = build_clause_bank(
            template_clusters.read_jsonl_gz(args.input),
            output_jsonl=args.output_jsonl,
            manifest_output=args.manifest,
            summary_output=args.summary_md,
            input_path=args.input,
            limit=args.limit,
        )
    print(
        json.dumps(
            {
                "records": manifest["statistics"]["records"],
                "occurrences": manifest["statistics"]["occurrences"],
                "unique_clause_fingerprints": manifest["statistics"][
                    "unique_clause_fingerprints"
                ],
                "repeated_clause_fingerprints": manifest["statistics"][
                    "repeated_clause_fingerprints"
                ],
                "clauses_jsonl_sha256": manifest["outputs"]["clauses_jsonl"][
                    "sha256"
                ],
                "automatic_label_or_decision_propagation_allowed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "GROUPS",
    "MANIFEST_SCHEMA_VERSION",
    "SCHEMA_VERSION",
    "build_clause_bank",
    "canonical_json",
    "render_summary_markdown",
    "sha256_object",
]
