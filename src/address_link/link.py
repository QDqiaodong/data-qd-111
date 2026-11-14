"""Conservative, explainable linkage; conflicts remain review groups."""
from collections import defaultdict
import re, unicodedata
from .io import fields, text, array

def normalize(value):
    value=unicodedata.normalize('NFKC',text(value)).casefold()
    return ' '.join(re.sub(r'[^\w\s]',' ',value).split())

def link(data):
    fields(data,'records');records=[];seen=set()
    for r in array(data['records']):
        fields(r,'id registry_id city postal street building unit')
        ident=text(r['id'])
        if ident in seen: raise ValueError('duplicate record id')
        seen.add(ident)
        normalized={k:normalize(r[k]) for k in ('city','postal','street','building')}
        if not all(normalized.values()): raise ValueError('normalization produced empty identity field')
        for k in ('unit','registry_id'):
            if r[k] is not None: text(r[k])
        unit=normalize(r['unit']) if r['unit'] is not None else None
        if unit=='': raise ValueError('empty normalized unit')
        records.append(dict(r,normalized=dict(normalized,unit=unit)))
    records.sort(key=lambda r:r['id']);by_id={r['id']:r for r in records}
    parent={r['id']:r['id'] for r in records};edges=[];indexes=defaultdict(list)
    def find(x):
        while parent[x]!=x:
            parent[x]=parent[parent[x]];x=parent[x]
        return x
    def union(a,b):
        x,y=sorted((find(a),find(b)));parent[y]=x
    for r in records:
        n=r['normalized'];keys=[]
        if r['registry_id'] is not None: keys.append(('registry',r['registry_id']))
        if n['unit'] is not None:
            keys.append(('address',n['city'],n['postal'],n['street'],n['building'],n['unit']))
        for key in keys:
            if indexes[key]:
                other=indexes[key][0];union(other,r['id'])
                edges.append({'left':other,'right':r['id'],'reason':key[0]})
            indexes[key].append(r['id'])
    groups=defaultdict(list)
    for r in records: groups[find(r['id'])].append(r['id'])
    entities=[]
    for ids in sorted(groups.values(),key=lambda ids:ids[0]):
        rows=[by_id[x] for x in ids]
        authorities=sorted({r['registry_id'] for r in rows if r['registry_id'] is not None})
        addresses={tuple(r['normalized'][k] for k in ('city','postal','street','building','unit')) for r in rows}
        reasons=[]
        if len(authorities)>1: reasons.append('conflicting_registry_ids')
        if len(addresses)>1 and len(ids)>1: reasons.append('different_address_assertions')
        entities.append({'group_id':ids[0],'members':ids,'registry_ids':authorities,
                        'status':'review' if reasons else ('linked' if len(ids)>1 else 'singleton'),
                        'review_reasons':reasons})
    return {'record_count':len(records),'group_count':len(entities),'groups':entities,'evidence_edges':edges,
            'normalized_records':records}
