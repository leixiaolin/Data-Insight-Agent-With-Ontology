"""Layered RDF/XML inspection tests (PRD FR-7, AC-13/AC-14)."""
from __future__ import annotations

import pytest

from src.config.settings import OntologyManagementConfig as Config
from src.ontology.management_errors import ManagementError
from src.ontology.validation import decode_content, inspect_documents

RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
OWL = 'http://www.w3.org/2002/07/owl#'


def document(iri: str, body: str = '', *, base: str | None = None, imports: list[str] | None = None) -> str:
    base_attr = f' xml:base="{base}"' if base else ''
    import_xml = ''.join(f'<owl:imports rdf:resource="{target}"/>' for target in imports or [])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="{RDF}" xmlns:owl="{OWL}"{base_attr}>
  <owl:Ontology rdf:about="{iri}">{import_xml}</owl:Ontology>
  {body}
</rdf:RDF>"""


CLASS = '<owl:Class rdf:about="http://example.org/o#C"/>'
PROP = '<owl:DatatypeProperty rdf:about="http://example.org/o#P"/>'


def codes(report: dict, severity: str = 'errors') -> list[str]:
    return [issue['code'] for issue in report[severity]]


def test_valid_document_counts_entities():
    report, output = inspect_documents({'a.owl': document('http://example.org/o', CLASS + PROP)})
    assert report['valid'] is True
    assert report['ontology_iri'] == 'http://example.org/o'
    assert (report['class_count'], report['property_count']) == (1, 1)
    assert b'RDF' in output['a.owl']


def test_dtd_and_entity_declarations_rejected():
    for payload in (document('http://example.org/o') + '<!DOCTYPE rdf:RDF>',
                    document('http://example.org/o').replace('<rdf:RDF',
                    '<!ENTITY xxe SYSTEM "file:///etc/passwd"><rdf:RDF')):
        report, _ = inspect_documents({'a.owl': payload})
        assert report['valid'] is False
        assert 'unsafe_xml' in codes(report)


def test_non_rdf_root_is_unsupported_format():
    report, _ = inspect_documents({'a.owl': '<?xml version="1.0"?><root/>',})
    assert 'unsupported_format' in codes(report)


def test_malformed_xml_rejected():
    report, _ = inspect_documents({'a.owl': '<?xml version="1.0"?><rdf:RDF><unclosed>'})
    assert 'invalid_xml' in codes(report)


def test_relative_ontology_iri_rejected():
    report, _ = inspect_documents({'a.owl': document('relative-name')})
    assert 'ontology_identity' in codes(report)


def test_duplicate_ontology_iri_is_error_across_files():
    report, _ = inspect_documents({'a.owl': document('http://example.org/dup'),
                                   'b.owl': document('http://example.org/dup')})
    assert report['valid'] is False
    assert 'duplicate_ontology' in codes(report)


def test_missing_import_is_warning_in_file_scope_and_error_in_publication():
    doc = document('http://example.org/a', imports=['http://example.org/missing'])
    file_report, _ = inspect_documents({'a.owl': doc})
    assert file_report['valid'] is True
    assert 'missing_import' in codes(file_report, 'warnings')
    pub_report, _ = inspect_documents({'a.owl': doc}, publication=True)
    assert pub_report['valid'] is False
    assert 'missing_import' in codes(pub_report)


def test_imports_resolve_within_the_enabled_set_only():
    docs = {'a.owl': document('http://example.org/a', imports=['http://example.org/b']),
            'b.owl': document('http://example.org/b')}
    report, sanitized = inspect_documents(docs, publication=True)
    assert report['valid'] is True
    assert b'imports' not in sanitized['a.owl'], 'imports must be stripped from parser input'


def test_conflicting_entity_kind_is_error_shared_reference_is_not():
    reference = '<owl:Restriction><owl:onProperty><owl:ObjectProperty rdf:about="http://example.org/o#P"/></owl:onProperty></owl:Restriction>'
    docs = {'a.owl': document('http://example.org/a', PROP),
            'b.owl': document('http://example.org/b', reference)}
    report, _ = inspect_documents(docs)
    assert 'conflicting_entity_kind' not in codes(report)
    assert 'duplicate_entity' not in codes(report)
    same_entity_as_property = '<owl:DatatypeProperty rdf:about="http://example.org/o#C"/>'
    conflict = {'a.owl': document('http://example.org/a', CLASS),
                'b.owl': document('http://example.org/b', same_entity_as_property)}
    report, _ = inspect_documents(conflict)
    assert 'conflicting_entity_kind' in codes(report)


def test_cross_file_duplicate_definition_is_warning():
    docs = {'a.owl': document('http://example.org/a', CLASS),
            'b.owl': document('http://example.org/b', CLASS)}
    report, _ = inspect_documents(docs)
    assert report['valid'] is True
    assert 'duplicate_entity' in codes(report, 'warnings')


def test_substituted_content_does_not_conflict_with_itself():
    old = document('http://example.org/a', CLASS)
    new = document('http://example.org/a', CLASS)
    report, _ = inspect_documents({'a.owl': new})
    assert report['valid'] is True
    assert codes(report) == [] and codes(report, 'warnings') == []


def test_publication_scope_requires_documents():
    with pytest.raises(ManagementError) as error:
        inspect_documents({}, publication=True)
    assert error.value.status == 409


def test_decode_content_rejects_non_utf8_and_oversize(monkeypatch):
    with pytest.raises(ManagementError) as error:
        decode_content(b'\xff\xfe\x00')
    assert error.value.status == 422
    monkeypatch.setattr(Config, 'MAX_FILE_BYTES', 10)
    with pytest.raises(ManagementError) as error:
        decode_content(b'x' * 11)
    assert error.value.status == 413


def test_bom_prefixed_utf8_is_accepted():
    content = decode_content(b'\xef\xbb\xbf' + document('http://example.org/a').encode())
    assert content.startswith('<?xml')


def test_collection_limits_enforced(monkeypatch):
    monkeypatch.setattr(Config, 'MAX_TOTAL_BYTES', 50)
    with pytest.raises(ManagementError) as error:
        inspect_documents({'a.owl': document('http://example.org/a')})
    assert error.value.status == 413
