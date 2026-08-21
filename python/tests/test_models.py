# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from ossie import (
    OSIDataType,
    OSIDimension,
    OSIDocument,
    OSIExpression,
    OSIFileSource,
    OSIField,
)


def _expression_data(value: str = "value") -> dict:
    return {"dialects": [{"dialect": "ANSI_SQL", "expression": value}]}


def _expression(value: str = "value") -> OSIExpression:
    return OSIExpression.model_validate(_expression_data(value))


def _document() -> dict:
    return {
        "version": "0.2.0.dev0",
        "semantic_model": [
            {
                "name": "typed_model",
                "datasets": [
                    {
                        "name": "events",
                        "source": "catalog.schema.events",
                        "fields": [
                            {
                                "name": "occurred_at",
                                "expression": _expression_data("occurred_at"),
                                "dimension": {},
                                "datatype": "DateTimeTz",
                            }
                        ],
                    }
                ],
                "metrics": [
                    {
                        "name": "revenue",
                        "expression": _expression_data("SUM(events.revenue)"),
                        "datatype": "Decimal",
                    }
                ],
            }
        ],
    }


def test_data_type_enum_matches_core_schema() -> None:
    schema_path = Path(__file__).parents[2] / "core-spec" / "osi-schema.json"
    schema = json.loads(schema_path.read_text())

    assert [member.value for member in OSIDataType] == schema["$defs"]["DataType"][
        "enum"
    ]
    assert schema["$defs"]["Field"]["properties"]["datatype"] == {
        "$ref": "#/$defs/DataType"
    }
    assert schema["$defs"]["Metric"]["properties"]["datatype"] == {
        "$ref": "#/$defs/DataType"
    }


def test_field_and_metric_datatypes_survive_serialization() -> None:
    document = OSIDocument.model_validate(_document())

    field = document.semantic_model[0].datasets[0].fields[0]
    metric = document.semantic_model[0].metrics[0]
    assert field.datatype is OSIDataType.DATE_TIME_TZ
    assert metric.datatype is OSIDataType.DECIMAL

    as_json = json.loads(document.to_osi_json())
    as_yaml = yaml.safe_load(document.to_osi_yaml())
    for serialized in (as_json, as_yaml):
        model = serialized["semantic_model"][0]
        assert model["datasets"][0]["fields"][0]["datatype"] == "DateTimeTz"
        assert model["metrics"][0]["datatype"] == "Decimal"


def test_invalid_datatype_is_rejected() -> None:
    document = _document()
    field = document["semantic_model"][0]["datasets"][0]["fields"][0]
    field["datatype"] = "timestamp"

    with pytest.raises(ValidationError):
        OSIDocument.model_validate(document)


@pytest.mark.parametrize(
    ("dimension", "datatype", "expected"),
    [
        (None, OSIDataType.DATE, False),
        (OSIDimension(), OSIDataType.DATE, True),
        (OSIDimension(is_time=False), OSIDataType.DATE_TIME_TZ, False),
        (OSIDimension(is_time=True), OSIDataType.STRING, True),
        (OSIDimension(), OSIDataType.STRING, False),
        (OSIDimension(), None, False),
    ],
)
def test_effective_time_dimension_role(
    dimension: OSIDimension | None,
    datatype: OSIDataType | None,
    expected: bool,
) -> None:
    field = OSIField(
        name="value",
        expression=_expression(),
        dimension=dimension,
        datatype=datatype,
    )

    assert field.is_time_dimension() is expected


def test_legacy_dataset_source_remains_a_string() -> None:
    document = OSIDocument.model_validate(_document())
    assert document.semantic_model[0].datasets[0].source == "catalog.schema.events"


def test_file_dataset_source_survives_serialization() -> None:
    data = _document()
    data["semantic_model"][0]["datasets"][0]["source"] = {
        "kind": "file",
        "format": "parquet",
        "locations": ["s3://analytics/events/*.parquet"],
    }

    document = OSIDocument.model_validate(data)
    source = document.semantic_model[0].datasets[0].source
    assert isinstance(source, OSIFileSource)
    assert source.kind == "file"
    assert source.format == "parquet"
    assert source.locations == ["s3://analytics/events/*.parquet"]

    for serialized in (json.loads(document.to_osi_json()), yaml.safe_load(document.to_osi_yaml())):
        source_data = serialized["semantic_model"][0]["datasets"][0]["source"]
        assert source_data == {
            "kind": "file",
            "format": "parquet",
            "locations": ["s3://analytics/events/*.parquet"],
        }


def test_file_dataset_source_requires_at_least_one_location() -> None:
    data = _document()
    data["semantic_model"][0]["datasets"][0]["source"] = {
        "kind": "file",
        "format": "parquet",
        "locations": [],
    }

    with pytest.raises(ValidationError):
        OSIDocument.model_validate(data)


def test_file_source_definition_matches_core_schema() -> None:
    schema_path = Path(__file__).parents[2] / "core-spec" / "osi-schema.json"
    schema = json.loads(schema_path.read_text())

    assert schema["$defs"]["Source"]["oneOf"] == [
        {"type": "string"},
        {"$ref": "#/$defs/FileSource"},
    ]
    assert schema["$defs"]["FileSource"]["required"] == [
        "kind",
        "format",
        "locations",
    ]


@pytest.mark.parametrize(
    "source",
    [
        {"kind": "file", "format": "", "locations": ["s3://bucket/events.parquet"]},
        {"kind": "file", "format": "parquet", "locations": [""]},
        {"kind": "table", "format": "parquet", "locations": ["s3://bucket/events.parquet"]},
        {
            "kind": "file",
            "format": "parquet",
            "locations": ["s3://bucket/events.parquet"],
            "unknown": True,
        },
    ],
)
def test_invalid_file_dataset_source_is_rejected(source: dict) -> None:
    data = _document()
    data["semantic_model"][0]["datasets"][0]["source"] = source

    with pytest.raises(ValidationError):
        OSIDocument.model_validate(data)
