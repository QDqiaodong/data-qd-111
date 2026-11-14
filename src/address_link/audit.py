"""Pre-import quality diagnostics for address batches.

Audit mode never builds candidate edges and never groups.  It applies the same
text and normalization contract as :mod:`address_link.link` and reports every
independent problem of every source row, so an operator can locate the whole
batch's contract issues in one pass.  Whole-batch failures (JSON syntax or
duplicate keys, root structure, input size limit) still raise before any
report exists.

Only rows with at least one problem appear in ``rows``; each entry keeps its
0-based source array index rather than the ID sort order used by the linkage
report.  Rule codes are sorted within a row, and ``rule_counts`` counts the
rows carrying each code across the whole batch.  Codes are ``<field>_empty``,
``_too_long``, ``_control_char``, ``_not_text``, and, for the NFKC-normalized
address fields and unit, ``_empty_normalized``; row-level codes are
``record_not_object``, ``missing_fields``, ``unexpected_fields`` and
``id_duplicate``.
"""
from collections import Counter
from .io import array, fields
from .link import normalized_text

FIELDS='id registry_id city postal street building unit'.split()
ADDRESS=('city','postal','street','building')

def _normalized_empty(value):
    """Whether the linkage contract's NFKC/casefold normalization erases the value.

    ``normalized_text`` performs only the text transformation, so a length or
    control-character problem can be reported alongside an empty normalized
    form instead of aborting the whole audit.
    """
    return normalized_text(value)==''

def _value_codes(value,field,normalized=False):
    """All independent rule codes for one value under the shared text contract.

    JSON null is an empty value; non-string values are not text.  For a string,
    emptiness, length and control characters are separate checks, as is
    emptiness after NFKC normalization (address fields and unit only).  A raw
    empty value is not reported twice: its normalized form is necessarily empty
    too, so ``_empty`` already covers the same underlying problem.
    """
    if value is None: return [field+'_empty']
    if not isinstance(value,str): return [field+'_not_text']
    codes=[]
    if not value.strip(): codes.append(field+'_empty')
    if len(value)>256: codes.append(field+'_too_long')
    if any(ord(c)<32 for c in value): codes.append(field+'_control_char')
    if normalized and value.strip() and _normalized_empty(value):
        codes.append(field+'_empty_normalized')
    return codes

def _row_codes(row,duplicate_ids):
    if not isinstance(row,dict): return ['record_not_object']
    codes=[];keys=set(row);expected=set(FIELDS)
    if expected-keys: codes.append('missing_fields')
    if keys-expected: codes.append('unexpected_fields')
    if 'id' in row:
        ident=row['id'];codes+=_value_codes(ident,'id')
        # IDs are opaque and never normalized, so duplicates compare exact raw
        # strings.  Every row sharing the same string is marked, including the
        # first occurrence and rows that also carry other ID problems.
        if isinstance(ident,str) and duplicate_ids[ident]>1: codes.append('id_duplicate')
    if row.get('registry_id') is not None:
        codes+=_value_codes(row['registry_id'],'registry_id')
    for field in ADDRESS:
        if field in row: codes+=_value_codes(row[field],field,normalized=True)
    if row.get('unit') is not None:
        codes+=_value_codes(row['unit'],'unit',normalized=True)
    return codes

def audit(data):
    """Return a deterministic per-row diagnostic report; never link or group.

    Raises ``ValueError`` for whole-batch contract failures (root shape, record
    array bounds, or JSON syntax/duplicate keys/input size in ``io.load``).
    A duplicate ID is reported on every involved row instead of aborting.
    """
    fields(data,'records');rows=array(data['records'])
    duplicate_ids=Counter(row['id'] for row in rows
                          if isinstance(row,dict) and isinstance(row.get('id'),str))
    report_rows=[];counts=Counter()
    for index,row in enumerate(rows):
        codes=sorted(set(_row_codes(row,duplicate_ids)))
        if codes:
            report_rows.append({'index':index,'rule_codes':codes});counts.update(codes)
    return {'record_count':len(rows),'valid_count':len(rows)-len(report_rows),
            'problem_count':len(report_rows),'rule_counts':dict(sorted(counts.items())),
            'rows':report_rows}
