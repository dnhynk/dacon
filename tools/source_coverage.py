"""Reading coverage across source-addressed excerpts; not citation or truth certification."""
from submission.pps.notice_search import merge_ranges


def reading_ranges(record,spans):
    """Join only overlap, adjacency and whitespace observed in this same source."""
    return [{'doc_index':di,'start':lo,'end':hi} for di,lo,hi in merge_ranges(
        [(s['doc_index'],s['start'],s['end']) for s in spans],record['docs'])]


def covered(record,spans,witness):
    return any(s['doc_index']==witness['doc_index'] and s['start']<=witness['start']
        and s['end']>=witness['end'] for s in reading_ranges(record,spans))
