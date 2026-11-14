from .io import load
from .link import link
from .audit import audit as build_report

def audit(path):
    """Read one source file and return its per-row diagnostic report."""
    return build_report(load(path))

def run(path,decisions_path=None,audit_only=False):
    """Link a batch, or produce its pre-import report when ``audit_only`` is true.

    Audit mode neither reads the decisions file nor builds candidate edges or
    groups, so the default linkage result (with or without decisions) cannot be
    affected.  Adjudication only constrains grouping, which audit mode never
    computes; supplying it there is rejected rather than silently ignored.
    """
    if audit_only:
        if decisions_path is not None:
            raise ValueError('--decisions cannot be combined with --audit-only')
        return audit(path)
    decisions=load(decisions_path) if decisions_path is not None else None
    return link(load(path),decisions)
