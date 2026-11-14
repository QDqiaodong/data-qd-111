"""Conservative, explainable linkage; conflicts remain review groups."""
from collections import defaultdict
import re, unicodedata
from .io import fields, text, array

def normalize(value):
    value=unicodedata.normalize('NFKC',text(value)).casefold()
    return ' '.join(re.sub(r'[^\w\s]',' ',value).split())

def _validate_decisions(decisions,known_ids):
    """Return canonical (same, different) pair tuples, sorted and de-duplicated."""
    if not isinstance(decisions,dict) or set(decisions)!={'same','different'}:
        raise ValueError('decisions must be an object containing only same and different')
    claims={};canonical={}
    for kind in ('same','different'):
        raw=decisions[kind]
        if not isinstance(raw,list): raise ValueError('decision %s must be an array of pairs'%kind)
        pairs=[]
        for item in raw:
            if not isinstance(item,list) or len(item)!=2 or not all(isinstance(v,str) for v in item):
                raise ValueError('decision entry must be a pair of id strings')
            a,b=item
            if a==b: raise ValueError('self pair is not a valid decision: %s'%a)
            if a not in known_ids or b not in known_ids:
                missing=a if a not in known_ids else b
                raise ValueError('unknown id in decision: %s'%missing)
            pair=tuple(sorted((a,b)))
            if pair in claims:
                if claims[pair]!=kind:
                    raise ValueError('pair declared both same and different: [%s, %s]'%pair)
                raise ValueError('duplicate decision pair: [%s, %s]'%pair)
            claims[pair]=kind;pairs.append(pair)
        canonical[kind]=sorted(pairs)
    return canonical['same'],canonical['different']

def _load_records(data):
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
    records.sort(key=lambda r:r['id'])
    return records,seen

def _candidate_edges(records):
    edges=[];indexes=defaultdict(list)
    for r in records:
        n=r['normalized'];keys=[]
        if r['registry_id'] is not None: keys.append(('registry',r['registry_id']))
        if n['unit'] is not None:
            keys.append(('address',n['city'],n['postal'],n['street'],n['building'],n['unit']))
        for key in keys:
            if indexes[key]:
                edges.append({'left':indexes[key][0],'right':r['id'],'reason':key[0]})
            indexes[key].append(r['id'])
    return edges

def _entities(groups,by_id):
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
    return entities

def link(data,decisions=None):
    records,seen=_load_records(data);by_id={r['id']:r for r in records}
    edges=_candidate_edges(records)
    parent={r['id']:r['id'] for r in records}
    def find(x):
        while parent[x]!=x:
            parent[x]=parent[parent[x]];x=parent[x]
        return x
    def union(a,b):
        x,y=sorted((find(a),find(b)));parent[y]=x
    if decisions is None:
        for edge in edges: union(edge['left'],edge['right'])
        groups=defaultdict(list)
        for r in records: groups[find(r['id'])].append(r['id'])
        entities=_entities(groups,by_id)
        return {'record_count':len(records),'group_count':len(entities),'groups':entities,'evidence_edges':edges,
                'normalized_records':records}
    same_pairs,different_pairs=_validate_decisions(decisions,seen)
    # Manual same-claims form the transitive closure before any candidate edge is tried.
    for a,b in same_pairs: union(a,b)
    for pair in different_pairs:
        if find(pair[0])==find(pair[1]):
            raise ValueError('different decision falls inside same closure: [%s, %s]'%pair)
    # enemy adjacency between current roots: neighbour root -> lexicographically smallest blocking pair
    enemies=defaultdict(dict)
    for pair in different_pairs:
        x,y=find(pair[0]),find(pair[1])
        prior=enemies[x].get(y)
        if prior is None or pair<prior: enemies[x][y]=pair;enemies[y][x]=pair
    candidates=sorted(edges,key=lambda e:(e['reason'],e['left'],e['right']))
    audit=[];accepted=[]
    for edge in candidates:
        entry={'left':edge['left'],'right':edge['right'],'reason':edge['reason']}
        x,y=find(edge['left']),find(edge['right'])
        if x==y:
            entry['outcome']='accepted'
        elif y in enemies[x]:
            entry['outcome']='blocked'
            entry['blocked_by']=list(enemies[x][y])
        else:
            ex,ey=enemies[x],enemies[y]
            if len(ex)>=len(ey): survivor,disappearing,big,small=x,y,ex,ey
            else: survivor,disappearing,big,small=y,x,ey,ex
            parent[disappearing]=survivor
            for neighbour,pair in small.items():
                old=big.get(neighbour)
                if old is None or pair<old: big[neighbour]=pair
                back=enemies[neighbour];back.pop(disappearing)
                cur=back.get(survivor)
                if cur is None or pair<cur: back[survivor]=pair
            enemies.pop(disappearing,None)
            entry['outcome']='accepted'
        audit.append(entry)
        if entry['outcome']=='accepted':
            accepted.append({'left':edge['left'],'right':edge['right'],'reason':edge['reason']})
    groups=defaultdict(list)
    for r in records: groups[find(r['id'])].append(r['id'])
    entities=_entities(groups,by_id)
    return {'record_count':len(records),'group_count':len(entities),'groups':entities,
            'evidence_edges':accepted,'normalized_records':records,
            'decision_audit':{'same':[list(p) for p in same_pairs],
                              'different':[list(p) for p in different_pairs],
                              'candidate_edges':audit}}
