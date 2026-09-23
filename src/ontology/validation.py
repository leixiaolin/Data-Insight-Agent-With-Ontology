"""No-network RDF/XML inspection before any Owlready2 parsing."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

from src.config.settings import OntologyManagementConfig as Config
from .management_errors import ManagementError

RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
OWL = 'http://www.w3.org/2002/07/owl#'
XML = 'http://www.w3.org/XML/1998/namespace'
RDFS = 'http://www.w3.org/2000/01/rdf-schema#'


def inspect_documents(documents: dict[str, str], *, publication: bool = False) -> tuple[dict, dict[str, bytes]]:
    """Inspect all declarations and remove imports from parser input.

    Imports are resolved solely against this explicit set, then attached in the
    isolated runtime after every document has loaded. Never let a parser resolve
    user-supplied resource locations.
    """
    report: dict[str, Any] = {
        'scope': 'publication' if publication else 'file', 'valid': True,
        'ontology_iri': None, 'class_count': 0, 'property_count': 0,
        'individual_count': 0, 'errors': [], 'warnings': [],
        'checks': [{'name': 'physical_mapping', 'status': 'not_checked'},
                   {'name': 'logical_consistency', 'status': 'not_checked'}],
        'imports': {}, 'ontology_iris': {},
    }
    output: dict[str, bytes] = {}
    owners: dict[str, str] = {}
    definitions: dict[str, tuple[str, str]] = {}

    def issue(code: str, message: str, file: str, error: bool = True):
        report['errors' if error else 'warnings'].append(
            {'code': code, 'message': message, 'file': file, 'severity': 'error' if error else 'warning'})

    if len(documents) > Config.MAX_FILES or sum(len(s.encode('utf-8')) for s in documents.values()) > Config.MAX_TOTAL_BYTES:
        raise ManagementError('size_limit', 'Ontology collection exceeds configured limits', 413)
    if publication and not documents:
        raise ManagementError('empty_publication', 'Enable at least one ontology before activation', 409)
    for file, content in documents.items():
        if len(content.encode('utf-8')) > Config.MAX_FILE_BYTES:
            raise ManagementError('size_limit', 'Ontology file exceeds configured limit', 413)
        # Reject before XML parsing, including declarations in otherwise valid XML.
        if re.search(r'<!\s*(?:DOCTYPE|ENTITY)\b', content, re.I):
            issue('unsafe_xml', 'DTD and entity declarations are forbidden', file)
            continue
        try:
            root = ET.fromstring(content)
        except (ET.ParseError, ValueError):
            issue('invalid_xml', 'Expected well-formed UTF-8 RDF/XML', file)
            continue
        if root.tag != f'{{{RDF}}}RDF':
            issue('unsupported_format', 'Only RDF/XML documents are supported', file)
            continue
        base = root.get(f'{{{XML}}}base', '')
        declarations = root.findall(f'{{{OWL}}}Ontology')
        if len(declarations) != 1:
            issue('ontology_identity', 'Declare exactly one owl:Ontology with an absolute IRI', file)
            continue
        iri = urljoin(base, declarations[0].get(f'{{{RDF}}}about', ''))
        if not re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', iri):
            issue('ontology_identity', 'Ontology IRI must be absolute', file)
            continue
        if iri in owners:
            issue('duplicate_ontology', 'Multiple files declare the same ontology IRI', file)
        owners[iri] = file
        report['ontology_iris'][file] = iri
        report['ontology_iri'] = iri if len(documents) == 1 else None
        imports = []
        for parent in root.iter():
            for child in list(parent):
                if child.tag == f'{{{OWL}}}imports':
                    target = urljoin(base, child.get(f'{{{RDF}}}resource', ''))
                    imports.append(target)
                    parent.remove(child)
        report['imports'][file] = imports
        for node in root.iter():
            kind = node.tag.removeprefix(f'{{{OWL}}}')
            if kind not in {'Class', 'DatatypeProperty', 'ObjectProperty', 'AnnotationProperty', 'NamedIndividual'}:
                continue
            name = node.get(f'{{{RDF}}}about')
            if name is None and node.get(f'{{{RDF}}}ID'):
                name = '#' + node.get(f'{{{RDF}}}ID', '')
            if name is None:
                continue
            identity = urljoin(base or iri, name)
            # Empty nested references aren't definitions.
            if node not in list(root) and not len(node):
                continue
            if identity in definitions:
                old_file, old_kind = definitions[identity]
                if old_kind != kind:
                    issue('conflicting_entity_kind', 'An entity has incompatible declaration kinds', file)
                elif old_file != file:
                    issue('duplicate_entity', 'Entity is defined in multiple files', file, False)
            definitions[identity] = (file, kind)
            key = 'class_count' if kind == 'Class' else 'individual_count' if kind == 'NamedIndividual' else 'property_count'
            report[key] += 1
        output[file] = ET.tostring(root, encoding='utf-8', xml_declaration=True)
    for file, targets in report['imports'].items():
        for target in targets:
            if target not in owners:
                issue('missing_import', 'Import must resolve to an enabled file in this publication', file, publication)
    report['valid'] = not report['errors']
    report['checks'].append({'name': 'rdf_xml_and_dependencies', 'status': 'passed' if report['valid'] else 'failed'})
    return report, output


def decode_content(data: bytes) -> str:
    if len(data) > Config.MAX_FILE_BYTES:
        raise ManagementError('size_limit', 'Ontology file exceeds configured limit', 413)
    try:
        return data.decode('utf-8-sig', errors='strict')
    except UnicodeDecodeError as exc:
        raise ManagementError('invalid_encoding', 'Ontology must use UTF-8') from exc
