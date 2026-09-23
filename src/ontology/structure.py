"""Structured RDF/XML view/edit model with surgical in-place patching.

The stored document is parsed into a JSON entity model (classes, properties,
individuals, annotations) mapped back to its original ElementTree elements.
Saving patches only the edited simple fields in place — complex axioms
(equivalentClass, restrictions, disjointWith, …), comments, and whitespace of
untouched entities are preserved byte-for-byte. No whole-document resynthesis.
"""
from __future__ import annotations

import copy
import io
import re
import threading
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

from .management_errors import ManagementError
from .validation import OWL, RDF, RDFS

XML_NS = 'http://www.w3.org/XML/1998/namespace'
XSD = 'http://www.w3.org/2001/XMLSchema#'

_KIND_BY_TAG = {
    f'{{{OWL}}}Class': 'class',
    f'{{{OWL}}}DatatypeProperty': 'datatype_property',
    f'{{{OWL}}}ObjectProperty': 'object_property',
    f'{{{OWL}}}AnnotationProperty': 'annotation_property',
    f'{{{RDFS}}}Datatype': 'datatype',
    f'{{{OWL}}}NamedIndividual': 'individual',
}
_TAG_BY_KIND = {kind: tag for tag, kind in _KIND_BY_TAG.items()}
_KIND_LABELS = {
    'class': '类', 'datatype_property': '数据属性', 'object_property': '对象属性',
    'annotation_property': '注解属性', 'datatype': '数据类型', 'individual': '个体',
}
_CHARACTERISTICS = {
    f'{{{OWL}}}FunctionalProperty': 'functional',
    f'{{{OWL}}}InverseFunctionalProperty': 'inverse_functional',
    f'{{{OWL}}}TransitiveProperty': 'transitive',
    f'{{{OWL}}}SymmetricProperty': 'symmetric',
}
_CHARACTERISTIC_TAG = {value: key for key, value in _CHARACTERISTICS.items()}
_COMPLEX_KINDS = {f'{{{OWL}}}equivalentClass', f'{{{OWL}}}disjointWith',
                  f'{{{OWL}}}inverseOf', f'{{{OWL}}}unionOf', f'{{{OWL}}}intersectionOf'}
_DECLARATION_MATCHED = re.compile(r'^\s*<\?xml[^?]*\?>')

_NS_LOCK = threading.RLock()


def _parse_tree(content: str) -> ET.Element:
    """Comment- and PI-preserving parse; plain error surfaces as 422."""
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    try:
        return ET.fromstring(content, parser=parser)
    except (ET.ParseError, ValueError) as exc:
        raise ManagementError('invalid_xml', f'Expected well-formed RDF/XML: {exc}', 422) from exc


def _capture_namespaces(content: str) -> list[tuple[str, str]]:
    """The document's own (prefix, uri) map, default namespace as ''. """
    captured: list[tuple[str, str]] = []
    try:
        for _event, element in ET.iterparse(io.StringIO(content), events=['start-ns']):
            captured.append((element[0] or '', element[1]))
    except ET.ParseError:
        pass
    seen = set()
    return [pair for pair in captured if not (pair in seen or seen.add(pair))]


def _resolve(base: str, value: str) -> str:
    return urljoin(base, value) if value else value


def _tag_iri(tag: str, base: str, known: set[str] | None = None) -> str:
    """Resolve a property element name to its absolute IRI.

    ElementTree reports namespaced tags in Clark form '{ns}local'; the default
    namespace makes unprefixed usage (Protégé's <unit>) Clark names too. RDF/XML
    also resolves genuinely unprefixed names against xml:base. Prefer whichever
    candidate matches the document's declared property set.
    """
    if tag.startswith('{'):
        namespace, local = tag[1:].split('}', 1)
        return namespace + local
    direct = urljoin(base, tag)
    if known is not None and direct not in known:
        fragment = base + ('' if base.endswith(('#', '/')) else '#') + tag
        if fragment in known:
            return fragment
    return direct


def _is_absolute(value: str) -> bool:
    return bool(re.match(r'^[A-Za-z][A-Za-z0-9+.\-]*:\S+$', value or ''))


def _local_name(iri: str) -> str:
    tail = iri.rsplit('#', 1)[-1].rsplit('/', 1)[-1]
    return tail or iri


def _lang_text(element: ET.Element) -> dict:
    return {'lang': element.get(f'{{{XML_NS}}}lang', ''), 'text': (element.text or '').strip()}


def _annotation_row(element: ET.Element, base: str, known: set[str] | None = None) -> dict:
    property_iri = _tag_iri(element.tag, base, known)
    datatype = element.get(f'{{{RDF}}}datatype', '')
    return {
        'property': property_iri,
        'value': element.get(f'{{{RDF}}}resource') or (element.text or '').strip(),
        'lang': element.get(f'{{{XML_NS}}}lang', ''),
        'datatype': _resolve(XSD, datatype) if datatype else '',
    }


def _involved_iris(element: ET.Element, base: str) -> list[str]:
    found: list[str] = []
    for node in element.iter():
        if not isinstance(node.tag, str):
            continue
        for attribute in (f'{{{RDF}}}about', f'{{{RDF}}}resource'):
            raw = node.get(attribute)
            if raw:
                found.append(_resolve(base, raw))
    return list(dict.fromkeys(found))


def _axiom_note(element: ET.Element, base: str) -> str:
    """Short deterministic structural summary for display only."""
    parts = []
    for node in element.iter():
        if not isinstance(node.tag, str):
            continue
        name = node.tag.rsplit('}', 1)[-1].rsplit('#', 1)[-1]
        on = node.get(f'{{{RDF}}}about') or node.get(f'{{{RDF}}}resource') or ''
        parts.append(f'{name}={_local_name(_resolve(base, on))}' if on else name)
    return '[' + ', '.join(parts[:8]) + ']'


def parse_structure(content: str) -> dict:
    """Parse stored RDF/XML content into the JSON entity model."""
    root = _parse_tree(content)
    if root.tag != f'{{{RDF}}}RDF':
        raise ManagementError('unsupported_format', 'Only RDF/XML documents are supported', 422)
    declarations = [child for child in root if child.tag == f'{{{OWL}}}Ontology']
    if len(declarations) != 1:
        raise ManagementError('ontology_identity', 'Declare exactly one owl:Ontology with an absolute IRI', 422)
    ontology = declarations[0]
    base = root.get(f'{{{XML_NS}}}base', '') or ontology.get(f'{{{RDF}}}about', '')
    ontology_iri = _resolve(base, ontology.get(f'{{{RDF}}}about', ''))
    if not _is_absolute(ontology_iri):
        raise ManagementError('ontology_identity', 'Ontology IRI must be absolute', 422)

    declared_annotations = set()
    declared_properties: set[str] = set()
    for child in root:
        if not isinstance(child.tag, str):
            continue
        iri = _resolve(base, child.get(f'{{{RDF}}}about', ''))
        if child.tag == f'{{{OWL}}}AnnotationProperty':
            declared_annotations.add(iri)
            declared_properties.add(iri)
        elif child.tag in (f'{{{OWL}}}DatatypeProperty', f'{{{OWL}}}ObjectProperty'):
            declared_properties.add(iri)

    def entity_from(element: ET.Element, kind: str) -> dict:
        iri = _resolve(base, element.get(f'{{{RDF}}}about', ''))
        entity: dict[str, Any] = {
            'iri': iri, 'local_name': _local_name(iri), 'kind': kind,
            'labels': [], 'comments': [], 'annotations': [],
            'complex_axioms': [],
        }
        if kind == 'class':
            entity['superclasses'] = []
        if kind in ('datatype_property', 'object_property', 'annotation_property'):
            entity['domain'] = []
            entity['range'] = []
            entity['characteristics'] = []
        if kind == 'individual':
            entity['types'] = []
            entity['values'] = []
        for child in list(element):
            if not isinstance(child.tag, str):
                continue  # XML comment or PI — preserved, never modeled
            tag = child.tag
            name = tag.rsplit('}', 1)[-1]
            tag_iri = _tag_iri(tag, base, declared_properties)
            if tag == f'{{{RDFS}}}label':
                entity['labels'].append(_lang_text(child))
            elif tag == f'{{{RDFS}}}comment':
                entity['comments'].append(_lang_text(child))
            elif tag == f'{{{RDFS}}}subClassOf':
                target = child.get(f'{{{RDF}}}resource')
                if target and len(child) == 0:
                    entity['superclasses'].append(_resolve(base, target))
                else:
                    entity['complex_axioms'].append(
                        {'kind': 'subClassOf', 'involved': _involved_iris(child, base),
                         'note': _axiom_note(child, base)})
            elif tag in (f'{{{RDFS}}}domain', f'{{{RDFS}}}range'):
                target = child.get(f'{{{RDF}}}resource')
                if target:
                    entity['domain' if tag == f'{{{RDFS}}}domain' else 'range'].append(_resolve(base, target))
                elif len(child):
                    entity['complex_axioms'].append(
                        {'kind': name, 'involved': _involved_iris(child, base), 'note': _axiom_note(child, base)})
            elif tag == f'{{{RDF}}}type':
                target = child.get(f'{{{RDF}}}resource')
                if target in _CHARACTERISTICS:
                    entity.setdefault('characteristics', []).append(_CHARACTERISTICS[target])
                elif target:
                    entity.setdefault('types', []).append(_resolve(base, target))
            elif tag_iri in declared_properties:
                if kind == 'individual' and tag_iri in declared_properties:
                    reference = child.get(f'{{{RDF}}}resource')
                    datatype = child.get(f'{{{RDF}}}datatype', '')
                    entity['values'].append({
                        'property': tag_iri,
                        'value': _resolve(base, reference) if reference else (child.text or '').strip(),
                        'datatype': _resolve(XSD, datatype) if datatype else '',
                        'lang': child.get(f'{{{XML_NS}}}lang', ''),
                        'is_ref': bool(reference),
                    })
                else:
                    entity['annotations'].append(_annotation_row(child, base, declared_annotations))
            elif tag in _COMPLEX_KINDS or (tag.startswith(f'{{{OWL}}}') and len(child)):
                entity['complex_axioms'].append(
                    {'kind': name, 'involved': _involved_iris(child, base), 'note': _axiom_note(child, base)})
        return entity

    entities: list[dict] = []
    for child in root:
        if not isinstance(child.tag, str):
            continue
        kind = _KIND_BY_TAG.get(child.tag)
        if kind and child.get(f'{{{RDF}}}about'):
            entities.append(entity_from(child, kind))

    version_info = next(((node.text or '').strip()
                         for node in ontology if node.tag == f'{{{OWL}}}versionInfo'), '') or None
    header = {
        'labels': [_lang_text(node) for node in ontology if node.tag == f'{{{RDFS}}}label'],
        'comments': [_lang_text(node) for node in ontology if node.tag == f'{{{RDFS}}}comment'],
        'annotations': [_annotation_row(node, ontology_iri, declared_annotations)
                        for node in ontology
                        if isinstance(node.tag, str)
                        and node.tag != f'{{{RDFS}}}label' and node.tag != f'{{{RDFS}}}comment'
                        and node.tag != f'{{{OWL}}}versionInfo'
                        and _tag_iri(node.tag, ontology_iri, declared_annotations) in declared_annotations],
    }
    counts = {key: 0 for key in ('classes', 'datatype_properties', 'object_properties',
                                 'annotation_properties', 'datatypes', 'individuals')}
    for entity in entities:
        counts[
            {'class': 'classes', 'datatype_property': 'datatype_properties',
             'object_property': 'object_properties', 'annotation_property': 'annotation_properties',
             'datatype': 'datatypes', 'individual': 'individuals'}[entity['kind']]
        ] += 1
    return {
        'ontology_iri': ontology_iri,
        'base': root.get(f'{{{XML_NS}}}base') or None,
        'version_info': version_info,
        'header': header,
        'namespaces': [{'prefix': prefix, 'uri': uri} for prefix, uri in _capture_namespaces(content)],
        'counts': counts,
        'entities': entities,
    }


# ─── Patching ──────────────────────────────────────────────────────────────────

def _category_of(child: ET.Element, base: str, declared: set[str]) -> str | None:
    """Classify an entity child into a rebuildable simple category, else None (preserve)."""
    if not isinstance(child.tag, str):
        return None
    tag = child.tag
    if tag == f'{{{RDFS}}}label':
        return 'labels'
    if tag == f'{{{RDFS}}}comment':
        return 'comments'
    if tag == f'{{{OWL}}}versionInfo':
        return 'version_info'
    if tag == f'{{{RDFS}}}subClassOf' and child.get(f'{{{RDF}}}resource') and not len(child):
        return 'superclasses'
    if tag == f'{{{RDFS}}}domain' and child.get(f'{{{RDF}}}resource') and not len(child):
        return 'domain'
    if tag == f'{{{RDFS}}}range' and child.get(f'{{{RDF}}}resource') and not len(child):
        return 'range'
    if tag == f'{{{RDF}}}type' and child.get(f'{{{RDF}}}resource') in _CHARACTERISTICS:
        return 'characteristics'
    if _tag_iri(tag, base, declared) in declared:
        return 'annotations'
    return None


def _category_of_individual(child: ET.Element, base: str, declared_props: set[str]) -> str | None:
    if not isinstance(child.tag, str):
        return None
    tag = child.tag
    if tag == f'{{{RDFS}}}label':
        return 'labels'
    if tag == f'{{{RDFS}}}comment':
        return 'comments'
    if tag == f'{{{RDF}}}type' and child.get(f'{{{RDF}}}resource'):
        return 'types'
    if _tag_iri(tag, base, declared_props) in declared_props:
        return 'values'
    return None


def _field_elements(field: str, value: Any, base: str) -> list[ET.Element]:
    """Build the replacement children for one simple category."""
    created: list[ET.Element] = []

    def sub(tag: str, text: str = '', attributes: dict | None = None) -> ET.Element:
        element = ET.Element(tag, attributes or {})
        element.text = text
        created.append(element)
        return element

    if field == 'labels' or field == 'comments':
        tag = f'{{{RDFS}}}label' if field == 'labels' else f'{{{RDFS}}}comment'
        for row in value:
            attributes = {}
            if row.get('lang'):
                attributes[f'{{{XML_NS}}}lang'] = row['lang']
            sub(tag, row.get('text', ''), attributes)
    elif field == 'superclasses':
        for iri in value:
            sub(f'{{{RDFS}}}subClassOf', None, {f'{{{RDF}}}resource': iri})
    elif field in ('domain', 'range'):
        for iri in value:
            sub(f'{{{RDFS}}}{field}', None, {f'{{{RDF}}}resource': iri})
    elif field == 'characteristics':
        for name in value:
            tag = _CHARACTERISTIC_TAG.get(name)
            if tag:
                sub(f'{{{RDF}}}type', None, {f'{{{RDF}}}resource': tag})
    elif field == 'types':
        for iri in value:
            sub(f'{{{RDF}}}type', None, {f'{{{RDF}}}resource': iri})
    elif field == 'annotations':
        for row in value:
            attributes = {}
            if row.get('lang'):
                attributes[f'{{{XML_NS}}}lang'] = row['lang']
            if row.get('datatype'):
                attributes[f'{{{RDF}}}datatype'] = row['datatype']
            element = sub(_clark(row['property']), None, attributes)
            if row.get('is_ref') or _looks_like_iri(row.get('value', '')):
                element.set(f'{{{RDF}}}resource', row['value'])
                element.text = None
            else:
                element.text = row.get('value', '')
    elif field == 'values':
        for row in value:
            attributes = {}
            if row.get('datatype') and not row.get('is_ref'):
                attributes[f'{{{RDF}}}datatype'] = row['datatype']
            if row.get('lang') and not row.get('is_ref'):
                attributes[f'{{{XML_NS}}}lang'] = row['lang']
            element = sub(_clark(row['property']), None, attributes)
            if row.get('is_ref'):
                element.set(f'{{{RDF}}}resource', row['value'])
            else:
                element.text = row.get('value', '')
    elif field == 'version_info':
        sub(f'{{{OWL}}}versionInfo', value)
    return created


def _looks_like_iri(value: str) -> bool:
    return _is_absolute(value) and not value.startswith(('urn:x-'))


def _clark(iri: str) -> str:
    """Convert an entity/property IRI into ElementTree's {namespace}local form."""
    if '#' in iri:
        namespace, local = iri.rsplit('#', 1)
        return '{' + namespace + '#}' + local
    if '/' in iri:
        namespace, local = iri.rsplit('/', 1)
        return '{' + namespace + '/}' + local
    raise ManagementError('invalid_reference_iri', f'Property IRI is not namespaced: {iri!r}', 422)


def _indent_pair(element: ET.Element) -> tuple[str, str]:
    """(child_indent, closing_indent) derived from the element's own whitespace."""
    text = element.text or ''
    child_indent = text[1:] if text.startswith('\n') else '  '
    if len(element) > 0:
        last_tail = element[-1].tail or ''
        if last_tail.startswith('\n'):
            closing_indent = last_tail[1:]
            if closing_indent.strip() == '':
                return child_indent, closing_indent
    return child_indent, child_indent[:-2] if len(child_indent) >= 2 else ''


def _apply_field_patch(entity_element: ET.Element, field: str, value: Any, base: str, declared: set[str]) -> None:
    """Replace one category's children in place, preserving position and neighbours."""
    classifier = _category_of_individual if entity_element.tag == f'{{{OWL}}}NamedIndividual' else _category_of
    children = list(entity_element)
    matching = [child for child in children if classifier(child, base, declared) == field]
    replacement = _field_elements(field, value, base)
    if not matching and not replacement:
        return
    if not matching:
        # Append after the last simple/preserved child, reusing sibling spacing.
        child_indent, closing_indent = _indent_pair(entity_element)
        for element in replacement:
            element.tail = f'\n{child_indent}'
            entity_element.append(element)
        if len(entity_element) > 1:
            previous = entity_element[-2]
            if not (previous.tail or '').strip():
                previous.tail = f'\n{child_indent}'
        else:
            entity_element.text = f'\n{child_indent}'
        return
    first_index = children.index(matching[0])
    anchor_tail = matching[0].tail
    insert_at = first_index
    for element in replacement:
        element.tail = anchor_tail
        entity_element.insert(insert_at, element)
        insert_at += 1
    for child in matching:
        entity_element.remove(child)


def _entity_fields(entity: dict) -> list[str]:
    fields = ['labels', 'comments', 'annotations']
    kind = entity.get('kind')
    if kind == 'class':
        fields.append('superclasses')
    if kind in ('datatype_property', 'object_property', 'annotation_property'):
        fields.extend(['domain', 'range', 'characteristics'])
    if kind == 'individual':
        fields.extend(['types', 'values'])
    return fields


def _canonical(value: Any) -> str:
    return repr(sorted((repr(row) for row in value), key=str)) if isinstance(value, list) else repr(value)


def _validate_model(model: dict, declared_annotations: set[str]) -> None:
    issues: list[dict] = []

    def problem(code: str, message: str, path: str):
        issues.append({'code': code, 'message': message, 'path': path})

    seen: set[str] = set()
    for index, entity in enumerate(model.get('entities') or []):
        path = f'entities[{index}]'
        iri = entity.get('iri', '')
        kind = entity.get('kind')
        if kind not in _KIND_BY_TAG.values():
            problem('unknown_kind', f'Unsupported entity kind: {kind!r}', path)
            continue
        if not _is_absolute(iri):
            problem('invalid_iri', f'Entity IRI must be absolute: {iri!r}', path)
            continue
        if iri in seen:
            problem('duplicate_iri', f'Entity declared twice: {iri}', path)
        seen.add(iri)
        for field in ('labels', 'comments'):
            for row in entity.get(field) or []:
                if not str(row.get('text', '')).strip():
                    problem('empty_lang_text', f'{field} rows must have text', f'{path}.{field}')
        for row in entity.get('annotations') or []:
            if row.get('property') not in declared_annotations:
                problem('annotation_property_not_declared',
                        f"Annotation property not declared in this file: {row.get('property')}",
                        f'{path}.annotations')
        for field in ('superclasses', 'domain', 'range', 'types'):
            for reference in entity.get(field) or []:
                if not _is_absolute(str(reference)):
                    problem('invalid_reference_iri', f'{field} reference must be an absolute IRI: {reference!r}',
                            f'{path}.{field}')
        for row in entity.get('values') or []:
            if row.get('property') not in declared_annotations and not _is_absolute(str(row.get('property', ''))):
                problem('invalid_reference_iri', f"Value property must be an absolute IRI: {row.get('property')!r}",
                        f'{path}.values')
    if issues:
        raise ManagementError('invalid_structure', 'Structure model failed validation', 422, issues=issues)


def _serialize(root: ET.Element, namespaces: list[tuple[str, str]], declaration: str) -> str:
    with _NS_LOCK:
        snapshot = dict(getattr(ET, '_namespace_map', {}))
        try:
            for prefix, uri in namespaces:
                ET.register_namespace(prefix, uri)
            body = ET.tostring(root, encoding='unicode')
        finally:
            setattr(ET, '_namespace_map', snapshot)
    return f'{declaration}\n{body}' if declaration else body


def _declaration_of(content: str) -> str:
    matched = _DECLARATION_MATCHED.match(content)
    if matched:
        return matched.group(0).rstrip('\r\n')
    return '<?xml version="1.0" encoding="UTF-8"?>'


def _epilog_of(content: str) -> str:
    """Text after the root close tag — ET cannot represent it (banner comments)."""
    marker = content.rindex('</rdf:RDF>') if '</rdf:RDF>' in content else -1
    if marker < 0:
        return ''
    return content[marker + len('</rdf:RDF>'):].lstrip('\r\n')


def patch_structure(content: str, model: dict) -> tuple[str, dict]:
    """Apply the edited model onto the stored content with minimal churn."""
    root = _parse_tree(content)
    if root.tag != f'{{{RDF}}}RDF':
        raise ManagementError('unsupported_format', 'Only RDF/XML documents are supported', 422)
    ontology = next((child for child in root if child.tag == f'{{{OWL}}}Ontology'), None)
    if ontology is None:
        raise ManagementError('ontology_identity', 'owl:Ontology declaration missing', 422)
    base = root.get(f'{{{XML_NS}}}base', '') or ontology.get(f'{{{RDF}}}about', '')

    declared_annotations: set[str] = set()
    declared_props: set[str] = set()
    index: dict[str, ET.Element] = {}
    for child in root:
        if not isinstance(child.tag, str):
            continue
        iri = _resolve(base, child.get(f'{{{RDF}}}about', ''))
        if child.tag == f'{{{OWL}}}AnnotationProperty':
            declared_annotations.add(iri)
            declared_props.add(iri)
        elif child.tag in (f'{{{OWL}}}DatatypeProperty', f'{{{OWL}}}ObjectProperty'):
            declared_props.add(iri)
        if _KIND_BY_TAG.get(child.tag) and child.get(f'{{{RDF}}}about'):
            index[iri] = child

    # Newly declared annotation properties in the model join the declared set.
    for entity in model.get('entities') or []:
        if entity.get('kind') == 'annotation_property' and _is_absolute(entity.get('iri', '')):
            declared_annotations.add(entity['iri'])
            declared_props.add(entity['iri'])
    _validate_model(model, declared_annotations)

    model_entities = {entity['iri']: entity for entity in model.get('entities') or []
                      if _is_absolute(entity.get('iri', '')) and entity.get('kind') in _KIND_BY_TAG.values()}
    added = [iri for iri in model_entities if iri not in index]
    removed = [iri for iri in index if iri not in model_entities]
    changed: list[dict] = []

    # One original-model parse drives every per-field comparison.
    original_model = parse_structure(content)
    original_entities = {entity['iri']: entity for entity in original_model['entities']}

    for iri, entity in model_entities.items():
        element = index.get(iri)
        if element is None:
            continue
        fields = []
        original = original_entities.get(iri)
        for field in _entity_fields(entity):
            new_value = entity.get(field)
            old_value = (original or {}).get(field)
            if _canonical(new_value) != _canonical(old_value):
                _apply_field_patch(element, field, new_value or [], base,
                                   declared_props if entity['kind'] == 'individual' else declared_annotations)
                fields.append(field)
        if fields:
            changed.append({'iri': iri, 'fields': fields})

    # Header (owl:Ontology labels/comments/annotations/versionInfo).
    header_changes: list[str] = []
    parsed_header = original_model['header']
    for field, key in (('labels', 'labels'), ('comments', 'comments'), ('annotations', 'annotations')):
        if _canonical((model.get('header') or {}).get(key)) != _canonical(parsed_header.get(key)):
            _apply_field_patch(ontology, field, (model.get('header') or {}).get(key) or [], base, declared_annotations)
            header_changes.append(key)
    if model.get('version_info') != original_model.get('version_info'):
        _apply_field_patch(ontology, 'version_info', model.get('version_info') or '', base, declared_annotations)
        header_changes.append('version_info')

    # Deletions: transfer tail whitespace to the previous sibling to keep spacing.
    for iri in removed:
        element = index[iri]
        siblings = list(root)
        position = siblings.index(element)
        if position > 0 and element.tail:
            previous = siblings[position - 1]
            previous.tail = (previous.tail or '') + element.tail
        root.remove(element)

    # Additions: append with the document's own indentation style.
    if added:
        child_indent, closing_indent = _indent_pair(root)
        last = list(root)[-1] if len(root) else None
        for iri in added:
            entity = model_entities[iri]
            element = ET.Element(_TAG_BY_KIND[entity['kind']], {f'{{{RDF}}}about': iri})
            element.text = f'\n{child_indent}'
            element.tail = last.tail if last is not None and last.tail else f'\n{closing_indent}'
            if last is not None:
                last.tail = f'\n{child_indent}'
            for field in _entity_fields(entity):
                for child in _field_elements(field, entity.get(field) or [], base):
                    child.tail = f'\n{child_indent}'
                    element.append(child)
            entity_element_indent, entity_close = _indent_pair(element)
            if len(element):
                element[-1].tail = f'\n{entity_close}'
            root.append(element)
            last = element

    if not (added or removed or changed or header_changes):
        return content, {'added': [], 'removed': [], 'changed': [], 'header_changes': [], 'warnings': []}

    namespaces = [(row['prefix'], row['uri']) for row in model.get('namespaces') or []] \
        or _capture_namespaces(content)
    epilog = _epilog_of(content)
    new_content = _serialize(root, namespaces, _declaration_of(content))
    if epilog:
        new_content = new_content.rstrip('\n') + '\n' + epilog if epilog.strip() else new_content + epilog
    # Self-check: the patched document must still be well-formed.
    _parse_tree(new_content)

    warnings = []
    removed_set = set(removed)
    if removed_set:
        dangling = set()
        for entity in model_entities.values():
            for field in ('superclasses', 'domain', 'range', 'types'):
                for reference in entity.get(field) or []:
                    if reference in removed_set:
                        dangling.add(reference)
            for axiom in entity.get('complex_axioms') or []:
                for reference in axiom.get('involved') or []:
                    if reference in removed_set:
                        dangling.add(reference)
        for iri in sorted(dangling):
            warnings.append(f'dangling_reference: {iri}')

    return new_content, {
        'added': added, 'removed': removed, 'changed': changed,
        'header_changes': header_changes, 'warnings': warnings,
    }
