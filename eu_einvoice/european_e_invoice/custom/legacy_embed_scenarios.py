# Copyright (c) 2026, ALYF GmbH and Contributors
# See license.txt

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

import yaml

_SCENARIOS_PATH = Path(__file__).with_name("legacy_embed_scenarios.yaml")


@dataclass(frozen=True)
class MockFileSpec:
	name: str
	file_url: str
	is_remote: bool
	content: bytes = b""


@dataclass(frozen=True)
class EmbedAttachmentExpectations:
	reference_count: int
	type_code: str | None = None
	issuer_assigned_id: str | None = None
	has_attached_object: bool | None = None
	filename: str | None = None
	mime_type: str | None = None
	uri_id: str | None = None


@dataclass(frozen=True)
class EmbedAttachmentScenario:
	id: str
	field_url: str
	expect: EmbedAttachmentExpectations
	mock_file: MockFileSpec | None = None


def load_embed_attachment_scenarios() -> list[EmbedAttachmentScenario]:
	raw = yaml.safe_load(_SCENARIOS_PATH.read_text(encoding="utf-8"))
	scenarios: list[EmbedAttachmentScenario] = []

	for row in raw:
		mock_row = row.get("mock_file")
		mock_file = None
		if mock_row:
			content = b""
			if content_b64 := mock_row.get("content_bytes_b64"):
				content = base64.b64decode(content_b64)
			mock_file = MockFileSpec(
				name=mock_row["name"],
				file_url=mock_row["file_url"],
				is_remote=bool(mock_row["is_remote"]),
				content=content,
			)

		expect_row = row["expect"]
		scenarios.append(
			EmbedAttachmentScenario(
				id=row["id"],
				field_url=row["field_url"],
				mock_file=mock_file,
				expect=EmbedAttachmentExpectations(
					reference_count=expect_row["reference_count"],
					type_code=expect_row.get("type_code"),
					issuer_assigned_id=expect_row.get("issuer_assigned_id"),
					has_attached_object=expect_row.get("has_attached_object"),
					filename=expect_row.get("filename"),
					mime_type=expect_row.get("mime_type"),
					uri_id=expect_row.get("uri_id"),
				),
			)
		)

	return scenarios
