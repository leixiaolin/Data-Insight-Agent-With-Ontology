"""Deterministic MySQL schema → ontology draft generation (PRD FR-10/FR-11).

Reads only tables, columns, comments and foreign keys inside the active MySQL
allowlist — never business rows. The result is an in-memory RDF/XML draft that
must pass the same inspection as any upload; it is never auto-saved, enabled or
activated.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

from src.config.settings import DataSourceConfig, MySQLConfig, OntologyManagementConfig as Config
from src.data_sources import get_active_metadata_provider
from .management_errors import ManagementError
from .validation import OWL, RDF, RDFS, inspect_documents

XML_NAMESPACE = 'http://www.w3.org/XML/1998/namespace'
DRAFT_MARKER = 'SCHEMA DRAFT — requires human review'
XSD_NS = 'http://www.w3.org/2001/XMLSchema#'

_STRING_LIKE = {'char', 'varchar', 'tinytext', 'text', 'mediumtext', 'longtext'}
_INT_LIKE = {'tinyint', 'smallint', 'mediumint', 'int', 'integer', 'bigint'}
_BINARY_LIKE = {'binary', 'varbinary', 'tinyblob', 'blob', 'mediumblob', 'longblob'}


def _xsd_type(raw_type: str) -> tuple[str, list[str]]:
    """Map a raw MySQL COLUMN_TYPE onto an OWL 2 datatype with honest warnings."""
    normalized = raw_type.strip().lower()
    base = re.split(r'[\s(]', normalized, maxsplit=1)[0]
    warnings: list[str] = []
    if base in _INT_LIKE:
        result = f'{XSD_NS}integer'
    elif base in ('decimal', 'numeric'):
        result = f'{XSD_NS}decimal'
    elif base in ('float', 'double', 'real'):
        result = f'{XSD_NS}double'
    elif base in _STRING_LIKE:
        result = f'{XSD_NS}string'
    elif base == 'date':
        result = f'{XSD_NS}date'
    elif base == 'datetime':
        result = f'{XSD_NS}dateTime'
    elif base == 'timestamp':
        # A MySQL timestamp carries a session timezone; do not infer one.
        result = f'{XSD_NS}dateTime'
        warnings.append('timestamp mapped to xsd:dateTime without timezone inference')
    elif base == 'time':
        result = f'{XSD_NS}string'
        warnings.append(f'MySQL time is not an intra-day value; {raw_type} kept as string')
    elif base in ('enum', 'set', 'json'):
        result = f'{XSD_NS}string'
        warnings.append(f'{base} has no direct OWL datatype; original type kept as annotation')
    elif base in _BINARY_LIKE:
        result = f'{XSD_NS}base64Binary'
    else:
        result = f'{XSD_NS}string'
        warnings.append(f'unknown type {raw_type!r} degraded to string')
    # unsigned and precision stay recorded via the raw sourceType annotation.
    return result, warnings


def generator_source_revision() -> str:
    """Digest of the non-secret active MySQL identity (change detection)."""
    payload = {
        'type': DataSourceConfig.TYPE,
        'host': MySQLConfig.HOST,
        'port': MySQLConfig.PORT,
        'user': MySQLConfig.USER,
        'databases': list(MySQLConfig.DATABASES),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode('utf-8')).hexdigest()


def _provider():
    if DataSourceConfig.TYPE != 'mysql':
        raise ManagementError(
            'unsupported_source',
            'Schema drafts are generated from MySQL only; switch the active data source to MySQL first', 422)
    return get_active_metadata_provider()


def list_generator_tables() -> dict[str, Any]:
    """Allowlist tables with support flags, plus the source identity revision."""
    revision = generator_source_revision()
    provider = _provider()
    tables: list[dict[str, Any]] = []
    for schema in provider.list_schemas():
        for table in provider.list_tables(schema=schema):
            base_table = table['table_type'] == 'BASE TABLE'
            tables.append({
                'full_name': table['full_name'],
                'table_type': table['table_type'],
                'comment': table['comment'],
                'supported': base_table,
            })
    tables.sort(key=lambda item: item['full_name'].casefold())
    if generator_source_revision() != revision:
        raise ManagementError('source_changed', 'The data source changed; refresh the table list', 409)
    return {
        'tables': tables,
        'source_revision': revision,
        'source_identity': {
            'type': 'mysql',
            'host': MySQLConfig.HOST,
            'databases': list(MySQLConfig.DATABASES),
        },
        'limits': {'max_tables': Config.MAX_TABLES, 'max_columns': Config.MAX_COLUMNS},
    }


def _safe_identifier(value: str, label: str) -> str:
    if not value or len(value) > 128:
        raise ManagementError('invalid_selection', f'Invalid {label}: {value!r}', 422)
    return value


def _split_full_name(full_name: str) -> tuple[str, str]:
    parts = full_name.split('.')
    if len(parts) != 2 or not all(parts):
        raise ManagementError('invalid_selection',
                               f'Use database.table names, got {full_name!r}', 422)
    return _safe_identifier(parts[0], 'database'), _safe_identifier(parts[1], 'table')


def _unique_iri(taken: dict[str, str], candidate: str, logical: str) -> tuple[str, bool]:
    """Return the IRI to use and whether an encoding collision was adjusted."""
    if candidate not in taken:
        taken[candidate] = logical
        return candidate, False
    suffix = 2
    while f'{candidate}__{suffix}' in taken:
        suffix += 1
    adjusted = f'{candidate}__{suffix}'
    taken[adjusted] = logical
    return adjusted, True


def generate_draft(
    tables: list[str],
    *,
    namespace: str | None = None,
    source_revision: str | None = None,
    existing_ontology_iris: set[str] | None = None,
) -> dict[str, Any]:
    """Build an in-memory RDF/XML draft from the selected allowlist tables."""
    if source_revision is None:
        raise ManagementError('precondition_required', 'Supply the source revision you reviewed', 428)
    current = generator_source_revision()
    if source_revision != current:
        raise ManagementError('source_changed',
                              'The MySQL connection has changed; regenerate the draft',
                              409, current_source_revision=current)
    if not isinstance(tables, list) or not tables:
        raise ManagementError('invalid_selection', 'Select at least one table', 422)
    if len(tables) > Config.MAX_TABLES:
        raise ManagementError('size_limit',
                               f'Select at most {Config.MAX_TABLES} tables', 413)
    if len(set(tables)) != len(tables):
        raise ManagementError('invalid_selection', 'Duplicate table selections', 422)

    provider = _provider()
    allowlist = set(provider.list_schemas())
    catalog: dict[str, dict[str, Any]] = {}
    for selection in tables:
        schema, table = _split_full_name(selection)
        if schema not in allowlist:
            raise ManagementError('invalid_selection',
                                  f'{schema} is outside the MySQL allowlist', 422)
        if selection in catalog:
            continue
        details = provider.get_table(catalog='', schema=schema, table=table)
        if details is None:
            raise ManagementError('invalid_selection', f'Table not found: {selection}', 422)
        if details['table_type'] != 'BASE TABLE':
            raise ManagementError('invalid_selection',
                                  f'{selection} is a view; views are not supported', 422)
        catalog[selection] = details

    column_count = sum(len(details['columns']) for details in catalog.values())
    if column_count > Config.MAX_COLUMNS:
        raise ManagementError('size_limit',
                              f'Selection exceeds the {Config.MAX_COLUMNS} column limit '
                              f'({column_count} columns)', 413)

    if namespace:
        if not re.match(r'^[A-Za-z][A-Za-z0-9+.\-]*:\S*$', namespace) \
                or not namespace.endswith(('#', '/')):
            raise ManagementError('invalid_namespace',
                                  'Namespace must be an absolute IRI ending with # or /', 422)
        chosen = namespace
    else:
        chosen = f'urn:oda:ontology:{uuid.uuid4()}#'
    ontology_iri = chosen[:-1] if chosen.endswith('#') else chosen
    if existing_ontology_iris and ontology_iri in existing_ontology_iris:
        raise ManagementError('invalid_namespace',
                              'Namespace is already used by an existing ontology', 409)

    # ── IRIs: physical scope in the IRI itself, originals kept as labels ──────
    taken: dict[str, str] = {}
    adjusted = 0
    warnings: list[str] = []
    table_iris: dict[str, str] = {}
    column_iris: dict[tuple[str, str], str] = {}
    for full_name in sorted(catalog):
        schema, table = full_name.split('.', 1)
        iri, collided = _unique_iri(
            taken, f"{chosen}table__{quote(schema, safe='')}__{quote(table, safe='')}", full_name)
        adjusted += collided
        table_iris[full_name] = iri
        for column in catalog[full_name]['columns']:
            column_iri, collided = _unique_iri(
                taken,
                f"{chosen}column__{quote(schema, safe='')}__{quote(table, safe='')}"
                f"__{quote(column['name'], safe='')}",
                f'{full_name}.{column["name"]}')
            adjusted += collided
            column_iris[(full_name, column['name'])] = column_iri

    # ── Foreign keys, restricted to selected source and target tables ────────
    missing_relations: list[dict[str, str]] = []
    foreign_keys: list[dict[str, Any]] = []
    for schema in sorted({full_name.split('.', 1)[0] for full_name in catalog}):
        for constraint in provider.list_foreign_keys(schema=schema):
            source_full = f"{schema}.{constraint['table']}"
            target_full = f"{constraint.get('referenced_schema', schema)}.{constraint['referenced_table']}"
            if source_full not in catalog:
                continue
            if target_full not in catalog:
                missing_relations.append({
                    'constraint': constraint['constraint'],
                    'from': source_full,
                    'to': target_full,
                })
                continue
            iri, collided = _unique_iri(
                taken,
                f"{chosen}fk__{quote(schema, safe='')}__{quote(constraint['constraint'], safe='')}",
                f"{schema}.{constraint['constraint']}")
            adjusted += collided
            foreign_keys.append({**constraint, 'iri': iri,
                                 'source_full': source_full, 'target_full': target_full})
    if missing_relations:
        warnings.append(
            f'{len(missing_relations)} foreign key(s) skipped because the target table is not selected')

    # ── Type mapping with degradation warnings ───────────────────────────────
    mapped: dict[str, str] = {}
    for full_name, details in catalog.items():
        for column in details['columns']:
            xsd, type_warnings = _xsd_type(column['type'])
            mapped[(full_name, column['name'])] = xsd
            warnings.extend(f'{full_name}.{column["name"]}: {warning}'
                            for warning in type_warnings)

    # ── RDF/XML serialization ────────────────────────────────────────────────
    # Owlready2 ignores xml:base for unprefixed property elements and relative
    # about values, so annotation properties are declared with absolute IRIs and
    # used with an explicit draft prefix.
    ET.register_namespace('rdf', RDF)
    ET.register_namespace('rdfs', RDFS)
    ET.register_namespace('owl', OWL)
    ET.register_namespace('xsd', XSD_NS)
    ET.register_namespace('oda', chosen)
    root = ET.Element(f'{{{RDF}}}RDF', {f'{{{XML_NAMESPACE}}}base': chosen})

    def annotation(parent: ET.Element, name: str, value: str):
        ET.SubElement(parent, f'{{{chosen}}}{name}').text = value

    ontology = ET.SubElement(root, f'{{{OWL}}}Ontology', {f'{{{RDF}}}about': ontology_iri})
    ET.SubElement(ontology, f'{{{RDFS}}}comment').text = (
        f'{DRAFT_MARKER}. Generated from MySQL information_schema; verify names, '
        'meanings and mappings before activating.')
    annotation(ontology, 'draftSource',
               f"mysql://{MySQLConfig.HOST}:{MySQLConfig.PORT}/"
               f"{','.join(MySQLConfig.DATABASES)}")
    annotation(ontology, 'generatedAt', datetime.now(timezone.utc).isoformat())
    for full_name in sorted(catalog):
        annotation(ontology, 'selectedTables', full_name)

    for name in ('sourceTable', 'sourceColumn', 'sourceType', 'sourceColumnPair',
                 'draftSource', 'generatedAt', 'selectedTables'):
        ET.SubElement(root, f'{{{OWL}}}AnnotationProperty',
                      {f'{{{RDF}}}about': f'{chosen}{name}'})

    for full_name in sorted(catalog):
        details = catalog[full_name]
        node = ET.SubElement(root, f'{{{OWL}}}Class', {f'{{{RDF}}}about': table_iris[full_name]})
        ET.SubElement(node, f'{{{RDFS}}}label').text = details['name']
        if details['comment']:
            ET.SubElement(node, f'{{{RDFS}}}comment').text = details['comment']
        annotation(node, 'sourceTable', full_name)
        for column in details['columns']:
            prop = ET.SubElement(root, f'{{{OWL}}}DatatypeProperty',
                                 {f'{{{RDF}}}about': column_iris[(full_name, column['name'])]})
            ET.SubElement(prop, f'{{{RDFS}}}domain',
                          {f'{{{RDF}}}resource': table_iris[full_name]})
            ET.SubElement(prop, f'{{{RDFS}}}range', {f'{{{RDF}}}resource': mapped[(full_name, column['name'])]})
            ET.SubElement(prop, f'{{{RDFS}}}label').text = column['name']
            if column['comment']:
                ET.SubElement(prop, f'{{{RDFS}}}comment').text = column['comment']
            annotation(prop, 'sourceTable', full_name)
            annotation(prop, 'sourceColumn', column['name'])
            annotation(prop, 'sourceType', column['type'])

    for foreign_key in foreign_keys:
        node = ET.SubElement(root, f'{{{OWL}}}ObjectProperty',
                             {f'{{{RDF}}}about': foreign_key['iri']})
        ET.SubElement(node, f'{{{RDFS}}}domain',
                      {f'{{{RDF}}}resource': table_iris[foreign_key['source_full']]})
        ET.SubElement(node, f'{{{RDFS}}}range',
                      {f'{{{RDF}}}resource': table_iris[foreign_key['target_full']]})
        ET.SubElement(node, f'{{{RDFS}}}label').text = foreign_key['constraint']
        ET.SubElement(node, f'{{{RDFS}}}comment').text = (
            f"Foreign key {foreign_key['source_full']} → {foreign_key['target_full']}")
        for ordinal, (source_column, target_column) in enumerate(foreign_key['column_pairs'], start=1):
            annotation(node, 'sourceColumnPair',
                       f"{ordinal}|{foreign_key['source_full']}.{source_column}"
                       f"|{foreign_key['target_full']}.{target_column}")

    ET.indent(root, space='  ')
    content = ET.tostring(root, encoding='unicode', xml_declaration=False)
    content = f"<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n{content}"

    # Self-check: the draft must satisfy the same inspection as any upload.
    report, _ = inspect_documents({'draft.owl': content}, publication=False)
    if not report['valid']:
        raise ManagementError('generation_failed',
                              'Generated draft failed self-inspection', 422, issues=report)

    if generator_source_revision() != current:
        raise ManagementError('source_changed', 'The data source changed; regenerate the draft', 409)
    return {
        'content': content,
        'source_revision': current,
        'filename_hint': 'mysql-schema-draft.owl',
        'namespace': chosen,
        'report': {
            'table_count': len(catalog),
            'column_count': column_count,
            'adjusted_iri_count': adjusted,
            'missing_relations': missing_relations,
            'warnings': warnings,
        },
    }
