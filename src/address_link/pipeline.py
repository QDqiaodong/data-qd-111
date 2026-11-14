from .io import load
from .link import link

def run(path):
    return link(load(path))
