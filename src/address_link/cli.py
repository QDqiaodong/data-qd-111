DESCRIPTION='Deterministic address linkage with conflict-aware connected components'
import argparse, json, sys
from pathlib import Path
from .io import publish
from .pipeline import run

def main():
    p=argparse.ArgumentParser(description=DESCRIPTION)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--decisions',type=Path,default=None,
                   help='JSON object with only same and different, each a list of source-id pairs')
    a=p.parse_args()
    try:
        if a.source.resolve()==a.output.resolve(): raise ValueError('source and output must differ')
        if a.decisions is not None and a.decisions.resolve()==a.output.resolve():
            raise ValueError('decisions and output must differ')
        result=run(a.source,a.decisions);publish(a.output,result)
    except (ValueError,OSError,KeyError,TypeError,OverflowError,RecursionError) as e:
        print(str(e),file=sys.stderr);return 2
    print(json.dumps(result,ensure_ascii=False,sort_keys=True,allow_nan=False));return 0

if __name__=='__main__': raise SystemExit(main())
