"""Caller-owned TEMP projection for recovery of already processed knowledge.

The main database is never changed. A relation crossing unfinished videos is
omitted as a whole; its unavailable association pointer is removed only from
the projection. Claims, evidence, derivation bodies and complete relations are
not rewritten. Historical relations whose members were replaced by an
unfinished rerun remain unavailable. Malformed relations, contradictory
complete memberships and broken local evidence remain errors.
"""
from contextlib import contextmanager
import json

from . import sync_protocol as protocol, sync_snapshot as snapshot
from .sync_protocol import SyncError


TABLES = ('video_asset', 'knowledge_atom', 'knowledge_cluster', 'conflict_case')


def _relation_set(con):
    """Select whole closed relations, then validate the retained graph."""
    videos = {row[0] for row in con.execute('SELECT video_id FROM main.video_asset LIMIT ?',
                                          (snapshot.MAX_VIDEOS + 1,))}
    if len(videos) > snapshot.MAX_VIDEOS:
        snapshot._invalid('Recovery source videos exceed their count limit', status=413)
    if con.execute('SELECT 1 FROM main.evidence_ref e LEFT JOIN main.knowledge_atom a ON a.id=e.atom_id '
                   'WHERE a.id IS NULL OR e.video_id IS NOT a.video_id LIMIT 1').fetchone():
        snapshot._incomplete('Evidence belongs to a different or missing atom video')
    atoms, graph = {}, {vid: [] for vid in videos}
    size = 0
    for row in con.execute('SELECT id,video_id,cluster_id FROM main.knowledge_atom ORDER BY id'):
        item = dict(row)
        if len(atoms) >= protocol.MAX_ROWS:
            snapshot._invalid('Recovery atom graph exceeds its count limit', status=413)
        size += len(snapshot._encoded(item))
        if size > snapshot.MAX_SNAPSHOT_BYTES:
            snapshot._invalid('Recovery atom graph exceeds its byte limit', status=413)
        if item['video_id'] not in videos:
            snapshot._incomplete('Knowledge atoms belong to a missing video')
        atoms[item['id']] = item
        graph[item['video_id']].append(item)
    budget = [size]
    processed = {row[0] for row in con.execute(
        "SELECT video_id FROM main.video_asset WHERE status='processed'")}
    for vid in sorted(processed):
        tables = {table: snapshot._wire_rows(con, table, video_id=vid, budget=budget)
                  for table in snapshot.VIDEO_TABLES}
        snapshot._check_local_references({'tables': tables})
    globals_ = {table: snapshot._wire_rows(con, table, budget=budget)
                for table in snapshot.GLOBAL_TABLES}
    retained_atoms = {aid for aid, atom in atoms.items() if atom['video_id'] in processed}
    conflicts = set()
    for row in globals_['conflict_case']:
        sides = [json.loads(row[column]) if row[column] is not None else {}
                 for column in ('side_a_json', 'side_b_json')]
        for side in sides:
            if side.get('video') is not None:
                if type(side['video']) is not str:
                    snapshot._invalid('Conflict side video must be a video identifier')
                protocol.validate_video_id(side['video'])
        refs = [set(snapshot._refs(side.get('atoms'), 'Conflict side atoms')) for side in sides]
        snapshot._check_derivation_references(row, set().union(*refs))
        if all(members <= retained_atoms
               and (side.get('video') is None or side['video'] in processed)
               for members, side in zip(refs, sides)):
            conflicts.add(row['conflict_id'])
    clusters = set()
    for row in globals_['knowledge_cluster']:
        members = set(snapshot._refs(json.loads(row['members_json']) if row['members_json'] is not None else None,
                                     'Cluster members'))
        snapshot._check_derivation_references(row, members)
        if members <= retained_atoms and (row['linked_conflict'] is None or row['linked_conflict'] in conflicts):
            clusters.add(row['cluster_id'])
    source_clusters = {row['cluster_id'] for row in globals_['knowledge_cluster']}
    projected_graph = []
    for vid in sorted(processed):
        rows = []
        for atom in graph[vid]:
            if atom['cluster_id'] is not None and atom['cluster_id'] not in source_clusters:
                snapshot._incomplete('Atom points to a missing source cluster')
            rows.append({**atom, 'cluster_id': atom['cluster_id'] if atom['cluster_id'] in clusters else None})
        projected_graph.append({'video_id': vid, 'tables': {'knowledge_atom': rows}})
    snapshot._check_global_references({'videos': projected_graph, 'globals': {
        'knowledge_cluster': [row for row in globals_['knowledge_cluster'] if row['cluster_id'] in clusters],
        'conflict_case': [row for row in globals_['conflict_case'] if row['conflict_id'] in conflicts]}})
    return clusters, conflicts


@contextmanager
def processed_view(con):
    """Keep one consistent caller transaction; restore its SQL namespace.

    TEMP tables shadow four read tables only for this connection. Operational
    tables, review events and version metadata remain the original database.
    No commit, rollback, model or external file operation occurs here.
    """
    if not con.in_transaction:
        raise SyncError('Recovery projection requires an existing read transaction',
                        'SYNC_READ_TRANSACTION_REQUIRED', 409)
    if con.execute("SELECT 1 FROM sqlite_temp_master WHERE lower(name) IN (?,?,?,?)",
                   TABLES).fetchone() is not None:
        raise SyncError('Recovery projection has a conflicting temporary table',
                        'SYNC_RECOVERY_PROJECTION_CONFLICT', 409)
    clusters, conflicts = _relation_set(con)
    created = []
    try:
        con.execute("CREATE TEMP TABLE video_asset AS SELECT * FROM main.video_asset WHERE status='processed'")
        created.append('video_asset')
        con.execute('CREATE TEMP TABLE knowledge_atom AS SELECT * FROM main.knowledge_atom '
                    'WHERE video_id IN (SELECT video_id FROM temp.video_asset)')
        created.append('knowledge_atom')
        for table, key, selected in (('knowledge_cluster', 'cluster_id', clusters),
                                     ('conflict_case', 'conflict_id', conflicts)):
            con.execute(f'CREATE TEMP TABLE "{table}" AS SELECT * FROM main."{table}" WHERE 0')
            created.append(table)
            con.executemany(f'INSERT INTO temp."{table}" SELECT * FROM main."{table}" WHERE "{key}"=?',
                            [(value,) for value in sorted(selected)])
        con.execute('UPDATE temp.knowledge_atom SET cluster_id=NULL WHERE cluster_id IS NOT NULL '
                    'AND cluster_id NOT IN (SELECT cluster_id FROM temp.knowledge_cluster)')
        yield con
    finally:
        for table in reversed(created):
            con.execute(f'DROP TABLE temp."{table}"')
