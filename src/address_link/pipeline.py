from .io import load
from .link import link

def run(path,decisions_path=None):
    decisions=load(decisions_path) if decisions_path is not None else None
    return link(load(path),decisions)
