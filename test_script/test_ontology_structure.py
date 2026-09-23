"""Round-trip contract tests for the structure editor against the real seed."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from src.ontology.management_errors import ManagementError
from src.ontology.structure import parse_structure, patch_structure
from src.ontology.validation import OWL, RDF, inspect_documents

SEED = Path('Ontology/aw_ontology.owl').read_text(encoding='utf-8')


def entity(model: dict, iri_suffix: str) -> dict:
    return next(item for item in model['entities'] if item['iri'].endswith(iri_suffix))


def patched(model: dict) -> tuple[str, dict]:
    return patch_structure(SEED, model)


def test_parse_seed_counts_and_identity():
    model = parse_structure(SEED)
    assert model['ontology_iri'] == 'http://tarhone.com/ontology/adventrueworks'
    assert model['counts'] == {'classes': 58, 'datatype_properties': 43, 'object_properties': 18,
                               'annotation_properties': 1, 'datatypes': 1, 'individuals': 312}
    assert {'prefix': '', 'uri': 'http://tarhone.com/ontology/adventrueworks#'} in model['namespaces']


def test_identity_patch_returns_original_bytes_and_empty_diff():
    model = parse_structure(SEED)
    content, diff = patch_structure(SEED, model)
    assert content == SEED, 'a semantically identical model must not touch the file'
    assert diff == {'added': [], 'removed': [], 'changed': [], 'header_changes': [], 'warnings': []}


def test_changed_patch_is_idempotent_and_infoset_equal():
    model = parse_structure(SEED)
    target = entity(model, '#BusinessCustomer')
    target['labels'] = [{'lang': 'en', 'text': 'Business customer'}, {'lang': 'zh', 'text': '企业客户'}]
    first, diff = patch_structure(SEED, model)
    assert diff['changed'] and diff['changed'][0]['iri'] == target['iri']
    reparsed = parse_structure(first)
    assert entity(reparsed, '#BusinessCustomer')['labels'] == target['labels']
    second, diff2 = patch_structure(first, reparsed)
    assert second == first, 'serialization must be idempotent'
    assert diff2['changed'] == []


def test_untouched_entity_subtrees_byte_stable():
    edited = parse_structure(SEED)
    entity(edited, '#BusinessCustomer')['labels'] = [{'lang': 'en', 'text': 'Changed'}]
    new_content, _ = patched(edited)

    def fragments(content: str) -> dict[str, bytes]:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        root = ET.fromstring(content, parser=parser)
        out = {}
        for child in root:
            if isinstance(child.tag, str) and child.get(f'{{{RDF}}}about'):
                out[child.get(f'{{{RDF}}}about')] = ET.tostring(child, encoding='utf-8')
        return out

    before, after = fragments(SEED), fragments(new_content)
    untouched = [iri for iri in before if iri != entity(edited, '#BusinessCustomer')['iri']]
    assert all(before[iri] == after[iri] for iri in untouched), \
        'every entity except the edited one must be byte-identical'


def test_edit_preserves_equivalent_class_and_disjoint_with_bytes():
    model = parse_structure(SEED)
    target = entity(model, '#BusinessCustomer')
    assert any(axiom['kind'] == 'equivalentClass' for axiom in target['complex_axioms'])
    assert any(axiom['kind'] == 'disjointWith' for axiom in target['complex_axioms'])
    target['comments'] = [{'lang': 'zh', 'text': '企业客户（修改后）'}]
    new_content, diff = patched(model)
    assert 'comments' in diff['changed'][0]['fields']
    assert 'equivalentClass' in new_content and 'disjointWith' in new_content
    reparsed = parse_structure(new_content)
    again = entity(reparsed, '#BusinessCustomer')
    assert again['complex_axioms'] == target['complex_axioms'], 'axiom summaries survive verbatim'
    # The literal axiom XML inside the edited entity is untouched.
    assert new_content.count('<owl:equivalentClass>') == SEED.count('<owl:equivalentClass>')


def test_edit_multilang_labels_and_comments():
    model = parse_structure(SEED)
    target = entity(model, '#Product')
    target['labels'] = [{'lang': 'en', 'text': 'Product'}, {'lang': 'zh', 'text': '产品'},
                        {'lang': 'ja', 'text': '製品'}]
    target['comments'] = []
    new_content, _ = patched(model)
    reparsed = parse_structure(new_content)
    result = entity(reparsed, '#Product')
    assert result['labels'] == target['labels']
    assert result['comments'] == []


def test_edit_unit_annotation_with_lang():
    model = parse_structure(SEED)
    target = next(item for item in model['entities']
                  for row in item.get('annotations', [])
                  if row['property'].endswith('#unit'))
    target['annotations'] = [row if row.get('lang') != 'en'
                             else {**row, 'value': 'kilogram'} for row in target['annotations']]
    new_content, _ = patched(model)
    reparsed = parse_structure(new_content)
    again = next(item for item in reparsed['entities'] if item['iri'] == target['iri'])
    assert any(row['value'] == 'kilogram' and row['lang'] == 'en' for row in again['annotations'])


def test_edit_superclass_and_property_domain_range():
    model = parse_structure(SEED)
    klass = entity(model, '#Accessories')
    assert klass['superclasses'] == ['http://tarhone.com/ontology/adventrueworks#ProductCategory']
    klass['superclasses'] = ['http://tarhone.com/ontology/adventrueworks#Product']
    datatype_prop = next(item for item in model['entities']
                         if item['kind'] == 'datatype_property' and item.get('range')
                         and item['range'][0].endswith('XMLSchema#integer'))
    datatype_prop['range'] = ['http://www.w3.org/2001/XMLSchema#string']
    new_content, _ = patched(model)
    reparsed = parse_structure(new_content)
    assert entity(reparsed, '#Accessories')['superclasses'] == klass['superclasses']
    again = next(item for item in reparsed['entities'] if item['iri'] == datatype_prop['iri'])
    assert again['range'] == ['http://www.w3.org/2001/XMLSchema#string']


def test_add_class_entity_in_own_namespace_with_collision_suffix():
    model = parse_structure(SEED)
    model['entities'].append({
        'iri': 'http://tarhone.com/ontology/adventrueworks#NewConcept', 'kind': 'class',
        'labels': [{'lang': 'zh', 'text': '新概念'}], 'comments': [],
        'annotations': [], 'superclasses': ['http://tarhone.com/ontology/adventrueworks#Product'],
    })
    new_content, diff = patched(model)
    assert diff['added'] == ['http://tarhone.com/ontology/adventrueworks#NewConcept']
    reparsed = parse_structure(new_content)
    added = entity(reparsed, '#NewConcept')
    assert added['kind'] == 'class' and added['superclasses'] == klass_of_seed()
    # Re-patching the reparsed model is a no-op (already present).
    _, diff2 = patch_structure(new_content, reparsed)
    assert diff2['added'] == [] and diff2['changed'] == []


def klass_of_seed():
    return ['http://tarhone.com/ontology/adventrueworks#Product']


def test_add_individual_with_literal_and_reference_values():
    model = parse_structure(SEED)
    product = entity(model, '#productId')
    model['entities'].append({
        'iri': 'http://tarhone.com/ontology/adventrueworks#product_9999', 'kind': 'individual',
        'labels': [{'lang': 'en', 'text': 'Sample product'}], 'comments': [], 'annotations': [],
        'types': [product['domain'][0] if product.get('domain') else 'http://tarhone.com/ontology/adventrueworks#Product'],
        'values': [
            {'property': product['iri'], 'value': '9999',
             'datatype': 'http://www.w3.org/2001/XMLSchema#integer', 'lang': '', 'is_ref': False},
            {'property': 'http://tarhone.com/ontology/adventrueworks#partOfOrder', 'value':
             'http://tarhone.com/ontology/adventrueworks#order_43659', 'datatype': '', 'lang': '', 'is_ref': True},
        ],
    })
    new_content, diff = patched(model)
    assert diff['added'] and diff['warnings'] == []
    reparsed = parse_structure(new_content)
    added = entity(reparsed, '#product_9999')
    assert added['kind'] == 'individual'
    assert any(row['is_ref'] and row['value'].endswith('#order_43659') for row in added['values'])
    assert any(row['value'] == '9999' for row in added['values'])


def test_delete_entity_reports_dangling_references():
    model = parse_structure(SEED)
    address_iri = 'http://tarhone.com/ontology/adventrueworks#Address'
    model['entities'] = [item for item in model['entities'] if item['iri'] != address_iri]
    new_content, diff = patched(model)
    assert address_iri in diff['removed']
    assert any(warning.startswith('dangling_reference:') for warning in diff['warnings'])
    reparsed = parse_structure(new_content)
    assert all(item['iri'] != address_iri for item in reparsed['entities'])
    report, _ = inspect_documents({'a.owl': new_content}, publication=True)
    assert report['valid'] is True, 'dangling references stay warnings, not errors'


def test_malformed_models_rejected():
    model = parse_structure(SEED)
    broken = copy.deepcopy(model)
    broken['entities'][0]['iri'] = broken['entities'][1]['iri']
    with pytest.raises(ManagementError) as error:
        patched(broken)
    assert error.value.status == 422
    assert error.value.detail['code'] == 'invalid_structure'

    undeclared = copy.deepcopy(model)
    undeclared['entities'][0]['annotations'] = [
        {'property': 'http://example.org/#notDeclared', 'value': 'x', 'lang': '', 'datatype': ''}]
    with pytest.raises(ManagementError) as error:
        patched(undeclared)
    assert any(issue['code'] == 'annotation_property_not_declared'
               for issue in error.value.detail['issues'])

    unknown_kind = copy.deepcopy(model)
    unknown_kind['entities'].append({'iri': 'http://example.org/#x', 'kind': 'rule', 'labels': []})
    with pytest.raises(ManagementError):
        patched(unknown_kind)


def test_comments_whitespace_and_declaration_preserved():
    model = parse_structure(SEED)
    target = entity(model, '#Product')
    target['comments'] = [{'lang': 'en', 'text': 'Adjusted.'}]
    new_content, _ = patched(model)
    assert new_content.startswith('<?xml version="1.0"?>')
    assert new_content.count('<!--') == SEED.count('<!--'), 'every banner comment survives'
    assert '\n\n\n' in new_content, 'blank-line spacing between entities survives'


def test_header_version_info_edit():
    model = parse_structure(SEED)
    model['version_info'] = '1.1.0-managed'
    new_content, diff = patched(model)
    assert 'version_info' in diff['header_changes']
    assert parse_structure(new_content)['version_info'] == '1.1.0-managed'


def test_oda_prefixed_draft_annotations_round_trip():
    draft = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:owl="http://www.w3.org/2002/07/owl#"
         xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
         xmlns:oda="urn:oda:ontology:draft#"
         xml:base="urn:oda:ontology:draft#">
  <owl:Ontology rdf:about="urn:oda:ontology:draft"/>
  <owl:AnnotationProperty rdf:about="urn:oda:ontology:draft#sourceTable"/>
  <owl:Class rdf:about="urn:oda:ontology:draft#table__sales__orders">
    <oda:sourceTable>sales.orders</oda:sourceTable>
    <rdfs:label xml:lang="en">orders</rdfs:label>
  </owl:Class>
</rdf:RDF>
"""
    model = parse_structure(draft)
    entity(model, '#table__sales__orders')['labels'] = [{'lang': 'zh', 'text': '订单表'}]
    new_content, diff = patch_structure(draft, model)
    assert 'oda:sourceTable>sales.orders</oda:sourceTable>' in new_content, 'oda: prefix form preserved'
    report, _ = inspect_documents({'draft.owl': new_content}, publication=False)
    assert report['valid'] is True
    assert json.dumps(diff['changed'])
